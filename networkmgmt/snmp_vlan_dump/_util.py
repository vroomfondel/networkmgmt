"""Private helper functions for VLAN dump processing."""

from __future__ import annotations

import re
from collections import defaultdict

from networkmgmt.snmp_vlan_dump.models import PortVlans, UnitInfo


def decode_portlist(data: bytes) -> set[int]:
    """Decode a dot1q PortList (raw bytes) into a set of port numbers (1-based)."""
    ports: set[int] = set()
    for byte_idx, byte_val in enumerate(data):
        for bit in range(8):
            if byte_val & (0x80 >> bit):
                ports.add(byte_idx * 8 + bit + 1)
    return ports


def build_port_map(
    if_descrs: dict[int, str],
) -> tuple[dict[int, str], dict[int, UnitInfo]]:
    """Build ifIndex-to-friendly-name mapping from ifDescr strings.

    Parses strings like:
        'unit 1 port 5 Gigabit - Level'        (S3300 stack)
        'unit 3 port 49 10G - Level'            (S3300 10G)
        'Slot: 0 Port: 3 Gigabit - Level'       (GS108T)
        'lag 3' / ' Link Aggregate'
    """
    port_map: dict[int, str] = {}
    unit_info: dict[int, UnitInfo] = {}

    for idx, descr in if_descrs.items():
        m = re.match(
            r"(?:unit|Slot:)\s*(\d+)\s+(?:port|Port:)\s*(\d+)\s+(.*)",
            descr,
            re.IGNORECASE,
        )
        if m:
            unit = int(m.group(1))
            port = int(m.group(2))
            speed = "x" if "10G" in m.group(3) else "g"
            port_map[idx] = f"U{unit}/{speed}{port}"

            if unit not in unit_info:
                unit_info[unit] = UnitInfo(
                    min_idx=idx,
                    max_idx=idx,
                    max_port=port,
                    ten_g_start=None,
                )
            ui = unit_info[unit]
            ui.max_idx = max(ui.max_idx, idx)
            ui.min_idx = min(ui.min_idx, idx)
            if speed == "x" and (ui.ten_g_start is None or port < ui.ten_g_start):
                ui.ten_g_start = port
            ui.max_port = max(ui.max_port, port)

        elif descr.strip().startswith("lag "):
            lag_num = int(descr.split()[1])
            port_map[idx] = f"LAG{lag_num}"

    return port_map, unit_info


def status_str(s: int) -> str:
    """Convert SNMP ifOperStatus code to human-readable string."""
    return {1: "UP", 2: "down", 6: "n/a"}.get(s, f"?({s})")


def unit_summary_str(ui: UnitInfo) -> str:
    """Return e.g. '44x1G + 4x10G' for a UnitInfo."""
    gig_count = (ui.ten_g_start - 1) if ui.ten_g_start else ui.max_port
    ten_g_count = (ui.max_port - ui.ten_g_start + 1) if ui.ten_g_start else 0
    return f"{gig_count}x1G + {ten_g_count}x10G"


def port_is_active(
    port_idx: int,
    port_vlans: dict[int, PortVlans],
    oper_status: dict[int, int],
) -> bool:
    """Return True if a port has VLAN membership or is link-UP."""
    info = port_vlans[port_idx]
    st = status_str(oper_status.get(port_idx, 0))
    return bool(info.tagged or info.untagged or st == "UP")


def format_port_range(port_names: list[str]) -> str:
    """Format port names into compact ranges.

    E.g. ['U1/g1', 'U1/g2', 'U1/g5'] -> 'g1-g2, g5'
    """
    parsed: list[tuple[str, int]] = []
    for name in port_names:
        m = re.match(r"U\d+/([gx])(\d+)", name)
        if m:
            parsed.append((m.group(1), int(m.group(2))))

    by_speed: dict[str, list[int]] = defaultdict(list)
    for speed, num in parsed:
        by_speed[speed].append(num)

    parts: list[str] = []
    for speed in sorted(by_speed):
        nums = sorted(by_speed[speed])
        ranges: list[tuple[int, int]] = []
        start = end = nums[0]
        for n in nums[1:]:
            if n == end + 1:
                end = n
            else:
                ranges.append((start, end))
                start = end = n
        ranges.append((start, end))
        for s, e in ranges:
            if s == e:
                parts.append(f"{speed}{s}")
            else:
                parts.append(f"{speed}{s}-{e}")

    return ", ".join(parts)
