"""VLAN-related data models."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class VLAN:
    """Represents a VLAN configuration."""

    vlan_id: int
    name: str = ""
    tagged_ports: list[str] = field(default_factory=list)
    untagged_ports: list[str] = field(default_factory=list)


@dataclass
class TrunkConfig:
    """Trunk port configuration."""

    port: str
    native_vlan: int = 1
    allowed_vlans: list[int] = field(default_factory=list)
