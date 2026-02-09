"""Utility functions for QNAP switch management."""

from __future__ import annotations

import hashlib


def generate_enable_password(serial: str) -> str:
    """Generate the enable-mode password from the switch serial number.

    The QNAP QSW hidden CLI uses a SHA-512 based password derived
    from the device serial number.

    Args:
        serial: The switch serial number (e.g. from system board info).

    Returns:
        The enable password string (last 8 characters of the hex digest).
    """
    digest = hashlib.sha512(serial.encode()).hexdigest()
    return digest[-8:]
