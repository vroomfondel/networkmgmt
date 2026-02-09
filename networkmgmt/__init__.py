"""Multi-Vendor Switch Management Library.

Provides programmatic management of network switches from multiple vendors
(QNAP, MikroTik, Netgear) using a unified API.
"""

__version__ = "0.0.1"

import os
import sys
from typing import Any, Callable, Dict

from loguru import logger as glogger

glogger.disable(__name__)


def _loguru_skiplog_filter(record: dict) -> bool:  # type: ignore[type-arg]
    """Filter function to hide records with ``extra['skiplog']`` set."""
    return not record.get("extra", {}).get("skiplog", False)


def configure_logging(
    loguru_filter: Callable[[Dict[str, Any]], bool] = _loguru_skiplog_filter,
) -> None:
    """Configure a default ``loguru`` sink with a convenient format and filter."""
    os.environ["LOGURU_LEVEL"] = os.getenv("LOGURU_LEVEL", "DEBUG")
    glogger.remove()
    logger_fmt: str = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{module}</cyan>::<cyan>{extra[classname]}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
    )
    glogger.add(sys.stderr, level=os.getenv("LOGURU_LEVEL"), format=logger_fmt, filter=loguru_filter)  # type: ignore[arg-type]
    glogger.configure(extra={"classname": "None", "skiplog": False})


# Import vendors to trigger registration
import networkmgmt.switchctrl.vendors  # noqa: F401, E402
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
    "glogger",
    "configure_logging",
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
