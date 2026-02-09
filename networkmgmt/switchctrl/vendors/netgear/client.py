"""Netgear switch client (stub)."""

from __future__ import annotations

from networkmgmt.switchctrl.base.client import BaseSwitchClient
from networkmgmt.switchctrl.base.managers import (
    BaseLACPManager,
    BaseMonitoringManager,
    BasePortManager,
    BaseVLANManager,
)
from typing import Any

from networkmgmt.switchctrl.factory import register_vendor


@register_vendor("netgear")
class NetgearSwitch(BaseSwitchClient):
    """Stub client for Netgear switch management.

    Not yet implemented — all operations raise NotImplementedError.
    """

    def __init__(self, host: str, **kwargs: Any) -> None:
        super().__init__(host)

    @property
    def monitoring(self) -> BaseMonitoringManager:
        raise NotImplementedError("Netgear support not yet implemented")

    @property
    def vlan(self) -> BaseVLANManager:
        raise NotImplementedError("Netgear support not yet implemented")

    @property
    def port(self) -> BasePortManager:
        raise NotImplementedError("Netgear support not yet implemented")

    @property
    def lacp(self) -> BaseLACPManager:
        raise NotImplementedError("Netgear support not yet implemented")

    def connect(self) -> None:
        raise NotImplementedError("Netgear support not yet implemented")

    def disconnect(self) -> None:
        pass
