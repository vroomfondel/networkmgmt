"""System-related data models."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SystemInfo:
    """System and board information."""

    hostname: str = ""
    mac_address: str = ""
    serial_number: str = ""
    firmware_version: str = ""
    firmware_date: str = ""
    model: str = ""
    uptime: int = 0


@dataclass
class SensorData:
    """Temperature and fan sensor readings."""

    temperature: float = 0.0
    max_temperature: float = 0.0
    fan_speed: int = 0


@dataclass
class LACPInfo:
    """LACP / port-channel information."""

    port_channel_id: int = 0
    member_ports: list[str] = field(default_factory=list)
    admin_key: int = 0
    partner_key: int = 0
    status: str = ""
