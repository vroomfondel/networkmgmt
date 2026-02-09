"""Tests for networkmgmt/discovery/mermaid.py"""

import pytest

from networkmgmt.discovery.mermaid import MermaidGenerator
from networkmgmt.discovery.models import (
    DiscoveredHost,
    NetworkInterface,
    NetworkTopology,
    SubnetScan,
)


@pytest.fixture
def minimal_topology():
    """Fixture providing minimal NetworkTopology for testing."""
    iface = NetworkInterface(name="eth0", ip="192.168.1.1", netmask="255.255.255.0")
    gateway = DiscoveredHost(ip="192.168.1.254", is_gateway=True, hostname="router")
    return NetworkTopology(
        local_interface=iface,
        gateway=gateway,
        local_hosts=[gateway, DiscoveredHost(ip="192.168.1.10", hostname="host1")],
    )


class TestSanitize:
    """Tests for MermaidGenerator._sanitize method."""

    def test_escapes_quotes(self, minimal_topology):
        """Test escaping double quotes."""
        gen = MermaidGenerator(minimal_topology)

        result = gen._sanitize('hello "world"')

        assert result == "hello 'world'"

    def test_escapes_html_tags(self, minimal_topology):
        """Test escaping HTML tags."""
        gen = MermaidGenerator(minimal_topology)

        result = gen._sanitize("<script>")

        assert result == "&lt;script&gt;"


class TestHostLabel:
    """Tests for MermaidGenerator._host_label method."""

    def test_hostname_and_vendor_included(self, minimal_topology):
        """Test label includes hostname and vendor."""
        gen = MermaidGenerator(minimal_topology)
        host = DiscoveredHost(ip="192.168.1.10", hostname="server1", vendor="Dell Inc.")

        result = gen._host_label(host)

        assert "192.168.1.10" in result
        assert "server1" in result
        assert "Dell" in result  # abbreviated

    def test_ip_only_when_no_hostname(self, minimal_topology):
        """Test label with IP only when no hostname."""
        gen = MermaidGenerator(minimal_topology)
        host = DiscoveredHost(ip="192.168.1.10")

        result = gen._host_label(host)

        assert result.startswith("192.168.1.10")
        assert "server" not in result.lower()

    def test_services_included_up_to_three(self, minimal_topology):
        """Test services included up to 3."""
        gen = MermaidGenerator(minimal_topology)
        host = DiscoveredHost(
            ip="192.168.1.10",
            hostname="server1",
            services=["ssh", "http", "https", "mysql"],
        )

        result = gen._host_label(host, compact=False)

        assert "ssh" in result
        assert "http" in result
        assert "https" in result
        assert "mysql" not in result  # Only first 3

    def test_compact_mode_omits_vendor_when_hostname_present(self, minimal_topology):
        """Test compact mode omits vendor when hostname is present."""
        gen = MermaidGenerator(minimal_topology)
        host = DiscoveredHost(ip="192.168.1.10", hostname="server1", vendor="Dell Inc.")

        result = gen._host_label(host, compact=True)

        assert "192.168.1.10" in result
        assert "server1" in result
        assert "Dell" not in result


class TestResolveStyle:
    """Tests for MermaidGenerator._resolve_style method."""

    def test_auto_with_single_subnet_no_hierarchy_returns_flat(self):
        """Test auto mode with single subnet and no hierarchy returns flat."""
        iface = NetworkInterface(name="eth0", ip="192.168.1.1", netmask="255.255.255.0")
        topology = NetworkTopology(local_interface=iface, topology_tree={})

        gen = MermaidGenerator(topology, diagram_style="auto")

        assert gen._resolve_style() == "flat"

    def test_auto_with_multi_subnet_returns_categorized(self):
        """Test auto mode with multiple subnets returns categorized."""
        iface1 = NetworkInterface(name="eth0", ip="192.168.1.1", netmask="255.255.255.0")
        iface2 = NetworkInterface(name="eth1", ip="192.168.2.1", netmask="255.255.255.0")
        topology = NetworkTopology(
            local_interface=iface1,
            subnets=[SubnetScan(interface=iface1), SubnetScan(interface=iface2)],
        )

        gen = MermaidGenerator(topology, diagram_style="auto")

        assert gen._resolve_style() == "categorized"

    def test_auto_with_hierarchy_returns_hierarchical(self):
        """Test auto mode with hierarchy returns hierarchical."""
        iface = NetworkInterface(name="eth0", ip="192.168.1.1", netmask="255.255.255.0")
        gateway = DiscoveredHost(ip="192.168.1.254", is_gateway=True)
        topology = NetworkTopology(
            local_interface=iface,
            gateway=gateway,
            topology_tree={"192.168.1.10": "192.168.1.5"},  # Non-trivial hierarchy
        )

        gen = MermaidGenerator(topology, diagram_style="auto")

        assert gen._resolve_style() == "hierarchical"

    def test_explicit_flat_returns_flat(self, minimal_topology):
        """Test explicit flat mode returns flat."""
        gen = MermaidGenerator(minimal_topology, diagram_style="flat")

        assert gen._resolve_style() == "flat"


class TestGenerateSmokeTests:
    """Smoke tests for MermaidGenerator.generate method."""

    def test_flat_style_generates_mermaid(self, minimal_topology):
        """Test flat style generates mermaid output."""
        gen = MermaidGenerator(minimal_topology, diagram_style="flat")

        result = gen.generate()

        assert result.startswith("```mermaid")
        assert "flowchart" in result
        assert result.endswith("```")

    def test_categorized_style_includes_subgraph(self):
        """Test categorized style includes subgraph."""
        iface = NetworkInterface(name="eth0", ip="192.168.1.1", netmask="255.255.255.0")
        gateway = DiscoveredHost(ip="192.168.1.254", is_gateway=True)
        hosts = [
            gateway,
            DiscoveredHost(ip="192.168.1.10", hostname="server1", category="Servers"),
            DiscoveredHost(ip="192.168.1.11", hostname="server2", category="Servers"),
        ]
        topology = NetworkTopology(local_interface=iface, gateway=gateway, local_hosts=hosts)

        gen = MermaidGenerator(topology, diagram_style="categorized")
        result = gen.generate()

        assert "subgraph" in result
        assert "Servers" in result

    def test_hierarchical_style_includes_infrastructure_subgraphs(self):
        """Test hierarchical style includes infrastructure subgraphs."""
        iface = NetworkInterface(name="eth0", ip="192.168.1.1", netmask="255.255.255.0")
        gateway = DiscoveredHost(ip="192.168.1.254", is_gateway=True)
        switch = DiscoveredHost(ip="192.168.1.5", hostname="switch1", is_infrastructure=True)
        host = DiscoveredHost(ip="192.168.1.10", hostname="host1")
        topology = NetworkTopology(
            local_interface=iface,
            gateway=gateway,
            local_hosts=[gateway, switch, host],
            topology_tree={"192.168.1.10": "192.168.1.5", "192.168.1.5": "192.168.1.254"},
        )

        gen = MermaidGenerator(topology, diagram_style="hierarchical")
        result = gen.generate()

        assert "subgraph" in result
        # Infrastructure nodes create subgraphs
        assert "192.168.1.5" in result or "switch1" in result

    def test_output_wrapped_in_code_block(self, minimal_topology):
        """Test output is wrapped in mermaid code block."""
        gen = MermaidGenerator(minimal_topology)

        result = gen.generate()

        assert result.startswith("```mermaid")
        assert result.endswith("```")

    def test_elk_renderer_option(self, minimal_topology):
        """Test ELK renderer option in output."""
        gen = MermaidGenerator(minimal_topology, elk=True)

        result = gen.generate()

        assert "defaultRenderer" in result or "elk" in result
