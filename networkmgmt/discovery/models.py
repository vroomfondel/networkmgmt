"""Pydantic models and enums for network topology discovery."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class DeviceCategory(str, Enum):
    INFRASTRUCTURE = "Infrastructure"
    SERVER = "Servers"
    IOT = "IoT / Smart"
    PHONE = "Phones / VoIP"
    MEDIA = "Media"
    HOME_AUTOMATION = "Home Automation"
    COMPUTER = "Computers / Printers"
    OTHER = "Other"


class NetworkInterface(BaseModel):
    name: str
    ip: str
    netmask: str
    mac: str = ""
    is_default: bool = False


class DiscoveredHost(BaseModel):
    ip: str
    mac: str = ""
    hostname: str = ""
    vendor: str = ""
    services: list[str] = Field(default_factory=list)
    is_gateway: bool = False
    is_infrastructure: bool = False
    category: str = ""


class TracerouteHop(BaseModel):
    hop_number: int
    ip: str = ""
    hostname: str = ""
    rtt_ms: float = 0.0
    is_timeout: bool = False


class TraceroutePath(BaseModel):
    target: str
    hops: list[TracerouteHop] = Field(default_factory=list)
    completed: bool = False


class SwitchPortMapping(BaseModel):
    switch_ip: str
    switch_name: str = ""
    port_index: int
    port_name: str = ""  # e.g. "U1/g3"


class L2TopologyEntry(BaseModel):
    host_ip: str
    host_mac: str
    switch: SwitchPortMapping
    source: str = ""  # "snmp", "lldp", "manual"


class SubnetScan(BaseModel):
    interface: NetworkInterface
    gateway: Optional[DiscoveredHost] = None
    hosts: list[DiscoveredHost] = Field(default_factory=list)
    topology_tree: dict[str, str] = Field(default_factory=dict)
    l2_topology: list[L2TopologyEntry] = Field(default_factory=list)


class NetworkTopology(BaseModel):
    local_interface: NetworkInterface
    subnets: list[SubnetScan] = Field(default_factory=list)
    gateway: Optional[DiscoveredHost] = None
    local_hosts: list[DiscoveredHost] = Field(default_factory=list)
    traceroute_paths: list[TraceroutePath] = Field(default_factory=list)
    topology_tree: dict[str, str] = Field(default_factory=dict)
    l2_topology: list[L2TopologyEntry] = Field(default_factory=list)
    timestamp: str = ""
