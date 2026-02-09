"""Tests for switchctrl data models and enums."""

import pytest

from networkmgmt.switchctrl.models.port import (
    DuplexMode,
    PortConfig,
    PortMode,
    PortSpeed,
    PortStatus,
)
from networkmgmt.switchctrl.models.stats import PortStatistics
from networkmgmt.switchctrl.models.system import LACPInfo, SensorData, SystemInfo
from networkmgmt.switchctrl.models.vlan import VLAN, TrunkConfig


class TestPortSpeed:
    """Test PortSpeed enum."""

    def test_enum_values(self):
        """Test all PortSpeed enum members have correct values."""
        assert PortSpeed.AUTO.value == "auto"
        assert PortSpeed.SPEED_10M.value == "10"
        assert PortSpeed.SPEED_100M.value == "100"
        assert PortSpeed.SPEED_1G.value == "1000"
        assert PortSpeed.SPEED_10G.value == "10000"


class TestPortMode:
    """Test PortMode enum."""

    def test_enum_values(self):
        """Test all PortMode enum members have correct values."""
        assert PortMode.ACCESS.value == "access"
        assert PortMode.TRUNK.value == "trunk"


class TestDuplexMode:
    """Test DuplexMode enum."""

    def test_enum_values(self):
        """Test all DuplexMode enum members have correct values."""
        assert DuplexMode.AUTO.value == "auto"
        assert DuplexMode.FULL.value == "full"
        assert DuplexMode.HALF.value == "half"


class TestPortConfig:
    """Test PortConfig dataclass."""

    def test_defaults(self):
        """Test PortConfig has correct default values."""
        config = PortConfig(port="gi1")
        assert config.port == "gi1"
        assert config.speed == PortSpeed.AUTO
        assert config.duplex == DuplexMode.AUTO
        assert config.mode == PortMode.ACCESS
        assert config.enabled is True
        assert config.description == ""
        assert config.access_vlan is None

    def test_custom_values(self):
        """Test PortConfig with custom values."""
        config = PortConfig(
            port="gi2",
            speed=PortSpeed.SPEED_1G,
            duplex=DuplexMode.FULL,
            mode=PortMode.TRUNK,
            enabled=False,
            description="Test Port",
            access_vlan=10,
        )
        assert config.port == "gi2"
        assert config.speed == PortSpeed.SPEED_1G
        assert config.duplex == DuplexMode.FULL
        assert config.mode == PortMode.TRUNK
        assert config.enabled is False
        assert config.description == "Test Port"
        assert config.access_vlan == 10


class TestPortStatus:
    """Test PortStatus dataclass."""

    def test_defaults(self):
        """Test PortStatus has correct default values."""
        status = PortStatus(port="gi1")
        assert status.port == "gi1"
        assert status.link_up is False
        assert status.speed == ""
        assert status.duplex == ""
        assert status.media_type == ""
        assert status.max_speed == ""

    def test_custom_values(self):
        """Test PortStatus with custom values."""
        status = PortStatus(
            port="gi2",
            link_up=True,
            speed="1000",
            duplex="Full",
            media_type="Copper",
            max_speed="1000",
        )
        assert status.port == "gi2"
        assert status.link_up is True
        assert status.speed == "1000"
        assert status.duplex == "Full"
        assert status.media_type == "Copper"
        assert status.max_speed == "1000"


class TestPortStatistics:
    """Test PortStatistics dataclass."""

    def test_defaults(self):
        """Test PortStatistics has correct default values."""
        stats = PortStatistics(port="gi1")
        assert stats.port == "gi1"
        assert stats.tx_bytes == 0
        assert stats.rx_bytes == 0
        assert stats.tx_packets == 0
        assert stats.rx_packets == 0
        assert stats.tx_errors == 0
        assert stats.rx_errors == 0
        assert stats.link_up is False
        assert stats.speed == ""

    def test_custom_values(self):
        """Test PortStatistics with custom values."""
        stats = PortStatistics(
            port="gi2",
            tx_bytes=1000,
            rx_bytes=2000,
            tx_packets=10,
            rx_packets=20,
            tx_errors=1,
            rx_errors=2,
            link_up=True,
            speed="1000",
        )
        assert stats.port == "gi2"
        assert stats.tx_bytes == 1000
        assert stats.rx_bytes == 2000
        assert stats.tx_packets == 10
        assert stats.rx_packets == 20
        assert stats.tx_errors == 1
        assert stats.rx_errors == 2
        assert stats.link_up is True
        assert stats.speed == "1000"


class TestSystemInfo:
    """Test SystemInfo dataclass."""

    def test_defaults(self):
        """Test SystemInfo has correct default values."""
        info = SystemInfo()
        assert info.hostname == ""
        assert info.mac_address == ""
        assert info.serial_number == ""
        assert info.firmware_version == ""
        assert info.firmware_date == ""
        assert info.model == ""
        assert info.uptime == 0

    def test_custom_values(self):
        """Test SystemInfo with custom values."""
        info = SystemInfo(
            hostname="switch1",
            mac_address="AA:BB:CC:DD:EE:FF",
            serial_number="ABC123",
            firmware_version="4.2.1",
            firmware_date="2023-01-15",
            model="C1200-8T-D",
            uptime=86400,
        )
        assert info.hostname == "switch1"
        assert info.mac_address == "AA:BB:CC:DD:EE:FF"
        assert info.serial_number == "ABC123"
        assert info.firmware_version == "4.2.1"
        assert info.firmware_date == "2023-01-15"
        assert info.model == "C1200-8T-D"
        assert info.uptime == 86400


class TestSensorData:
    """Test SensorData dataclass."""

    def test_defaults(self):
        """Test SensorData has correct default values."""
        sensor = SensorData()
        assert sensor.temperature == 0.0
        assert sensor.max_temperature == 0.0
        assert sensor.fan_speed == 0

    def test_custom_values(self):
        """Test SensorData with custom values."""
        sensor = SensorData(temperature=45.5, max_temperature=70.0, fan_speed=3000)
        assert sensor.temperature == 45.5
        assert sensor.max_temperature == 70.0
        assert sensor.fan_speed == 3000


class TestLACPInfo:
    """Test LACPInfo dataclass."""

    def test_defaults(self):
        """Test LACPInfo has correct default values."""
        lacp = LACPInfo()
        assert lacp.port_channel_id == 0
        assert lacp.member_ports == []
        assert lacp.admin_key == 0
        assert lacp.partner_key == 0
        assert lacp.status == ""

    def test_custom_values(self):
        """Test LACPInfo with custom values."""
        lacp = LACPInfo(
            port_channel_id=1,
            member_ports=["gi1", "gi2"],
            admin_key=10,
            partner_key=20,
            status="SU",
        )
        assert lacp.port_channel_id == 1
        assert lacp.member_ports == ["gi1", "gi2"]
        assert lacp.admin_key == 10
        assert lacp.partner_key == 20
        assert lacp.status == "SU"

    def test_member_ports_no_shared_state(self):
        """Test that member_ports list is not shared between instances."""
        lacp1 = LACPInfo()
        lacp2 = LACPInfo()
        lacp1.member_ports.append("gi1")
        assert lacp1.member_ports == ["gi1"]
        assert lacp2.member_ports == []


class TestVLAN:
    """Test VLAN dataclass."""

    def test_defaults(self):
        """Test VLAN has correct default values."""
        vlan = VLAN(vlan_id=10)
        assert vlan.vlan_id == 10
        assert vlan.name == ""
        assert vlan.tagged_ports == []
        assert vlan.untagged_ports == []

    def test_custom_values(self):
        """Test VLAN with custom values."""
        vlan = VLAN(
            vlan_id=20,
            name="Management",
            tagged_ports=["gi1", "gi2"],
            untagged_ports=["gi3"],
        )
        assert vlan.vlan_id == 20
        assert vlan.name == "Management"
        assert vlan.tagged_ports == ["gi1", "gi2"]
        assert vlan.untagged_ports == ["gi3"]

    def test_tagged_ports_no_shared_state(self):
        """Test that tagged_ports list is not shared between instances."""
        vlan1 = VLAN(vlan_id=10)
        vlan2 = VLAN(vlan_id=20)
        vlan1.tagged_ports.append("gi1")
        assert vlan1.tagged_ports == ["gi1"]
        assert vlan2.tagged_ports == []

    def test_untagged_ports_no_shared_state(self):
        """Test that untagged_ports list is not shared between instances."""
        vlan1 = VLAN(vlan_id=10)
        vlan2 = VLAN(vlan_id=20)
        vlan1.untagged_ports.append("gi1")
        assert vlan1.untagged_ports == ["gi1"]
        assert vlan2.untagged_ports == []


class TestTrunkConfig:
    """Test TrunkConfig dataclass."""

    def test_defaults(self):
        """Test TrunkConfig has correct default values."""
        trunk = TrunkConfig(port="gi1")
        assert trunk.port == "gi1"
        assert trunk.native_vlan == 1
        assert trunk.allowed_vlans == []

    def test_custom_values(self):
        """Test TrunkConfig with custom values."""
        trunk = TrunkConfig(port="gi2", native_vlan=10, allowed_vlans=[10, 20, 30])
        assert trunk.port == "gi2"
        assert trunk.native_vlan == 10
        assert trunk.allowed_vlans == [10, 20, 30]

    def test_allowed_vlans_no_shared_state(self):
        """Test that allowed_vlans list is not shared between instances."""
        trunk1 = TrunkConfig(port="gi1")
        trunk2 = TrunkConfig(port="gi2")
        trunk1.allowed_vlans.append(10)
        assert trunk1.allowed_vlans == [10]
        assert trunk2.allowed_vlans == []
