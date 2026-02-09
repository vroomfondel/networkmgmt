"""Tests for networkmgmt/discovery/scanner.py parsing methods."""

import socket
from unittest.mock import Mock, patch

import pytest

from networkmgmt.discovery.models import DiscoveredHost, TracerouteHop, TraceroutePath
from networkmgmt.discovery.scanner import NetworkTopologyScanner


@pytest.fixture
def scanner():
    """Fixture providing a NetworkTopologyScanner instance."""
    return NetworkTopologyScanner()


class TestCleanupTrace:
    """Tests for NetworkTopologyScanner._cleanup_trace static method."""

    def test_completed_path_returned_unchanged(self):
        """Test completed path is returned unchanged."""
        path = TraceroutePath(
            target="8.8.8.8",
            hops=[
                TracerouteHop(hop_number=1, ip="192.168.1.1"),
                TracerouteHop(hop_number=2, ip="8.8.8.8"),
            ],
            completed=True,
        )

        result = NetworkTopologyScanner._cleanup_trace(path)

        assert result.completed is True
        assert len(result.hops) == 2

    def test_all_timeouts_returns_single_unreachable(self):
        """Test all timeouts returns single UNREACHABLE hop."""
        path = TraceroutePath(
            target="10.0.0.5",
            hops=[
                TracerouteHop(hop_number=1, is_timeout=True),
                TracerouteHop(hop_number=2, is_timeout=True),
                TracerouteHop(hop_number=3, is_timeout=True),
            ],
            completed=False,
        )

        result = NetworkTopologyScanner._cleanup_trace(path)

        assert len(result.hops) == 1
        assert result.hops[0].ip == "10.0.0.5"
        assert result.hops[0].hostname == "UNREACHABLE"
        assert result.hops[0].is_timeout is True

    def test_partial_path_with_trailing_timeouts(self):
        """Test partial path with trailing timeouts adds single UNREACHABLE."""
        path = TraceroutePath(
            target="8.8.8.8",
            hops=[
                TracerouteHop(hop_number=1, ip="192.168.1.1"),
                TracerouteHop(hop_number=2, ip="10.0.0.1"),
                TracerouteHop(hop_number=3, is_timeout=True),
                TracerouteHop(hop_number=4, is_timeout=True),
            ],
            completed=False,
        )

        result = NetworkTopologyScanner._cleanup_trace(path)

        assert len(result.hops) == 3
        assert result.hops[0].ip == "192.168.1.1"
        assert result.hops[1].ip == "10.0.0.1"
        assert result.hops[2].ip == "8.8.8.8"
        assert result.hops[2].hostname == "UNREACHABLE"
        assert result.hops[2].is_timeout is True


class TestParseTraceroute:
    """Tests for NetworkTopologyScanner._parse_traceroute method."""

    def test_full_path_parsing(self, scanner):
        """Test parsing a full traceroute path."""
        output = """traceroute to 8.8.8.8 (8.8.8.8), 30 hops max
 1  192.168.1.1  1.234 ms  1.456 ms  1.789 ms
 2  10.0.0.1  5.123 ms  5.234 ms  5.345 ms
 3  8.8.8.8  20.123 ms  20.234 ms  20.345 ms
"""

        with (
            patch.object(scanner, "_reverse_dns", return_value=""),
            patch("socket.gethostbyname", return_value="8.8.8.8"),
        ):
            result = scanner._parse_traceroute("8.8.8.8", output)

        assert result.target == "8.8.8.8"
        assert result.completed is True
        assert len(result.hops) == 3
        assert result.hops[0].ip == "192.168.1.1"
        assert result.hops[0].rtt_ms == 1.234
        assert result.hops[2].ip == "8.8.8.8"

    def test_timeout_line_parsing(self, scanner):
        """Test parsing timeout lines."""
        output = """ 1  192.168.1.1  1.234 ms  1.456 ms  1.789 ms
 2  * * *
 3  8.8.8.8  20.123 ms  20.234 ms  20.345 ms
"""

        with (
            patch.object(scanner, "_reverse_dns", return_value=""),
            patch("socket.gethostbyname", return_value="8.8.8.8"),
        ):
            result = scanner._parse_traceroute("8.8.8.8", output)

        assert len(result.hops) == 3
        assert result.hops[1].is_timeout is True
        assert result.hops[1].hop_number == 2

    def test_completed_when_final_hop_matches_target(self, scanner):
        """Test completed flag set when final hop matches target."""
        output = """ 1  192.168.1.1  1.234 ms  1.456 ms  1.789 ms
 2  8.8.8.8  20.123 ms  20.234 ms  20.345 ms
"""

        with (
            patch.object(scanner, "_reverse_dns", return_value=""),
            patch("socket.gethostbyname", return_value="8.8.8.8"),
        ):
            result = scanner._parse_traceroute("8.8.8.8", output)

        assert result.completed is True


class TestParseTracepath:
    """Tests for NetworkTopologyScanner._parse_tracepath method."""

    def test_multiple_hops_parsing(self, scanner):
        """Test parsing multiple hops."""
        output = """ 1:  192.168.1.1    1.234ms
 2:  10.0.0.1    5.123ms
 3:  8.8.8.8    20.123ms reached
"""

        with (
            patch.object(scanner, "_reverse_dns", return_value=""),
            patch("socket.gethostbyname", return_value="8.8.8.8"),
        ):
            result = scanner._parse_tracepath("8.8.8.8", output)

        assert result.target == "8.8.8.8"
        assert len(result.hops) == 3
        assert result.hops[0].ip == "192.168.1.1"
        assert result.hops[0].rtt_ms == 1.234
        assert result.hops[1].ip == "10.0.0.1"
        assert result.hops[2].ip == "8.8.8.8"
        assert result.completed is True

    def test_no_reply_line(self, scanner):
        """Test parsing 'no reply' timeout lines."""
        output = """ 1:  192.168.1.1    1.234ms
 2:  no reply
 3:  8.8.8.8    20.123ms
"""

        with (
            patch.object(scanner, "_reverse_dns", return_value=""),
            patch("socket.gethostbyname", return_value="8.8.8.8"),
        ):
            result = scanner._parse_tracepath("8.8.8.8", output)

        assert len(result.hops) == 3
        assert result.hops[1].is_timeout is True

    def test_skips_localhost_lines(self, scanner):
        """Test skipping [LOCALHOST] lines."""
        output = """ 1?: [LOCALHOST]     pmtu 1500
 1:  192.168.1.1    1.234ms
"""

        with (
            patch.object(scanner, "_reverse_dns", return_value=""),
            patch("socket.gethostbyname", return_value="192.168.1.1"),
        ):
            result = scanner._parse_tracepath("192.168.1.1", output)

        assert len(result.hops) == 1
        assert result.hops[0].ip == "192.168.1.1"

    def test_skips_resume_lines(self, scanner):
        """Test skipping Resume: lines."""
        output = """ 1:  192.168.1.1    1.234ms
     Resume: pmtu 1500
 2:  10.0.0.1    5.123ms
"""

        with (
            patch.object(scanner, "_reverse_dns", return_value=""),
            patch("socket.gethostbyname", return_value="10.0.0.1"),
        ):
            result = scanner._parse_tracepath("10.0.0.1", output)

        assert len(result.hops) == 2

    def test_skips_too_many_hops_lines(self, scanner):
        """Test skipping 'Too many hops' lines."""
        output = """ 1:  192.168.1.1    1.234ms
 2:  Too many hops: pmtu 1500
"""

        with patch.object(scanner, "_reverse_dns", return_value=""):
            result = scanner._parse_tracepath("8.8.8.8", output)

        assert len(result.hops) == 1

    def test_deduplicates_hop_numbers(self, scanner):
        """Test deduplication of hop numbers."""
        output = """ 1:  192.168.1.1    1.234ms
 1:  192.168.1.1    1.456ms
 2:  10.0.0.1    5.123ms
"""

        with (
            patch.object(scanner, "_reverse_dns", return_value=""),
            patch("socket.gethostbyname", return_value="10.0.0.1"),
        ):
            result = scanner._parse_tracepath("10.0.0.1", output)

        assert len(result.hops) == 2


class TestBuildTopologyTree:
    """Tests for NetworkTopologyScanner.build_topology_tree static method."""

    def test_one_hop_paths_parent_is_gateway(self, scanner):
        """Test 1-hop paths have gateway as parent."""
        hosts = [
            DiscoveredHost(ip="192.168.1.1", is_gateway=True),
            DiscoveredHost(ip="192.168.1.10"),
            DiscoveredHost(ip="192.168.1.11"),
        ]
        trace_paths = [
            TraceroutePath(
                target="192.168.1.10",
                hops=[TracerouteHop(hop_number=1, ip="192.168.1.1")],
            ),
            TraceroutePath(
                target="192.168.1.11",
                hops=[TracerouteHop(hop_number=1, ip="192.168.1.1")],
            ),
        ]

        tree = scanner.build_topology_tree(hosts, trace_paths, "192.168.1.1")

        assert tree["192.168.1.10"] == "192.168.1.1"
        assert tree["192.168.1.11"] == "192.168.1.1"

    def test_multi_hop_parent_is_penultimate(self, scanner):
        """Test multi-hop paths have penultimate hop as parent."""
        hosts = [
            DiscoveredHost(ip="192.168.1.1", is_gateway=True),
            DiscoveredHost(ip="192.168.1.10"),
            DiscoveredHost(ip="192.168.1.20"),
        ]
        trace_paths = [
            TraceroutePath(
                target="192.168.1.20",
                hops=[
                    TracerouteHop(hop_number=1, ip="192.168.1.1"),
                    TracerouteHop(hop_number=2, ip="192.168.1.10"),
                    TracerouteHop(hop_number=3, ip="192.168.1.20"),
                ],
            ),
        ]

        tree = scanner.build_topology_tree(hosts, trace_paths, "192.168.1.1")

        assert tree["192.168.1.20"] == "192.168.1.10"

    def test_intermediate_hops_marked_infrastructure(self, scanner):
        """Test intermediate hops are marked as infrastructure."""
        hosts = [
            DiscoveredHost(ip="192.168.1.1", is_gateway=True),
            DiscoveredHost(ip="192.168.1.10"),
            DiscoveredHost(ip="192.168.1.20"),
        ]
        trace_paths = [
            TraceroutePath(
                target="192.168.1.20",
                hops=[
                    TracerouteHop(hop_number=1, ip="192.168.1.1"),
                    TracerouteHop(hop_number=2, ip="192.168.1.10"),
                    TracerouteHop(hop_number=3, ip="192.168.1.20"),
                ],
            ),
        ]

        scanner.build_topology_tree(hosts, trace_paths, "192.168.1.1")

        # Gateway not marked as infrastructure
        assert hosts[0].is_infrastructure is False
        # Intermediate hop marked as infrastructure
        assert hosts[1].is_infrastructure is True
        # Final hop not marked as infrastructure
        assert hosts[2].is_infrastructure is False


class TestReverseDns:
    """Tests for NetworkTopologyScanner._reverse_dns method."""

    @patch("socket.gethostbyaddr")
    def test_dns_success(self, mock_gethostbyaddr, scanner):
        """Test successful DNS lookup."""
        mock_gethostbyaddr.return_value = ("router.local", [], ["192.168.1.1"])

        result = scanner._reverse_dns("192.168.1.1")

        assert result == "router.local"

    @patch("socket.gethostbyaddr")
    def test_dns_failure(self, mock_gethostbyaddr, scanner):
        """Test DNS lookup failure returns empty string."""
        mock_gethostbyaddr.side_effect = socket.herror()

        result = scanner._reverse_dns("192.168.1.1")

        assert result == ""


class TestDetectTraceCmd:
    """Tests for NetworkTopologyScanner._detect_trace_cmd method."""

    @patch("shutil.which")
    def test_detects_tracepath(self, mock_which, scanner):
        """Test detection of tracepath command."""
        mock_which.side_effect = lambda cmd: "/usr/bin/tracepath" if cmd == "tracepath" else None

        result = scanner._detect_trace_cmd()

        assert result == "tracepath"

    @patch("shutil.which")
    def test_detects_traceroute(self, mock_which, scanner):
        """Test detection of traceroute command."""
        mock_which.side_effect = lambda cmd: "/usr/bin/traceroute" if cmd == "traceroute" else None

        result = scanner._detect_trace_cmd()

        assert result == "traceroute"

    @patch("shutil.which")
    def test_no_trace_command_available(self, mock_which, scanner):
        """Test no trace command available returns empty string."""
        mock_which.return_value = None

        result = scanner._detect_trace_cmd()

        assert result == ""
