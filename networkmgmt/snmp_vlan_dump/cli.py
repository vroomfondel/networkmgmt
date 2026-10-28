"""CLI entry point for VLAN dump — standalone-capable."""

from __future__ import annotations

import argparse
import os
import re
import sys

from loguru import logger

from networkmgmt.snmp import AUTH_PROTOCOLS, PRIV_PROTOCOLS, SnmpCredentials, SnmpError
from networkmgmt.snmp_vlan_dump.mermaid import VlanMermaidGenerator
from networkmgmt.snmp_vlan_dump.snmp import HAS_PYSNMP


def add_snmp_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the SNMPv3 and transport options to ``parser``."""
    group = parser.add_argument_group("SNMPv3 (used instead of the community when --snmp-user is set)")
    group.add_argument("--snmp-user", help="SNMPv3 user name")
    group.add_argument(
        "--snmp-auth-key",
        default=os.environ.get("SNMP_AUTH_KEY"),
        help="SNMPv3 authentication passphrase (default: $SNMP_AUTH_KEY)",
    )
    group.add_argument(
        "--snmp-auth-protocol",
        choices=AUTH_PROTOCOLS,
        default="sha512",
        help="SNMPv3 authentication protocol (default: sha512)",
    )
    group.add_argument(
        "--snmp-priv-key",
        default=os.environ.get("SNMP_PRIV_KEY"),
        help="SNMPv3 privacy passphrase; unset means authNoPriv (default: $SNMP_PRIV_KEY)",
    )
    group.add_argument(
        "--snmp-priv-protocol",
        choices=PRIV_PROTOCOLS,
        default="aes128",
        help="SNMPv3 privacy protocol (default: aes128)",
    )
    group.add_argument("--snmp-timeout", type=float, default=5.0, help="SNMP timeout per request in s (default: 5)")
    group.add_argument("--snmp-retries", type=int, default=1, help="SNMP retries per request (default: 1)")


def credentials_from_args(parser: argparse.ArgumentParser, parsed: argparse.Namespace) -> SnmpCredentials:
    """Build credentials from parsed arguments; v3 when ``--snmp-user`` is set, else the v2c community."""
    if not parsed.snmp_user:
        return SnmpCredentials.v2c(parsed.community)
    if not parsed.snmp_auth_key:
        parser.error("--snmp-user requires --snmp-auth-key (or $SNMP_AUTH_KEY)")
    return SnmpCredentials.v3(
        parsed.snmp_user,
        parsed.snmp_auth_key,
        auth_protocol=parsed.snmp_auth_protocol,
        priv_key=parsed.snmp_priv_key or None,
        priv_protocol=parsed.snmp_priv_protocol,
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser for VLAN dump."""
    parser = _build_parser()
    add_snmp_arguments(parser)
    return parser


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dump VLAN-port assignments from Netgear switches via SNMPv2c or SNMPv3.",
    )
    parser.add_argument("ip", help="Switch IP address or hostname")
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
    return parser


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments for VLAN dump."""
    return build_parser().parse_args(args)


def main(args: list[str] | None = None) -> None:
    """Main entry point for VLAN dump CLI."""
    parser = build_parser()
    parsed = parser.parse_args(args)
    credentials = credentials_from_args(parser, parsed)

    if not parsed.verbose:
        logger.remove()
        logger.add(sys.stderr, level="INFO")

    if not HAS_PYSNMP:
        logger.error("pysnmp is required (pip install pysnmp)")
        sys.exit(1)

    from networkmgmt.snmp_vlan_dump.collector import VlanDataCollector
    from networkmgmt.snmp_vlan_dump.formatters import MarkdownFormatter, TerminalFormatter

    collector = VlanDataCollector(
        parsed.ip, credentials=credentials, timeout=parsed.snmp_timeout, retries=parsed.snmp_retries
    )
    try:
        data = collector.collect()
    except SnmpError:
        sys.exit(1)

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
