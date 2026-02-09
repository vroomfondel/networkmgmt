"""SNMP Bridge Discovery — L2 topology via switch MAC forwarding tables."""

from __future__ import annotations

import re
from typing import Any

from loguru import logger

from networkmgmt.discovery.models import (
    DiscoveredHost,
    L2TopologyEntry,
    SwitchPortMapping,
)

# Optional pysnmp import
try:
    import asyncio

    from pysnmp.hlapi.asyncio import (
        CommunityData,
        ContextData,
        ObjectIdentity,
        ObjectType,
        SnmpEngine,
        UdpTransportTarget,
        bulk_walk_cmd,
        get_cmd,
    )

    HAS_PYSNMP = True
except ImportError:
    HAS_PYSNMP = False

# BRIDGE-MIB / Q-BRIDGE-MIB OIDs for MAC forwarding table
_OID_SYS_NAME = "1.3.6.1.2.1.1.5.0"
_OID_IF_DESCR = "1.3.6.1.2.1.2.2.1.2"
_OID_DOT1D_TP_FDB_ADDRESS = "1.3.6.1.2.1.17.4.3.1.1"
_OID_DOT1D_TP_FDB_PORT = "1.3.6.1.2.1.17.4.3.1.2"
_OID_DOT1D_BASE_PORT_IF_INDEX = "1.3.6.1.2.1.17.1.4.1.2"
_OID_DOT1Q_TP_FDB_PORT = "1.3.6.1.2.1.17.7.1.2.2.1.2"


async def _snmp_get_scalars(engine: Any, auth: Any, target: Any, *oids: str, host: str = "") -> list[Any]:
    """GET one or more scalar OIDs, return list of values."""
    tag = f" [{host}]" if host else ""
    error_indication, error_status, _, var_binds = await get_cmd(
        engine,
        auth,
        target,
        ContextData(),
        *[ObjectType(ObjectIdentity(oid)) for oid in oids],
    )
    if error_indication:
        logger.warning(f"SNMP error{tag}: {error_indication}")
        return [None] * len(oids)
    if error_status:
        logger.warning(f"SNMP error{tag}: {error_status.prettyPrint()}")
        return [None] * len(oids)
    return [val for _, val in var_binds]


async def _snmp_walk_table(
    engine: Any, auth: Any, target: Any, oid: str, index_len: int = 1, host: str = ""
) -> list[tuple[Any, Any]]:
    """Bulk-walk an OID subtree.

    index_len=1: return (last_index, value) tuples.
    index_len=2: return ((idx[-2], idx[-1]), value) tuples.
    """
    tag = f" [{host}]" if host else ""
    results = []
    async for error_indication, error_status, _, var_binds in bulk_walk_cmd(
        engine,
        auth,
        target,
        ContextData(),
        0,
        25,
        ObjectType(ObjectIdentity(oid)),
        lexicographicMode=False,
    ):
        if error_indication:
            logger.warning(f"SNMP walk error{tag} on {oid}: {error_indication}")
            break
        if error_status:
            logger.warning(f"SNMP walk error{tag} on {oid}: {error_status.prettyPrint()}")
            break
        for var_bind_oid, val in var_binds:
            idx: Any
            if index_len == 1:
                idx = int(var_bind_oid[-1])
            else:
                idx = tuple(int(var_bind_oid[-i]) for i in range(index_len, 0, -1))
            results.append((idx, val))
    return results


def _build_port_name_map(if_descrs: dict[int, str]) -> dict[int, str]:
    """Build ifIndex -> friendly port name from ifDescr strings.

    Parses Netgear-style strings like:
        'unit 1 port 5 Gigabit - Level'
        'Slot: 0 Port: 3 Gigabit - Level'
    """
    port_map: dict[int, str] = {}
    for idx, descr in if_descrs.items():
        m = re.match(
            r"(?:unit|Slot:)\s*(\d+)\s+(?:port|Port:)\s*(\d+)\s+(.*)",
            descr,
            re.IGNORECASE,
        )
        if m:
            unit = int(m.group(1))
            port = int(m.group(2))
            speed = "x" if "10G" in m.group(3) else "g"
            port_map[idx] = f"U{unit}/{speed}{port}"
        elif descr.strip().startswith("lag "):
            lag_num = int(descr.split()[1])
            port_map[idx] = f"LAG{lag_num}"
        else:
            # Use ifDescr directly for non-Netgear switches
            port_map[idx] = descr.strip()
    return port_map


class SnmpBridgeDiscovery:
    """Discover L2 topology by querying switch MAC forwarding tables via SNMP."""

    def __init__(self, switches: list[tuple[str, str]]):
        """
        Args:
            switches: List of (ip, community) tuples.
        """
        self.switches = switches

    async def _query_switch(
        self,
        switch_ip: str,
        community: str,
    ) -> dict[str, SwitchPortMapping]:
        """Query a single switch's MAC forwarding table.

        Returns dict {mac_address: SwitchPortMapping}.
        """
        logger.info(f"SNMP querying switch {switch_ip} (community: {community})...")
        engine = SnmpEngine()
        auth = CommunityData(community)
        target = await UdpTransportTarget.create((switch_ip, 161))

        result: dict[str, SwitchPortMapping] = {}

        try:
            # Get system name
            scalars = await _snmp_get_scalars(
                engine,
                auth,
                target,
                _OID_SYS_NAME,
                host=switch_ip,
            )
            sys_name = str(scalars[0]) if scalars[0] is not None else ""

            # Get ifDescr for port names
            if_descr_rows = await _snmp_walk_table(
                engine,
                auth,
                target,
                _OID_IF_DESCR,
                host=switch_ip,
            )
            if_descrs = {idx: str(val) for idx, val in if_descr_rows}
            port_names = _build_port_name_map(if_descrs)

            # Get bridge port -> ifIndex mapping
            bp_if_rows = await _snmp_walk_table(
                engine,
                auth,
                target,
                _OID_DOT1D_BASE_PORT_IF_INDEX,
                host=switch_ip,
            )
            bridge_port_to_if: dict[int, int] = {int(bp): int(if_idx) for bp, if_idx in bp_if_rows}

            # Try Q-BRIDGE-MIB (VLAN-aware) first: dot1qTpFdbPort
            # Index is (vlan_id, mac_octet1, mac_octet2, ..., mac_octet6)
            q_fdb_rows = await _snmp_walk_table(
                engine,
                auth,
                target,
                _OID_DOT1Q_TP_FDB_PORT,
                index_len=7,
                host=switch_ip,
            )

            if q_fdb_rows:
                logger.info(f"  {switch_ip}: {len(q_fdb_rows)} MAC entries via Q-BRIDGE-MIB")
                for idx, val in q_fdb_rows:
                    # idx = (vlan_id, mac1, mac2, mac3, mac4, mac5, mac6)
                    mac_octets = idx[1:]
                    mac = ":".join(f"{o:02x}" for o in mac_octets)
                    bridge_port = int(val)
                    if bridge_port == 0:
                        continue
                    if_index = bridge_port_to_if.get(bridge_port, bridge_port)
                    pname = port_names.get(if_index, f"port{if_index}")
                    # Deduplicate: keep first occurrence
                    if mac not in result:
                        result[mac] = SwitchPortMapping(
                            switch_ip=switch_ip,
                            switch_name=sys_name,
                            port_index=if_index,
                            port_name=pname,
                        )
            else:
                # Fallback: BRIDGE-MIB dot1dTpFdbPort
                fdb_addr_rows = await _snmp_walk_table(
                    engine,
                    auth,
                    target,
                    _OID_DOT1D_TP_FDB_ADDRESS,
                    index_len=6,
                    host=switch_ip,
                )
                fdb_port_rows = await _snmp_walk_table(
                    engine,
                    auth,
                    target,
                    _OID_DOT1D_TP_FDB_PORT,
                    index_len=6,
                    host=switch_ip,
                )

                # Build MAC -> bridge_port from dot1dTpFdbPort
                mac_to_bp: dict[str, int] = {}
                for idx, val in fdb_port_rows:
                    mac = ":".join(f"{o:02x}" for o in idx)
                    mac_to_bp[mac] = int(val)

                logger.info(f"  {switch_ip}: {len(mac_to_bp)} MAC entries via BRIDGE-MIB")

                for mac, bridge_port in mac_to_bp.items():
                    if bridge_port == 0:
                        continue
                    if_index = bridge_port_to_if.get(bridge_port, bridge_port)
                    pname = port_names.get(if_index, f"port{if_index}")
                    result[mac] = SwitchPortMapping(
                        switch_ip=switch_ip,
                        switch_name=sys_name,
                        port_index=if_index,
                        port_name=pname,
                    )

            logger.info(f"  {switch_ip} ({sys_name}): {len(result)} MAC->port mappings")
        except Exception as e:
            logger.error(f"SNMP query failed for {switch_ip}: {e}")
        finally:
            engine.close_dispatcher()

        return result

    async def _discover_all(self) -> dict[str, list[SwitchPortMapping]]:
        """Query all switches concurrently.

        Returns dict {mac_address: [SwitchPortMapping, ...]} — a MAC may appear
        on multiple switches (e.g. learned via uplink).
        """
        tasks = [self._query_switch(ip, community) for ip, community in self.switches]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        merged: dict[str, list[SwitchPortMapping]] = {}
        for r in results:
            if isinstance(r, BaseException):
                logger.error(f"Switch query failed: {r}")
                continue
            for mac, mapping in r.items():
                merged.setdefault(mac, []).append(mapping)

        return merged

    def discover(self) -> dict[str, list[SwitchPortMapping]]:
        """Synchronous entry point. Returns {mac: [SwitchPortMapping, ...]}."""
        return asyncio.run(self._discover_all())

    @staticmethod
    def build_l2_topology(
        hosts: list[DiscoveredHost],
        mac_table: dict[str, list[SwitchPortMapping]],
        switch_ips: set[str],
    ) -> tuple[list[L2TopologyEntry], dict[str, str]]:
        """Cross-reference ARP results with switch MAC tables.

        Args:
            hosts: Discovered hosts with IP and MAC.
            mac_table: {mac: [SwitchPortMapping, ...]} from all switches.
            switch_ips: Set of switch IPs to identify uplinks.

        Returns:
            (l2_entries, topology_tree) where topology_tree maps host_ip -> switch_ip.
        """
        # Build MAC -> host IP lookup
        mac_to_host: dict[str, DiscoveredHost] = {}
        for host in hosts:
            if host.mac:
                mac_to_host[host.mac.lower()] = host

        # Find MAC addresses of the switches themselves
        switch_macs: dict[str, str] = {}  # switch_ip -> mac
        for host in hosts:
            if host.ip in switch_ips and host.mac:
                switch_macs[host.ip] = host.mac.lower()

        # Identify uplink ports: ports where another switch's MAC was learned
        uplink_ports: set[tuple[str, int]] = set()  # (switch_ip, if_index)
        for sw_ip, sw_mac in switch_macs.items():
            if sw_mac in mac_table:
                for mapping in mac_table[sw_mac]:
                    if mapping.switch_ip != sw_ip:
                        uplink_ports.add((mapping.switch_ip, mapping.port_index))

        l2_entries: list[L2TopologyEntry] = []
        topology_tree: dict[str, str] = {}

        for host in hosts:
            if not host.mac or host.is_gateway:
                continue
            mac = host.mac.lower()
            if mac not in mac_table:
                continue

            mappings = mac_table[mac]

            # Prefer the most specific switch: the one where this MAC is NOT on
            # an uplink port (i.e. directly connected)
            best: SwitchPortMapping | None = None
            for m in mappings:
                is_uplink = (m.switch_ip, m.port_index) in uplink_ports
                if not is_uplink:
                    best = m
                    break
            if best is None:
                # All are uplink ports — take the first one
                best = mappings[0]

            l2_entries.append(
                L2TopologyEntry(
                    host_ip=host.ip,
                    host_mac=host.mac,
                    switch=best,
                    source="snmp",
                )
            )
            topology_tree[host.ip] = best.switch_ip

        # Switch-to-switch connections: if switch B's MAC is on switch A's port,
        # that port is the uplink from A to B (i.e. B is "behind" A via that port)
        for sw_ip, sw_mac in switch_macs.items():
            if sw_mac in mac_table:
                for mapping in mac_table[sw_mac]:
                    if mapping.switch_ip != sw_ip:
                        # sw_ip is reachable from mapping.switch_ip via mapping.port_index
                        topology_tree[sw_ip] = mapping.switch_ip
                        l2_entries.append(
                            L2TopologyEntry(
                                host_ip=sw_ip,
                                host_mac=sw_mac,
                                switch=mapping,
                                source="snmp",
                            )
                        )

        return l2_entries, topology_tree
