"""Network topology discovery subpackage.

Provides tools for discovering network topology via ARP scanning, reverse DNS,
MAC vendor lookup, SNMP bridge tables, LLDP, nmap, and traceroute. Generates
Mermaid flowchart diagrams or JSON output.
"""

from networkmgmt.discovery.lldp import LldpDiscovery
from networkmgmt.discovery.mermaid import MermaidGenerator
from networkmgmt.discovery.models import (
    DeviceCategory,
    DiscoveredHost,
    L2TopologyEntry,
    NetworkInterface,
    NetworkTopology,
    SubnetScan,
    SwitchPortMapping,
    TracerouteHop,
    TraceroutePath,
)
from networkmgmt.discovery.scanner import NetworkTopologyScanner
from networkmgmt.discovery.snmp import SnmpBridgeDiscovery

__all__ = [
    "NetworkTopologyScanner",
    "MermaidGenerator",
    "SnmpBridgeDiscovery",
    "LldpDiscovery",
    "DeviceCategory",
    "DiscoveredHost",
    "L2TopologyEntry",
    "NetworkInterface",
    "NetworkTopology",
    "SubnetScan",
    "SwitchPortMapping",
    "TracerouteHop",
    "TraceroutePath",
]
