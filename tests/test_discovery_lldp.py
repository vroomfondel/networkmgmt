"""Tests for networkmgmt/discovery/lldp.py"""

import json
from pathlib import Path
from unittest.mock import Mock, mock_open, patch

import pytest

from networkmgmt.discovery.lldp import LldpDiscovery
from networkmgmt.discovery.models import DiscoveredHost


class TestParseLldpJson:
    """Tests for LldpDiscovery._parse_lldp_json static method."""

    def test_parse_dict_interface_format(self):
        """Test parsing LLDP JSON with dict interface format."""
        host = DiscoveredHost(ip="192.168.1.10", mac="aa:bb:cc:dd:ee:ff")
        data = {
            "lldp": {
                "interface": {
                    "eth0": {
                        "chassis": {
                            "id": {"type": "mac", "value": "11:22:33:44:55:66"},
                            "name": "switch1",
                        },
                        "port": {
                            "id": {"value": "gi1"},
                            "descr": "GigabitEthernet1",
                        },
                    }
                }
            }
        }
        mac_to_host = {"11:22:33:44:55:66": DiscoveredHost(ip="192.168.1.1", mac="11:22:33:44:55:66")}
        name_to_host = {}

        result = LldpDiscovery._parse_lldp_json(host, data, mac_to_host, name_to_host)

        assert len(result) == 1
        assert result[0].host_ip == "192.168.1.10"
        assert result[0].host_mac == "aa:bb:cc:dd:ee:ff"
        assert result[0].switch.switch_ip == "192.168.1.1"
        assert result[0].switch.switch_name == "switch1"
        assert result[0].switch.port_name == "gi1"
        assert result[0].source == "lldp"

    def test_parse_list_interface_format(self):
        """Test parsing LLDP JSON with list interface format."""
        host = DiscoveredHost(ip="192.168.1.10", mac="aa:bb:cc:dd:ee:ff")
        data = {
            "lldp": {
                "interface": [
                    {
                        "eth0": {
                            "chassis": {
                                "id": {"type": "mac", "value": "11:22:33:44:55:66"},
                                "name": "switch1",
                            },
                            "port": {"id": {"value": "gi1"}},
                        }
                    }
                ]
            }
        }
        mac_to_host = {"11:22:33:44:55:66": DiscoveredHost(ip="192.168.1.1", mac="11:22:33:44:55:66")}
        name_to_host = {}

        result = LldpDiscovery._parse_lldp_json(host, data, mac_to_host, name_to_host)

        assert len(result) == 1
        assert result[0].switch.port_name == "gi1"

    def test_missing_chassis_skips_gracefully(self):
        """Test missing chassis data skips gracefully."""
        host = DiscoveredHost(ip="192.168.1.10", mac="aa:bb:cc:dd:ee:ff")
        data = {"lldp": {"interface": {"eth0": {"port": {"id": {"value": "gi1"}}}}}}
        mac_to_host = {}
        name_to_host = {}

        result = LldpDiscovery._parse_lldp_json(host, data, mac_to_host, name_to_host)

        # Should still create entry with empty switch info
        assert len(result) == 1
        assert result[0].switch.switch_ip == ""

    def test_missing_port_skips_gracefully(self):
        """Test missing port data skips gracefully."""
        host = DiscoveredHost(ip="192.168.1.10", mac="aa:bb:cc:dd:ee:ff")
        data = {
            "lldp": {
                "interface": {
                    "eth0": {
                        "chassis": {
                            "id": {"type": "mac", "value": "11:22:33:44:55:66"},
                            "name": "switch1",
                        }
                    }
                }
            }
        }
        mac_to_host = {}
        name_to_host = {}

        result = LldpDiscovery._parse_lldp_json(host, data, mac_to_host, name_to_host)

        assert len(result) == 1
        assert result[0].switch.port_name == ""

    def test_uses_port_descr_when_no_port_id(self):
        """Test using port descr when port id is missing."""
        host = DiscoveredHost(ip="192.168.1.10", mac="aa:bb:cc:dd:ee:ff")
        data = {
            "lldp": {
                "interface": {
                    "eth0": {
                        "chassis": {
                            "id": {"type": "mac", "value": "11:22:33:44:55:66"},
                            "name": "switch1",
                        },
                        "port": {"descr": "GigabitEthernet1"},
                    }
                }
            }
        }
        mac_to_host = {}
        name_to_host = {}

        result = LldpDiscovery._parse_lldp_json(host, data, mac_to_host, name_to_host)

        assert len(result) == 1
        assert result[0].switch.port_name == "GigabitEthernet1"

    def test_matches_switch_by_hostname(self):
        """Test matching switch by hostname when MAC not available."""
        host = DiscoveredHost(ip="192.168.1.10", mac="aa:bb:cc:dd:ee:ff")
        data = {
            "lldp": {
                "interface": {
                    "eth0": {
                        "chassis": {
                            "id": {"type": "local", "value": "local-id"},
                            "name": "switch1.local",
                        },
                        "port": {"id": {"value": "gi1"}},
                    }
                }
            }
        }
        mac_to_host = {}
        name_to_host = {"switch1": DiscoveredHost(ip="192.168.1.1", hostname="switch1.local")}

        result = LldpDiscovery._parse_lldp_json(host, data, mac_to_host, name_to_host)

        assert len(result) == 1
        assert result[0].switch.switch_ip == "192.168.1.1"


class TestBuildL2FromLldp:
    """Tests for LldpDiscovery.build_l2_from_lldp static method."""

    def test_builds_topology_tree(self):
        """Test building topology tree from LLDP entries."""
        from networkmgmt.discovery.models import L2TopologyEntry, SwitchPortMapping

        entries = [
            L2TopologyEntry(
                host_ip="192.168.1.10",
                host_mac="aa:bb:cc:dd:ee:ff",
                switch=SwitchPortMapping(switch_ip="192.168.1.1", switch_name="switch1", port_index=0, port_name="gi1"),
                source="lldp",
            ),
            L2TopologyEntry(
                host_ip="192.168.1.11",
                host_mac="11:22:33:44:55:66",
                switch=SwitchPortMapping(switch_ip="192.168.1.1", switch_name="switch1", port_index=0, port_name="gi2"),
                source="lldp",
            ),
        ]

        l2_entries, topology_tree = LldpDiscovery.build_l2_from_lldp(entries)

        assert l2_entries == entries
        assert topology_tree["192.168.1.10"] == "192.168.1.1"
        assert topology_tree["192.168.1.11"] == "192.168.1.1"

    def test_skips_entries_without_switch_ip(self):
        """Test skipping entries without switch IP."""
        from networkmgmt.discovery.models import L2TopologyEntry, SwitchPortMapping

        entries = [
            L2TopologyEntry(
                host_ip="192.168.1.10",
                host_mac="aa:bb:cc:dd:ee:ff",
                switch=SwitchPortMapping(switch_ip="", switch_name="switch1", port_index=0, port_name="gi1"),
                source="lldp",
            ),
        ]

        l2_entries, topology_tree = LldpDiscovery.build_l2_from_lldp(entries)

        assert len(topology_tree) == 0


class TestLoadAndParse:
    """Tests for LldpDiscovery.load_and_parse static method."""

    @patch("pathlib.Path.glob")
    @patch("pathlib.Path.read_text")
    def test_loads_and_parses_json_files(self, mock_read_text, mock_glob):
        """Test loading and parsing LLDP JSON files."""
        hosts = [
            DiscoveredHost(ip="192.168.1.10", mac="aa:bb:cc:dd:ee:ff"),
            DiscoveredHost(ip="192.168.1.1", mac="11:22:33:44:55:66", hostname="switch1"),
        ]

        mock_file = Mock()
        mock_file.stem = "192.168.1.10"
        mock_file.read_text.return_value = json.dumps(
            {
                "lldp": {
                    "interface": {
                        "eth0": {
                            "chassis": {
                                "id": {"type": "mac", "value": "11:22:33:44:55:66"},
                                "name": "switch1",
                            },
                            "port": {"id": {"value": "gi1"}},
                        }
                    }
                }
            }
        )
        mock_glob.return_value = [mock_file]

        result = LldpDiscovery.load_and_parse(Path("/fake/dir"), hosts)

        assert len(result) == 1
        assert result[0].host_ip == "192.168.1.10"
        assert result[0].switch.switch_ip == "192.168.1.1"

    @patch("pathlib.Path.glob")
    def test_malformed_json_skipped(self, mock_glob):
        """Test malformed JSON files are skipped."""
        hosts = [DiscoveredHost(ip="192.168.1.10", mac="aa:bb:cc:dd:ee:ff")]

        mock_file = Mock()
        mock_file.stem = "192.168.1.10"
        mock_file.read_text.return_value = "invalid json {"
        mock_glob.return_value = [mock_file]

        result = LldpDiscovery.load_and_parse(Path("/fake/dir"), hosts)

        assert len(result) == 0

    @patch("pathlib.Path.glob")
    def test_no_json_files_returns_empty(self, mock_glob):
        """Test no JSON files returns empty list."""
        mock_glob.return_value = []
        hosts = [DiscoveredHost(ip="192.168.1.10")]

        result = LldpDiscovery.load_and_parse(Path("/fake/dir"), hosts)

        assert len(result) == 0
