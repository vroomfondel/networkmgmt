"""VLAN data collector — orchestrates SNMP queries into VlanDumpData."""

from __future__ import annotations

import asyncio
from collections import defaultdict

from loguru import logger

from networkmgmt.snmp_vlan_dump._util import build_port_map, decode_portlist
from networkmgmt.snmp_vlan_dump.models import PortVlans, VlanDumpData
from networkmgmt.snmp_vlan_dump.snmp import (
    OID_DOT1Q_PVID,
    OID_IF_DESCR,
    OID_IF_OPER_STATUS,
    OID_SYS_DESCR,
    OID_SYS_NAME,
    OID_SYS_UPTIME,
    OID_VLAN_CURRENT_EGRESS,
    OID_VLAN_CURRENT_UNTAG,
    OID_VLAN_EGRESS_PORTS,
    OID_VLAN_STATIC_NAME,
    OID_VLAN_UNTAGGED_PORTS,
    snmp_get_scalars,
    snmp_walk_table,
)


class VlanDataCollector:
    """Collect VLAN-port assignment data from a switch via SNMPv2c."""

    def __init__(self, host: str, community: str = "public") -> None:
        self.host = host
        self.community = community

    def collect(self) -> VlanDumpData:
        """Synchronous entry point — wraps the async implementation."""
        return asyncio.run(self._collect())

    async def _collect(self) -> VlanDumpData:
        """Async implementation: query switch and return populated VlanDumpData."""
        from pysnmp.hlapi.asyncio import (
            CommunityData,
            SnmpEngine,
            UdpTransportTarget,
        )

        logger.info(f"Querying {self.host} (community: {self.community}) ...")

        engine = SnmpEngine()
        auth = CommunityData(self.community)
        target = await UdpTransportTarget.create((self.host, 161))

        # ── Collect SNMP data ──────────────────────────────────────────
        scalars = await snmp_get_scalars(
            engine,
            auth,
            target,
            OID_SYS_DESCR,
            OID_SYS_NAME,
            OID_SYS_UPTIME,
            host=self.host,
        )
        sys_descr = str(scalars[0]) if scalars[0] is not None else "?"
        sys_name = str(scalars[1]) if scalars[1] is not None else "?"
        if scalars[2] is not None:
            ticks = int(scalars[2])
            secs = ticks // 100
            days, secs = divmod(secs, 86400)
            hours, secs = divmod(secs, 3600)
            mins, secs = divmod(secs, 60)
            sys_uptime = f"{days} days, {hours:02d}:{mins:02d}:{secs:02d}"
        else:
            sys_uptime = "?"

        if_descr_rows = await snmp_walk_table(
            engine,
            auth,
            target,
            OID_IF_DESCR,
            host=self.host,
        )
        if_oper_rows = await snmp_walk_table(
            engine,
            auth,
            target,
            OID_IF_OPER_STATUS,
            host=self.host,
        )
        vlan_name_rows = await snmp_walk_table(
            engine,
            auth,
            target,
            OID_VLAN_STATIC_NAME,
            host=self.host,
        )
        pvid_rows = await snmp_walk_table(
            engine,
            auth,
            target,
            OID_DOT1Q_PVID,
            host=self.host,
        )
        egress_rows = await snmp_walk_table(
            engine,
            auth,
            target,
            OID_VLAN_EGRESS_PORTS,
            host=self.host,
        )
        untagged_rows = await snmp_walk_table(
            engine,
            auth,
            target,
            OID_VLAN_UNTAGGED_PORTS,
            host=self.host,
        )

        # Fallback: if Static table is empty, use Current table (e.g. GS108T)
        if not egress_rows:
            cur_egress = await snmp_walk_table(
                engine,
                auth,
                target,
                OID_VLAN_CURRENT_EGRESS,
                index_len=2,
                host=self.host,
            )
            cur_untag = await snmp_walk_table(
                engine,
                auth,
                target,
                OID_VLAN_CURRENT_UNTAG,
                index_len=2,
                host=self.host,
            )
            # Current table index is (TimeMark, VlanIndex) — extract VlanIndex only
            egress_rows = [(vid, val) for (_, vid), val in cur_egress]
            untagged_rows = [(vid, val) for (_, vid), val in cur_untag]

        engine.close_dispatcher()

        # ── Build dicts ────────────────────────────────────────────────
        if_descrs = {idx: str(val) for idx, val in if_descr_rows}
        oper_status = {idx: int(val) for idx, val in if_oper_rows}
        vlan_names = {idx: str(val) for idx, val in vlan_name_rows}
        pvid_data = {idx: int(val) for idx, val in pvid_rows}
        egress_data = {idx: bytes(val) for idx, val in egress_rows}
        untagged_data = {idx: bytes(val) for idx, val in untagged_rows}

        port_map, unit_info = build_port_map(if_descrs)

        # ── Physical port indices per unit (sorted) ────────────────────
        unit_ports: dict[int, list[int]] = defaultdict(list)
        for idx, name in sorted(port_map.items()):
            if name.startswith("U"):
                unit_num = int(name[1])
                unit_ports[unit_num].append(idx)

        all_phys: list[int] = []
        for u in sorted(unit_ports):
            all_phys.extend(unit_ports[u])

        # ── VLAN membership per port ───────────────────────────────────
        port_vlans: dict[int, PortVlans] = {p: PortVlans() for p in all_phys}

        for vlan_id in sorted(egress_data):
            egress = decode_portlist(egress_data[vlan_id])
            untag = decode_portlist(untagged_data.get(vlan_id, b""))
            for p in all_phys:
                if p in egress:
                    if p in untag:
                        port_vlans[p].untagged.append(vlan_id)
                    else:
                        port_vlans[p].tagged.append(vlan_id)

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
        )
