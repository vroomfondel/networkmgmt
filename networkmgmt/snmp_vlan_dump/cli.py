"""CLI entry point for VLAN dump — standalone-capable."""

from __future__ import annotations

import argparse
import re
import sys

from loguru import logger

from networkmgmt.snmp_vlan_dump.mermaid import VlanMermaidGenerator
from networkmgmt.snmp_vlan_dump.snmp import HAS_PYSNMP


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """Build argparse parser for VLAN dump."""
    parser = argparse.ArgumentParser(
        description="Dump VLAN-port assignments from Netgear switches via SNMPv2c.",
    )
    parser.add_argument(
        "ip",
        nargs="?",
        default="192.168.101.38",
        help="Switch IP address (default: 192.168.101.38)",
    )
    parser.add_argument(
        "community",
        nargs="?",
        default="public",
        help="SNMP community string (default: public)",
    )
    parser.add_argument(
        "-m",
        "--markdown",
        action="store_true",
        help="Write output as Markdown file with Mermaid diagram",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Output filename for --markdown (default: vlan_dump_<sysname>.md)",
    )
    parser.add_argument(
        "-d",
        "--diagram-style",
        choices=VlanMermaidGenerator.DIAGRAM_STYLES,
        default="aggregated",
        help="Mermaid diagram style (default: aggregated)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose logging",
    )
    return parser.parse_args(args)


def main(args: list[str] | None = None) -> None:
    """Main entry point for VLAN dump CLI."""
    parsed = parse_args(args)

    if not parsed.verbose:
        logger.remove()
        logger.add(sys.stderr, level="INFO")

    if not HAS_PYSNMP:
        logger.error("pysnmp is required (pip install pysnmp)")
        sys.exit(1)

    from networkmgmt.snmp_vlan_dump.collector import VlanDataCollector
    from networkmgmt.snmp_vlan_dump.formatters import MarkdownFormatter, TerminalFormatter

    collector = VlanDataCollector(parsed.ip, parsed.community)
    data = collector.collect()

    if parsed.markdown:
        md_formatter = MarkdownFormatter(data, diagram_style=parsed.diagram_style)
        output = md_formatter.format()

        if parsed.output:
            output_path = parsed.output
        else:
            safe_name = re.sub(r"[^\w\-.]", "_", data.sys_name)
            output_path = f"vlan_dump_{safe_name}.md"

        with open(output_path, "w") as f:
            f.write(output + "\n")
        logger.info(f"Markdown written to {output_path}")
    else:
        term_formatter = TerminalFormatter(data)
        print(term_formatter.format())
