"""Abstract base manager classes for switch operations."""

from __future__ import annotations

from abc import ABC, abstractmethod

from networkmgmt.switchctrl.models.port import PortConfig, PortStatus
from networkmgmt.switchctrl.models.stats import PortStatistics
from networkmgmt.switchctrl.models.system import LACPInfo, SensorData, SystemInfo
from networkmgmt.switchctrl.models.vlan import VLAN, TrunkConfig


class BaseMonitoringManager(ABC):
    """Abstract base class for monitoring operations."""

    @abstractmethod
    def get_port_status(self) -> list[PortStatus]:
        """Get operational status of all ports."""

    @abstractmethod
    def get_port_statistics(self) -> list[PortStatistics]:
        """Get traffic statistics for all ports."""

    @abstractmethod
    def get_system_info(self) -> SystemInfo:
        """Get system board information."""

    @abstractmethod
    def get_sensor_data(self) -> SensorData:
        """Get temperature and fan sensor readings."""

    @abstractmethod
    def get_lacp_info(self) -> list[LACPInfo]:
        """Get LACP port-channel information."""


class BaseVLANManager(ABC):
    """Abstract base class for VLAN management."""

    @abstractmethod
    def create_vlan(self, vlan_id: int, name: str = "") -> None:
        """Create a new VLAN."""

    @abstractmethod
    def delete_vlan(self, vlan_id: int) -> None:
        """Delete a VLAN."""

    @abstractmethod
    def list_vlans(self) -> list[VLAN]:
        """List all configured VLANs."""

    @abstractmethod
    def assign_port_to_vlan(self, port: str, vlan_id: int, tagged: bool = False) -> None:
        """Assign a port to a VLAN."""

    @abstractmethod
    def configure_trunk(self, config: TrunkConfig) -> None:
        """Configure a port as trunk with allowed VLANs."""


class BasePortManager(ABC):
    """Abstract base class for port configuration."""

    @abstractmethod
    def configure_port(self, config: PortConfig) -> None:
        """Apply a full port configuration."""

    @abstractmethod
    def enable_port(self, port: str) -> None:
        """Enable (no shutdown) a port."""

    @abstractmethod
    def disable_port(self, port: str) -> None:
        """Disable (shutdown) a port."""

    @abstractmethod
    def get_port_status(self, port: str | None = None) -> list[PortStatus]:
        """Get port status via CLI."""


class BaseLACPManager(ABC):
    """Abstract base class for LACP management."""

    @abstractmethod
    def create_port_channel(self, channel_id: int, member_ports: list[str]) -> None:
        """Create an LACP port-channel and add member ports."""

    @abstractmethod
    def delete_port_channel(self, channel_id: int) -> None:
        """Delete a port-channel."""

    @abstractmethod
    def get_port_channel_info(self) -> list[LACPInfo]:
        """Get port-channel information."""
