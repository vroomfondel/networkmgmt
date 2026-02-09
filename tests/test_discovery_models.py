"""Tests for networkmgmt/discovery/models.py"""

import pytest

from networkmgmt.discovery.models import (
    DeviceCategory,
    DiscoveredHost,
    L2TopologyEntry,
    NetworkInterface,
    NetworkTopology,
    SubnetScan,
    SwitchPortMapping,
    TracerouteHop,
    TraceroutePath,
)


class TestDeviceCategory:
    """Tests for DeviceCategory enum."""

    def test_has_all_eight_values(self):
        """Test DeviceCategory has all 8 expected values."""
        expected = {
            "INFRASTRUCTURE",
            "SERVER",
            "IOT",
            "PHONE",
            "MEDIA",
            "HOME_AUTOMATION",
            "COMPUTER",
            "OTHER",
        }
        actual = {e.name for e in DeviceCategory}
        assert actual == expected

    def test_is_str_subclass(self):
        """Test DeviceCategory is a str subclass."""
        assert isinstance(DeviceCategory.INFRASTRUCTURE, str)

    def test_enum_values(self):
        """Test enum values are correct."""
        assert DeviceCategory.INFRASTRUCTURE == "Infrastructure"
        assert DeviceCategory.SERVER == "Servers"
        assert DeviceCategory.IOT == "IoT / Smart"
        assert DeviceCategory.PHONE == "Phones / VoIP"
        assert DeviceCategory.MEDIA == "Media"
        assert DeviceCategory.HOME_AUTOMATION == "Home Automation"
        assert DeviceCategory.COMPUTER == "Computers / Printers"
        assert DeviceCategory.OTHER == "Other"


class TestModelDefaults:
    """Tests for model default values."""

    def test_network_interface_defaults(self):
        """Test NetworkInterface default values."""
        iface = NetworkInterface(name="eth0", ip="192.168.1.1", netmask="255.255.255.0")
        assert iface.mac == ""
        assert iface.is_default is False

    def test_discovered_host_defaults(self):
        """Test DiscoveredHost default values."""
        host = DiscoveredHost(ip="192.168.1.1")
        assert host.mac == ""
        assert host.hostname == ""
        assert host.vendor == ""
        assert host.services == []
        assert host.is_gateway is False
        assert host.is_infrastructure is False
        assert host.category == ""

    def test_traceroute_hop_defaults(self):
        """Test TracerouteHop default values."""
        hop = TracerouteHop(hop_number=1)
        assert hop.ip == ""
        assert hop.hostname == ""
        assert hop.rtt_ms == 0.0
        assert hop.is_timeout is False

    def test_traceroute_path_defaults(self):
        """Test TraceroutePath default values."""
        path = TraceroutePath(target="8.8.8.8")
        assert path.hops == []
        assert path.completed is False

    def test_switch_port_mapping_defaults(self):
        """Test SwitchPortMapping default values."""
        mapping = SwitchPortMapping(switch_ip="192.168.1.1", port_index=1)
        assert mapping.switch_name == ""
        assert mapping.port_name == ""

    def test_l2_topology_entry_defaults(self):
        """Test L2TopologyEntry default values."""
        switch = SwitchPortMapping(switch_ip="192.168.1.1", port_index=1)
        entry = L2TopologyEntry(host_ip="192.168.1.10", host_mac="aa:bb:cc:dd:ee:ff", switch=switch)
        assert entry.source == ""

    def test_subnet_scan_defaults(self):
        """Test SubnetScan default values."""
        iface = NetworkInterface(name="eth0", ip="192.168.1.1", netmask="255.255.255.0")
        scan = SubnetScan(interface=iface)
        assert scan.gateway is None
        assert scan.hosts == []
        assert scan.topology_tree == {}
        assert scan.l2_topology == []

    def test_network_topology_defaults(self):
        """Test NetworkTopology default values."""
        iface = NetworkInterface(name="eth0", ip="192.168.1.1", netmask="255.255.255.0")
        topology = NetworkTopology(local_interface=iface)
        assert topology.subnets == []
        assert topology.gateway is None
        assert topology.local_hosts == []
        assert topology.traceroute_paths == []
        assert topology.topology_tree == {}
        assert topology.l2_topology == []
        assert topology.timestamp == ""


class TestListFieldIsolation:
    """Tests for list field isolation (default_factory)."""

    def test_discovered_host_services_not_shared(self):
        """Test services list is not shared between instances."""
        host1 = DiscoveredHost(ip="192.168.1.1")
        host2 = DiscoveredHost(ip="192.168.1.2")

        host1.services.append("ssh")

        assert host1.services == ["ssh"]
        assert host2.services == []

    def test_traceroute_path_hops_not_shared(self):
        """Test hops list is not shared between instances."""
        path1 = TraceroutePath(target="8.8.8.8")
        path2 = TraceroutePath(target="1.1.1.1")

        path1.hops.append(TracerouteHop(hop_number=1, ip="192.168.1.1"))

        assert len(path1.hops) == 1
        assert len(path2.hops) == 0

    def test_subnet_scan_hosts_not_shared(self):
        """Test hosts list is not shared between instances."""
        iface1 = NetworkInterface(name="eth0", ip="192.168.1.1", netmask="255.255.255.0")
        iface2 = NetworkInterface(name="eth1", ip="192.168.2.1", netmask="255.255.255.0")

        scan1 = SubnetScan(interface=iface1)
        scan2 = SubnetScan(interface=iface2)

        scan1.hosts.append(DiscoveredHost(ip="192.168.1.10"))

        assert len(scan1.hosts) == 1
        assert len(scan2.hosts) == 0


class TestJsonRoundTrip:
    """Tests for JSON serialization round-trip."""

    def test_network_interface_json_round_trip(self):
        """Test NetworkInterface JSON round-trip."""
        iface = NetworkInterface(
            name="eth0",
            ip="192.168.1.1",
            netmask="255.255.255.0",
            mac="aa:bb:cc:dd:ee:ff",
            is_default=True,
        )

        json_data = iface.model_dump()
        restored = NetworkInterface.model_validate(json_data)

        assert restored == iface

    def test_discovered_host_json_round_trip(self):
        """Test DiscoveredHost JSON round-trip."""
        host = DiscoveredHost(
            ip="192.168.1.10",
            mac="aa:bb:cc:dd:ee:ff",
            hostname="server1",
            vendor="Dell",
            services=["ssh", "http"],
            is_gateway=False,
            is_infrastructure=True,
            category="Servers",
        )

        json_data = host.model_dump()
        restored = DiscoveredHost.model_validate(json_data)

        assert restored == host

    def test_traceroute_path_json_round_trip(self):
        """Test TraceroutePath JSON round-trip."""
        path = TraceroutePath(
            target="8.8.8.8",
            hops=[
                TracerouteHop(hop_number=1, ip="192.168.1.1", hostname="router", rtt_ms=1.5),
                TracerouteHop(hop_number=2, is_timeout=True),
            ],
            completed=True,
        )

        json_data = path.model_dump()
        restored = TraceroutePath.model_validate(json_data)

        assert restored == path

    def test_network_topology_json_round_trip(self):
        """Test NetworkTopology JSON round-trip."""
        iface = NetworkInterface(name="eth0", ip="192.168.1.1", netmask="255.255.255.0")
        gateway = DiscoveredHost(ip="192.168.1.254", is_gateway=True)

        topology = NetworkTopology(
            local_interface=iface,
            gateway=gateway,
            local_hosts=[gateway, DiscoveredHost(ip="192.168.1.10")],
            timestamp="2025-01-01T00:00:00Z",
        )

        json_data = topology.model_dump()
        restored = NetworkTopology.model_validate(json_data)

        assert restored == topology
