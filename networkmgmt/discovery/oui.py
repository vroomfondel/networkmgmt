"""OUI (Organizationally Unique Identifier) database loading and vendor lookup."""

from __future__ import annotations

import subprocess
from pathlib import Path

from loguru import logger

OUI_URL = "https://standards-oui.ieee.org/oui/oui.txt"
OUI_CACHE_PATH = Path("/tmp/oui.txt")

# Shorten verbose OUI vendor names for diagram labels
VENDOR_ABBREV: dict[str, str] = {
    "AVM Audiovisuelles Marketing und Computersysteme GmbH": "AVM",
    "Hangzhou BroadLink Technology Co.,Ltd": "BroadLink",
    "Salcomp (Shenzhen) CO., LTD.": "Salcomp",
    "GL Technologies (Hong Kong) Limited": "GL.iNet",
    "REALTEK SEMICONDUCTOR CORP.": "Realtek",
    "Shenzhen Ogemray Technology Co.,Ltd": "Ogemray",
    "StreamUnlimited Engineering GmbH": "StreamUnlimited",
    "Espressif Inc.": "Espressif",
    "Raspberry Pi Trading Ltd": "Raspberry Pi",
    "Raspberry Pi (Trading) Ltd": "Raspberry Pi",
    "NETGEAR": "Netgear",
    "Ubiquiti Inc": "Ubiquiti",
    "TP-LINK TECHNOLOGIES CO.,LTD.": "TP-Link",
    "Super Micro Computer, Inc.": "Supermicro",
    "Grandstream Networks, Inc.": "Grandstream",
    "Google, Inc.": "Google",
    "Samsung Electronics Co.,Ltd": "Samsung",
    "Philips Lighting BV": "Philips Hue",
    "Nabu Casa, Inc.": "Nabu Casa",
    "Nuki Home Solutions GmbH": "Nuki",
    "HUMAX Co., Ltd.": "HUMAX",
    "Hewlett Packard": "HP",
    "HP Inc.": "HP",
    "Brother Industries, LTD.": "Brother",
    "Intel Corporate": "Intel",
    "NVIDIA": "NVIDIA",
    "Denon/Marantz": "Denon",
    "D&M Holdings Inc.": "Denon/Marantz",
    "iRobot Corporation": "iRobot",
    "Weinzierl Engineering GmbH": "Weinzierl",
    "Fa. GIRA": "GIRA",
    "Slim Devices": "Slim Devices",
    "Cisco Systems, Inc": "Cisco",
    "UGREEN GROUP LIMITED": "Ugreen",
    "Part II Research, Inc.": "Part II",
    "snom technology GmbH": "snom",
    "Climax Technology Co. Ltd": "Climax",
}


def _abbreviate_vendor(vendor: str) -> str:
    """Return abbreviated vendor name if available."""
    return VENDOR_ABBREV.get(vendor, vendor)


def load_oui_db() -> dict[str, str]:
    """Load IEEE OUI database, downloading if not cached."""
    oui_db: dict[str, str] = {}
    oui_path = OUI_CACHE_PATH

    if not oui_path.exists():
        logger.info("Downloading OUI database...")
        try:
            result = subprocess.run(
                ["curl", "-sL", "-o", str(oui_path), OUI_URL],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                logger.warning(f"Failed to download OUI database: {result.stderr}")
                return oui_db
        except (subprocess.TimeoutExpired, FileNotFoundError):
            logger.warning("Could not download OUI database")
            return oui_db

    try:
        with open(oui_path) as f:
            for line in f:
                if "(hex)" in line:
                    parts = line.split("(hex)")
                    if len(parts) == 2:
                        prefix = parts[0].strip().replace("-", ":").upper()
                        vendor = parts[1].strip()
                        oui_db[prefix] = vendor
    except OSError:
        logger.warning("Could not read OUI database")

    return oui_db


def lookup_vendor(mac: str, oui_db: dict[str, str]) -> str:
    """Look up vendor from MAC address using OUI prefix."""
    prefix = mac.upper()[:8]  # XX:XX:XX
    return oui_db.get(prefix, "")
