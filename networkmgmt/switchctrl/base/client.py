"""Abstract base switch client."""

from __future__ import annotations

from abc import ABC, abstractmethod
from types import TracebackType
from typing import Any, Self

from networkmgmt.switchctrl.base.managers import (
    BaseLACPManager,
    BaseMonitoringManager,
    BasePortManager,
    BaseVLANManager,
)


class BaseSwitchClient(ABC):
    """Abstract base class for vendor-specific switch clients.

    Provides a unified interface for switch management across vendors.
    Each vendor implements concrete manager properties.
    """

    def __init__(self, host: str, **kwargs: Any) -> None:
        self.host = host

    @property
    @abstractmethod
    def monitoring(self) -> BaseMonitoringManager:
        """Access monitoring operations."""

    @property
    @abstractmethod
    def vlan(self) -> BaseVLANManager:
        """Access VLAN management."""

    @property
    @abstractmethod
    def port(self) -> BasePortManager:
        """Access port configuration."""

    @property
    @abstractmethod
    def lacp(self) -> BaseLACPManager:
        """Access LACP management."""

    @abstractmethod
    def connect(self) -> None:
        """Explicitly connect all transports."""

    @abstractmethod
    def disconnect(self) -> None:
        """Disconnect all transports."""

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: TracebackType | None
    ) -> None:
        self.disconnect()
