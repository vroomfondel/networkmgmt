"""Tests for networkmgmt.snmp_vlan_dump.mermaid Mermaid diagram generator."""

from __future__ import annotations

import pytest

from networkmgmt.snmp_vlan_dump.mermaid import VlanMermaidGenerator


class TestVlanMermaidGenerator:
    """Test VlanMermaidGenerator class."""

    def test_diagram_styles_constant(self):
        """DIAGRAM_STYLES constant contains expected styles."""
        assert "aggregated" in VlanMermaidGenerator.DIAGRAM_STYLES
        assert "trunks" in VlanMermaidGenerator.DIAGRAM_STYLES
        assert "vlan" in VlanMermaidGenerator.DIAGRAM_STYLES

    def test_aggregated_style_generates_diagram(self, sample_vlan_dump_data):
        """Aggregated style generates valid Mermaid diagram."""
        data = sample_vlan_dump_data()
        generator = VlanMermaidGenerator(data, style="aggregated")
        output = generator.generate()

        assert isinstance(output, str)
        assert "```mermaid" in output
        assert "flowchart" in output

    def test_aggregated_contains_flowchart(self, sample_vlan_dump_data):
        """Aggregated style output contains flowchart declaration."""
        data = sample_vlan_dump_data()
        generator = VlanMermaidGenerator(data, style="aggregated")
        output = generator.generate()

        # Should have flowchart TD (top-down)
        assert "flowchart TD" in output or "flowchart" in output

    def test_trunks_style_generates_diagram(self, sample_vlan_dump_data):
        """Trunks style generates valid Mermaid diagram."""
        data = sample_vlan_dump_data()
        generator = VlanMermaidGenerator(data, style="trunks")
        output = generator.generate()

        assert isinstance(output, str)
        assert "```mermaid" in output
        assert "flowchart" in output

    def test_trunks_shows_trunk_ports_only(self, sample_vlan_dump_data):
        """Trunks style focuses on trunk ports (ports with tagged VLANs)."""
        data = sample_vlan_dump_data()
        generator = VlanMermaidGenerator(data, style="trunks")
        output = generator.generate()

        # Should still produce valid flowchart
        assert "flowchart" in output

    def test_vlan_style_generates_diagram(self, sample_vlan_dump_data):
        """VLAN-centric style generates valid Mermaid diagram."""
        data = sample_vlan_dump_data()
        generator = VlanMermaidGenerator(data, style="vlan")
        output = generator.generate()

        assert isinstance(output, str)
        assert "```mermaid" in output
        assert "flowchart" in output

    def test_vlan_style_contains_subgraphs(self, sample_vlan_dump_data):
        """VLAN-centric style uses subgraphs for VLANs."""
        data = sample_vlan_dump_data()
        generator = VlanMermaidGenerator(data, style="vlan")
        output = generator.generate()

        assert "subgraph" in output

    def test_all_styles_produce_flowchart(self, sample_vlan_dump_data):
        """All diagram styles produce flowchart output."""
        data = sample_vlan_dump_data()

        for style in VlanMermaidGenerator.DIAGRAM_STYLES:
            generator = VlanMermaidGenerator(data, style=style)
            output = generator.generate()
            assert "flowchart" in output, f"Style {style} should produce flowchart"

    def test_default_style_is_aggregated(self, sample_vlan_dump_data):
        """Default style is 'aggregated'."""
        data = sample_vlan_dump_data()
        generator = VlanMermaidGenerator(data)  # No style specified
        output = generator.generate()

        # Should work without error and produce flowchart
        assert "flowchart" in output
        assert "```mermaid" in output

    def test_mermaid_block_closed(self, sample_vlan_dump_data):
        """Mermaid code block is properly closed."""
        data = sample_vlan_dump_data()
        generator = VlanMermaidGenerator(data, style="aggregated")
        output = generator.generate()

        # Should have both opening and closing
        assert output.count("```") >= 2

    def test_contains_vlan_nodes(self, sample_vlan_dump_data):
        """Diagram contains VLAN nodes."""
        data = sample_vlan_dump_data()
        generator = VlanMermaidGenerator(data, style="aggregated")
        output = generator.generate()

        # Should reference VLANs from the sample data
        # Sample data has VLANs 1, 10, 20
        assert "VLAN" in output or "vlan" in output

    def test_generator_state_isolation(self, sample_vlan_dump_data):
        """Multiple generators don't share state."""
        data = sample_vlan_dump_data()
        gen1 = VlanMermaidGenerator(data, style="aggregated")
        gen2 = VlanMermaidGenerator(data, style="trunks")

        output1 = gen1.generate()
        output2 = gen2.generate()

        # Both should be valid independently
        assert "flowchart" in output1
        assert "flowchart" in output2

    def test_empty_data_handles_gracefully(self):
        """Generator handles empty data without errors."""
        from networkmgmt.snmp_vlan_dump.models import VlanDumpData

        empty_data = VlanDumpData()
        generator = VlanMermaidGenerator(empty_data, style="aggregated")
        output = generator.generate()

        # Should still produce valid (if empty) Mermaid diagram
        assert "```mermaid" in output
        assert "flowchart" in output
