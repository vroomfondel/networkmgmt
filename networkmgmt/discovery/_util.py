"""Shared helper functions for network topology discovery."""

from __future__ import annotations

import ipaddress
import re
import subprocess

from loguru import logger

# Hostname suffixes to strip for shorter labels
_HOSTNAME_SUFFIXES = (".fritz.box", ".local", ".lan")


def _strip_hostname_suffix(hostname: str) -> str:
    """Strip common DNS suffixes from hostnames for shorter labels."""
    lower = hostname.lower()
    for suffix in _HOSTNAME_SUFFIXES:
        if lower.endswith(suffix):
            return hostname[: -len(suffix)]
    return hostname


def _run_cmd(cmd: list[str], timeout: int = 30) -> str:
    """Run a subprocess command and return stdout."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.debug(f"Command {cmd[0]} failed: {e}")
        return ""


def _validate_interface_name(name: str) -> bool:
    """Validate interface name to prevent injection."""
    return bool(re.match(r"^[a-zA-Z0-9._-]+$", name))


def _validate_ip(ip: str) -> bool:
    """Validate IP address string."""
    try:
        ipaddress.ip_address(ip)
        return True
    except ValueError:
        return False
