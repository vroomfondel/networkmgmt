"""Port statistics data model."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PortStatistics:
    """Traffic statistics for a single port."""

    port: str
    tx_bytes: int = 0
    rx_bytes: int = 0
    tx_packets: int = 0
    rx_packets: int = 0
    tx_errors: int = 0
    rx_errors: int = 0
    link_up: bool = False
    speed: str = ""
