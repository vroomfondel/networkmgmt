"""Managers for Netgear Smart Managed Plus switches (web UI via netgear-tool)."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from loguru import logger

from networkmgmt.switchctrl.base.managers import (
    BaseLACPManager,
    BaseMonitoringManager,
    BasePortManager,
    BaseVLANManager,
)
from networkmgmt.switchctrl.exceptions import PortError, VLANError
from networkmgmt.switchctrl.models.port import DuplexMode, PortConfig, PortSpeed, PortStatus
from networkmgmt.switchctrl.models.stats import PortStatistics
from networkmgmt.switchctrl.models.system import LACPInfo, SensorData, SystemInfo
from networkmgmt.switchctrl.models.vlan import VLAN, TrunkConfig
from networkmgmt.switchctrl.vendors.netgear_plus.sdk import (
    NOT_MEMBER,
    TAGGED,
    UNTAGGED,
    NetgearPlusSession,
    RawPortInfo,
)

SDK_SPEED_AUTO = 1
SDK_SPEED_DISABLE = 2
SDK_SPEED_BY_MODE: dict[tuple[PortSpeed, DuplexMode], int] = {
    (PortSpeed.AUTO, DuplexMode.AUTO): 1,
    (PortSpeed.SPEED_1G, DuplexMode.AUTO): 1,
    (PortSpeed.SPEED_1G, DuplexMode.FULL): 1,
    (PortSpeed.SPEED_10M, DuplexMode.HALF): 3,
    (PortSpeed.SPEED_10M, DuplexMode.FULL): 4,
    (PortSpeed.SPEED_100M, DuplexMode.HALF): 5,
    (PortSpeed.SPEED_100M, DuplexMode.FULL): 6,
}
NO_LINK_SPEEDS = {"", "no speed"}


@dataclass
class PortVlanSpec:
    """Desired VLAN state of one port; ``untagged`` also implies the PVID."""

    untagged: int | None = None
    tagged: list[int] = field(default_factory=list)
    pvid: int | None = None


@dataclass
class VlanPlan:
    """Difference between the switch state and the desired VLAN table."""

    current_membership: dict[int, str]
    current_pvids: dict[int, int]
    want_membership: dict[int, str]
    want_pvids: dict[int, int]
    created: list[int]
    edited: list[int]
    deleted: list[int]
    pvid_changes: dict[int, int]

    @property
    def changed(self) -> bool:
        """Whether any write is needed."""
        return bool(self.created or self.edited or self.deleted or self.pvid_changes)

    def render(self, desired: bool = False) -> str:
        """Render the current or desired table as text for diffs."""
        membership = self.want_membership if desired else self.current_membership
        pvids = self.want_pvids if desired else self.current_pvids
        return render_vlan_table(membership, pvids)


def render_vlan_table(memberships: Mapping[int, str], pvids: Mapping[int, int]) -> str:
    """Render VLAN membership and PVIDs as human-readable text.

    Args:
        memberships: Membership string per VLAN.
        pvids: PVID per port.

    Returns:
        One line per VLAN plus a PVID line.
    """
    lines = []
    for vid in sorted(memberships):
        mem = memberships[vid]
        tagged = [str(i + 1) for i, c in enumerate(mem) if c == TAGGED]
        untagged = [str(i + 1) for i, c in enumerate(mem) if c == UNTAGGED]
        lines.append(f"VLAN {vid:4d}  tagged {','.join(tagged) or '-':<12} untagged {','.join(untagged) or '-'}")
    lines.append("PVID       " + " ".join(f"{p}:{pvids[p]}" for p in sorted(pvids)))
    return "\n".join(lines) + "\n"


def normalize_port_specs(
    ports: Mapping[int, PortVlanSpec], vlans: set[int], port_count: int
) -> dict[int, PortVlanSpec]:
    """Validate port specs against the VLAN set and fill in the implied PVID.

    Args:
        ports: Desired state per 1-based port.
        vlans: VLAN IDs that will exist.
        port_count: Number of ports on the switch.

    Returns:
        Normalized specs where ``pvid`` equals ``untagged`` when not given.

    Raises:
        VLANError: A spec is inconsistent.
    """
    out: dict[int, PortVlanSpec] = {}
    for port, spec in ports.items():
        if not 1 <= port <= port_count:
            raise VLANError(f"port {port} outside 1..{port_count}")
        pvid = spec.untagged if spec.pvid is None else spec.pvid
        if spec.untagged is not None and pvid != spec.untagged:
            raise VLANError(f"port {port}: pvid {pvid} differs from untagged VLAN {spec.untagged}")
        for vid in [*spec.tagged, *(v for v in (spec.untagged, pvid) if v is not None)]:
            if vid not in vlans:
                raise VLANError(f"port {port} references VLAN {vid}, which does not exist")
        if spec.untagged in spec.tagged:
            raise VLANError(f"port {port}: VLAN {spec.untagged} is both tagged and untagged")
        if pvid is not None and pvid != spec.untagged and pvid not in spec.tagged:
            raise VLANError(f"port {port}: pvid {pvid} is not a VLAN of this port")
        out[port] = PortVlanSpec(untagged=spec.untagged, tagged=list(spec.tagged), pvid=pvid)
    return out


def plan_vlan_changes(
    current_membership: Mapping[int, str],
    current_pvids: Mapping[int, int],
    vlans: Iterable[int],
    ports: Mapping[int, PortVlanSpec],
    port_count: int,
    purge_vlans: bool = False,
) -> VlanPlan:
    """Compute the writes needed to reach the desired VLAN table.

    A declared port is owned completely: it is removed from every VLAN not listed for it.
    Undeclared ports stay untouched. VLAN 1 is never purged.

    Args:
        current_membership: Membership string per existing VLAN.
        current_pvids: PVID per port.
        vlans: VLAN IDs that must exist.
        ports: Desired state per 1-based port.
        port_count: Number of ports on the switch.
        purge_vlans: Delete VLANs that exist but are not in ``vlans``.

    Returns:
        The plan, empty (``changed`` False) when the switch already matches.

    Raises:
        VLANError: The desired state is inconsistent.
    """
    wanted_vlans = set(vlans)
    specs = normalize_port_specs(ports, wanted_vlans, port_count)
    members = {vid: list(mem) for vid, mem in current_membership.items()}
    for vid in wanted_vlans:
        members.setdefault(vid, [NOT_MEMBER] * port_count)
    if purge_vlans:
        for vid in [v for v in members if v not in wanted_vlans and v != 1]:
            del members[vid]
    want_pvids = dict(current_pvids)
    for port, spec in specs.items():
        for mem in members.values():
            mem[port - 1] = NOT_MEMBER
        for vid in spec.tagged:
            members[vid][port - 1] = TAGGED
        if spec.untagged is not None:
            members[spec.untagged][port - 1] = UNTAGGED
        if spec.pvid is not None:
            want_pvids[port] = spec.pvid
    want_membership = {vid: "".join(mem) for vid, mem in members.items()}
    empty = NOT_MEMBER * port_count
    return VlanPlan(
        current_membership=dict(current_membership),
        current_pvids=dict(current_pvids),
        want_membership=want_membership,
        want_pvids=want_pvids,
        created=sorted(v for v in want_membership if v not in current_membership),
        edited=sorted(v for v in want_membership if want_membership[v] != current_membership.get(v, empty)),
        deleted=sorted(v for v in current_membership if v not in want_membership),
        pvid_changes={p: v for p, v in want_pvids.items() if current_pvids.get(p) != v},
    )


def _link_up(port: RawPortInfo) -> bool:
    return port.speed_act.strip().lower() not in NO_LINK_SPEEDS


def _port_status(port: RawPortInfo) -> PortStatus:
    return PortStatus(port=str(port.port), link_up=_link_up(port), speed=port.speed_act)


class NetgearPlusMonitoringManager(BaseMonitoringManager):
    """Read-only system, port status and counter access."""

    def __init__(self, session: NetgearPlusSession) -> None:
        self._session = session

    def get_port_status(self) -> list[PortStatus]:
        """Get link state and negotiated speed of all ports."""
        return [_port_status(p) for p in self._session.port_settings()]

    def get_port_statistics(self) -> list[PortStatistics]:
        """Get byte and CRC counters of all ports.

        Packet counters and TX errors are not exposed by the web UI and stay 0.
        """
        settings = {p.port: p for p in self._session.port_settings()}
        stats = self._session.port_stats()
        if stats and not any(s.bytes_rx or s.bytes_tx for s in stats) and not self._session.dot1q_enabled():
            logger.warning("All port counters are 0 and 802.1Q mode is off; counters may be unreliable")
        result = []
        for s in stats:
            info = settings.get(s.port)
            result.append(
                PortStatistics(
                    port=str(s.port),
                    tx_bytes=s.bytes_tx,
                    rx_bytes=s.bytes_rx,
                    rx_errors=s.crc_errors,
                    link_up=_link_up(info) if info else False,
                    speed=info.speed_act if info else "",
                )
            )
        return result

    def get_system_info(self) -> SystemInfo:
        """Get model, name, serial, MAC and firmware; uptime is not available."""
        cfg = self._session.system_config()
        return SystemInfo(
            hostname=cfg.name,
            mac_address=cfg.mac,
            serial_number=cfg.serial,
            firmware_version=cfg.firmware,
            model=cfg.model,
        )

    def get_sensor_data(self) -> SensorData:
        """Not supported: Plus models have no sensors."""
        raise NotImplementedError("Netgear Smart Managed Plus switches expose no sensor data")

    def get_lacp_info(self) -> list[LACPInfo]:
        """Not supported: Plus models have no LACP."""
        raise NotImplementedError("Netgear Smart Managed Plus switches do not support LACP")


class NetgearPlusVLANManager(BaseVLANManager):
    """Declarative 802.1Q VLAN management through the web UI."""

    def __init__(self, session: NetgearPlusSession) -> None:
        self._session = session

    def _require_dot1q(self) -> None:
        if not self._session.dot1q_enabled():
            raise VLANError("802.1Q VLAN mode is disabled on the switch; enable it in the web UI first")

    def reconcile(
        self,
        vlans: Iterable[int],
        ports: Mapping[int, PortVlanSpec] | None = None,
        purge_vlans: bool = False,
        dry_run: bool = False,
    ) -> VlanPlan:
        """Bring the VLAN table to the desired state, writing only what differs.

        Order: create, widened membership, PVIDs, final membership, delete. The firmware
        rejects a PVID outside the port's VLANs. The result is read back and verified.

        Args:
            vlans: VLAN IDs that must exist.
            ports: Desired state per 1-based port; declared ports are owned completely.
            purge_vlans: Delete VLANs not in ``vlans`` (VLAN 1 is kept).
            dry_run: Compute the plan without writing.

        Returns:
            The applied (or, for ``dry_run``, pending) plan; ``changed`` tells whether a write was needed.

        Raises:
            VLANError: 802.1Q is off, the request is inconsistent, or the readback differs.
        """
        self._require_dot1q()
        count = self._session.port_count
        current, pvids = self._session.read_vlans()
        plan = plan_vlan_changes(current, pvids, vlans, ports or {}, count, purge_vlans)
        if not plan.changed or dry_run:
            return plan
        for vid in plan.created:
            self._session.add_vlan(vid)
        empty = NOT_MEMBER * count
        for vid in plan.edited:
            before = current.get(vid, empty)
            widened = "".join(w if w != NOT_MEMBER else c for w, c in zip(plan.want_membership[vid], before))
            if widened != before:
                self._session.set_vlan_membership(vid, widened)
        for port, pvid in sorted(plan.pvid_changes.items()):
            self._session.set_port_pvid(port, pvid)
        for vid in plan.edited:
            self._session.set_vlan_membership(vid, plan.want_membership[vid])
        for vid in plan.deleted:
            self._session.delete_vlan(vid)
        got_membership, got_pvids = self._session.read_vlans()
        if got_membership != plan.want_membership or got_pvids != plan.want_pvids:
            raise VLANError(
                "switch did not apply the VLAN table as written (wanted -> read back):\n"
                + plan.render(desired=True)
                + render_vlan_table(got_membership, got_pvids)
            )
        return plan

    def ensure_vlan(self, vlan_id: int) -> bool:
        """Create a VLAN if missing.

        Args:
            vlan_id: VLAN ID.

        Returns:
            True if the switch was changed.
        """
        self._require_dot1q()
        existing = set(self._session.read_vlans()[0])
        return self.reconcile(existing | {vlan_id}).changed

    def ensure_vlan_absent(self, vlan_id: int) -> bool:
        """Delete a VLAN if present.

        Args:
            vlan_id: VLAN ID; VLAN 1 cannot be deleted.

        Returns:
            True if the switch was changed.

        Raises:
            VLANError: ``vlan_id`` is 1.
        """
        if vlan_id == 1:
            raise VLANError("VLAN 1 cannot be deleted")
        self._require_dot1q()
        existing = set(self._session.read_vlans()[0])
        return self.reconcile(existing - {vlan_id}, purge_vlans=True).changed

    def ensure_port_vlans(self, port: int, spec: PortVlanSpec) -> bool:
        """Set the complete VLAN membership of one port.

        Args:
            port: 1-based port number.
            spec: Desired untagged/tagged/PVID state; all VLANs must already exist.

        Returns:
            True if the switch was changed.
        """
        self._require_dot1q()
        existing = set(self._session.read_vlans()[0])
        return self.reconcile(existing, {port: spec}).changed

    def create_vlan(self, vlan_id: int, name: str = "") -> None:
        """Create a VLAN; the web UI has no VLAN names, ``name`` is ignored."""
        if name:
            logger.debug("Netgear Plus has no VLAN names; ignoring name {!r} for VLAN {}", name, vlan_id)
        self.ensure_vlan(vlan_id)

    def delete_vlan(self, vlan_id: int) -> None:
        """Delete a VLAN if it exists."""
        self.ensure_vlan_absent(vlan_id)

    def list_vlans(self) -> list[VLAN]:
        """List all VLANs with tagged and untagged ports.

        Raises:
            VLANError: 802.1Q mode is disabled.
        """
        self._require_dot1q()
        memberships, _ = self._session.read_vlans()
        return [
            VLAN(
                vlan_id=vid,
                tagged_ports=[str(i + 1) for i, c in enumerate(mem) if c == TAGGED],
                untagged_ports=[str(i + 1) for i, c in enumerate(mem) if c == UNTAGGED],
            )
            for vid, mem in sorted(memberships.items())
        ]

    def get_pvids(self) -> dict[int, int]:
        """Return the PVID per 1-based port."""
        self._require_dot1q()
        return self._session.read_vlans()[1]

    def assign_port_to_vlan(self, port: str, vlan_id: int, tagged: bool = False) -> None:
        """Add a port to a VLAN, keeping its other VLANs.

        An untagged assignment replaces the port's previous untagged VLAN and sets the PVID.
        """
        number = _port_number(port, VLANError)
        self._require_dot1q()
        memberships, pvids = self._session.read_vlans()
        spec = _spec_from_state(number, memberships, pvids)
        if tagged:
            if spec.untagged == vlan_id:
                spec.untagged = None
            if vlan_id not in spec.tagged:
                spec.tagged.append(vlan_id)
        else:
            spec.tagged = [v for v in spec.tagged if v != vlan_id]
            spec.untagged = vlan_id
            spec.pvid = vlan_id
        self.reconcile(memberships, {number: spec})

    def configure_trunk(self, config: TrunkConfig) -> None:
        """Make a port untagged in the native VLAN and tagged in all allowed VLANs."""
        number = _port_number(config.port, VLANError)
        spec = PortVlanSpec(
            untagged=config.native_vlan,
            tagged=[v for v in config.allowed_vlans if v != config.native_vlan],
            pvid=config.native_vlan,
        )
        self.ensure_port_vlans(number, spec)


class NetgearPlusPortManager(BasePortManager):
    """Port status and admin state through the web UI."""

    def __init__(self, session: NetgearPlusSession, vlan: NetgearPlusVLANManager) -> None:
        self._session = session
        self._vlan = vlan

    def _settings(self, number: int) -> RawPortInfo:
        for info in self._session.port_settings():
            if info.port == number:
                return info
        raise PortError(f"port {number} does not exist")

    def configure_port(self, config: PortConfig) -> None:
        """Apply speed/duplex, admin state, description and access VLAN.

        Raises:
            PortError: Speed/duplex combination is not supported by the firmware.
        """
        number = _port_number(config.port, PortError)
        if config.enabled:
            speed = SDK_SPEED_BY_MODE.get((config.speed, config.duplex))
            if speed is None:
                raise PortError(f"unsupported speed/duplex {config.speed.value}/{config.duplex.value}")
        else:
            speed = SDK_SPEED_DISABLE
        self._session.set_port(
            number, speed=speed, description=config.description or self._settings(number).description
        )
        if config.access_vlan is not None:
            self._vlan.assign_port_to_vlan(config.port, config.access_vlan)

    def enable_port(self, port: str) -> None:
        """Set the port speed to auto."""
        self._set_admin(port, SDK_SPEED_AUTO)

    def disable_port(self, port: str) -> None:
        """Disable the port."""
        self._set_admin(port, SDK_SPEED_DISABLE)

    def _set_admin(self, port: str, speed: int) -> None:
        number = _port_number(port, PortError)
        self._session.set_port(number, speed=speed, description=self._settings(number).description)

    def get_port_status(self, port: str | None = None) -> list[PortStatus]:
        """Get port status, optionally for one port.

        Raises:
            PortError: The requested port does not exist.
        """
        statuses = [_port_status(p) for p in self._session.port_settings()]
        if port is None:
            return statuses
        selected = [s for s in statuses if s.port == port]
        if not selected:
            raise PortError(f"port {port} does not exist")
        return selected


class NetgearPlusLACPManager(BaseLACPManager):
    """Placeholder: Smart Managed Plus models have no LACP."""

    def create_port_channel(self, channel_id: int, member_ports: list[str]) -> None:
        """Not supported."""
        raise NotImplementedError("Netgear Smart Managed Plus switches do not support LACP")

    def delete_port_channel(self, channel_id: int) -> None:
        """Not supported."""
        raise NotImplementedError("Netgear Smart Managed Plus switches do not support LACP")

    def get_port_channel_info(self) -> list[LACPInfo]:
        """Not supported."""
        raise NotImplementedError("Netgear Smart Managed Plus switches do not support LACP")


def _port_number(port: str, error: type[VLANError] | type[PortError]) -> int:
    try:
        return int(port)
    except ValueError:
        raise error(f"invalid port {port!r}; Netgear Plus ports are numbered 1..N") from None


def _spec_from_state(port: int, memberships: Mapping[int, str], pvids: Mapping[int, int]) -> PortVlanSpec:
    untagged = [vid for vid, mem in sorted(memberships.items()) if mem[port - 1] == UNTAGGED]
    tagged = [vid for vid, mem in sorted(memberships.items()) if mem[port - 1] == TAGGED]
    pvid = pvids.get(port)
    chosen = pvid if pvid in untagged else (untagged[0] if untagged else None)
    return PortVlanSpec(untagged=chosen, tagged=tagged, pvid=chosen)
