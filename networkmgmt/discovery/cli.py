"""CLI entry point for network topology discovery — standalone-capable."""

from __future__ import annotations

import argparse
import ipaddress
import os
import sys
from pathlib import Path

from loguru import logger

from networkmgmt.discovery.mermaid import MermaidGenerator
from networkmgmt.discovery.scanner import NetworkTopologyScanner
from networkmgmt.discovery.snmp import HAS_PYSNMP, SwitchSpec
from networkmgmt.snmp import AUTH_PROTOCOLS, PRIV_PROTOCOLS, SnmpCredentials


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    """Build argparse parser for network topology discovery."""
    parser = argparse.ArgumentParser(
        description="Network topology discovery with Mermaid diagram output",
    )
    parser.add_argument(
        "-i",
        "--interface",
        help="Network interface(s), comma-separated for multi-subnet (default: auto-detect)",
    )
    parser.add_argument(
        "-t",
        "--targets",
        help="Traceroute targets: comma-separated IPs, CIDR (192.168.1.0/24), or range (192.168.1.1-254)",
    )
    parser.add_argument(
        "--nmap",
        action="store_true",
        help="Enable nmap service detection",
    )
    parser.add_argument(
        "--top-ports",
        type=int,
        default=100,
        help="nmap top ports to scan (default: 100)",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Output file (default: stdout)",
    )
    parser.add_argument(
        "--format",
        choices=["mermaid", "json"],
        default="mermaid",
        help="Output format (default: mermaid)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=10,
        help="Scan timeout in seconds (default: 10)",
    )
    parser.add_argument(
        "--max-hops",
        type=int,
        default=30,
        help="Max traceroute hops (default: 30)",
    )
    parser.add_argument(
        "--trace-local",
        action="store_true",
        help="Run tracepath to all discovered LAN hosts to detect switch hierarchy",
    )
    parser.add_argument(
        "--switches",
        help="SNMP L2 discovery: comma-separated switch IPs with optional community "
        "(e.g. 192.0.2.38:public,192.0.2.19). Default community: public; "
        "entries without community use the --snmp-user credentials when given",
    )
    snmp_group = parser.add_argument_group("SNMPv3 for --switches entries without community")
    snmp_group.add_argument("--snmp-user", help="SNMPv3 user name")
    snmp_group.add_argument(
        "--snmp-auth-key",
        default=os.environ.get("SNMP_AUTH_KEY"),
        help="SNMPv3 authentication passphrase (default: $SNMP_AUTH_KEY)",
    )
    snmp_group.add_argument(
        "--snmp-auth-protocol",
        choices=AUTH_PROTOCOLS,
        default="sha512",
        help="SNMPv3 authentication protocol (default: sha512)",
    )
    snmp_group.add_argument(
        "--snmp-priv-key",
        default=os.environ.get("SNMP_PRIV_KEY"),
        help="SNMPv3 privacy passphrase; unset means authNoPriv (default: $SNMP_PRIV_KEY)",
    )
    snmp_group.add_argument(
        "--snmp-priv-protocol",
        choices=PRIV_PROTOCOLS,
        default="aes128",
        help="SNMPv3 privacy protocol (default: aes128)",
    )
    snmp_group.add_argument(
        "--snmp-timeout", type=float, default=5.0, help="SNMP timeout per request in s (default: 5)"
    )
    snmp_group.add_argument("--snmp-retries", type=int, default=1, help="SNMP retries per request (default: 1)")
    parser.add_argument(
        "--lldp-collect",
        metavar="DIR",
        help="SSH into discovered hosts, run lldpctl -f json, write results to DIR/<ip>.json",
    )
    parser.add_argument(
        "--lldp-dir",
        metavar="DIR",
        help="Read LLDP data from DIR/<ip>.json files for L2 topology (no SSH needed)",
    )
    parser.add_argument(
        "--topology",
        help="Manual L2 topology: comma-separated HOST_IP:SWITCH_IP:PORT_NAME entries "
        "(e.g. 192.0.2.50:192.0.2.19:g3,192.0.2.51:192.0.2.19:g4)",
    )
    parser.add_argument(
        "-d",
        "--diagram-style",
        choices=MermaidGenerator.DIAGRAM_STYLES,
        default="auto",
        help="Diagram style: auto (default), flat, categorized, hierarchical",
    )
    parser.add_argument(
        "--direction",
        choices=["LR", "TD"],
        default="",
        help="Mermaid flowchart direction (default: auto — TD for 40+ hosts, LR otherwise)",
    )
    parser.add_argument(
        "--elk",
        action="store_true",
        help="Use ELK layout engine (better node placement for large diagrams)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Verbose logging",
    )
    return parser.parse_args(args)


def _expand_targets(targets_str: str) -> list[str]:
    """Expand comma-separated targets (IPs, CIDR, ranges) into a flat list."""
    result: list[str] = []
    for part in targets_str.split(","):
        part = part.strip()
        if not part:
            continue
        if "/" in part:
            # CIDR notation: 192.0.2.0/24
            try:
                net = ipaddress.IPv4Network(part, strict=False)
                result.extend(str(ip) for ip in net.hosts())
            except ValueError:
                logger.warning(f"Invalid CIDR target: {part}")
        elif "-" in part.rsplit(".", 1)[-1]:
            # Last-octet range: 192.0.2.1-254
            prefix, last = part.rsplit(".", 1)
            try:
                start_s, end_s = last.split("-", 1)
                start, end = int(start_s), int(end_s)
                result.extend(f"{prefix}.{i}" for i in range(start, end + 1))
            except ValueError, TypeError:
                logger.warning(f"Invalid range target: {part}")
        else:
            result.append(part)
    return result


def _credentials_from_args(parsed: argparse.Namespace) -> SnmpCredentials | None:
    """Build the global SNMPv3 credentials from --snmp-* options; None when --snmp-user is not set."""
    if not parsed.snmp_user:
        return None
    if not parsed.snmp_auth_key:
        raise SystemExit("error: --snmp-user requires --snmp-auth-key (or $SNMP_AUTH_KEY)")
    return SnmpCredentials.v3(
        parsed.snmp_user,
        parsed.snmp_auth_key,
        auth_protocol=parsed.snmp_auth_protocol,
        priv_key=parsed.snmp_priv_key or None,
        priv_protocol=parsed.snmp_priv_protocol,
    )


def _parse_switches(switches_str: str, default_credentials: SnmpCredentials | None = None) -> list[SwitchSpec]:
    """Parse --switches argument: IP[:COMMUNITY],... (entries without community use ``default_credentials``)."""
    switches: list[SwitchSpec] = []
    for part in switches_str.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            ip, community = part.split(":", 1)
            switches.append((ip, community))
        else:
            switches.append((part, default_credentials or "public"))
    return switches


def _parse_topology(topology_str: str) -> list[tuple[str, str, str]]:
    """Parse --topology argument: HOST_IP:SWITCH_IP:PORT_NAME,..."""
    entries: list[tuple[str, str, str]] = []
    for entry in topology_str.split(","):
        entry = entry.strip()
        if not entry:
            continue
        parts = entry.split(":", 2)
        if len(parts) != 3:
            logger.warning(f"Invalid --topology entry (need HOST:SWITCH:PORT): {entry}")
            continue
        entries.append((parts[0], parts[1], parts[2]))
    return entries


def main(args: list[str] | None = None) -> None:
    """Main entry point for discovery CLI."""
    parsed = parse_args(args)

    if not parsed.verbose:
        logger.remove()
        logger.add(sys.stderr, level="INFO")

    traceroute_targets: list[str] = []
    if parsed.targets:
        traceroute_targets = _expand_targets(parsed.targets)

    interfaces = [i.strip() for i in parsed.interface.split(",") if i.strip()] if parsed.interface else []

    # Parse --switches
    switches: list[SwitchSpec] = []
    if parsed.switches:
        if not HAS_PYSNMP:
            logger.error("pysnmp is required for --switches (pip install pysnmp)")
            sys.exit(1)
        switches = _parse_switches(parsed.switches, _credentials_from_args(parsed))

    scanner = NetworkTopologyScanner(
        interfaces=interfaces,
        use_nmap=parsed.nmap,
        top_ports=parsed.top_ports,
        timeout=parsed.timeout,
        max_hops=parsed.max_hops,
    )

    # Parse --topology
    manual_topology: list[tuple[str, str, str]] = []
    if parsed.topology:
        manual_topology = _parse_topology(parsed.topology)

    topology = scanner.run_discovery(
        traceroute_targets=traceroute_targets,
        trace_local=parsed.trace_local,
        switches=switches,
        snmp_timeout=parsed.snmp_timeout,
        snmp_retries=parsed.snmp_retries,
        lldp_collect_dir=Path(parsed.lldp_collect) if parsed.lldp_collect else None,
        lldp_dir=Path(parsed.lldp_dir) if parsed.lldp_dir else None,
        manual_topology=manual_topology or None,
    )

    if parsed.format == "json":
        output = topology.model_dump_json(indent=2)
    else:
        generator = MermaidGenerator(
            topology,
            direction=parsed.direction,
            diagram_style=parsed.diagram_style,
            elk=parsed.elk,
        )
        output = generator.generate()

    if parsed.output:
        Path(parsed.output).write_text(output + "\n")
        logger.info(f"Output written to {parsed.output}")
    else:
        print(output)
