"""Monitoring, VLAN, port and LACP managers for Netgear Smart Managed Pro switches (SNMP)."""

import re
from collections.abc import Callable
from functools import wraps

from loguru import logger

from networkmgmt.snmp import (
    SnmpError,
    SnmpNoSuchObjectError,
    SnmpSettleTimeoutError,
    integer,
    octets,
    poll_until,
    portlist_patch,
    ports_to_portlist,
    to_switch_error,
    unsigned,
)
from networkmgmt.switchctrl.base.managers import (
    BaseLACPManager,
    BaseMonitoringManager,
    BasePortManager,
    BaseVLANManager,
)
from networkmgmt.switchctrl.exceptions import LACPError, PortError, SwitchError, VLANError
from networkmgmt.switchctrl.models.port import PortConfig, PortMode, PortSpeed, PortStatus
from networkmgmt.switchctrl.models.stats import PortStatistics
from networkmgmt.switchctrl.models.system import LACPInfo, SensorData, SystemInfo
from networkmgmt.switchctrl.models.vlan import VLAN, TrunkConfig
from networkmgmt.switchctrl.vendors.netgear import oids
from networkmgmt.switchctrl.vendors.netgear.backend import NetgearBackend, VlanState, VlanTable
from networkmgmt.switchctrl.vendors.netgear.models import StackPort, StackUnit

_STACK_MODES = {oids.STACK_MODE_STACK: "stack", oids.STACK_MODE_ETHERNET: "ethernet"}
_LAG_NAME_RE = re.compile(r"^l(\d+)$", re.I)


def snmp_errors[**P, R](fallback: type[SwitchError]) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator mapping :class:`SnmpError` onto the switchctrl exception hierarchy.

    Args:
        fallback: Exception class for every failure that is not an authentication error.

    Returns:
        The decorator.
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            try:
                return func(*args, **kwargs)
            except SnmpError as exc:
                raise to_switch_error(exc, fallback) from exc

        return wrapper

    return decorator


def _is_port_name(name: str) -> bool:
    return not name.lower().startswith(("vlan", "cpu"))


def _format_mac(raw: bytes) -> str:
    return ":".join(f"{b:02x}" for b in raw)


class NetgearMonitoringManager(BaseMonitoringManager):
    """Read-only state via IF-MIB, ENTITY-MIB and the FASTPATH stack tables."""

    def __init__(self, backend: NetgearBackend):
        """Create the manager.

        Args:
            backend: Shared SNMP backend.
        """
        self._b = backend

    @snmp_errors(PortError)
    def get_port_status(self) -> list[PortStatus]:
        """Return link state and speed of all ports and LAGs, keyed by ifName."""
        s = self._b.session
        names = {i: n for i, n in self._b.ifnames().items() if _is_port_name(n)}
        oper = s.walk_int(oids.IF_OPER_STATUS)
        speed = s.walk_int(oids.IF_HIGH_SPEED)
        duplex = self._duplex()
        result = []
        for idx, name in sorted(names.items()):
            up = oper.get(str(idx)) == oids.OPER_UP
            mbps = speed.get(str(idx), 0)
            result.append(
                PortStatus(
                    port=name,
                    link_up=up,
                    speed=str(mbps) if up and mbps else "",
                    duplex=duplex.get(idx, "") if up else "",
                    max_speed="",
                )
            )
        return result

    def _duplex(self) -> dict[int, str]:
        try:
            raw = self._b.session.walk_int(oids.DOT3_STATS_DUPLEX)
        except SnmpError as exc:
            logger.debug(f"dot3StatsDuplexStatus not readable: {exc}")
            return {}
        label = {oids.DUPLEX_HALF: "half", oids.DUPLEX_FULL: "full"}
        return {int(k): label[v] for k, v in raw.items() if v in label}

    @snmp_errors(PortError)
    def get_port_statistics(self) -> list[PortStatistics]:
        """Return traffic counters per port; falls back to 32-bit counters if HC counters are absent."""
        s = self._b.session
        names = {i: n for i, n in self._b.ifnames().items() if _is_port_name(n)}
        oper = s.walk_int(oids.IF_OPER_STATUS)
        speed = s.walk_int(oids.IF_HIGH_SPEED)
        in_errors = s.walk_int(oids.IF_IN_ERRORS)
        out_errors = s.walk_int(oids.IF_OUT_ERRORS)
        rx_bytes = s.walk_int(oids.IF_HC_IN_OCTETS)
        if rx_bytes:
            tx_bytes = s.walk_int(oids.IF_HC_OUT_OCTETS)
            rx_pkts = [
                s.walk_int(o)
                for o in (oids.IF_HC_IN_UCAST_PKTS, oids.IF_HC_IN_MULTICAST_PKTS, oids.IF_HC_IN_BROADCAST_PKTS)
            ]
            tx_pkts = [
                s.walk_int(o)
                for o in (oids.IF_HC_OUT_UCAST_PKTS, oids.IF_HC_OUT_MULTICAST_PKTS, oids.IF_HC_OUT_BROADCAST_PKTS)
            ]
        else:
            rx_bytes, tx_bytes = s.walk_int(oids.IF_IN_OCTETS), s.walk_int(oids.IF_OUT_OCTETS)
            rx_pkts, tx_pkts = [s.walk_int(oids.IF_IN_UCAST_PKTS)], [s.walk_int(oids.IF_OUT_UCAST_PKTS)]
        result = []
        for idx, name in sorted(names.items()):
            k = str(idx)
            up = oper.get(k) == oids.OPER_UP
            result.append(
                PortStatistics(
                    port=name,
                    tx_bytes=tx_bytes.get(k, 0),
                    rx_bytes=rx_bytes.get(k, 0),
                    tx_packets=sum(t.get(k, 0) for t in tx_pkts),
                    rx_packets=sum(t.get(k, 0) for t in rx_pkts),
                    tx_errors=out_errors.get(k, 0),
                    rx_errors=in_errors.get(k, 0),
                    link_up=up,
                    speed=str(speed.get(k, 0)) if up and speed.get(k) else "",
                )
            )
        return result

    @snmp_errors(PortError)
    def get_system_info(self) -> SystemInfo:
        """Return name, model, firmware, uptime and bridge MAC of the (first) chassis.

        ``serial_number`` stays empty: the ENTITY-MIB of FW 6.6.4.28 reports the management unit's serial
        for every stack member, which is not the serial of the unit it is listed under.
        """
        s = self._b.session
        classes = s.walk_int(oids.ENT_CLASS)
        chassis = sorted((int(k) for k, v in classes.items() if v == oids.ENT_CLASS_CHASSIS))
        models = s.walk(oids.ENT_MODEL)
        firmware = s.walk(oids.ENT_SOFTWARE_REV)
        first = str(chassis[0]) if chassis else ""
        try:
            mac = _format_mac(s.get_octets(oids.BRIDGE_ADDRESS))
        except SnmpError as exc:
            logger.debug(f"dot1dBaseBridgeAddress not readable: {exc}")
            mac = ""
        return SystemInfo(
            hostname=s.get(oids.SYS_NAME),
            mac_address=mac,
            serial_number="",
            firmware_version=firmware.get(first, ""),
            model=models.get(first) or s.get(oids.SYS_DESCR),
            uptime=s.get_int(oids.SYS_UPTIME) // 100,
        )

    def get_sensor_data(self) -> SensorData:
        """Not available: no sensor OID has been validated against hardware."""
        raise NotImplementedError("Netgear sensor data (boxServices MIB) is not implemented")

    @snmp_errors(LACPError)
    def get_lacp_info(self) -> list[LACPInfo]:
        """Return the LAGs (ifName ``l<n>``) with their member ports."""
        return NetgearLACPManager(self._b).get_port_channel_info()

    @snmp_errors(PortError)
    def get_stack_units(self) -> list[StackUnit]:
        """Return the chassis units of the stack, in ENTITY-MIB order."""
        s = self._b.session
        classes = s.walk_int(oids.ENT_CLASS)
        names, models, firmware = s.walk(oids.ENT_NAME), s.walk(oids.ENT_MODEL), s.walk(oids.ENT_SOFTWARE_REV)
        return [
            StackUnit(name=names.get(k, ""), model=models.get(k, ""), firmware=firmware.get(k, ""))
            for k in sorted((k for k, v in classes.items() if v == oids.ENT_CLASS_CHASSIS), key=int)
        ]

    @snmp_errors(PortError)
    def get_stack_ports(self) -> list[StackPort]:
        """Return the 10G ports of agentInventoryStackPortTable; empty on standalone switches."""
        s = self._b.session
        units = s.walk_int(oids.STACK_PORT_UNIT)
        tags = s.walk(oids.STACK_PORT_TAG)
        cfg = s.walk_int(oids.STACK_PORT_CONFIGURED_MODE)
        run = s.walk_int(oids.STACK_PORT_RUNNING_MODE)
        link = s.walk_int(oids.STACK_PORT_LINK)
        speed = s.walk_int(oids.STACK_PORT_SPEED)
        errors = s.walk_int(oids.STACK_PORT_ERRORS)
        by_unit_port: dict[tuple[int, str], str] = {}
        for name in self._b.ifnames().values():
            unit_text, _, rest = name.partition("/")
            num = rest.lstrip("abcdefghijklmnopqrstuvwxyz")
            if unit_text.isdigit() and num.isdigit():
                by_unit_port[(int(unit_text), num)] = name
        result = []
        for idx, unit in sorted(units.items(), key=lambda kv: int(kv[0])):
            num = tags.get(idx, "").rpartition("/")[2]
            result.append(
                StackPort(
                    name=by_unit_port.get((unit, num), f"{unit}/{num}"),
                    unit=unit,
                    configured_mode=_STACK_MODES.get(cfg.get(idx, 0), "unknown"),
                    running_mode=_STACK_MODES.get(run.get(idx, 0), "unknown"),
                    link_up=link.get(idx) == 1,
                    speed_gbps=speed.get(idx, 0),
                    errors=errors.get(idx, 0),
                )
            )
        return result


class NetgearVLANManager(BaseVLANManager):
    """VLAN management via Q-BRIDGE-MIB with read-back after every write."""

    def __init__(self, backend: NetgearBackend):
        """Create the manager.

        Args:
            backend: Shared SNMP backend.
        """
        self._b = backend

    @snmp_errors(VLANError)
    def list_vlans(self) -> list[VLAN]:
        """Return all VLANs (static and factory) with member port names; stack links are omitted."""
        names = self._b.ifnames()
        bridged = self._b.bridge_ports()
        result = []
        for vid, v in sorted(self._b.read_vlans().vlans.items()):
            members = v.egress & bridged if bridged else v.egress
            tagged = members - v.untagged
            result.append(
                VLAN(
                    vlan_id=vid,
                    name=v.name,
                    tagged_ports=[names.get(i, str(i)) for i in sorted(tagged)],
                    untagged_ports=[names.get(i, str(i)) for i in sorted(members & v.untagged)],
                )
            )
        return result

    @snmp_errors(VLANError)
    def create_vlan(self, vlan_id: int, name: str = "") -> None:
        """Create a VLAN, or rename it if it exists.

        Creation and naming are two PDUs: ``createAndGo`` plus the name in one PDU is answered with
        ``commitFailed`` by the GS108Tv2.

        Args:
            vlan_id: VLAN ID, 1-4094.
            name: VLAN name; empty keeps the firmware default.

        Raises:
            VLANError: If the ID is invalid or the VLAN does not show up on read-back.
        """
        if not 1 <= vlan_id <= 4094:
            raise VLANError(f"VLAN ID {vlan_id} out of range 1-4094")
        s = self._b.session
        current = self._b.read_vlans().vlans.get(vlan_id)
        if current is None:
            s.set((f"{oids.VLAN_STATIC_ROWSTATUS}.{vlan_id}", integer(oids.ROW_CREATE_AND_GO)), single_varbind=True)
        if name and (current is None or current.name != name):
            s.set((f"{oids.VLAN_STATIC_NAME}.{vlan_id}", octets(name)), single_varbind=True)

        def done(table: VlanTable) -> bool:
            row = table.vlans.get(vlan_id)
            return row is not None and (not name or not row.static or row.name == name)

        self._settle(self._b.read_vlans, done, f"VLAN {vlan_id}")

    @snmp_errors(VLANError)
    def delete_vlan(self, vlan_id: int) -> None:
        """Delete a VLAN; a missing VLAN is a no-op.

        Raises:
            VLANError: For VLAN 1 or if the VLAN is still present on read-back.
        """
        if vlan_id == 1:
            raise VLANError("VLAN 1 cannot be deleted")
        if vlan_id not in self._b.read_vlans().vlans:
            return
        self._b.session.set((f"{oids.VLAN_STATIC_ROWSTATUS}.{vlan_id}", integer(oids.ROW_DESTROY)), single_varbind=True)
        self._settle(self._b.read_vlans, lambda t: vlan_id not in t.vlans, f"deletion of VLAN {vlan_id}")

    @snmp_errors(VLANError)
    def assign_port_to_vlan(self, port: str, vlan_id: int, tagged: bool = False) -> None:
        """Make a port a member of a VLAN.

        Untagged assignment has access-port semantics: the port leaves every other VLAN it was an untagged
        member of and its PVID becomes ``vlan_id``. Tagged membership leaves PVID and other VLANs alone and
        turns an existing untagged membership in this VLAN into a tagged one.

        Args:
            port: ifName, for example ``1/g3``.
            vlan_id: Existing VLAN.
            tagged: Tagged instead of untagged membership.

        Raises:
            VLANError: For unknown VLANs and ports, stack links, ports not in switchport mode general, or if
                the switch does not apply the change.
        """
        names = self._b.ifnames()
        idx = self._port_index(port, names)
        table = self._b.read_vlans()
        if vlan_id not in table.vlans:
            raise VLANError(f"VLAN {vlan_id} does not exist")
        target = table.vlans[vlan_id]
        targets = {vlan_id: (target.egress | {idx}, (target.untagged - {idx}) if tagged else (target.untagged | {idx}))}
        pvids: dict[int, int] = {}
        if not tagged:
            pvids[idx] = vlan_id
            for vid, v in table.vlans.items():
                if vid != vlan_id and idx in v.untagged:
                    targets[vid] = (v.egress - {idx}, v.untagged - {idx})
        self._apply(table, targets, pvids)

    @snmp_errors(VLANError)
    def configure_trunk(self, config: TrunkConfig) -> None:
        """Make a port a trunk.

        The native VLAN becomes the only untagged membership and the PVID; ``allowed_vlans`` become tagged
        memberships; every other VLAN loses the port. An empty ``allowed_vlans`` means native only.

        Args:
            config: Trunk definition.

        Raises:
            VLANError: For unknown VLANs and ports, stack links, ports not in switchport mode general, or if
                the switch does not apply the change.
        """
        names = self._b.ifnames()
        idx = self._port_index(config.port, names)
        table = self._b.read_vlans()
        tagged = set(config.allowed_vlans) - {config.native_vlan}
        missing = sorted(({config.native_vlan} | tagged) - set(table.vlans))
        if missing:
            raise VLANError(f"VLANs {missing} do not exist")
        targets = {}
        for vid, v in table.vlans.items():
            egress, untagged = set(v.egress) - {idx}, set(v.untagged) - {idx}
            if vid == config.native_vlan:
                egress, untagged = egress | {idx}, untagged | {idx}
            elif vid in tagged:
                egress = egress | {idx}
            targets[vid] = (egress, untagged)
        self._apply(table, targets, {idx: config.native_vlan})

    def _port_index(self, port: str, names: dict[int, str]) -> int:
        try:
            idx = self._b.resolve_port(port, names)
        except KeyError:
            raise VLANError(f"port {port} does not exist on the switch") from None
        if idx not in self._b.bridge_ports():
            raise VLANError(f"{port} is not a bridge port (stack link?)")
        mode = self._b.switchport_modes().get(idx, oids.SWITCHPORT_MODE_GENERAL)
        if mode != oids.SWITCHPORT_MODE_GENERAL:
            raise VLANError(f"{port} is not in switchport mode general")
        return idx

    def _write_members(self, table: VlanTable, vid: int, egress: set[int], untagged: set[int]) -> None:
        cur = table.vlans[vid]
        everything = egress | untagged | cur.egress | cur.untagged
        moved = {i for i in everything if (i in egress, i in untagged) != (i in cur.egress, i in cur.untagged)}
        s = self._b.session
        s.set(
            (f"{oids.VLAN_STATIC_EGRESS}.{vid}", octets(ports_to_portlist(egress, table.length))), single_varbind=True
        )
        patch = {i: i in untagged for i in moved}
        bits = portlist_patch(cur.untagged_raw, table.length, patch)
        s.set((f"{oids.VLAN_STATIC_UNTAGGED}.{vid}", octets(bits)), single_varbind=True)

    def _apply(self, table: VlanTable, targets: dict[int, tuple[set[int], set[int]]], pvids: dict[int, int]) -> None:
        cur = table.vlans
        changed = {vid: t for vid, t in targets.items() if t != (cur[vid].egress, cur[vid].untagged)}
        pvid_now = self._b.read_pvids()
        pvid_changes = {i: p for i, p in pvids.items() if pvid_now.get(i) != p}
        if not changed and not pvid_changes:
            return
        union = {}
        for vid, (egress, untagged) in changed.items():
            union[vid] = (cur[vid].egress | egress, cur[vid].untagged | untagged)
            if union[vid] != (cur[vid].egress, cur[vid].untagged):
                self._write_members(table, vid, *union[vid])
        for idx, pvid in sorted(pvid_changes.items()):
            self._b.session.set((f"{oids.PVID}.{idx}", unsigned(pvid)), single_varbind=True)
        for vid, final in changed.items():
            if final != union[vid]:
                self._write_members(table, vid, *final)

        def done(state: tuple[VlanTable, dict[int, int]]) -> bool:
            now, pv = state
            return all(
                vid in now.vlans and (now.vlans[vid].egress, now.vlans[vid].untagged) == t for vid, t in changed.items()
            ) and all(pv.get(i) == p for i, p in pvid_changes.items())

        self._settle(lambda: (self._b.read_vlans(), self._b.read_pvids()), done, "the VLAN membership")

    def _settle[T](self, read: Callable[[], T], done: Callable[[T], bool], what: str) -> T:
        try:
            return poll_until(read, done, timeout=self._b.settle_timeout, interval=self._b.settle_interval, what=what)
        except SnmpSettleTimeoutError as exc:
            raise VLANError(str(exc)) from exc


class NetgearPortManager(BasePortManager):
    """Port administration via IF-MIB."""

    def __init__(self, backend: NetgearBackend):
        """Create the manager.

        Args:
            backend: Shared SNMP backend.
        """
        self._b = backend
        self._monitoring = NetgearMonitoringManager(backend)

    @snmp_errors(PortError)
    def enable_port(self, port: str) -> None:
        """Set ifAdminStatus to up and wait for read-back."""
        self._set_admin(port, oids.ADMIN_UP)

    @snmp_errors(PortError)
    def disable_port(self, port: str) -> None:
        """Set ifAdminStatus to down and wait for read-back."""
        self._set_admin(port, oids.ADMIN_DOWN)

    def _set_admin(self, port: str, status: int) -> None:
        names = self._b.ifnames()
        try:
            idx = self._b.resolve_port(port, names)
        except KeyError:
            raise PortError(f"port {port} does not exist on the switch") from None
        if not _is_port_name(names[idx]) or (names[idx].count("/") and idx not in self._b.bridge_ports()):
            raise PortError(f"{port} is not a configurable port (stack link or internal interface)")
        oid = f"{oids.IF_ADMIN_STATUS}.{idx}"
        if self._b.session.get_int(oid) == status:
            return
        self._b.session.set((oid, integer(status)))
        try:
            poll_until(
                lambda: self._b.session.get_int(oid),
                lambda v: v == status,
                timeout=self._b.settle_timeout,
                interval=self._b.settle_interval,
                what=f"ifAdminStatus of {port}",
            )
        except SnmpSettleTimeoutError as exc:
            raise PortError(str(exc)) from exc

    @snmp_errors(PortError)
    def configure_port(self, config: PortConfig) -> None:
        """Apply admin state, description (ifAlias) and, for access ports, the access VLAN.

        Raises:
            NotImplementedError: For non-auto speed or duplex; those are not configurable via SNMP here.
            PortError: If the port or its VLAN cannot be configured.
        """
        if config.speed != PortSpeed.AUTO or config.duplex.value != "auto":
            raise NotImplementedError("Netgear speed/duplex configuration is not implemented (SNMP)")
        names = self._b.ifnames()
        try:
            idx = self._b.resolve_port(config.port, names)
        except KeyError:
            raise PortError(f"port {config.port} does not exist on the switch") from None
        if config.description:
            self._b.session.set((f"{oids.IF_ALIAS}.{idx}", octets(config.description)))
        if config.mode == PortMode.ACCESS and config.access_vlan is not None:
            try:
                NetgearVLANManager(self._b).assign_port_to_vlan(config.port, config.access_vlan)
            except VLANError as exc:
                raise PortError(str(exc)) from exc
        if config.enabled:
            self.enable_port(config.port)
        else:
            self.disable_port(config.port)

    def get_port_status(self, port: str | None = None) -> list[PortStatus]:
        """Return the status of all ports, or of the one named ``port``."""
        status = self._monitoring.get_port_status()
        return status if port is None else [p for p in status if p.port == port]


class NetgearLACPManager(BaseLACPManager):
    """Read-only LACP view based on IEEE8023-LAG-MIB and LAG interfaces named ``l<n>``."""

    def __init__(self, backend: NetgearBackend):
        """Create the manager.

        Args:
            backend: Shared SNMP backend.
        """
        self._b = backend

    def create_port_channel(self, channel_id: int, member_ports: list[str]) -> None:
        """Not implemented: LAG writes are not supported over SNMP here."""
        raise NotImplementedError("Netgear LAG creation is not implemented; configure LAGs in the web UI")

    def delete_port_channel(self, channel_id: int) -> None:
        """Not implemented: LAG writes are not supported over SNMP here."""
        raise NotImplementedError("Netgear LAG deletion is not implemented; configure LAGs in the web UI")

    @snmp_errors(LACPError)
    def get_port_channel_info(self) -> list[LACPInfo]:
        """Return every LAG with member ports, keys and operational state."""
        s = self._b.session
        names = self._b.ifnames()
        attached = s.walk_int(oids.LAG_ATTACHED_AGG_ID)
        members: dict[int, list[str]] = {}
        for port_idx, agg in attached.items():
            if agg and agg != int(port_idx) and int(port_idx) in names:
                members.setdefault(agg, []).append(names[int(port_idx)])
        try:
            admin_keys = s.walk_int(oids.LAG_ACTOR_ADMIN_KEY)
            partner_keys = s.walk_int(oids.LAG_PARTNER_OPER_KEY)
        except SnmpNoSuchObjectError:
            admin_keys, partner_keys = {}, {}
        oper = s.walk_int(oids.IF_OPER_STATUS)
        result = []
        for idx, name in sorted(names.items()):
            m = _LAG_NAME_RE.match(name)
            if not m:
                continue
            result.append(
                LACPInfo(
                    port_channel_id=int(m.group(1)),
                    member_ports=sorted(members.get(idx, [])),
                    admin_key=admin_keys.get(str(idx), 0),
                    partner_key=partner_keys.get(str(idx), 0),
                    status="up" if oper.get(str(idx)) == oids.OPER_UP else "down",
                )
            )
        return result
