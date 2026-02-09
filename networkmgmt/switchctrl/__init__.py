"""Switch control — multi-vendor switch management via REST / SSH / CLI."""

import networkmgmt.switchctrl.vendors  # noqa: F401  # trigger vendor registration

from networkmgmt.switchctrl.base.client import BaseSwitchClient
from networkmgmt.switchctrl.base.transport import BaseTransport
from networkmgmt.switchctrl.exceptions import (
    APIError,
    AuthenticationError,
    LACPError,
    PortError,
    SSHError,
    SwitchError,
    VLANError,
)
from networkmgmt.switchctrl.factory import create_switch, list_vendors

__all__ = [
    "create_switch",
    "list_vendors",
    "BaseSwitchClient",
    "BaseTransport",
    "SwitchError",
    "AuthenticationError",
    "APIError",
    "SSHError",
    "VLANError",
    "PortError",
    "LACPError",
]
