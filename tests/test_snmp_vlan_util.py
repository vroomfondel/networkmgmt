"""Tests for networkmgmt.snmp_vlan_dump._util pure functions."""

from __future__ import annotations

import pytest

from networkmgmt.snmp_vlan_dump._util import (
    build_port_map,
    decode_portlist,
    format_port_range,
    port_is_active,
    status_str,
    unit_summary_str,
)
from networkmgmt.snmp_vlan_dump.models import PortVlans, UnitInfo


class TestDecodePortlist:
    """Test decode_portlist function."""

    def test_single_port_first_bit(self):
        """First bit of first byte represents port 1."""
        assert decode_portlist(b"\x80") == {1}

    def test_single_port_second_bit(self):
        """Second bit of first byte represents port 2."""
        assert decode_portlist(b"\x40") == {2}

    def test_multiple_ports_same_byte(self):
        """First two bits represent ports 1 and 2."""
        assert decode_portlist(b"\xc0") == {1, 2}

    def test_empty_byte(self):
        """Empty byte returns empty set."""
        assert decode_portlist(b"\x00") == set()

    def test_ports_across_bytes(self):
        """Ports span multiple bytes."""
        assert decode_portlist(b"\x80\x01") == {1, 16}

    def test_empty_bytes(self):
        """Empty bytes return empty set."""
        assert decode_portlist(b"") == set()

    def test_complex_pattern(self):
        """Test more complex bit pattern."""
        # b'\xff' = all 8 bits set = ports 1-8
        assert decode_portlist(b"\xff") == {1, 2, 3, 4, 5, 6, 7, 8}

    def test_multiple_bytes_complex(self):
        """Test multiple bytes with various patterns."""
        # First byte: 0xaa = 10101010 = ports 1,3,5,7
        # Second byte: 0x55 = 01010101 = ports 10,12,14,16
        assert decode_portlist(b"\xaa\x55") == {1, 3, 5, 7, 10, 12, 14, 16}


class TestBuildPortMap:
    """Test build_port_map function."""

    def test_unit_port_format(self):
        """Parse 'unit X port Y' format."""
        if_descrs = {1: "unit 1 port 5 Gigabit - Level"}
        port_map, unit_info = build_port_map(if_descrs)
        assert port_map == {1: "U1/g5"}
        assert 1 in unit_info
        assert unit_info[1].max_port == 5

    def test_slot_port_format(self):
        """Parse 'Slot: X Port: Y' format (GS108T)."""
        if_descrs = {2: "Slot: 0 Port: 3 Gigabit - Level"}
        port_map, unit_info = build_port_map(if_descrs)
        assert port_map == {2: "U0/g3"}
        assert 0 in unit_info

    def test_lag_format(self):
        """Parse 'lag X' format."""
        if_descrs = {3: "lag 3"}
        port_map, unit_info = build_port_map(if_descrs)
        assert port_map == {3: "LAG3"}
        assert unit_info == {}

    def test_10g_detection(self):
        """Detect 10G ports and set ten_g_start."""
        if_descrs = {
            1: "unit 1 port 1 Gigabit - Level",
            2: "unit 1 port 49 10G - Level",
        }
        port_map, unit_info = build_port_map(if_descrs)
        assert port_map[2] == "U1/x49"
        assert unit_info[1].ten_g_start == 49

    def test_unit_info_ranges(self):
        """Track min/max ifIndex and max_port."""
        if_descrs = {
            10: "unit 1 port 1 Gigabit - Level",
            20: "unit 1 port 48 Gigabit - Level",
        }
        port_map, unit_info = build_port_map(if_descrs)
        assert unit_info[1].min_idx == 10
        assert unit_info[1].max_idx == 20
        assert unit_info[1].max_port == 48

    def test_mixed_units(self):
        """Handle multiple stacking units."""
        if_descrs = {
            1: "unit 1 port 1 Gigabit - Level",
            2: "unit 2 port 1 Gigabit - Level",
        }
        port_map, unit_info = build_port_map(if_descrs)
        assert 1 in unit_info
        assert 2 in unit_info


class TestStatusStr:
    """Test status_str function."""

    def test_status_up(self):
        """Status 1 is UP."""
        assert status_str(1) == "UP"

    def test_status_down(self):
        """Status 2 is down."""
        assert status_str(2) == "down"

    def test_status_not_applicable(self):
        """Status 6 is n/a."""
        assert status_str(6) == "n/a"

    def test_status_unknown(self):
        """Unknown status code returns formatted code."""
        assert status_str(99) == "?(99)"


class TestUnitSummaryStr:
    """Test unit_summary_str function."""

    def test_with_10g_ports(self):
        """Unit with both 1G and 10G ports."""
        ui = UnitInfo(min_idx=1, max_idx=48, max_port=48, ten_g_start=45)
        assert unit_summary_str(ui) == "44x1G + 4x10G"

    def test_without_10g_ports(self):
        """Unit with only 1G ports."""
        ui = UnitInfo(min_idx=1, max_idx=48, max_port=48, ten_g_start=None)
        assert unit_summary_str(ui) == "48x1G + 0x10G"

    def test_8_port_switch(self):
        """8-port switch without 10G."""
        ui = UnitInfo(min_idx=1, max_idx=8, max_port=8, ten_g_start=None)
        assert unit_summary_str(ui) == "8x1G + 0x10G"


class TestPortIsActive:
    """Test port_is_active function."""

    def test_port_with_vlans(self):
        """Port with VLANs is active."""
        port_vlans = {1: PortVlans(untagged=[10])}
        oper_status = {1: 2}  # down
        assert port_is_active(1, port_vlans, oper_status) is True

    def test_port_with_link_up(self):
        """Port with link UP is active."""
        port_vlans = {1: PortVlans()}
        oper_status = {1: 1}  # UP
        assert port_is_active(1, port_vlans, oper_status) is True

    def test_port_inactive(self):
        """Port with no VLANs and link down is inactive."""
        port_vlans = {1: PortVlans()}
        oper_status = {1: 2}  # down
        assert port_is_active(1, port_vlans, oper_status) is False

    def test_port_with_tagged_vlans(self):
        """Port with tagged VLANs is active."""
        port_vlans = {1: PortVlans(tagged=[10, 20])}
        oper_status = {1: 2}  # down
        assert port_is_active(1, port_vlans, oper_status) is True

    def test_port_missing_status(self):
        """Port with missing oper_status defaults to inactive if no VLANs."""
        port_vlans = {1: PortVlans()}
        oper_status = {}
        assert port_is_active(1, port_vlans, oper_status) is False


class TestFormatPortRange:
    """Test format_port_range function."""

    def test_consecutive_ports(self):
        """Consecutive ports collapse into range."""
        assert format_port_range(["U1/g1", "U1/g2", "U1/g3"]) == "g1-3"

    def test_non_consecutive_ports(self):
        """Non-consecutive ports listed separately."""
        assert format_port_range(["U1/g1", "U1/g2", "U1/g5"]) == "g1-2, g5"

    def test_mixed_speeds(self):
        """Mixed 1G and 10G ports."""
        assert format_port_range(["U1/g1", "U1/x49"]) == "g1, x49"

    def test_single_port(self):
        """Single port returns just the port."""
        assert format_port_range(["U1/g1"]) == "g1"

    def test_multiple_ranges(self):
        """Multiple ranges and singles."""
        ports = ["U1/g1", "U1/g2", "U1/g3", "U1/g5", "U1/g6", "U1/g10"]
        assert format_port_range(ports) == "g1-3, g5-6, g10"

    def test_10g_range(self):
        """10G ports in range."""
        assert format_port_range(["U1/x49", "U1/x50", "U1/x51"]) == "x49-51"

    def test_mixed_units_ignored_in_range(self):
        """Different units but same speed type."""
        # This tests the current implementation which groups by speed only
        ports = ["U1/g1", "U1/g2", "U2/g1"]
        result = format_port_range(ports)
        # Should have g1-g2 (from both units as they're sorted together)
        assert "g1" in result
