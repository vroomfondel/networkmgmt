"""Port-related data models and enums."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PortSpeed(Enum):
    """Port speed settings."""

    AUTO = "auto"
    SPEED_10M = "10"
    SPEED_100M = "100"
    SPEED_1G = "1000"
    SPEED_10G = "10000"


class PortMode(Enum):
    """Port switchport mode."""

    ACCESS = "access"
    TRUNK = "trunk"


class DuplexMode(Enum):
    """Port duplex mode."""

    AUTO = "auto"
    FULL = "full"
    HALF = "half"


@dataclass
class PortConfig:
    """Desired port configuration."""

    port: str
    speed: PortSpeed = PortSpeed.AUTO
    duplex: DuplexMode = DuplexMode.AUTO
    mode: PortMode = PortMode.ACCESS
    enabled: bool = True
    description: str = ""
    access_vlan: int | None = None


@dataclass
class PortStatus:
    """Current port status (from REST API or CLI)."""

    port: str
    link_up: bool = False
    speed: str = ""
    duplex: str = ""
    media_type: str = ""
    max_speed: str = ""
