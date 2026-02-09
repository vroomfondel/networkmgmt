"""Cisco Catalyst 1200 switch client (SSH-only, no REST API)."""

from __future__ import annotations

import logging
from typing import Any

from networkmgmt.switchctrl.base.client import BaseSwitchClient
from networkmgmt.switchctrl.base.managers import (
    BaseLACPManager,
    BaseMonitoringManager,
    BasePortManager,
    BaseVLANManager,
)
from networkmgmt.switchctrl.factory import register_vendor
from networkmgmt.switchctrl.vendors.common.cisco_cli import CiscoCLITransport
from networkmgmt.switchctrl.vendors.cisco.managers import (
    CiscoCatalystLACPManager,
    CiscoCatalystPortManager,
    CiscoCatalystVLANManager,
    CiscoCLIMonitoringManager,
)

logger = logging.getLogger(__name__)


@register_vendor("cisco")
class CiscoSwitch(BaseSwitchClient):
    """Client for Cisco Catalyst 1200 (C1200-8T-D) switch management.

    Uses SSH CLI exclusively — the C1200 has no REST API.
    No default credentials: the C1200 enforces password change on first login.

    Usage::

        with CiscoSwitch(
            host="192.168.1.254",
            username="admin",
            password="mypass",
            enable_password="myenable",
        ) as switch:
            switch.enable()
            info = switch.monitoring.get_system_info()
            print(info.model, info.firmware_version)
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        enable_password: str,
        ssh_port: int = 22,
        **kwargs: Any,
    ) -> None:
        super().__init__(host)
        self._enable_password = enable_password

        self._ssh = CiscoCLITransport(
            host=host,
            username=username,
            password=password,
            port=ssh_port,
            enable_password=enable_password,
        )

        # Lazy-initialized managers
        self._monitoring: CiscoCLIMonitoringManager | None = None
        self._vlan: CiscoCatalystVLANManager | None = None
        self._port: CiscoCatalystPortManager | None = None
        self._lacp: CiscoCatalystLACPManager | None = None

    @property
    def monitoring(self) -> BaseMonitoringManager:
        """Access monitoring operations (SSH CLI)."""
        if self._monitoring is None:
            self._ensure_ssh()
            self._monitoring = CiscoCLIMonitoringManager(self._ssh)
        return self._monitoring

    @property
    def vlan(self) -> BaseVLANManager:
        """Access VLAN management (SSH CLI)."""
        if self._vlan is None:
            self._ensure_ssh()
            self._vlan = CiscoCatalystVLANManager(self._ssh)
        return self._vlan

    @property
    def port(self) -> BasePortManager:
        """Access port configuration (SSH CLI)."""
        if self._port is None:
            self._ensure_ssh()
            self._port = CiscoCatalystPortManager(self._ssh)
        return self._port

    @property
    def lacp(self) -> BaseLACPManager:
        """Access LACP management (SSH CLI)."""
        if self._lacp is None:
            self._ensure_ssh()
            self._lacp = CiscoCatalystLACPManager(self._ssh)
        return self._lacp

    def enable(self, password: str | None = None) -> None:
        """Enter privileged EXEC mode on the SSH CLI.

        Args:
            password: Enable password. If None, uses the password from init.
        """
        self._ensure_ssh()
        self._ssh.enter_enable_mode(password or self._enable_password)

    def connect(self) -> None:
        """Establish SSH connection."""
        self._ssh.connect()

    def disconnect(self) -> None:
        """Disconnect SSH and clear manager references."""
        self._monitoring = None
        self._vlan = None
        self._port = None
        self._lacp = None

        if self._ssh.is_connected():
            self._ssh.disconnect()

    def _ensure_ssh(self) -> None:
        if not self._ssh.is_connected():
            self._ssh.connect()
