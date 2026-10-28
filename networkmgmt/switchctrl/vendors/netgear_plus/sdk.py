"""Thin typed adapter around the optional ``netgear-tool`` web-scraping SDK."""

import warnings
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Protocol

from loguru import logger

from networkmgmt.switchctrl.exceptions import APIError, AuthenticationError, SwitchError

_make_switch: Callable[..., "RawSwitch"] | None
try:
    from netgear_tool import make_switch as _imported_make_switch

    _make_switch = _imported_make_switch
    HAS_NETGEAR_TOOL = True
except ImportError:
    _make_switch = None
    HAS_NETGEAR_TOOL = False

UNTAGGED = "1"
TAGGED = "2"
NOT_MEMBER = "3"

_AUTH_MARKERS = ("login failed", "password is invalid", "invalid password")


class RawSystemConfig(Protocol):
    """Subset of ``netgear_tool.SwitchConfig`` used here."""

    model: str
    name: str
    serial: str
    mac: str
    firmware: str


class RawPortInfo(Protocol):
    """Subset of ``netgear_tool.PortInfo`` used here."""

    port: int
    description: str
    enabled: bool
    speed_act: str


class RawPortStats(Protocol):
    """Subset of ``netgear_tool.PortStats`` used here."""

    port: int
    bytes_rx: int
    bytes_tx: int
    crc_errors: int


class RawSwitch(Protocol):
    """Subset of ``netgear_tool.Switch`` used here."""

    _port_count: int

    def logout(self) -> None:
        """Log out and free the single session slot."""

    def get_switch_config(self) -> RawSystemConfig:
        """Return model, name, serial, MAC and firmware."""

    def get_port_settings(self) -> list[RawPortInfo]:
        """Return per-port settings."""

    def get_port_stats(self) -> list[RawPortStats]:
        """Return per-port counters."""

    def set_port(
        self, port: int, speed: int | None = None, fc_enabled: bool | None = None, description: str | None = None
    ) -> None:
        """Change port speed (2 disables, 1 is auto), flow control or description."""

    def get_dot1q_enabled(self) -> bool:
        """Return whether Advanced 802.1Q VLAN mode is active."""

    def get_vlan_ids(self) -> list[int]:
        """Return all configured 802.1Q VLAN IDs."""

    def get_vlan_membership(self, vid: int) -> str:
        """Return one character per port: 1 untagged, 2 tagged, 3 not a member."""

    def set_vlan_membership(self, vid: int, membership: str) -> None:
        """Write the membership string of a VLAN."""

    def add_vlan(self, vid: int) -> None:
        """Create a VLAN."""

    def delete_vlan(self, vid: int) -> None:
        """Delete a VLAN."""

    def get_port_pvids(self) -> dict[int, int]:
        """Return the PVID per port."""

    def set_port_pvid(self, port: int, pvid: int) -> None:
        """Set the PVID of a port."""


def translate_error(exc: Exception) -> SwitchError:
    """Map an exception raised by the SDK or its HTTP layer to the switchctrl hierarchy.

    Args:
        exc: Exception raised by ``netgear_tool``.

    Returns:
        ``AuthenticationError`` for rejected logins, ``APIError`` otherwise.
    """
    if isinstance(exc, SwitchError):
        return exc
    message = str(exc)
    if any(marker in message.lower() for marker in _AUTH_MARKERS):
        return AuthenticationError(message)
    return APIError(message)


@contextmanager
def translated() -> Iterator[None]:
    """Re-raise SDK failures as ``SwitchError`` subclasses."""
    try:
        yield
    except SwitchError, NotImplementedError:
        raise
    except Exception as exc:
        raise translate_error(exc) from exc


class NetgearPlusSession:
    """Logged-in switch session with errors translated to ``SwitchError``."""

    def __init__(self, raw: RawSwitch) -> None:
        self._raw = raw

    @property
    def port_count(self) -> int:
        """Number of front-panel ports."""
        return self._raw._port_count

    def logout(self) -> None:
        """Log out, ignoring failures."""
        try:
            self._raw.logout()
        except Exception as exc:
            logger.debug("Netgear Plus logout failed: {}", exc)

    def system_config(self) -> RawSystemConfig:
        """Read model, name, serial, MAC and firmware."""
        with translated():
            return self._raw.get_switch_config()

    def port_settings(self) -> list[RawPortInfo]:
        """Read per-port settings."""
        with translated():
            return self._raw.get_port_settings()

    def port_stats(self) -> list[RawPortStats]:
        """Read per-port counters."""
        with translated():
            return self._raw.get_port_stats()

    def set_port(self, port: int, speed: int | None = None, description: str | None = None) -> None:
        """Change port speed or description."""
        with translated():
            self._raw.set_port(port, speed=speed, description=description)

    def dot1q_enabled(self) -> bool:
        """Return whether 802.1Q mode is active."""
        with translated():
            return self._raw.get_dot1q_enabled()

    def read_vlans(self) -> tuple[dict[int, str], dict[int, int]]:
        """Read membership strings per VLAN and PVID per port."""
        with translated():
            memberships = {vid: self._raw.get_vlan_membership(vid) for vid in self._raw.get_vlan_ids()}
            return memberships, self._raw.get_port_pvids()

    def add_vlan(self, vid: int) -> None:
        """Create a VLAN."""
        with translated():
            self._raw.add_vlan(vid)

    def delete_vlan(self, vid: int) -> None:
        """Delete a VLAN."""
        with translated():
            self._raw.delete_vlan(vid)

    def set_vlan_membership(self, vid: int, membership: str) -> None:
        """Write a VLAN membership string."""
        with translated():
            self._raw.set_vlan_membership(vid, membership)

    def set_port_pvid(self, port: int, pvid: int) -> None:
        """Set a port PVID."""
        with translated():
            self._raw.set_port_pvid(port, pvid)


def open_session(host: str, password: str, timeout: float) -> NetgearPlusSession:
    """Log in to a switch via ``netgear-tool``.

    Args:
        host: Switch IP or hostname.
        password: Web UI password.
        timeout: HTTP timeout in seconds.

    Returns:
        A logged-in session.

    Raises:
        SwitchError: ``netgear-tool`` is not installed.
        AuthenticationError: The switch rejected the password.
    """
    if not HAS_NETGEAR_TOOL or _make_switch is None:
        raise SwitchError(
            "The netgear_plus vendor requires the optional 'netgear-tool' package (pip install netgear-tool)"
        )
    with translated(), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return NetgearPlusSession(_make_switch(host, password=password, timeout=timeout))
