"""QNAP REST API transport and monitoring manager."""

from __future__ import annotations

import base64
import logging
import time
from collections.abc import Sequence
from typing import Any, TypedDict

import requests

from networkmgmt.switchctrl.base.managers import BaseMonitoringManager
from networkmgmt.switchctrl.base.transport import BaseTransport
from networkmgmt.switchctrl.exceptions import APIError, AuthenticationError
from networkmgmt.switchctrl.models.port import PortStatus
from networkmgmt.switchctrl.models.stats import PortStatistics
from networkmgmt.switchctrl.models.system import LACPInfo, SensorData, SystemInfo

logger = logging.getLogger(__name__)

API_PATH_V1 = "api/v1"

DEFAULT_DELAY = 0.3
DEFAULT_TIMEOUT = 10.0

DEFAULT_ENDPOINTS: tuple[str, ...] = (
    "system/board",
    "system/info",
    "system/sensor",
    "system/time",
    "firmware/info",
    "firmware/condition",
    "ports/status",
    "ports/statistics",
    "ports/config",
    "ports/transceiver",
    "vlan",
    "lacp/info",
    "lacp/group",
    "rstp",
    "igmp",
    "mirror",
    "loopProtect/state",
    "ip/ipv4/config",
    "qos/mode",
    "snmp/service",
)

BLOCKED_ENDPOINTS: frozenset[str] = frozenset({"lldp/neighbors/status"})


class QNAPNonJSONError(APIError):
    """The endpoint answered without a JSON body (ip/ipv4/config, ports/config, loopProtect/state on QSW-M408)."""


class QNAPBlockedEndpointError(APIError):
    """The endpoint is known to time out and is suspected of killing the switch backend."""


class QNAPFacts(TypedDict):
    """Result of :meth:`QNAPMonitoringManager.get_facts`."""

    facts: dict[str, Any]
    unavailable: dict[str, str]


def normalize_endpoint(endpoint: str) -> str:
    """Strip slashes and the ``api/v1`` prefix from an endpoint path.

    Args:
        endpoint: Endpoint such as ``"api/v1/vlan"`` or ``"/vlan/"``.

    Returns:
        The path relative to ``/api/v1``.
    """
    path = endpoint.strip("/")
    prefix = f"{API_PATH_V1}/"
    return path[len(prefix) :] if path.startswith(prefix) else path


class QNAPRESTTransport(BaseTransport):
    """HTTP REST transport using Bearer token authentication.

    The QNAP QSW REST API requires login via POST with a Base64-encoded
    password. The response contains a Bearer token used for subsequent requests.

    Every request is serialised and spaced by ``delay`` seconds. The switch backend
    (``aricent-backend.arm``) forks ``/bin/ubus`` per API call; request bursts end in
    ``fork/exec /bin/ubus: cannot allocate memory`` and take down the web UI and ``/api/v1``
    (switching keeps running). Failed requests are never retried. On the switch the fix is
    ``vm.overcommit_memory=1`` in ``/etc/sysctl.conf`` (a firmware update resets it).
    """

    def __init__(
        self,
        host: str,
        password: str,
        username: str = "admin",
        port: int = 443,
        verify_ssl: bool = False,
        delay: float = DEFAULT_DELAY,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        super().__init__(host, username, password, port)
        self.verify_ssl = verify_ssl
        self.delay = delay
        self.timeout = timeout
        self._last_request: float | None = None
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
            self._pace()
            resp = self._session.post(
                url, json={"username": self.username, "password": encoded_pw}, timeout=self.timeout
            )
            resp.raise_for_status()
        except requests.RequestException as e:
            raise AuthenticationError(f"REST login failed: {e}") from e

        try:
            data = resp.json()
        except ValueError as e:
            raise AuthenticationError("REST login returned no JSON") from e
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
                self._pace()
                self._session.post(f"{self.base_url}/{API_PATH_V1}/users/exit", json={}, timeout=self.timeout)
            except requests.RequestException:
                logger.debug("REST logout request failed (ignored)")
            self._token = None
        if self._session:
            self._session.close()
            self._session = None

    def is_connected(self) -> bool:
        """Check if REST session has a valid token."""
        return self._session is not None and self._token is not None

    def get(self, endpoint: str, allow_blocked: bool = False) -> dict[str, Any]:
        """Send a GET request to the API.

        Args:
            endpoint: Path relative to base URL (e.g. "api/v1/ports/status").
            allow_blocked: Permit endpoints in ``BLOCKED_ENDPOINTS`` (logs a warning).

        Returns:
            Parsed JSON response as dict.

        Raises:
            QNAPBlockedEndpointError: Endpoint is blocked and ``allow_blocked`` is false.
            QNAPNonJSONError: The endpoint answered without JSON.
            APIError: Transport failure or ``error_code`` other than 200.
        """
        path = normalize_endpoint(endpoint)
        if path in BLOCKED_ENDPOINTS:
            if not allow_blocked:
                raise QNAPBlockedEndpointError(f"GET {path} is blocked (times out, may crash the switch backend)")
            logger.warning("GET %s requested explicitly; it may time out and crash the switch backend", path)
        return self._request("GET", endpoint)

    def post(self, endpoint: str, data: dict | None = None) -> dict[str, Any]:
        """Send a POST request to the API.

        Args:
            endpoint: Path relative to base URL.
            data: JSON body payload.

        Returns:
            Parsed JSON response as dict.
        """
        return self._request("POST", endpoint, data)

    def put(self, endpoint: str, data: dict | None = None) -> dict[str, Any]:
        """Send a PUT request to the API.

        Args:
            endpoint: Path relative to base URL.
            data: JSON body payload.

        Returns:
            Parsed JSON response as dict.
        """
        return self._request("PUT", endpoint, data)

    def delete(self, endpoint: str, data: dict | None = None) -> dict[str, Any]:
        """Send a DELETE request with a JSON body to the API.

        Args:
            endpoint: Path relative to base URL.
            data: JSON body payload.

        Returns:
            Parsed JSON response as dict.
        """
        return self._request("DELETE", endpoint, data)

    def _request(self, method: str, endpoint: str, data: dict | None = None) -> dict[str, Any]:
        self._ensure_connected()
        assert self._session is not None
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        kwargs: dict[str, Any] = {"timeout": self.timeout}
        if method != "GET":
            kwargs["json"] = data or {}
        self._pace()
        try:
            resp = getattr(self._session, method.lower())(url, **kwargs)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise APIError(f"{method} {endpoint} failed: {e}") from e

        if not resp.content:
            return {}
        try:
            body = resp.json()
        except ValueError as e:
            raise QNAPNonJSONError(f"{method} {endpoint}: HTTP {resp.status_code}, no JSON") from e
        if not isinstance(body, dict):
            raise QNAPNonJSONError(f"{method} {endpoint}: unexpected JSON type {type(body).__name__}")
        error_code = body.get("error_code")
        if error_code is not None and error_code != 200:
            raise APIError(f"{method} {endpoint}: error_code {error_code}: {body.get('error_message')}")
        return body

    def _pace(self) -> None:
        if self.delay > 0 and self._last_request is not None:
            wait = self.delay - (time.monotonic() - self._last_request)
            if wait > 0:
                time.sleep(wait)
        self._last_request = time.monotonic()

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
            serial_number=result.get("serialNum") or result.get("SerialNumber", ""),
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

    def get_facts(self, endpoints: Sequence[str] | None = None, include_lldp: bool = False) -> QNAPFacts:
        """Read a list of endpoints serially, collecting failures instead of aborting.

        Args:
            endpoints: Paths relative to ``/api/v1``. Defaults to ``DEFAULT_ENDPOINTS``.
            include_lldp: Also query ``lldp/neighbors/status`` (times out, may crash the switch backend).

        Returns:
            ``facts`` maps each path (``/`` replaced by ``_``) to its ``result``; ``unavailable`` maps
            paths that gave no JSON, an error or are blocked to the reason.
        """
        paths = [normalize_endpoint(e) for e in (endpoints if endpoints is not None else DEFAULT_ENDPOINTS)]
        if include_lldp and "lldp/neighbors/status" not in paths:
            paths.append("lldp/neighbors/status")

        facts: dict[str, Any] = {}
        unavailable: dict[str, str] = {}
        for path in paths:
            try:
                body = self._transport.get(f"{API_PATH_V1}/{path}", allow_blocked=include_lldp)
            except APIError as e:
                unavailable[path] = str(e)
                continue
            facts[path.replace("/", "_")] = body.get("result")
        return QNAPFacts(facts=facts, unavailable=unavailable)
