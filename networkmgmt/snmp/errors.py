"""Exceptions of the shared SNMP core and their mapping onto the switchctrl hierarchy."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from networkmgmt.switchctrl.exceptions import SwitchError


class SnmpError(Exception):
    """Base class for all SNMP failures raised by :mod:`networkmgmt.snmp`."""


class SnmpUnavailableError(SnmpError):
    """The optional dependency ``pysnmp`` (>= 7) is not installed."""


class SnmpAuthenticationError(SnmpError):
    """SNMPv3 authentication or privacy failed, or the community/user is unknown."""


class SnmpTimeoutError(SnmpError):
    """The agent did not answer within timeout and retries."""


class SnmpNoSuchObjectError(SnmpError):
    """The agent answered noSuchObject, noSuchInstance or endOfMibView for a GET."""


class SnmpWriteError(SnmpError):
    """A SET was rejected by the agent (for example ``commitFailed``)."""

    def __init__(self, message: str, oid: str = "", status: str = ""):
        """Create the error.

        Args:
            message: Human readable description.
            oid: OID of the varbind the agent complained about.
            status: Error status reported by the agent, for example ``commitFailed``.
        """
        super().__init__(message)
        self.oid = oid
        self.status = status


class SnmpSettleTimeoutError(SnmpError):
    """A read-back poll did not reach the expected value in time."""


def to_switch_error(exc: SnmpError, fallback: type[SwitchError] | None = None) -> SwitchError:
    """Map an :class:`SnmpError` onto the switchctrl exception hierarchy.

    Args:
        exc: The SNMP error.
        fallback: Exception class for everything that is not an authentication failure.
            Defaults to :class:`~networkmgmt.switchctrl.exceptions.APIError`.

    Returns:
        A new exception instance carrying ``str(exc)``; the caller raises it ``from exc``.
    """
    from networkmgmt.switchctrl.exceptions import APIError, AuthenticationError

    if isinstance(exc, SnmpAuthenticationError):
        return AuthenticationError(str(exc))
    cls = fallback or APIError
    return cls(str(exc))
