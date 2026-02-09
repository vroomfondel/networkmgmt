"""QNAP QSW switch client."""

from __future__ import annotations

import logging
from typing import Self

from networkmgmt.switchctrl.base.client import BaseSwitchClient
from networkmgmt.switchctrl.base.managers import (
    BaseLACPManager,
    BaseMonitoringManager,
    BasePortManager,
    BaseVLANManager,
)
from networkmgmt.switchctrl.factory import register_vendor
from networkmgmt.switchctrl.vendors.common.cisco_cli import CiscoCLITransport
from networkmgmt.switchctrl.vendors.common.cisco_managers import (
    CiscoLACPManager,
    CiscoPortManager,
    CiscoVLANManager,
)
from networkmgmt.switchctrl.vendors.qnap.rest import QNAPMonitoringManager, QNAPRESTTransport
from networkmgmt.switchctrl.vendors.qnap.utils import generate_enable_password

logger = logging.getLogger(__name__)


@register_vendor("qnap")
class QNAPSwitch(BaseSwitchClient):
    """High-level client for QNAP QSW-M408 switch management.

    Uses REST API for monitoring and SSH CLI for configuration.

    Usage::

        with QNAPSwitch(host="192.168.1.1", password="admin123") as switch:
            # Monitoring (REST)
            info = switch.monitoring.get_system_info()
            print(info.model, info.firmware_version)

            # Configuration (SSH) - requires enable mode
            switch.enable()
            switch.vlan.create_vlan(100, "servers")
    """

    def __init__(
        self,
        host: str,
        password: str,
        username: str = "admin",
        ssh_username: str = "guest",
        ssh_password: str = "guest123",
        rest_port: int = 443,
        ssh_port: int = 22,
        verify_ssl: bool = False,
        enable_password: str | None = None,
    ):
        super().__init__(host)
        self._enable_password = enable_password

        # REST transport for monitoring
        self._rest = QNAPRESTTransport(
            host=host,
            password=password,
            username=username,
            port=rest_port,
            verify_ssl=verify_ssl,
        )

        # SSH transport for configuration (Cisco-style CLI)
        self._ssh = CiscoCLITransport(
            host=host,
            username=ssh_username,
            password=ssh_password,
            port=ssh_port,
            enable_password=enable_password,
        )

        # Lazy-initialized managers
        self._monitoring: QNAPMonitoringManager | None = None
        self._vlan: CiscoVLANManager | None = None
        self._port: CiscoPortManager | None = None
        self._lacp: CiscoLACPManager | None = None

    @property
    def monitoring(self) -> BaseMonitoringManager:
        """Access monitoring operations (REST API).

        Connects REST transport on first access if not yet connected.
        """
        if self._monitoring is None:
            if not self._rest.is_connected():
                self._rest.connect()
            self._monitoring = QNAPMonitoringManager(self._rest)
        return self._monitoring

    @property
    def vlan(self) -> BaseVLANManager:
        """Access VLAN management (SSH CLI)."""
        if self._vlan is None:
            self._ensure_ssh()
            self._vlan = CiscoVLANManager(self._ssh)
        return self._vlan

    @property
    def port(self) -> BasePortManager:
        """Access port configuration (SSH CLI)."""
        if self._port is None:
            self._ensure_ssh()
            self._port = CiscoPortManager(self._ssh)
        return self._port

    @property
    def lacp(self) -> BaseLACPManager:
        """Access LACP management (SSH CLI)."""
        if self._lacp is None:
            self._ensure_ssh()
            self._lacp = CiscoLACPManager(self._ssh)
        return self._lacp

    def enable(self, password: str | None = None) -> None:
        """Enter privileged EXEC mode on the SSH CLI.

        If no password is provided, attempts to auto-generate it
        from the switch serial number (requires REST connection).

        Args:
            password: Enable password. If None, auto-generated from serial.
        """
        self._ensure_ssh()

        if password:
            self._ssh.enter_enable_mode(password)
            return

        if self._enable_password:
            self._ssh.enter_enable_mode(self._enable_password)
            return

        # Auto-generate from serial number
        logger.info("Auto-generating enable password from serial number")
        info = self.monitoring.get_system_info()
        if not info.serial_number:
            raise RuntimeError("Could not retrieve serial number for enable password generation")

        generated = generate_enable_password(info.serial_number)
        self._ssh.enter_enable_mode(generated)

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
