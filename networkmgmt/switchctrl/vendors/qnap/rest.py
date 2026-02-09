"""QNAP REST API transport and monitoring manager."""

from __future__ import annotations

import base64
import logging
from typing import Any

import requests

from networkmgmt.switchctrl.base.managers import BaseMonitoringManager
from networkmgmt.switchctrl.base.transport import BaseTransport
from networkmgmt.switchctrl.exceptions import APIError, AuthenticationError
from networkmgmt.switchctrl.models.port import PortStatus
from networkmgmt.switchctrl.models.stats import PortStatistics
from networkmgmt.switchctrl.models.system import LACPInfo, SensorData, SystemInfo

logger = logging.getLogger(__name__)

API_PATH_V1 = "api/v1"


class QNAPRESTTransport(BaseTransport):
    """HTTP REST transport using Bearer token authentication.

    The QNAP QSW REST API requires login via POST with a Base64-encoded
    password. The response contains a Bearer token used for subsequent requests.
    """

    def __init__(
        self,
        host: str,
        password: str,
        username: str = "admin",
        port: int = 443,
        verify_ssl: bool = False,
    ):
        super().__init__(host, username, password, port)
        self.verify_ssl = verify_ssl
        self.base_url = f"https://{host}:{port}"
        self._session: requests.Session | None = None
        self._token: str | None = None

    def connect(self) -> None:
        """Login to the REST API and obtain a Bearer token."""
        self._session = requests.Session()
        self._session.verify = self.verify_ssl

        encoded_pw = base64.b64encode(self.password.encode()).decode()
        url = f"{self.base_url}/{API_PATH_V1}/users/login"

        try:
            resp = self._session.post(url, json={"username": self.username, "password": encoded_pw})
            resp.raise_for_status()
        except requests.RequestException as e:
            raise AuthenticationError(f"REST login failed: {e}") from e

        data = resp.json()
        token = data.get("result")
        if not token:
            raise AuthenticationError("REST login returned no token")

        self._token = token
        self._session.headers["Authorization"] = f"Bearer {self._token}"
        logger.info("REST login successful to %s", self.host)

    def disconnect(self) -> None:
        """Logout and close the REST session."""
        if self._session and self._token:
            try:
                self._session.post(f"{self.base_url}/{API_PATH_V1}/users/exit")
            except requests.RequestException:
                logger.debug("REST logout request failed (ignored)")
            self._token = None
        if self._session:
            self._session.close()
            self._session = None

    def is_connected(self) -> bool:
        """Check if REST session has a valid token."""
        return self._session is not None and self._token is not None

    def get(self, endpoint: str) -> dict[str, Any]:
        """Send a GET request to the API.

        Args:
            endpoint: Path relative to base URL (e.g. "api/v1/ports/status").

        Returns:
            Parsed JSON response as dict.
        """
        self._ensure_connected()
        assert self._session is not None
        url = f"{self.base_url}/{endpoint}"
        try:
            resp = self._session.get(url)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise APIError(f"GET {endpoint} failed: {e}") from e

        return resp.json()

    def post(self, endpoint: str, data: dict | None = None) -> dict[str, Any]:
        """Send a POST request to the API.

        Args:
            endpoint: Path relative to base URL.
            data: JSON body payload.

        Returns:
            Parsed JSON response as dict.
        """
        self._ensure_connected()
        assert self._session is not None
        url = f"{self.base_url}/{endpoint}"
        try:
            resp = self._session.post(url, json=data or {})
            resp.raise_for_status()
        except requests.RequestException as e:
            raise APIError(f"POST {endpoint} failed: {e}") from e

        return resp.json()

    def _ensure_connected(self) -> None:
        if not self.is_connected():
            raise APIError("Not connected. Call connect() first.")


class QNAPMonitoringManager(BaseMonitoringManager):
    """Read-only monitoring operations via QNAP REST API."""

    def __init__(self, transport: QNAPRESTTransport):
        self._transport = transport

    def get_port_status(self) -> list[PortStatus]:
        """Get operational status of all ports."""
        data = self._transport.get(f"{API_PATH_V1}/ports/status")
        result = data.get("result", {})
        ports: list[PortStatus] = []

        for port_key, port_data in sorted(result.items()):
            ports.append(
                PortStatus(
                    port=port_key,
                    link_up=port_data.get("linkStatus", 0) == 1,
                    speed=str(port_data.get("speed", "")),
                    duplex=port_data.get("duplex", ""),
                    media_type=port_data.get("mediaType", ""),
                    max_speed=str(port_data.get("maxSpeed", "")),
                )
            )

        return ports

    def get_port_statistics(self) -> list[PortStatistics]:
        """Get traffic statistics for all ports."""
        data = self._transport.get(f"{API_PATH_V1}/ports/statistics")
        result = data.get("result", {})
        stats: list[PortStatistics] = []

        for port_key, port_data in sorted(result.items()):
            stats.append(
                PortStatistics(
                    port=port_key,
                    tx_bytes=port_data.get("txOctets", 0),
                    rx_bytes=port_data.get("rxOctets", 0),
                    tx_packets=port_data.get("txPkts", 0),
                    rx_packets=port_data.get("rxPkts", 0),
                    tx_errors=port_data.get("txErrors", 0),
                    rx_errors=port_data.get("rxErrors", 0),
                    link_up=port_data.get("linkStatus", 0) == 1,
                    speed=str(port_data.get("speed", "")),
                )
            )

        return stats

    def get_system_info(self) -> SystemInfo:
        """Get system board information."""
        data = self._transport.get(f"{API_PATH_V1}/system/board")
        result = data.get("result", {})

        return SystemInfo(
            hostname=result.get("hostname", ""),
            mac_address=result.get("macAddr", ""),
            serial_number=result.get("serialNum", ""),
            firmware_version=result.get("fwVer", ""),
            firmware_date=result.get("fwDate", ""),
            model=result.get("modelName", ""),
            uptime=result.get("uptime", 0),
        )

    def get_sensor_data(self) -> SensorData:
        """Get temperature and fan sensor readings."""
        data = self._transport.get(f"{API_PATH_V1}/system/sensor")
        result = data.get("result", {})

        return SensorData(
            temperature=result.get("tempVal", 0.0),
            max_temperature=result.get("tempMax", 0.0),
            fan_speed=result.get("fanSpeed", 0),
        )

    def get_lacp_info(self) -> list[LACPInfo]:
        """Get LACP port-channel information."""
        data = self._transport.get(f"{API_PATH_V1}/lacp/info")
        result = data.get("result", {})
        infos: list[LACPInfo] = []

        for channel_key, channel_data in sorted(result.items()):
            members = channel_data.get("memberPorts", [])
            if isinstance(members, str):
                members = [m.strip() for m in members.split(",") if m.strip()]
            infos.append(
                LACPInfo(
                    port_channel_id=channel_data.get("trunkId", 0),
                    member_ports=members,
                    admin_key=channel_data.get("adminKey", 0),
                    partner_key=channel_data.get("partnerKey", 0),
                    status=channel_data.get("status", ""),
                )
            )

        return infos

    def get_firmware_info(self) -> dict:
        """Get firmware version and update information."""
        try:
            info = self._transport.get(f"{API_PATH_V1}/firmware/info")
        except APIError:
            info = {}
        try:
            condition = self._transport.get(f"{API_PATH_V1}/firmware/condition")
        except APIError:
            condition = {}

        return {
            "info": info.get("result", {}),
            "condition": condition.get("result", {}),
        }
