"""Orchestrator CLI — dispatches to sub-CLIs.

Sub-commands:
  switchctrl  Multi-vendor switch management (monitor, VLAN, port config)
  discover    Network topology discovery (ARP, DNS, SNMP, LLDP, traceroute)
  vlan-dump   SNMP VLAN-port dump for Netgear switches

Examples:
  networkmgmt switchctrl --vendor cisco --host 192.168.1.254 \\
      --username admin --password <PW> --enable-password <EN> monitor

  networkmgmt discover -i eth0 --nmap -o topology.md

  networkmgmt vlan-dump 192.168.101.38 public --markdown
"""

from __future__ import annotations

import os
import sys

from tabulate import tabulate

from networkmgmt import __version__, configure_logging
from networkmgmt import glogger

COMMANDS = {
    "switchctrl": ("networkmgmt.switchctrl.cli", "Multi-vendor switch management"),
    "discover": ("networkmgmt.discovery.cli", "Network topology discovery"),
    "vlan-dump": ("networkmgmt.snmp_vlan_dump.cli", "SNMP VLAN-port dump"),
}


def _print_usage() -> None:
    print("usage: networkmgmt <command> [options]\n")
    print("Available commands:")
    for cmd, (_, desc) in COMMANDS.items():
        print(f"  {cmd:14s}  {desc}")
    print("\nRun 'networkmgmt <command> --help' for command-specific options.")


def _print_startup_banner() -> None:
    startup_rows = [
        ["version", __version__],
        ["github", "https://github.com/vroomfondel/networkmgmt"],
        ["pypi", "https://pypi.org/project/networkmgmt"],
        ["Docker Hub", "https://hub.docker.com/r/xomoxcc/networkmgmt"],
    ]

    for var in ("GITHUB_REF", "GITHUB_SHA", "BUILDTIME"):
        val = os.environ.get(var)
        if val and not val.endswith("_is_undefined"):
            startup_rows.append([var, val])

    table_str = tabulate(startup_rows, tablefmt="mixed_grid")
    lines = table_str.split("\n")
    table_width = len(lines[0])
    title = "networkmgmt starting up"
    title_border = "┍" + "━" * (table_width - 2) + "┑"
    title_row = "│ " + title.center(table_width - 4) + " │"
    separator = lines[0].replace("┍", "┝").replace("┑", "┥").replace("┯", "┿")

    glogger.opt(raw=True).info(
        "\n{}\n", title_border + "\n" + title_row + "\n" + separator + "\n" + "\n".join(lines[1:])
    )


def main() -> None:
    """Main entry point — dispatch to sub-CLI."""
    configure_logging()
    _print_startup_banner()

    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        _print_usage()
        sys.exit(0 if len(sys.argv) >= 2 else 1)

    command = sys.argv[1]
    if command not in COMMANDS:
        print(f"networkmgmt: unknown command '{command}'\n", file=sys.stderr)
        _print_usage()
        sys.exit(1)

    module_path, _ = COMMANDS[command]

    # Import and call the sub-CLI's main(), passing remaining args
    from importlib import import_module

    module = import_module(module_path)
    module.main(sys.argv[2:])


if __name__ == "__main__":
    main()
