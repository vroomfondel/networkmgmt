"""Tests for Cisco CLI operations using mocked transport."""

from unittest.mock import MagicMock, Mock

import pytest

from networkmgmt.switchctrl.exceptions import LACPError, PortError, VLANError
from networkmgmt.switchctrl.models.port import (
    DuplexMode,
    PortConfig,
    PortMode,
    PortSpeed,
)
from networkmgmt.switchctrl.models.vlan import TrunkConfig
from networkmgmt.switchctrl.vendors.cisco.managers import (
    CiscoCatalystLACPManager,
    CiscoCatalystVLANManager,
)
from networkmgmt.switchctrl.vendors.common.cisco_managers import (
    CiscoLACPManager,
    CiscoPortManager,
    CiscoVLANManager,
)


@pytest.fixture
def mock_cisco_transport():
    """Create a mock Cisco CLI transport."""
    transport = MagicMock()
    transport.send_command = Mock(return_value="")
    transport.send_config_commands = Mock(return_value="")
    return transport


class TestCiscoVLANManager:
    """Test CiscoVLANManager operations."""

    def test_create_vlan_success(self, mock_cisco_transport):
        """Create VLAN should send correct commands."""
        manager = CiscoVLANManager(mock_cisco_transport)
        manager.create_vlan(10, "MGMT")

        mock_cisco_transport.send_config_commands.assert_called_once()
        commands = mock_cisco_transport.send_config_commands.call_args[0][0]
        assert "vlan 10" in commands
        assert "name MGMT" in commands
        assert "exit" in commands

    def test_create_vlan_without_name(self, mock_cisco_transport):
        """Create VLAN without name should send correct commands."""
        manager = CiscoVLANManager(mock_cisco_transport)
        manager.create_vlan(20)

        mock_cisco_transport.send_config_commands.assert_called_once()
        commands = mock_cisco_transport.send_config_commands.call_args[0][0]
        assert "vlan 20" in commands
        assert "exit" in commands
        # Should not have name command
        assert not any("name" in cmd for cmd in commands)

    def test_create_vlan_invalid_id_too_low(self, mock_cisco_transport):
        """Create VLAN with ID 0 should raise VLANError."""
        manager = CiscoVLANManager(mock_cisco_transport)
        with pytest.raises(VLANError) as exc_info:
            manager.create_vlan(0, "invalid")
        assert "2-4094" in str(exc_info.value)

    def test_create_vlan_invalid_id_too_high(self, mock_cisco_transport):
        """Create VLAN with ID 4095 should raise VLANError."""
        manager = CiscoVLANManager(mock_cisco_transport)
        with pytest.raises(VLANError) as exc_info:
            manager.create_vlan(4095, "invalid")
        assert "2-4094" in str(exc_info.value)

    def test_create_vlan_with_error_output(self, mock_cisco_transport):
        """Create VLAN with error in output should raise VLANError."""
        mock_cisco_transport.send_config_commands.return_value = "Error: invalid VLAN ID"
        manager = CiscoVLANManager(mock_cisco_transport)

        with pytest.raises(VLANError) as exc_info:
            manager.create_vlan(10)
        assert "Failed to create VLAN" in str(exc_info.value)

    def test_delete_vlan_success(self, mock_cisco_transport):
        """Delete VLAN should send correct command."""
        manager = CiscoVLANManager(mock_cisco_transport)
        manager.delete_vlan(10)

        mock_cisco_transport.send_config_commands.assert_called_once()
        commands = mock_cisco_transport.send_config_commands.call_args[0][0]
        assert "no vlan 10" in commands

    def test_delete_vlan_protected(self, mock_cisco_transport):
        """Delete VLAN 1 should raise VLANError."""
        manager = CiscoVLANManager(mock_cisco_transport)
        with pytest.raises(VLANError) as exc_info:
            manager.delete_vlan(1)
        assert "Cannot delete default VLAN 1" in str(exc_info.value)

    def test_delete_vlan_with_error_output(self, mock_cisco_transport):
        """Delete VLAN with error in output should raise VLANError."""
        mock_cisco_transport.send_config_commands.return_value = "Error: VLAN not found"
        manager = CiscoVLANManager(mock_cisco_transport)

        with pytest.raises(VLANError) as exc_info:
            manager.delete_vlan(10)
        assert "Failed to delete VLAN" in str(exc_info.value)

    def test_assign_port_to_vlan_untagged(self, mock_cisco_transport):
        """Assign port to VLAN as untagged (access) should send correct commands."""
        manager = CiscoVLANManager(mock_cisco_transport)
        manager.assign_port_to_vlan("gi1", 10, tagged=False)

        mock_cisco_transport.send_config_commands.assert_called_once()
        commands = mock_cisco_transport.send_config_commands.call_args[0][0]
        assert "interface gi1" in commands
        assert "switchport mode access" in commands
        assert "switchport access vlan 10" in commands
        assert "exit" in commands

    def test_assign_port_to_vlan_tagged(self, mock_cisco_transport):
        """Assign port to VLAN as tagged (trunk) should send correct commands."""
        manager = CiscoVLANManager(mock_cisco_transport)
        manager.assign_port_to_vlan("gi2", 20, tagged=True)

        mock_cisco_transport.send_config_commands.assert_called_once()
        commands = mock_cisco_transport.send_config_commands.call_args[0][0]
        assert "interface gi2" in commands
        assert "switchport mode trunk" in commands
        assert "switchport trunk allowed vlan add 20" in commands
        assert "exit" in commands

    def test_configure_trunk_with_allowed_vlans(self, mock_cisco_transport):
        """Configure trunk should set native VLAN and allowed VLANs."""
        manager = CiscoVLANManager(mock_cisco_transport)
        config = TrunkConfig(port="gi3", native_vlan=10, allowed_vlans=[10, 20, 30])
        manager.configure_trunk(config)

        mock_cisco_transport.send_config_commands.assert_called_once()
        commands = mock_cisco_transport.send_config_commands.call_args[0][0]
        assert "interface gi3" in commands
        assert "switchport mode trunk" in commands
        assert "switchport trunk native vlan 10" in commands
        assert "switchport trunk allowed vlan 10,20,30" in commands
        assert "exit" in commands

    def test_configure_trunk_without_allowed_vlans(self, mock_cisco_transport):
        """Configure trunk without allowed VLANs should use 'all'."""
        manager = CiscoVLANManager(mock_cisco_transport)
        config = TrunkConfig(port="gi4", native_vlan=1, allowed_vlans=[])
        manager.configure_trunk(config)

        mock_cisco_transport.send_config_commands.assert_called_once()
        commands = mock_cisco_transport.send_config_commands.call_args[0][0]
        assert "switchport trunk allowed vlan all" in commands


class TestCiscoPortManager:
    """Test CiscoPortManager operations."""

    def test_configure_port_full(self, mock_cisco_transport):
        """Configure port with all settings should send correct commands."""
        manager = CiscoPortManager(mock_cisco_transport)
        config = PortConfig(
            port="gi1",
            speed=PortSpeed.SPEED_1G,
            duplex=DuplexMode.FULL,
            mode=PortMode.ACCESS,
            enabled=True,
            description="Test Port",
            access_vlan=10,
        )
        manager.configure_port(config)

        mock_cisco_transport.send_config_commands.assert_called_once()
        commands = mock_cisco_transport.send_config_commands.call_args[0][0]
        assert "interface gi1" in commands
        assert "no shutdown" in commands
        assert "speed 1000" in commands
        assert "duplex full" in commands
        assert "description Test Port" in commands
        assert "switchport mode access" in commands
        assert "switchport access vlan 10" in commands
        assert "exit" in commands

    def test_configure_port_disabled(self, mock_cisco_transport):
        """Configure disabled port should send shutdown command."""
        manager = CiscoPortManager(mock_cisco_transport)
        config = PortConfig(port="gi2", enabled=False)
        manager.configure_port(config)

        mock_cisco_transport.send_config_commands.assert_called_once()
        commands = mock_cisco_transport.send_config_commands.call_args[0][0]
        assert "interface gi2" in commands
        assert "shutdown" in commands

    def test_configure_port_auto_speed_duplex(self, mock_cisco_transport):
        """Configure port with auto speed/duplex should send auto commands."""
        manager = CiscoPortManager(mock_cisco_transport)
        config = PortConfig(port="gi3", speed=PortSpeed.AUTO, duplex=DuplexMode.AUTO)
        manager.configure_port(config)

        mock_cisco_transport.send_config_commands.assert_called_once()
        commands = mock_cisco_transport.send_config_commands.call_args[0][0]
        assert "speed auto" in commands
        assert "duplex auto" in commands

    def test_configure_port_trunk_mode(self, mock_cisco_transport):
        """Configure port in trunk mode should send trunk command."""
        manager = CiscoPortManager(mock_cisco_transport)
        config = PortConfig(port="gi4", mode=PortMode.TRUNK)
        manager.configure_port(config)

        mock_cisco_transport.send_config_commands.assert_called_once()
        commands = mock_cisco_transport.send_config_commands.call_args[0][0]
        assert "switchport mode trunk" in commands
        # Should not have access vlan command in trunk mode
        assert not any("switchport access vlan" in cmd for cmd in commands)

    def test_configure_port_with_error(self, mock_cisco_transport):
        """Configure port with error in output should raise PortError."""
        mock_cisco_transport.send_config_commands.return_value = "Error: invalid command"
        manager = CiscoPortManager(mock_cisco_transport)
        config = PortConfig(port="gi1")

        with pytest.raises(PortError) as exc_info:
            manager.configure_port(config)
        assert "Failed to configure" in str(exc_info.value)

    def test_enable_port(self, mock_cisco_transport):
        """Enable port should send no shutdown command."""
        manager = CiscoPortManager(mock_cisco_transport)
        manager.enable_port("gi1")

        mock_cisco_transport.send_config_commands.assert_called_once()
        commands = mock_cisco_transport.send_config_commands.call_args[0][0]
        assert "interface gi1" in commands
        assert "no shutdown" in commands
        assert "exit" in commands

    def test_disable_port(self, mock_cisco_transport):
        """Disable port should send shutdown command."""
        manager = CiscoPortManager(mock_cisco_transport)
        manager.disable_port("gi2")

        mock_cisco_transport.send_config_commands.assert_called_once()
        commands = mock_cisco_transport.send_config_commands.call_args[0][0]
        assert "interface gi2" in commands
        assert "shutdown" in commands
        assert "exit" in commands


class TestCiscoLACPManager:
    """Test CiscoLACPManager operations."""

    def test_create_port_channel_success(self, mock_cisco_transport):
        """Create port channel should send channel-group commands with mode active."""
        manager = CiscoLACPManager(mock_cisco_transport)
        manager.create_port_channel(1, ["gi1", "gi2"])

        mock_cisco_transport.send_config_commands.assert_called_once()
        commands = mock_cisco_transport.send_config_commands.call_args[0][0]
        assert "interface gi1" in commands
        assert "channel-group 1 mode active" in commands
        assert "interface gi2" in commands
        # Count how many times we see the channel-group command
        channel_group_count = sum(1 for cmd in commands if "channel-group 1 mode active" in cmd)
        assert channel_group_count == 2  # Once for each port

    def test_create_port_channel_invalid_id_too_high(self, mock_cisco_transport):
        """Create port channel with ID 9 should raise LACPError."""
        manager = CiscoLACPManager(mock_cisco_transport)
        with pytest.raises(LACPError) as exc_info:
            manager.create_port_channel(9, ["gi1"])
        assert "must be 1-8" in str(exc_info.value)

    def test_create_port_channel_invalid_id_too_low(self, mock_cisco_transport):
        """Create port channel with ID 0 should raise LACPError."""
        manager = CiscoLACPManager(mock_cisco_transport)
        with pytest.raises(LACPError) as exc_info:
            manager.create_port_channel(0, ["gi1"])
        assert "must be 1-8" in str(exc_info.value)

    def test_create_port_channel_empty_members(self, mock_cisco_transport):
        """Create port channel with empty member list should raise LACPError."""
        manager = CiscoLACPManager(mock_cisco_transport)
        with pytest.raises(LACPError) as exc_info:
            manager.create_port_channel(1, [])
        assert "At least one member port is required" in str(exc_info.value)

    def test_create_port_channel_with_error(self, mock_cisco_transport):
        """Create port channel with error in output should raise LACPError."""
        mock_cisco_transport.send_config_commands.return_value = "Error: invalid port"
        manager = CiscoLACPManager(mock_cisco_transport)

        with pytest.raises(LACPError) as exc_info:
            manager.create_port_channel(1, ["gi1"])
        assert "Failed to create port-channel" in str(exc_info.value)


class TestCiscoCatalystVLANManager:
    """Test CiscoCatalystVLANManager operations (uses vlan database mode)."""

    def test_create_vlan_uses_vlan_database(self, mock_cisco_transport):
        """Create VLAN should use vlan database mode."""
        manager = CiscoCatalystVLANManager(mock_cisco_transport)
        manager.create_vlan(10, "MGMT")

        # Should call send_command three times: vlan database, vlan 10 name MGMT, exit
        assert mock_cisco_transport.send_command.call_count == 3
        calls = [call[0][0] for call in mock_cisco_transport.send_command.call_args_list]
        assert "vlan database" in calls
        assert "vlan 10 name MGMT" in calls
        assert "exit" in calls

    def test_create_vlan_without_name_uses_vlan_database(self, mock_cisco_transport):
        """Create VLAN without name should use vlan database mode."""
        manager = CiscoCatalystVLANManager(mock_cisco_transport)
        manager.create_vlan(20)

        assert mock_cisco_transport.send_command.call_count == 3
        calls = [call[0][0] for call in mock_cisco_transport.send_command.call_args_list]
        assert "vlan database" in calls
        assert "vlan 20" in calls
        assert "exit" in calls

    def test_create_vlan_invalid_id(self, mock_cisco_transport):
        """Create VLAN with invalid ID should raise VLANError."""
        manager = CiscoCatalystVLANManager(mock_cisco_transport)
        with pytest.raises(VLANError) as exc_info:
            manager.create_vlan(1, "invalid")
        assert "2-4094" in str(exc_info.value)

    def test_create_vlan_with_error(self, mock_cisco_transport):
        """Create VLAN with error in output should raise VLANError."""
        mock_cisco_transport.send_command.return_value = "Error: invalid VLAN"
        manager = CiscoCatalystVLANManager(mock_cisco_transport)

        with pytest.raises(VLANError) as exc_info:
            manager.create_vlan(10)
        assert "Failed to create VLAN" in str(exc_info.value)

    def test_delete_vlan_uses_vlan_database(self, mock_cisco_transport):
        """Delete VLAN should use vlan database mode."""
        manager = CiscoCatalystVLANManager(mock_cisco_transport)
        manager.delete_vlan(10)

        assert mock_cisco_transport.send_command.call_count == 3
        calls = [call[0][0] for call in mock_cisco_transport.send_command.call_args_list]
        assert "vlan database" in calls
        assert "no vlan 10" in calls
        assert "exit" in calls

    def test_delete_vlan_protected(self, mock_cisco_transport):
        """Delete VLAN 1 should raise VLANError."""
        manager = CiscoCatalystVLANManager(mock_cisco_transport)
        with pytest.raises(VLANError) as exc_info:
            manager.delete_vlan(1)
        assert "Cannot delete default VLAN 1" in str(exc_info.value)


class TestCiscoCatalystLACPManager:
    """Test CiscoCatalystLACPManager operations (uses mode auto)."""

    def test_create_port_channel_uses_mode_auto(self, mock_cisco_transport):
        """Create port channel should use channel-group mode auto."""
        manager = CiscoCatalystLACPManager(mock_cisco_transport)
        manager.create_port_channel(1, ["gi1", "gi2"])

        mock_cisco_transport.send_config_commands.assert_called_once()
        commands = mock_cisco_transport.send_config_commands.call_args[0][0]
        assert "interface gi1" in commands
        assert "channel-group 1 mode auto" in commands
        assert "interface gi2" in commands
        # Count occurrences of mode auto
        auto_mode_count = sum(1 for cmd in commands if "channel-group 1 mode auto" in cmd)
        assert auto_mode_count == 2  # Once for each port

    def test_create_port_channel_invalid_id(self, mock_cisco_transport):
        """Create port channel with invalid ID should raise LACPError."""
        manager = CiscoCatalystLACPManager(mock_cisco_transport)
        with pytest.raises(LACPError) as exc_info:
            manager.create_port_channel(10, ["gi1"])
        assert "must be 1-8" in str(exc_info.value)

    def test_create_port_channel_empty_members(self, mock_cisco_transport):
        """Create port channel with empty members should raise LACPError."""
        manager = CiscoCatalystLACPManager(mock_cisco_transport)
        with pytest.raises(LACPError) as exc_info:
            manager.create_port_channel(1, [])
        assert "At least one member port is required" in str(exc_info.value)
