"""Tests for networkmgmt/discovery/cli.py"""

import pytest

from networkmgmt.discovery.cli import (
    _expand_targets,
    _parse_switches,
    _parse_topology,
    parse_args,
)


class TestParseArgs:
    """Tests for parse_args function."""

    def test_minimal_arguments(self):
        """Test parsing minimal arguments."""
        args = parse_args(["-i", "eth0"])

        assert args.interface == "eth0"

    def test_default_values(self):
        """Test default values are set."""
        args = parse_args(["-i", "eth0"])

        assert args.timeout == 10
        assert args.max_hops == 30
        assert args.format == "mermaid"

    def test_all_flags(self):
        """Test all flags are parsed correctly."""
        args = parse_args(["--nmap", "--trace-local", "--elk", "-v", "-i", "eth0"])

        assert args.nmap is True
        assert args.trace_local is True
        assert args.elk is True
        assert args.verbose is True
        assert args.interface == "eth0"

    def test_output_file_argument(self):
        """Test output file argument."""
        args = parse_args(["-i", "eth0", "-o", "output.md"])

        assert args.output == "output.md"

    def test_format_choices(self):
        """Test format choices."""
        args = parse_args(["-i", "eth0", "--format", "json"])

        assert args.format == "json"

    def test_diagram_style_choices(self):
        """Test diagram style choices."""
        args = parse_args(["-i", "eth0", "-d", "hierarchical"])

        assert args.diagram_style == "hierarchical"


class TestExpandTargets:
    """Tests for _expand_targets function."""

    def test_single_ip(self):
        """Test single IP address."""
        result = _expand_targets("8.8.8.8")

        assert result == ["8.8.8.8"]

    def test_cidr_notation(self):
        """Test CIDR notation expansion."""
        result = _expand_targets("192.168.1.0/30")

        assert result == ["192.168.1.1", "192.168.1.2"]

    def test_range_notation(self):
        """Test range notation expansion."""
        result = _expand_targets("192.168.1.1-3")

        assert result == ["192.168.1.1", "192.168.1.2", "192.168.1.3"]

    def test_mixed_comma_separated(self):
        """Test mixed comma-separated targets."""
        result = _expand_targets("8.8.8.8,1.1.1.1")

        assert result == ["8.8.8.8", "1.1.1.1"]

    def test_empty_parts_skipped(self):
        """Test empty parts are skipped."""
        result = _expand_targets("8.8.8.8,,1.1.1.1")

        assert result == ["8.8.8.8", "1.1.1.1"]

    def test_invalid_cidr_skipped(self):
        """Test invalid CIDR is skipped."""
        result = _expand_targets("invalid/cidr,8.8.8.8")

        assert result == ["8.8.8.8"]

    def test_invalid_range_skipped(self):
        """Test invalid range is skipped."""
        result = _expand_targets("192.168.1.a-b,8.8.8.8")

        assert result == ["8.8.8.8"]


class TestParseSwitches:
    """Tests for _parse_switches function."""

    def test_ip_with_community(self):
        """Test parsing IP with community."""
        result = _parse_switches("10.0.0.1:private")

        assert result == [("10.0.0.1", "private")]

    def test_ip_without_community_defaults_to_public(self):
        """Test IP without community defaults to 'public'."""
        result = _parse_switches("10.0.0.1")

        assert result == [("10.0.0.1", "public")]

    def test_multiple_switches(self):
        """Test parsing multiple switches."""
        result = _parse_switches("10.0.0.1:private,10.0.0.2")

        assert result == [("10.0.0.1", "private"), ("10.0.0.2", "public")]

    def test_empty_parts_skipped(self):
        """Test empty parts are skipped."""
        result = _parse_switches("10.0.0.1,,10.0.0.2")

        assert result == [("10.0.0.1", "public"), ("10.0.0.2", "public")]

    def test_whitespace_trimmed(self):
        """Test whitespace around entry is trimmed (parts after split keep internal ws)."""
        result = _parse_switches(" 10.0.0.1:private , 10.0.0.2 ")

        assert result == [("10.0.0.1", "private"), ("10.0.0.2", "public")]


class TestParseTopology:
    """Tests for _parse_topology function."""

    def test_valid_entry(self):
        """Test parsing valid topology entry."""
        result = _parse_topology("10.0.0.5:10.0.0.1:port1")

        assert result == [("10.0.0.5", "10.0.0.1", "port1")]

    def test_multiple_entries(self):
        """Test parsing multiple entries."""
        result = _parse_topology("a:b:c,d:e:f")

        assert result == [("a", "b", "c"), ("d", "e", "f")]

    def test_malformed_entry_skipped(self):
        """Test malformed entry is skipped."""
        result = _parse_topology("invalid")

        assert result == []

    def test_malformed_entry_skipped_with_valid(self):
        """Test malformed entry skipped but valid entries parsed."""
        result = _parse_topology("invalid,a:b:c")

        assert result == [("a", "b", "c")]

    def test_empty_parts_skipped(self):
        """Test empty parts are skipped."""
        result = _parse_topology("a:b:c,,d:e:f")

        assert result == [("a", "b", "c"), ("d", "e", "f")]

    def test_colon_in_port_name_preserved(self):
        """Test colons in port name are preserved."""
        result = _parse_topology("host:switch:port:with:colons")

        assert result == [("host", "switch", "port:with:colons")]

    def test_whitespace_trimmed(self):
        """Test whitespace around entries is trimmed (parts after colon-split keep internal ws)."""
        result = _parse_topology("a:b:c , d:e:f")

        assert result == [("a", "b", "c"), ("d", "e", "f")]
