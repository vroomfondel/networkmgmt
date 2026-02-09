"""Exception hierarchy for switch management."""


class SwitchError(Exception):
    """Base exception for all switch management errors."""


class AuthenticationError(SwitchError):
    """Authentication failed (REST or SSH)."""


class APIError(SwitchError):
    """REST API request failed."""

    def __init__(self, message: str, status_code: int | None = None):
        self.status_code = status_code
        super().__init__(message)


class SSHError(SwitchError):
    """SSH connection or command execution failed."""


class VLANError(SwitchError):
    """VLAN operation failed."""


class PortError(SwitchError):
    """Port configuration failed."""


class LACPError(SwitchError):
    """LACP operation failed."""
