"""VLAN data collector — orchestrates SNMP queries into VlanDumpData."""

from __future__ import annotations

import asyncio
from collections import defaultdict

from loguru import logger

from networkmgmt.snmp import AsyncSnmpSession, SnmpCredentials, SnmpError, portlist_to_ports
from networkmgmt.snmp_vlan_dump._util import build_port_map, classify_port, ifname_to_descr
from networkmgmt.snmp_vlan_dump.models import PortVlans, VlanDumpData
from networkmgmt.snmp_vlan_dump.snmp import (
    OID_BASE_PORT_IFINDEX,
    OID_DOT1Q_PVID,
    OID_IF_DESCR,
    OID_IF_NAME,
    OID_IF_OPER_STATUS,
    OID_SYS_DESCR,
    OID_SYS_NAME,
    OID_SYS_UPTIME,
    OID_VLAN_CURRENT_EGRESS,
    OID_VLAN_CURRENT_UNTAG,
    OID_VLAN_EGRESS_PORTS,
    OID_VLAN_STATIC_NAME,
    OID_VLAN_UNTAGGED_PORTS,
    log_snmp_error,
)


def _int_keys[T](rows: dict[str, T]) -> dict[int, T]:
    return {int(k): v for k, v in rows.items()}


def _merge_vlan_tables(static: dict[str, bytes], current: dict[str, bytes]) -> dict[int, bytes]:
    """Merge a static and a current VLAN PortList table; static rows win, current rows add missing VLANs."""
    merged = {int(vid): raw for vid, raw in static.items()}
    for key, raw in current.items():
        merged.setdefault(int(key.rsplit(".", 1)[-1]), raw)
    return merged


class VlanDataCollector:
    """Collect VLAN-port assignment data from a switch via SNMPv2c or SNMPv3."""

    def __init__(
        self,
        host: str,
        community: str = "public",
        credentials: SnmpCredentials | None = None,
        timeout: float = 5.0,
        retries: int = 1,
    ) -> None:
        """Create the collector.

        Args:
            host: Switch IP or hostname.
            community: v2c community, used when ``credentials`` is None.
            credentials: SNMP credentials (v2c or v3); takes precedence over ``community``.
            timeout: Per-request SNMP timeout in seconds.
            retries: SNMP retries per request.
        """
        self.host = host
        self.community = community
        self.credentials = credentials or SnmpCredentials.v2c(community)
        self.timeout = timeout
        self.retries = retries

    def collect(self) -> VlanDumpData:
        """Synchronous entry point — wraps the async implementation.

        Raises:
            SnmpError: If the switch cannot be queried (the cause is logged with credential hints first).
        """
        return asyncio.run(self._collect())

    async def _collect(self) -> VlanDumpData:
        creds = self.credentials
        who = f"user {creds.user}, {creds.auth_protocol}" if creds.user else f"community {creds.community}"
        logger.info(f"Querying {self.host} (SNMP {creds.version}, {who}) ...")
        session = AsyncSnmpSession(self.host, creds, timeout=self.timeout, retries=self.retries)
        try:
            async with session:
                return await self._query(session)
        except SnmpError as exc:
            log_snmp_error(exc, self.host, creds)
            raise

    async def _query(self, snmp: AsyncSnmpSession) -> VlanDumpData:
        sys_descr = await snmp.get(OID_SYS_DESCR)
        sys_name = await snmp.get(OID_SYS_NAME)
        sys_uptime = self._format_uptime(await snmp.get_int(OID_SYS_UPTIME))

        if_descrs = _int_keys(await snmp.walk(OID_IF_DESCR))
        if_names = _int_keys(await snmp.walk(OID_IF_NAME))
        oper_status = _int_keys(await snmp.walk_int(OID_IF_OPER_STATUS))
        bridge_ifindexes = set((await snmp.walk_int(OID_BASE_PORT_IFINDEX)).values())
        vlan_names = _int_keys(await snmp.walk(OID_VLAN_STATIC_NAME))
        pvid_data = _int_keys(await snmp.walk_int(OID_DOT1Q_PVID))

        static_egress = await snmp.walk_octets(OID_VLAN_EGRESS_PORTS)
        egress_data = _merge_vlan_tables(static_egress, await snmp.walk_octets(OID_VLAN_CURRENT_EGRESS))
        untagged_data = _merge_vlan_tables(
            await snmp.walk_octets(OID_VLAN_UNTAGGED_PORTS), await snmp.walk_octets(OID_VLAN_CURRENT_UNTAG)
        )
        factory = sorted(set(egress_data) - {int(k) for k in static_egress})
        if factory:
            logger.debug(f"VLANs only in dot1qVlanCurrentTable (factory): {factory}")
        if not egress_data:
            logger.warning(f"{self.host}: no VLAN rows in dot1qVlanStaticTable or dot1qVlanCurrentTable")

        return self.build(
            sys_descr=sys_descr,
            sys_name=sys_name,
            sys_uptime=sys_uptime,
            if_descrs=if_descrs,
            if_names=if_names,
            bridge_ifindexes=bridge_ifindexes,
            oper_status=oper_status,
            vlan_names=vlan_names,
            pvid_data=pvid_data,
            egress_data=egress_data,
            untagged_data=untagged_data,
        )

    @staticmethod
    def _format_uptime(ticks: int) -> str:
        secs = ticks // 100
        days, secs = divmod(secs, 86400)
        hours, secs = divmod(secs, 3600)
        mins, secs = divmod(secs, 60)
        return f"{days} days, {hours:02d}:{mins:02d}:{secs:02d}"

    @staticmethod
    def build(
        *,
        sys_descr: str,
        sys_name: str,
        sys_uptime: str,
        if_descrs: dict[int, str],
        if_names: dict[int, str],
        bridge_ifindexes: set[int],
        oper_status: dict[int, int],
        vlan_names: dict[int, str],
        pvid_data: dict[int, int],
        egress_data: dict[int, bytes],
        untagged_data: dict[int, bytes],
    ) -> VlanDumpData:
        """Assemble a :class:`VlanDumpData` from raw table contents; stack links are kept out of the port lists.

        Args:
            sys_descr: sysDescr.
            sys_name: sysName.
            sys_uptime: Formatted uptime.
            if_descrs: ``{ifIndex: ifDescr}``.
            if_names: ``{ifIndex: ifName}``.
            bridge_ifindexes: ifIndexes listed in dot1dBasePortIfIndex (bridge port == ifIndex on GS108T/S3300).
            oper_status: ``{ifIndex: ifOperStatus}``.
            vlan_names: ``{vlan: name}``.
            pvid_data: ``{port: PVID}``.
            egress_data: ``{vlan: egress PortList}``, static and current merged.
            untagged_data: ``{vlan: untagged PortList}``, static and current merged.

        Returns:
            The populated dump.
        """
        bridge_known: bool | None = True if bridge_ifindexes else None
        descrs = dict(if_descrs)
        for idx, name in if_names.items():
            if idx not in descrs or not descrs[idx].strip():
                synthetic = ifname_to_descr(name)
                if synthetic:
                    descrs[idx] = synthetic

        port_kind: dict[int, str] = {}
        for idx in sorted(set(descrs) | set(if_names)):
            in_bridge = None if bridge_known is None else idx in bridge_ifindexes
            port_kind[idx] = classify_port(if_names.get(idx, ""), descrs.get(idx, ""), in_bridge)
        stack_ports = sorted(idx for idx, kind in port_kind.items() if kind == "stack")

        port_map, unit_info = build_port_map({i: d for i, d in descrs.items() if port_kind.get(i) != "stack"})
        stack_map, _ = build_port_map({i: descrs[i] for i in stack_ports})
        port_map.update({i: f"{n} (stack)" for i, n in stack_map.items()})

        unit_ports: dict[int, list[int]] = defaultdict(list)
        for idx, name in sorted(port_map.items()):
            if name.startswith("U") and port_kind.get(idx) == "front":
                unit_ports[int(name[1:].split("/")[0])].append(idx)

        all_phys: list[int] = []
        for u in sorted(unit_ports):
            all_phys.extend(unit_ports[u])

        port_vlans: dict[int, PortVlans] = {p: PortVlans() for p in all_phys}
        for vlan_id in sorted(egress_data):
            egress = portlist_to_ports(egress_data[vlan_id])
            untag = portlist_to_ports(untagged_data.get(vlan_id, b""))
            for p in all_phys:
                if p in egress:
                    (port_vlans[p].untagged if p in untag else port_vlans[p].tagged).append(vlan_id)

        return VlanDumpData(
            sys_descr=sys_descr,
            sys_name=sys_name,
            sys_uptime=sys_uptime,
            unit_info=unit_info,
            port_map=port_map,
            unit_ports=dict(unit_ports),
            port_vlans=port_vlans,
            vlan_names=vlan_names,
            pvid_data=pvid_data,
            oper_status=oper_status,
            egress_data=egress_data,
            untagged_data=untagged_data,
            all_phys=all_phys,
            port_kind=port_kind,
            stack_ports=stack_ports,
        )
