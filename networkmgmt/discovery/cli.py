"""CLI entry point for network topology discovery — standalone-capable."""

from __future__ import annotations

import argparse
import ipaddress
import sys
from pathlib import Path

from loguru import logger

from networkmgmt.discovery.mermaid import MermaidGenerator
from networkmgmt.discovery.scanner import NetworkTopologyScanner
from networkmgmt.discovery.snmp import HAS_PYSNMP


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
        "(e.g. 192.168.101.38:public,192.168.101.19). Default community: public",
    )
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
        "(e.g. 192.168.101.50:192.168.101.19:g3,192.168.101.51:192.168.101.19:g4)",
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
            # CIDR notation: 192.168.101.0/24
            try:
                net = ipaddress.IPv4Network(part, strict=False)
                result.extend(str(ip) for ip in net.hosts())
            except ValueError:
                logger.warning(f"Invalid CIDR target: {part}")
        elif "-" in part.rsplit(".", 1)[-1]:
            # Last-octet range: 192.168.101.1-254
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


def _parse_switches(switches_str: str) -> list[tuple[str, str]]:
    """Parse --switches argument: IP[:COMMUNITY],..."""
    switches: list[tuple[str, str]] = []
    for part in switches_str.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" in part:
            ip, community = part.split(":", 1)
        else:
            ip, community = part, "public"
        switches.append((ip, community))
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
    switches: list[tuple[str, str]] = []
    if parsed.switches:
        if not HAS_PYSNMP:
            logger.error("pysnmp is required for --switches (pip install pysnmp)")
            sys.exit(1)
        switches = _parse_switches(parsed.switches)

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
