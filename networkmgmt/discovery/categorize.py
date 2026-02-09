"""Device category classification rules."""

from __future__ import annotations

from networkmgmt.discovery.models import DeviceCategory, DiscoveredHost

# Match patterns: (category, vendor_substrings, hostname_patterns)
_CATEGORY_RULES: list[tuple[DeviceCategory, list[str], list[str]]] = [
    (
        DeviceCategory.INFRASTRUCTURE,
        [
            "NETGEAR",
            "Netgear",
            "Ubiquiti",
            "TP-LINK",
            "TP-Link",
            "Cisco",
        ],
        [
            "switch",
            "gs108",
            "gs305",
            "efh24",
            "gs116",
        ],
    ),
    (
        DeviceCategory.SERVER,
        [
            "Raspberry Pi",
            "Super Micro",
            "Supermicro",
            "UGREEN",
            "Ugreen",
        ],
        [
            "node",
            "nas",
            "supermicro",
            "rpi",
            "raspi",
            "thinkcentre",
            "acerrevo",
            "revo",
            "peekabooboo",
        ],
    ),
    (
        DeviceCategory.IOT,
        [
            "Espressif",
            "BroadLink",
            "Hangzhou BroadLink",
            "Nuki",
            "Climax",
            "Ogemray",
            "Shenzhen Ogemray",
        ],
        [
            "tasmota",
            "nous",
            "shelly",
            "plug",
            "sensor",
        ],
    ),
    (
        DeviceCategory.PHONE,
        [
            "Grandstream",
            "snom",
        ],
        [
            "wp820",
            "gxp",
            "phone",
            "sip",
        ],
    ),
    (
        DeviceCategory.MEDIA,
        [
            "Google, Inc.",
            "Google",
            "NVIDIA",
            "Samsung",
            "HUMAX",
            "Slim Devices",
            "Denon",
            "D&M Holdings",
            "StreamUnlimited",
        ],
        [
            "chromecast",
            "shield",
            "tv",
            "denon",
            "humax",
            "squeezebox",
        ],
    ),
    (
        DeviceCategory.HOME_AUTOMATION,
        [
            "Philips Lighting",
            "Nabu Casa",
            "Weinzierl",
            "Fa. GIRA",
            "GIRA",
            "iRobot",
        ],
        [
            "hue",
            "homeassistant",
            "home-assistant",
            "knx",
            "roomba",
        ],
    ),
    (
        DeviceCategory.COMPUTER,
        [
            "HP Inc.",
            "Hewlett Packard",
            "HP",
            "Brother",
            "Intel",
            "Part II Research",
            "Realtek",
            "REALTEK",
        ],
        [
            "printer",
            "brother",
            "laptop",
        ],
    ),
]


def _categorize_host(host: DiscoveredHost) -> DeviceCategory:
    """Classify a host into a device category by vendor and hostname patterns."""
    vendor_lower = host.vendor.lower()
    hostname_lower = host.hostname.lower()

    for category, vendor_patterns, hostname_patterns in _CATEGORY_RULES:
        for vp in vendor_patterns:
            if vp.lower() in vendor_lower:
                return category
        for hp in hostname_patterns:
            if hp in hostname_lower:
                return category

    return DeviceCategory.OTHER
