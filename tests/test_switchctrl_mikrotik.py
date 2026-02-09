"""Tests for MikroTik RouterOS switch management."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from networkmgmt.switchctrl.exceptions import LACPError, VLANError
from networkmgmt.switchctrl.models.port import PortStatus
from networkmgmt.switchctrl.models.system import LACPInfo, SystemInfo
from networkmgmt.switchctrl.models.vlan import VLAN
from networkmgmt.switchctrl.vendors.mikrotik.client import MikroTikSwitch
from networkmgmt.switchctrl.vendors.mikrotik.managers import (
    MikroTikLACPManager,
    MikroTikMonitoringManager,
    MikroTikPortManager,
    MikroTikVLANManager,
)

# ── MikroTikMonitoringManager (REST transport) ─────────────────────────


class TestMikroTikMonitoringManager:
    """Test MikroTikMonitoringManager using REST transport."""

    def test_get_system_info(self, mock_mikrotik_rest):
        """get_system_info returns SystemInfo with correct fields."""
        mock_mikrotik_rest.get.side_effect = [
            [{"name": "router1"}],  # system/identity
            [{"version": "7.1", "uptime": "1d2h3m"}],  # system/resource
            [{"serial-number": "SN123", "model": "CRS326"}],  # system/routerboard
        ]

        manager = MikroTikMonitoringManager(mock_mikrotik_rest)
        info = manager.get_system_info()

        assert isinstance(info, SystemInfo)
        assert info.hostname == "router1"
        assert info.serial_number == "SN123"
        assert info.firmware_version == "7.1"
        assert info.model == "CRS326"
        assert info.uptime == 93780  # 1d2h3m = 86400 + 7200 + 180

    def test_get_port_status(self, mock_mikrotik_rest):
        """get_port_status returns list[PortStatus] from REST interface/ethernet."""
        mock_mikrotik_rest.get.return_value = [
            {"name": "ether1", "running": True, "speed": "1000Mbps", "full-duplex": "yes"},
            {"name": "ether2", "running": False, "speed": "", "full-duplex": "no"},
        ]

        manager = MikroTikMonitoringManager(mock_mikrotik_rest)
        ports = manager.get_port_status()

        assert len(ports) == 2
        assert isinstance(ports[0], PortStatus)
        assert ports[0].port == "ether1"
        assert ports[0].link_up is True
        assert ports[0].speed == "1000Mbps"
        assert ports[0].duplex == "yes"
        assert ports[0].media_type == "Ethernet"

        assert ports[1].port == "ether2"
        assert ports[1].link_up is False

    def test_parse_uptime_full(self):
        """_parse_uptime parses full format: 1w2d3h4m5s."""
        result = MikroTikMonitoringManager._parse_uptime("1w2d3h4m5s")
        expected = (1 * 604800) + (2 * 86400) + (3 * 3600) + (4 * 60) + 5
        assert result == expected
        assert result == 788645

    def test_parse_uptime_partial(self):
        """_parse_uptime parses partial format: 1d2h3m."""
        result = MikroTikMonitoringManager._parse_uptime("1d2h3m")
        expected = (1 * 86400) + (2 * 3600) + (3 * 60)
        assert result == expected
        assert result == 93780

    def test_parse_uptime_short(self):
        """_parse_uptime parses short format: 5m30s."""
        result = MikroTikMonitoringManager._parse_uptime("5m30s")
        expected = (5 * 60) + 30
        assert result == expected
        assert result == 330


# ── MikroTikVLANManager (SSH transport) ────────────────────────────────


class TestMikroTikVLANManager:
    """Test MikroTikVLANManager using SSH transport."""

    def test_create_vlan(self, mock_routeros_transport):
        """create_vlan sends correct RouterOS command."""
        manager = MikroTikVLANManager(mock_routeros_transport)
        manager.create_vlan(100, "test")

        mock_routeros_transport.send_command.assert_called_once_with(
            "/interface vlan add name=test vlan-id=100 interface=bridge"
        )

    def test_create_vlan_invalid_id(self, mock_routeros_transport):
        """create_vlan with vlan_id=0 raises VLANError."""
        manager = MikroTikVLANManager(mock_routeros_transport)

        with pytest.raises(VLANError, match="Invalid VLAN ID"):
            manager.create_vlan(0)

    def test_delete_vlan(self, mock_routeros_transport):
        """delete_vlan finds item number then removes it."""
        # First call returns item number, second call removes it
        mock_routeros_transport.send_command.side_effect = [
            '  0  name="vlan100" vlan-id=100 interface=bridge',  # print where
            "",  # remove
        ]

        manager = MikroTikVLANManager(mock_routeros_transport)
        manager.delete_vlan(100)

        calls = mock_routeros_transport.send_command.call_args_list
        assert len(calls) == 2
        assert "/interface vlan print where vlan-id=100" in calls[0][0][0]
        assert "/interface vlan remove 0" in calls[1][0][0]

    def test_delete_vlan_id_one_raises(self, mock_routeros_transport):
        """delete_vlan(1) raises VLANError for default VLAN."""
        manager = MikroTikVLANManager(mock_routeros_transport)

        with pytest.raises(VLANError, match="Cannot delete default VLAN"):
            manager.delete_vlan(1)

    def test_parse_vlan_print(self):
        """_parse_vlan_print parses RouterOS vlan interface list."""
        output = """
  0  name="vlan100" vlan-id=100 interface=bridge
  1  name="vlan200" vlan-id=200 interface=bridge
  2  name=vlan300 vlan-id=300 interface=bridge
"""
        vlans = MikroTikVLANManager._parse_vlan_print(output)

        assert len(vlans) == 3
        assert isinstance(vlans[0], VLAN)
        assert vlans[0].vlan_id == 100
        assert vlans[0].name == "vlan100"
        assert vlans[1].vlan_id == 200
        assert vlans[1].name == "vlan200"
        assert vlans[2].vlan_id == 300
        assert vlans[2].name == "vlan300"


# ── MikroTikPortManager (SSH) ──────────────────────────────────────────


class TestMikroTikPortManager:
    """Test MikroTikPortManager using SSH transport."""

    def test_enable_port(self, mock_routeros_transport):
        """enable_port sends correct command with disabled=no."""
        manager = MikroTikPortManager(mock_routeros_transport)
        manager.enable_port("ether1")

        mock_routeros_transport.send_command.assert_called_once_with(
            "/interface ethernet set [find name=ether1] disabled=no"
        )

    def test_disable_port(self, mock_routeros_transport):
        """disable_port sends correct command with disabled=yes."""
        manager = MikroTikPortManager(mock_routeros_transport)
        manager.disable_port("ether1")

        mock_routeros_transport.send_command.assert_called_once_with(
            "/interface ethernet set [find name=ether1] disabled=yes"
        )

    def test_parse_ethernet_print(self):
        """_parse_ethernet_print parses RouterOS ethernet interface list."""
        output = """
0R  name="ether1" speed=1000Mbps
1   name="ether2" speed=auto
2R  name="ether3" speed=100Mbps
"""
        ports = MikroTikPortManager._parse_ethernet_print(output)

        assert len(ports) == 3
        assert isinstance(ports[0], PortStatus)
        assert ports[0].port == "ether1"
        assert ports[0].link_up is True
        assert ports[0].speed == "1000Mbps"

        assert ports[1].port == "ether2"
        assert ports[1].link_up is False
        assert ports[1].speed == "auto"

        assert ports[2].port == "ether3"
        assert ports[2].link_up is True


# ── MikroTikLACPManager (SSH) ──────────────────────────────────────────


class TestMikroTikLACPManager:
    """Test MikroTikLACPManager using SSH transport."""

    def test_create_port_channel(self, mock_routeros_transport):
        """create_port_channel sends bonding add command with 802.3ad mode."""
        manager = MikroTikLACPManager(mock_routeros_transport)
        manager.create_port_channel(1, ["ether1", "ether2"])

        mock_routeros_transport.send_command.assert_called_once_with(
            "/interface bonding add name=bond1 slaves=ether1,ether2 mode=802.3ad"
        )

    def test_create_port_channel_empty_members_raises(self, mock_routeros_transport):
        """create_port_channel with empty member list raises LACPError."""
        manager = MikroTikLACPManager(mock_routeros_transport)

        with pytest.raises(LACPError, match="At least one member port is required"):
            manager.create_port_channel(1, [])

    def test_parse_bonding_print(self):
        """_parse_bonding_print parses RouterOS bonding interface list."""
        output = """
0R  name="bond1" slaves="ether1,ether2" mode=802.3ad
1   name="bond2" slaves="ether3,ether4" mode=802.3ad
"""
        channels = MikroTikLACPManager._parse_bonding_print(output)

        assert len(channels) == 2
        assert isinstance(channels[0], LACPInfo)
        assert channels[0].port_channel_id == 1
        assert channels[0].member_ports == ["ether1", "ether2"]
        assert channels[0].status == "active"

        assert channels[1].port_channel_id == 2
        assert channels[1].member_ports == ["ether3", "ether4"]
        assert channels[1].status == "inactive"


# ── MikroTikSwitch client ──────────────────────────────────────────────


class TestMikroTikSwitch:
    """Test MikroTikSwitch client with both transports."""

    @patch("networkmgmt.switchctrl.vendors.mikrotik.client.RouterOSTransport")
    @patch("networkmgmt.switchctrl.vendors.mikrotik.client.MikroTikRESTTransport")
    def test_connect(self, mock_rest_class, mock_ssh_class):
        """connect() connects both REST and SSH transports."""
        mock_rest = MagicMock()
        mock_ssh = MagicMock()
        mock_rest_class.return_value = mock_rest
        mock_ssh_class.return_value = mock_ssh

        client = MikroTikSwitch(host="192.168.1.1", password="admin")
        client.connect()

        mock_rest.connect.assert_called_once()
        mock_ssh.connect.assert_called_once()

    @patch("networkmgmt.switchctrl.vendors.mikrotik.client.RouterOSTransport")
    @patch("networkmgmt.switchctrl.vendors.mikrotik.client.MikroTikRESTTransport")
    def test_disconnect(self, mock_rest_class, mock_ssh_class):
        """disconnect() disconnects both transports and clears managers."""
        mock_rest = MagicMock()
        mock_ssh = MagicMock()
        mock_rest.is_connected.return_value = True
        mock_ssh.is_connected.return_value = True
        mock_rest_class.return_value = mock_rest
        mock_ssh_class.return_value = mock_ssh

        client = MikroTikSwitch(host="192.168.1.1", password="admin")
        client.connect()

        # Access properties to initialize managers
        _ = client.monitoring
        _ = client.vlan

        # Verify managers are initialized
        assert client._monitoring is not None
        assert client._vlan is not None

        client.disconnect()

        # Verify disconnection
        mock_rest.disconnect.assert_called_once()
        mock_ssh.disconnect.assert_called_once()

        # Verify managers are cleared
        assert client._monitoring is None
        assert client._vlan is None
        assert client._port is None
        assert client._lacp is None

    @patch("networkmgmt.switchctrl.vendors.mikrotik.client.RouterOSTransport")
    @patch("networkmgmt.switchctrl.vendors.mikrotik.client.MikroTikRESTTransport")
    def test_lazy_manager_initialization(self, mock_rest_class, mock_ssh_class):
        """Accessing monitoring property auto-connects REST if not connected."""
        mock_rest = MagicMock()
        mock_rest.is_connected.return_value = False
        mock_rest_class.return_value = mock_rest
        mock_ssh_class.return_value = MagicMock()

        client = MikroTikSwitch(host="192.168.1.1", password="admin")

        # Access monitoring property
        monitoring = client.monitoring

        # Verify REST was connected
        mock_rest.connect.assert_called_once()
        assert monitoring is not None
        assert client._monitoring is not None
