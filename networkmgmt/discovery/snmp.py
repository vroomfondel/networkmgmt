"""SNMP Bridge Discovery — L2 topology via switch MAC forwarding tables."""

from __future__ import annotations

import asyncio
import re

from loguru import logger

from networkmgmt.discovery.models import (
    DiscoveredHost,
    L2TopologyEntry,
    SwitchPortMapping,
)
from networkmgmt.snmp import (
    HAS_PYSNMP,
    AsyncSnmpSession,
    SnmpAuthenticationError,
    SnmpCredentials,
    SnmpError,
    SnmpTimeoutError,
)

__all__ = ["HAS_PYSNMP", "SnmpBridgeDiscovery", "SwitchSpec", "describe_snmp_error"]

# BRIDGE-MIB / Q-BRIDGE-MIB OIDs for MAC forwarding table
_OID_SYS_NAME = "1.3.6.1.2.1.1.5.0"
_OID_IF_DESCR = "1.3.6.1.2.1.2.2.1.2"
_OID_DOT1D_TP_FDB_PORT = "1.3.6.1.2.1.17.4.3.1.2"
_OID_DOT1D_BASE_PORT_IF_INDEX = "1.3.6.1.2.1.17.1.4.1.2"
_OID_DOT1Q_TP_FDB_PORT = "1.3.6.1.2.1.17.7.1.2.2.1.2"

SwitchSpec = tuple[str, str | SnmpCredentials]


def _mac_from_octets(octets: list[str]) -> str:
    return ":".join(f"{int(o):02x}" for o in octets)


def describe_snmp_error(exc: SnmpError, host: str, credentials: SnmpCredentials) -> str:
    """Return an actionable one-line description of an SNMP failure."""
    if isinstance(exc, SnmpAuthenticationError):
        who = f"user {credentials.user!r}" if credentials.user else "community"
        return f"SNMP authentication failed for {host} ({who}): {exc}"
    if isinstance(exc, SnmpTimeoutError) and credentials.user:
        return (
            f"SNMP timeout from {host}: unreachable, or SNMPv3 user {credentials.user!r} / auth key / "
            f"auth protocol ({credentials.auth_protocol}) / priv settings rejected (agents often stay silent): {exc}"
        )
    if isinstance(exc, SnmpTimeoutError):
        return f"SNMP timeout from {host}: unreachable or wrong community: {exc}"
    return f"SNMP error from {host}: {exc}"


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

    def __init__(self, switches: list[SwitchSpec], timeout: float = 5.0, retries: int = 1):
        """
        Args:
            switches: List of (ip, community or SnmpCredentials) tuples.
            timeout: Per-request SNMP timeout in seconds.
            retries: SNMP retries per request.
        """
        self.switches = switches
        self.timeout = timeout
        self.retries = retries

    async def _query_switch(
        self,
        switch_ip: str,
        credentials: str | SnmpCredentials,
    ) -> dict[str, SwitchPortMapping]:
        """Query a single switch's MAC forwarding table.

        Returns dict {mac_address: SwitchPortMapping}.
        """
        creds = SnmpCredentials.v2c(credentials) if isinstance(credentials, str) else credentials
        who = f"user {creds.user}, {creds.auth_protocol}" if creds.user else f"community {creds.community}"
        logger.info(f"SNMP querying switch {switch_ip} (SNMP {creds.version}, {who})...")

        result: dict[str, SwitchPortMapping] = {}

        try:
            async with AsyncSnmpSession(switch_ip, creds, timeout=self.timeout, retries=self.retries) as snmp:
                sys_name = await snmp.get(_OID_SYS_NAME)
                port_names = _build_port_name_map({int(k): v for k, v in (await snmp.walk(_OID_IF_DESCR)).items()})
                bridge_port_to_if = {
                    int(bp): if_idx for bp, if_idx in (await snmp.walk_int(_OID_DOT1D_BASE_PORT_IF_INDEX)).items()
                }

                q_fdb = await snmp.walk_int(_OID_DOT1Q_TP_FDB_PORT)
                if q_fdb:
                    logger.info(f"  {switch_ip}: {len(q_fdb)} MAC entries via Q-BRIDGE-MIB")
                    entries: dict[str, int] = {}
                    for suffix, port in q_fdb.items():
                        entries.setdefault(_mac_from_octets(suffix.split(".")[1:]), port)
                else:
                    fdb = await snmp.walk_int(_OID_DOT1D_TP_FDB_PORT)
                    logger.info(f"  {switch_ip}: {len(fdb)} MAC entries via BRIDGE-MIB")
                    entries = {_mac_from_octets(suffix.split(".")): port for suffix, port in fdb.items()}

            for mac, bridge_port in entries.items():
                if bridge_port == 0:
                    continue
                if_index = bridge_port_to_if.get(bridge_port, bridge_port)
                result[mac] = SwitchPortMapping(
                    switch_ip=switch_ip,
                    switch_name=sys_name,
                    port_index=if_index,
                    port_name=port_names.get(if_index, f"port{if_index}"),
                )
            logger.info(f"  {switch_ip} ({sys_name}): {len(result)} MAC->port mappings")
        except SnmpError as e:
            logger.error(describe_snmp_error(e, switch_ip, creds))
        except Exception as e:
            logger.error(f"SNMP query failed for {switch_ip}: {e}")

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
