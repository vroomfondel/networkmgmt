"""Vendor registry and factory for switch client creation."""

from __future__ import annotations

from typing import Any, Callable

from networkmgmt.switchctrl.base.client import BaseSwitchClient

_VENDOR_REGISTRY: dict[str, type[BaseSwitchClient]] = {}


def register_vendor(name: str) -> Callable[[type[BaseSwitchClient]], type[BaseSwitchClient]]:
    """Decorator to register a vendor switch client class.

    Usage::

        @register_vendor("qnap")
        class QNAPSwitch(BaseSwitchClient):
            ...
    """

    def decorator(cls: type[BaseSwitchClient]) -> type[BaseSwitchClient]:
        _VENDOR_REGISTRY[name.lower()] = cls
        return cls

    return decorator


def create_switch(vendor: str, host: str, **kwargs: Any) -> BaseSwitchClient:
    """Create a switch client for the given vendor.

    Args:
        vendor: Vendor name (e.g. "qnap", "mikrotik", "netgear").
        host: Switch IP address or hostname.
        **kwargs: Vendor-specific keyword arguments.

    Returns:
        An instance of the vendor-specific switch client.

    Raises:
        ValueError: If the vendor is not registered.
    """
    vendor_lower = vendor.lower()
    if vendor_lower not in _VENDOR_REGISTRY:
        available = ", ".join(sorted(_VENDOR_REGISTRY.keys()))
        raise ValueError(f"Unknown vendor '{vendor}'. Available: {available}")

    cls = _VENDOR_REGISTRY[vendor_lower]
    return cls(host=host, **kwargs)


def list_vendors() -> list[str]:
    """Return a sorted list of registered vendor names."""
    return sorted(_VENDOR_REGISTRY.keys())
