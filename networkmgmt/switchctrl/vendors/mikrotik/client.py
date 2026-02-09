"""MikroTik RouterOS switch client."""

from __future__ import annotations

import logging

from networkmgmt.switchctrl.base.client import BaseSwitchClient
from networkmgmt.switchctrl.base.managers import (
    BaseLACPManager,
    BaseMonitoringManager,
    BasePortManager,
    BaseVLANManager,
)
from networkmgmt.switchctrl.factory import register_vendor
from networkmgmt.switchctrl.vendors.mikrotik.managers import (
    MikroTikLACPManager,
    MikroTikMonitoringManager,
    MikroTikPortManager,
    MikroTikVLANManager,
)
from networkmgmt.switchctrl.vendors.mikrotik.rest import MikroTikRESTTransport
from networkmgmt.switchctrl.vendors.mikrotik.ssh import RouterOSTransport

logger = logging.getLogger(__name__)


@register_vendor("mikrotik")
class MikroTikSwitch(BaseSwitchClient):
    """High-level client for MikroTik RouterOS switch management.

    Uses REST API for monitoring and SSH CLI for configuration.
    No enable-mode concept — RouterOS uses direct admin access.

    Usage::

        with MikroTikSwitch(host="192.168.88.1", password="") as switch:
            info = switch.monitoring.get_system_info()
            print(info.model, info.firmware_version)

            switch.vlan.create_vlan(100, "servers")
    """

    def __init__(
        self,
        host: str,
        password: str = "",
        username: str = "admin",
        ssh_port: int = 22,
        rest_port: int = 443,
        verify_ssl: bool = False,
    ):
        super().__init__(host)

        # REST transport for monitoring
        self._rest = MikroTikRESTTransport(
            host=host,
            username=username,
            password=password,
            port=rest_port,
            verify_ssl=verify_ssl,
        )

        # SSH transport for configuration
        self._ssh = RouterOSTransport(
            host=host,
            username=username,
            password=password,
            port=ssh_port,
        )

        # Lazy-initialized managers
        self._monitoring: MikroTikMonitoringManager | None = None
        self._vlan: MikroTikVLANManager | None = None
        self._port: MikroTikPortManager | None = None
        self._lacp: MikroTikLACPManager | None = None

    @property
    def monitoring(self) -> BaseMonitoringManager:
        """Access monitoring operations (REST API)."""
        if self._monitoring is None:
            if not self._rest.is_connected():
                self._rest.connect()
            self._monitoring = MikroTikMonitoringManager(self._rest)
        return self._monitoring

    @property
    def vlan(self) -> BaseVLANManager:
        """Access VLAN management (SSH CLI)."""
        if self._vlan is None:
            self._ensure_ssh()
            self._vlan = MikroTikVLANManager(self._ssh)
        return self._vlan

    @property
    def port(self) -> BasePortManager:
        """Access port configuration (SSH CLI)."""
        if self._port is None:
            self._ensure_ssh()
            self._port = MikroTikPortManager(self._ssh)
        return self._port

    @property
    def lacp(self) -> BaseLACPManager:
        """Access LACP management (SSH CLI)."""
        if self._lacp is None:
            self._ensure_ssh()
            self._lacp = MikroTikLACPManager(self._ssh)
        return self._lacp

    def connect(self) -> None:
        """Explicitly connect both transports."""
        self._rest.connect()
        self._ssh.connect()

    def disconnect(self) -> None:
        """Disconnect both transports."""
        self._monitoring = None
        self._vlan = None
        self._port = None
        self._lacp = None

        if self._rest.is_connected():
            self._rest.disconnect()
        if self._ssh.is_connected():
            self._ssh.disconnect()

    def _ensure_ssh(self) -> None:
        if not self._ssh.is_connected():
            self._ssh.connect()
