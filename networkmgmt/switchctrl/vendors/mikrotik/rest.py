"""MikroTik RouterOS REST API transport (RouterOS v7+)."""

from __future__ import annotations

import logging
from typing import Any

import requests

from networkmgmt.switchctrl.base.transport import BaseTransport
from networkmgmt.switchctrl.exceptions import APIError, AuthenticationError

logger = logging.getLogger(__name__)


class MikroTikRESTTransport(BaseTransport):
    """HTTP REST transport using Basic Auth for RouterOS v7+.

    RouterOS REST API uses Basic Authentication and endpoints under /rest/*.
    """

    def __init__(
        self,
        host: str,
        username: str = "admin",
        password: str = "",
        port: int = 443,
        verify_ssl: bool = False,
    ):
        super().__init__(host, username, password, port)
        self.verify_ssl = verify_ssl
        self.base_url = f"https://{host}:{port}"
        self._session: requests.Session | None = None
        self._connected: bool = False

    def connect(self) -> None:
        """Establish REST session with Basic Auth."""
        self._session = requests.Session()
        self._session.verify = self.verify_ssl
        self._session.auth = (self.username, self.password)

        # Verify connectivity with a test request
        try:
            resp = self._session.get(f"{self.base_url}/rest/system/identity")
            resp.raise_for_status()
        except requests.RequestException as e:
            self._session.close()
            self._session = None
            raise AuthenticationError(f"MikroTik REST authentication failed: {e}") from e

        self._connected = True
        logger.info("MikroTik REST connected to %s", self.host)

    def disconnect(self) -> None:
        """Close the REST session."""
        if self._session:
            self._session.close()
            self._session = None
        self._connected = False

    def is_connected(self) -> bool:
        """Check if REST session is active."""
        return self._session is not None and self._connected

    def get(self, endpoint: str) -> Any:
        """Send a GET request to the REST API.

        Args:
            endpoint: Path relative to /rest/ (e.g. "interface").

        Returns:
            Parsed JSON response.
        """
        self._ensure_connected()
        assert self._session is not None
        url = f"{self.base_url}/rest/{endpoint}"
        try:
            resp = self._session.get(url)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise APIError(f"GET {endpoint} failed: {e}") from e

        return resp.json()

    def post(self, endpoint: str, data: dict | None = None) -> Any:
        """Send a POST (add/create) request to the REST API.

        Args:
            endpoint: Path relative to /rest/.
            data: JSON body payload.

        Returns:
            Parsed JSON response.
        """
        self._ensure_connected()
        assert self._session is not None
        url = f"{self.base_url}/rest/{endpoint}"
        try:
            resp = self._session.put(url, json=data or {})
            resp.raise_for_status()
        except requests.RequestException as e:
            raise APIError(f"POST {endpoint} failed: {e}") from e

        return resp.json()

    def delete(self, endpoint: str, item_id: str) -> Any:
        """Send a DELETE request to the REST API.

        Args:
            endpoint: Path relative to /rest/.
            item_id: The .id of the item to delete.

        Returns:
            Parsed JSON response.
        """
        self._ensure_connected()
        assert self._session is not None
        url = f"{self.base_url}/rest/{endpoint}/{item_id}"
        try:
            resp = self._session.delete(url)
            resp.raise_for_status()
        except requests.RequestException as e:
            raise APIError(f"DELETE {endpoint}/{item_id} failed: {e}") from e

        return resp.json()

    def _ensure_connected(self) -> None:
        if not self.is_connected():
            raise APIError("Not connected. Call connect() first.")
