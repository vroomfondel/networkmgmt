"""Data models for switch management."""

from networkmgmt.switchctrl.models.port import DuplexMode, PortConfig, PortMode, PortSpeed, PortStatus
from networkmgmt.switchctrl.models.stats import PortStatistics
from networkmgmt.switchctrl.models.system import LACPInfo, SensorData, SystemInfo
from networkmgmt.switchctrl.models.vlan import VLAN, TrunkConfig

__all__ = [
    "VLAN",
    "TrunkConfig",
    "PortConfig",
    "PortStatus",
    "PortSpeed",
    "PortMode",
    "DuplexMode",
    "PortStatistics",
    "SystemInfo",
    "SensorData",
    "LACPInfo",
]
