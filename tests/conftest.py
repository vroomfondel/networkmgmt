"""Shared fixtures for the networkmgmt test suite."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from networkmgmt.discovery.models import DiscoveredHost
from networkmgmt.snmp_vlan_dump.models import PortVlans, UnitInfo, VlanDumpData

# ── switchctrl transport mocks ────────────────────────────────────────


@pytest.fixture()
def mock_cisco_transport():
    """MagicMock of CiscoCLITransport with configurable return values."""
    transport = MagicMock()
    transport.send_command.return_value = ""
    transport.send_config_commands.return_value = ""
    return transport


@pytest.fixture()
def mock_routeros_transport():
    """MagicMock of RouterOSTransport with send_command."""
    transport = MagicMock()
    transport.send_command.return_value = ""
    return transport


@pytest.fixture()
def mock_mikrotik_rest():
    """MagicMock of MikroTikRESTTransport with get/post/delete."""
    transport = MagicMock()
    transport.get.return_value = []
    transport.post.return_value = {}
    transport.delete.return_value = {}
    return transport


@pytest.fixture()
def mock_qnap_rest():
    """MagicMock of QNAPRESTTransport with get/post."""
    transport = MagicMock()
    transport.get.return_value = {}
    transport.post.return_value = {}
    return transport


# ── discovery fixtures ────────────────────────────────────────────────


@pytest.fixture()
def sample_discovered_host():
    """Factory fixture returning a DiscoveredHost with customizable fields."""

    def _make(**kwargs):
        defaults = {
            "ip": "192.168.1.100",
            "mac": "aa:bb:cc:dd:ee:ff",
            "hostname": "",
            "vendor": "",
            "services": [],
            "is_gateway": False,
            "is_infrastructure": False,
            "category": "",
        }
        defaults.update(kwargs)
        return DiscoveredHost(**defaults)

    return _make


# ── snmp_vlan_dump fixtures ───────────────────────────────────────────


@pytest.fixture()
def sample_vlan_dump_data():
    """Factory fixture returning a populated VlanDumpData."""

    def _make(**overrides):
        defaults = dict(
            sys_descr="Netgear GS308T",
            sys_name="switch01",
            sys_uptime="1 days, 02:30:00",
            unit_info={
                1: UnitInfo(min_idx=1, max_idx=8, max_port=8, ten_g_start=None),
            },
            port_map={
                1: "U1/g1",
                2: "U1/g2",
                3: "U1/g3",
                4: "U1/g4",
                5: "U1/g5",
                6: "U1/g6",
                7: "U1/g7",
                8: "U1/g8",
            },
            unit_ports={1: [1, 2, 3, 4, 5, 6, 7, 8]},
            port_vlans={
                1: PortVlans(tagged=[], untagged=[1]),
                2: PortVlans(tagged=[], untagged=[1]),
                3: PortVlans(tagged=[10, 20], untagged=[1]),
                4: PortVlans(tagged=[], untagged=[10]),
                5: PortVlans(tagged=[], untagged=[10]),
                6: PortVlans(tagged=[], untagged=[20]),
                7: PortVlans(tagged=[], untagged=[20]),
                8: PortVlans(tagged=[10, 20], untagged=[1]),
            },
            vlan_names={1: "default", 10: "servers", 20: "clients"},
            pvid_data={1: 1, 2: 1, 3: 1, 4: 10, 5: 10, 6: 20, 7: 20, 8: 1},
            oper_status={1: 1, 2: 1, 3: 1, 4: 1, 5: 2, 6: 1, 7: 2, 8: 1},
            egress_data={
                1: b"\xff",  # ports 1-8
                10: b"\x3c",  # ports 3-6
                20: b"\xc4",  # ports 3,6,7 (rough approximation)
            },
            untagged_data={
                1: b"\xc3",  # ports 1,2,7,8
                10: b"\x18",  # ports 4,5
                20: b"\x06",  # ports 6,7
            },
            all_phys=[1, 2, 3, 4, 5, 6, 7, 8],
        )
        defaults.update(overrides)
        return VlanDumpData(**defaults)

    return _make
