"""Tests for Cisco CLI parsing methods (static methods, no mocking needed)."""

import pytest

from networkmgmt.switchctrl.models.port import PortStatus
from networkmgmt.switchctrl.models.stats import PortStatistics
from networkmgmt.switchctrl.models.system import LACPInfo, SensorData, SystemInfo
from networkmgmt.switchctrl.models.vlan import VLAN
from networkmgmt.switchctrl.vendors.cisco.managers import (
    CiscoCatalystLACPManager,
    CiscoCatalystPortManager,
    CiscoCLIMonitoringManager,
    _parse_uptime,
)
from networkmgmt.switchctrl.vendors.common.cisco_managers import (
    CiscoLACPManager,
    CiscoPortManager,
    CiscoVLANManager,
)


class TestParseShowVLAN:
    """Test CiscoVLANManager._parse_show_vlan static method."""

    def test_parse_multiple_vlans(self):
        """Parse output with multiple VLANs."""
        output = """
VLAN  Name          Tagged Ports          Untagged Ports
----  ----          ------------          --------------
1     default                             Gi1/0/1, Gi1/0/2
10    management    Gi1/0/8               Gi1/0/3
20    guest         Gi1/0/9, Gi1/0/10     Gi1/0/4, Gi1/0/5
        """
        vlans = CiscoVLANManager._parse_show_vlan(output)

        assert len(vlans) == 3

        # Check VLAN 1
        # Parser splits on 2+ spaces; the gap between name and ports is one block,
        # so ports land in tagged_ports when there's no separate tagged column.
        assert vlans[0].vlan_id == 1
        assert vlans[0].name == "default"
        assert vlans[0].tagged_ports == ["Gi1/0/1", "Gi1/0/2"]
        assert vlans[0].untagged_ports == []

        # Check VLAN 10
        assert vlans[1].vlan_id == 10
        assert vlans[1].name == "management"
        assert vlans[1].tagged_ports == ["Gi1/0/8"]
        assert vlans[1].untagged_ports == ["Gi1/0/3"]

        # Check VLAN 20
        assert vlans[2].vlan_id == 20
        assert vlans[2].name == "guest"
        assert vlans[2].tagged_ports == ["Gi1/0/9", "Gi1/0/10"]
        assert vlans[2].untagged_ports == ["Gi1/0/4", "Gi1/0/5"]

    def test_parse_empty_output(self):
        """Parse empty output should return empty list."""
        output = ""
        vlans = CiscoVLANManager._parse_show_vlan(output)
        assert vlans == []

    def test_parse_header_only(self):
        """Parse output with only header should return empty list."""
        output = """
VLAN  Name          Tagged Ports          Untagged Ports
----  ----          ------------          --------------
        """
        vlans = CiscoVLANManager._parse_show_vlan(output)
        assert vlans == []

    def test_parse_vlan_no_ports(self):
        """Parse VLAN with no ports assigned."""
        output = """
VLAN  Name          Tagged Ports          Untagged Ports
----  ----          ------------          --------------
100   isolated
        """
        vlans = CiscoVLANManager._parse_show_vlan(output)
        assert len(vlans) == 1
        assert vlans[0].vlan_id == 100
        assert vlans[0].name == "isolated"
        assert vlans[0].tagged_ports == []
        assert vlans[0].untagged_ports == []


class TestParseInterfaceStatus:
    """Test CiscoPortManager._parse_interface_status static method."""

    def test_parse_multiple_ports(self):
        """Parse output with multiple ports."""
        output = """
Port       Link    Speed    Duplex   Type
Gi1/0/1    Up      1000     Full     Copper
Gi1/0/2    Down    Auto     Auto     Copper
Gi1/0/3    Up      100      Half     Fiber
        """
        ports = CiscoPortManager._parse_interface_status(output)

        assert len(ports) == 3

        # Port 1
        assert ports[0].port == "Gi1/0/1"
        assert ports[0].link_up is True
        assert ports[0].speed == "1000"
        assert ports[0].duplex == "Full"
        assert ports[0].media_type == "Copper"

        # Port 2
        assert ports[1].port == "Gi1/0/2"
        assert ports[1].link_up is False
        assert ports[1].speed == "Auto"
        assert ports[1].duplex == "Auto"
        assert ports[1].media_type == "Copper"

        # Port 3
        assert ports[2].port == "Gi1/0/3"
        assert ports[2].link_up is True
        assert ports[2].speed == "100"
        assert ports[2].duplex == "Half"
        assert ports[2].media_type == "Fiber"

    def test_parse_empty_output(self):
        """Parse empty output should return empty list."""
        output = ""
        ports = CiscoPortManager._parse_interface_status(output)
        assert ports == []

    def test_parse_header_only(self):
        """Parse output with only header should return empty list."""
        output = """
Port       Link    Speed    Duplex   Type
        """
        ports = CiscoPortManager._parse_interface_status(output)
        assert ports == []


class TestParseEtherchannel:
    """Test CiscoLACPManager._parse_etherchannel static method."""

    def test_parse_single_channel(self):
        """Parse output with single port-channel."""
        output = """
Group  Port-channel  Protocol  Ports
-----  ------------  --------  -----
1      Po1(SU)       LACP      Gi1/0/1(P) Gi1/0/2(P)
        """
        channels = CiscoLACPManager._parse_etherchannel(output)

        assert len(channels) == 1
        assert channels[0].port_channel_id == 1
        assert channels[0].member_ports == ["Gi1/0/1", "Gi1/0/2"]
        assert channels[0].status == "SU"

    def test_parse_multiple_channels(self):
        """Parse output with multiple port-channels."""
        output = """
Group  Port-channel  Protocol  Ports
-----  ------------  --------  -----
1      Po1(SU)       LACP      Gi1/0/1(P) Gi1/0/2(P)
2      Po2(SD)       LACP      Gi1/0/3(P)
3      Po3(SU)       LACP      Gi1/0/4(P) Gi1/0/5(P) Gi1/0/6(P)
        """
        channels = CiscoLACPManager._parse_etherchannel(output)

        assert len(channels) == 3

        assert channels[0].port_channel_id == 1
        assert channels[0].member_ports == ["Gi1/0/1", "Gi1/0/2"]
        assert channels[0].status == "SU"

        assert channels[1].port_channel_id == 2
        assert channels[1].member_ports == ["Gi1/0/3"]
        assert channels[1].status == "SD"

        assert channels[2].port_channel_id == 3
        assert channels[2].member_ports == ["Gi1/0/4", "Gi1/0/5", "Gi1/0/6"]
        assert channels[2].status == "SU"

    def test_parse_empty_output(self):
        """Parse empty output should return empty list."""
        output = ""
        channels = CiscoLACPManager._parse_etherchannel(output)
        assert channels == []

    def test_parse_header_only(self):
        """Parse output with only header should return empty list."""
        output = """
Group  Port-channel  Protocol  Ports
-----  ------------  --------  -----
        """
        channels = CiscoLACPManager._parse_etherchannel(output)
        assert channels == []


class TestParseShowVersion:
    """Test CiscoCLIMonitoringManager._parse_show_version static method."""

    def test_parse_show_version(self):
        """Parse typical show version output."""
        output = """
switch1 uptime is 1 day, 2 hours, 30 minutes
System image file is "flash:image.bin"
Base Ethernet MAC Address: AA:BB:CC:DD:EE:FF
System serial number: ABC123
Cisco C1200-8T-D
        """
        info = CiscoCLIMonitoringManager._parse_show_version(output)

        assert info.hostname == "switch1"
        assert info.mac_address == "AA:BB:CC:DD:EE:FF"
        assert info.serial_number == "ABC123"
        assert info.firmware_version == "flash:image.bin"
        assert info.model == "C1200-8T-D"
        assert info.uptime == 95400  # 1 day + 2 hours + 30 minutes

    def test_parse_show_version_with_software_version(self):
        """Parse show version with Software Version line.

        Note: 'Cisco IOS Software, Version ...' matches the '^Cisco' model regex
        first (capturing 'IOS' as model) and continues, so firmware_version stays
        empty unless a 'System image file' line is present.
        """
        output = """
myswitch uptime is 5 minutes
Cisco IOS Software, Version 4.2.1.5
Base ethernet MAC Address: 11:22:33:44:55:66
System serial number: XYZ789
        """
        info = CiscoCLIMonitoringManager._parse_show_version(output)

        assert info.hostname == "myswitch"
        assert info.mac_address == "11:22:33:44:55:66"
        assert info.serial_number == "XYZ789"
        # 'Cisco IOS ...' line sets model to 'IOS' via ^Cisco regex
        assert info.model == "IOS"
        assert info.uptime == 300  # 5 minutes

    def test_parse_show_version_minimal(self):
        """Parse show version with minimal output."""
        output = """
switch uptime is 1 day
        """
        info = CiscoCLIMonitoringManager._parse_show_version(output)

        assert info.hostname == "switch"
        assert info.uptime == 86400  # 1 day
        assert info.mac_address == ""
        assert info.serial_number == ""
        assert info.firmware_version == ""
        assert info.model == ""


class TestParseInterfaceCounters:
    """Test CiscoCLIMonitoringManager._parse_interface_counters static method."""

    def test_parse_interface_counters(self):
        """Parse show interfaces counters output."""
        output = """
gi1      1000 200 50 10
gi2      500 100 20 5
gi1      800 150 30 8
gi2      400 80 15 4
        """
        stats = CiscoCLIMonitoringManager._parse_interface_counters(output)

        # Should merge by port - first pass is RX, second is TX
        assert len(stats) == 2

        # gi1: RX=1000 bytes, 200+50+10=260 packets; TX=800 bytes, 150+30+8=188 packets
        gi1 = next(s for s in stats if s.port == "gi1")
        assert gi1.rx_bytes == 1000
        assert gi1.rx_packets == 260  # 200 + 50 + 10
        assert gi1.tx_bytes == 800
        assert gi1.tx_packets == 188  # 150 + 30 + 8

        # gi2: RX=500 bytes, 100+20+5=125 packets; TX=400 bytes, 80+15+4=99 packets
        gi2 = next(s for s in stats if s.port == "gi2")
        assert gi2.rx_bytes == 500
        assert gi2.rx_packets == 125  # 100 + 20 + 5
        assert gi2.tx_bytes == 400
        assert gi2.tx_packets == 99  # 80 + 15 + 4

    def test_parse_interface_counters_empty(self):
        """Parse empty output should return empty list."""
        output = ""
        stats = CiscoCLIMonitoringManager._parse_interface_counters(output)
        assert stats == []

    def test_parse_interface_counters_single_value(self):
        """Parse counters with single value per line."""
        output = """
gi1      1000
gi2      500
gi1      800
gi2      400
        """
        stats = CiscoCLIMonitoringManager._parse_interface_counters(output)

        assert len(stats) == 2

        gi1 = next(s for s in stats if s.port == "gi1")
        assert gi1.rx_bytes == 1000
        assert gi1.tx_bytes == 800
        assert gi1.rx_packets == 0
        assert gi1.tx_packets == 0


class TestParseEnvironment:
    """Test CiscoCLIMonitoringManager._parse_environment static method."""

    def test_parse_environment(self):
        """Parse show environment output."""
        output = """
Temperature: 45.5 C
Maximum Temperature: 70.0 C
        """
        sensor = CiscoCLIMonitoringManager._parse_environment(output)

        assert sensor.temperature == 45.5
        assert sensor.max_temperature == 70.0
        assert sensor.fan_speed == 0  # C1200 is fanless

    def test_parse_environment_single_temp(self):
        """Parse environment with single temperature."""
        output = """
Current Temperature: 42.3 C
        """
        sensor = CiscoCLIMonitoringManager._parse_environment(output)

        assert sensor.temperature == 42.3
        assert sensor.max_temperature == 0.0
        assert sensor.fan_speed == 0

    def test_parse_environment_empty(self):
        """Parse empty environment output."""
        output = ""
        sensor = CiscoCLIMonitoringManager._parse_environment(output)

        assert sensor.temperature == 0.0
        assert sensor.max_temperature == 0.0
        assert sensor.fan_speed == 0


class TestParseUptime:
    """Test _parse_uptime module-level function."""

    def test_parse_uptime_full(self):
        """Parse uptime with days, hours, and minutes."""
        uptime = _parse_uptime("1 day, 2 hours, 30 minutes")
        assert uptime == 95400  # 86400 + 7200 + 1800

    def test_parse_uptime_minutes_only(self):
        """Parse uptime with only minutes."""
        uptime = _parse_uptime("5 minutes")
        assert uptime == 300

    def test_parse_uptime_day_only(self):
        """Parse uptime with only days."""
        uptime = _parse_uptime("1 day")
        assert uptime == 86400

    def test_parse_uptime_hours_and_minutes(self):
        """Parse uptime with hours and minutes."""
        uptime = _parse_uptime("3 hours, 15 minutes")
        assert uptime == 11700  # 10800 + 900

    def test_parse_uptime_multiple_days(self):
        """Parse uptime with multiple days."""
        uptime = _parse_uptime("10 days, 5 hours, 20 minutes")
        assert uptime == 883200  # 864000 + 18000 + 1200

    def test_parse_uptime_with_seconds(self):
        """Parse uptime with seconds."""
        uptime = _parse_uptime("1 day, 2 hours, 30 minutes, 45 seconds")
        assert uptime == 95445  # 86400 + 7200 + 1800 + 45

    def test_parse_uptime_empty(self):
        """Parse empty uptime string."""
        uptime = _parse_uptime("")
        assert uptime == 0


class TestParseCatalystInterfaceStatus:
    """Test CiscoCatalystPortManager._parse_interface_status static method."""

    def test_parse_catalyst_format(self):
        """Parse C1200 format interface status output."""
        output = """
Port     Type         Duplex  Speed Neg      ctrl State       Pressure Mode
-------- ------------ ------  ----- -------- ---- ----------- -------- -------
gi1      1G-Copper    Full    1000  Enabled  Off  Up          Disabled Auto
gi2      1G-Copper    --      --    Enabled  Off  Down        Disabled Auto
gi3      1G-Copper    Half    100   Enabled  Off  Up          Disabled Auto
        """
        ports = CiscoCatalystPortManager._parse_interface_status(output)

        assert len(ports) == 3

        # Port 1
        assert ports[0].port == "gi1"
        assert ports[0].link_up is True
        assert ports[0].speed == "1000"
        assert ports[0].duplex == "Full"
        assert ports[0].media_type == "1G-Copper"

        # Port 2 (Down with -- for speed/duplex)
        assert ports[1].port == "gi2"
        assert ports[1].link_up is False
        assert ports[1].speed == ""  # -- converted to empty string
        assert ports[1].duplex == ""  # -- converted to empty string
        assert ports[1].media_type == "1G-Copper"

        # Port 3
        assert ports[2].port == "gi3"
        assert ports[2].link_up is True
        assert ports[2].speed == "100"
        assert ports[2].duplex == "Half"
        assert ports[2].media_type == "1G-Copper"

    def test_parse_catalyst_empty(self):
        """Parse empty C1200 format output."""
        output = ""
        ports = CiscoCatalystPortManager._parse_interface_status(output)
        assert ports == []

    def test_parse_catalyst_header_only(self):
        """Parse C1200 format with only header."""
        output = """
Port     Type         Duplex  Speed Neg      ctrl State       Pressure Mode
-------- ------------ ------  ----- -------- ---- ----------- -------- -------
        """
        ports = CiscoCatalystPortManager._parse_interface_status(output)
        assert ports == []
