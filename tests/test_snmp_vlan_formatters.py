"""Tests for networkmgmt.snmp_vlan_dump.formatters output formatters."""

from __future__ import annotations

import pytest

from networkmgmt.snmp_vlan_dump.formatters import MarkdownFormatter, TerminalFormatter


class TestTerminalFormatter:
    """Test TerminalFormatter."""

    def test_format_returns_string(self, sample_vlan_dump_data):
        """format() produces a string."""
        data = sample_vlan_dump_data()
        formatter = TerminalFormatter(data)
        output = formatter.format()
        assert isinstance(output, str)
        assert len(output) > 0

    def test_contains_system_info(self, sample_vlan_dump_data):
        """Output contains system information."""
        data = sample_vlan_dump_data()
        formatter = TerminalFormatter(data)
        output = formatter.format()
        assert "Switch:" in output
        assert "Name:" in output
        assert "Uptime:" in output
        assert data.sys_descr in output
        assert data.sys_name in output

    def test_contains_unit_table_headers(self, sample_vlan_dump_data):
        """Output contains per-unit table headers."""
        data = sample_vlan_dump_data()
        formatter = TerminalFormatter(data)
        output = formatter.format()
        assert "Port" in output
        assert "Link" in output
        assert "PVID" in output

    def test_contains_vlan_summary(self, sample_vlan_dump_data):
        """Output contains VLAN summary section."""
        data = sample_vlan_dump_data()
        formatter = TerminalFormatter(data)
        output = formatter.format()
        assert "VLAN-ZUSAMMENFASSUNG" in output

    def test_contains_vlan_names(self, sample_vlan_dump_data):
        """Output contains VLAN names from data."""
        data = sample_vlan_dump_data()
        formatter = TerminalFormatter(data)
        output = formatter.format()
        # Check for VLAN entries
        assert "VLAN 1" in output
        assert "VLAN 10" in output
        assert "VLAN 20" in output
        # Check for VLAN names
        assert "default" in output
        assert "servers" in output
        assert "clients" in output

    def test_contains_port_names(self, sample_vlan_dump_data):
        """Output contains friendly port names."""
        data = sample_vlan_dump_data()
        formatter = TerminalFormatter(data)
        output = formatter.format()
        # Should contain at least some port names
        assert "U1/g" in output

    def test_unit_summary_in_output(self, sample_vlan_dump_data):
        """Output contains unit summary (e.g., '8x1G + 0x10G')."""
        data = sample_vlan_dump_data()
        formatter = TerminalFormatter(data)
        output = formatter.format()
        # From fixture: 8-port unit without 10G
        assert "8x1G + 0x10G" in output


class TestMarkdownFormatter:
    """Test MarkdownFormatter."""

    def test_format_returns_string(self, sample_vlan_dump_data):
        """format() produces a string."""
        data = sample_vlan_dump_data()
        formatter = MarkdownFormatter(data)
        output = formatter.format()
        assert isinstance(output, str)
        assert len(output) > 0

    def test_contains_markdown_headings(self, sample_vlan_dump_data):
        """Output contains Markdown headings with #."""
        data = sample_vlan_dump_data()
        formatter = MarkdownFormatter(data)
        output = formatter.format()
        assert "# VLAN Dump:" in output
        assert "## Unit" in output
        assert "## VLAN Summary" in output

    def test_contains_markdown_tables(self, sample_vlan_dump_data):
        """Output contains Markdown table format with pipes."""
        data = sample_vlan_dump_data()
        formatter = MarkdownFormatter(data)
        output = formatter.format()
        # Markdown tables use | delimiters
        assert "|" in output
        # Check for table headers
        assert "| Port |" in output or "|Port|" in output.replace(" ", "")
        assert "| Link |" in output or "|Link|" in output.replace(" ", "")

    def test_contains_topology_section(self, sample_vlan_dump_data):
        """Output contains Topology section."""
        data = sample_vlan_dump_data()
        formatter = MarkdownFormatter(data)
        output = formatter.format()
        assert "## Topology" in output

    def test_contains_mermaid_diagram(self, sample_vlan_dump_data):
        """Output contains Mermaid diagram block."""
        data = sample_vlan_dump_data()
        formatter = MarkdownFormatter(data)
        output = formatter.format()
        assert "```mermaid" in output
        assert "flowchart" in output

    def test_diagram_style_parameter(self, sample_vlan_dump_data):
        """MarkdownFormatter accepts diagram_style parameter."""
        data = sample_vlan_dump_data()
        # Should not raise
        formatter = MarkdownFormatter(data, diagram_style="aggregated")
        output = formatter.format()
        assert "```mermaid" in output

    def test_trunks_style(self, sample_vlan_dump_data):
        """MarkdownFormatter supports 'trunks' diagram style."""
        data = sample_vlan_dump_data()
        formatter = MarkdownFormatter(data, diagram_style="trunks")
        output = formatter.format()
        assert "```mermaid" in output
        assert "flowchart" in output

    def test_vlan_style(self, sample_vlan_dump_data):
        """MarkdownFormatter supports 'vlan' diagram style."""
        data = sample_vlan_dump_data()
        formatter = MarkdownFormatter(data, diagram_style="vlan")
        output = formatter.format()
        assert "```mermaid" in output
        assert "flowchart" in output

    def test_contains_system_info_bullet_points(self, sample_vlan_dump_data):
        """Output contains system info as Markdown bullet points."""
        data = sample_vlan_dump_data()
        formatter = MarkdownFormatter(data)
        output = formatter.format()
        # Markdown uses - for bullet points
        assert "- **Switch:**" in output or "- Switch:" in output
        assert data.sys_name in output

    def test_contains_vlan_summary_section(self, sample_vlan_dump_data):
        """Output contains VLAN summary with names."""
        data = sample_vlan_dump_data()
        formatter = MarkdownFormatter(data)
        output = formatter.format()
        assert "**VLAN 1" in output or "VLAN 1" in output
        assert "default" in output
        assert "servers" in output
