"""CLI entry point for multi-vendor switch management — standalone-capable.

Supported vendors:
  - cisco     Cisco Catalyst 1200 (C1200-8T-D) — SSH CLI only, no REST API
  - mikrotik  MikroTik RouterOS switches — REST API + SSH CLI
  - netgear   Netgear managed switches (stub, not yet implemented)
  - qnap      QNAP QSW managed switches — REST API + SSH CLI (Cisco-style)

Examples:
  # Cisco Catalyst 1200 — enable-password is required
  networkmgmt-switchctrl --vendor cisco --host 192.168.1.254 \\
      --username admin --password <PW> --enable-password <EN> monitor

  # QNAP QSW — enable-password auto-generated from serial if omitted
  networkmgmt-switchctrl --vendor qnap --host 192.168.1.1 --password <PW> monitor

  # VLAN management
  networkmgmt-switchctrl --vendor cisco --host 192.168.1.254 \\
      --username admin --password <PW> --enable-password <EN> \\
      vlan create 100 --name servers

  # Port configuration (Cisco: gi1-gi8, QNAP: GigabitEthernet1/0/1)
  networkmgmt-switchctrl --vendor cisco --host 192.168.1.254 \\
      --username admin --password <PW> --enable-password <EN> \\
      port config gi1 --speed 1000 --duplex full
"""

from __future__ import annotations

import argparse
import logging
import sys

from networkmgmt.switchctrl import SwitchError, create_switch, list_vendors
from networkmgmt.switchctrl.base.client import BaseSwitchClient
from networkmgmt.switchctrl.models.port import DuplexMode, PortConfig, PortMode, PortSpeed


def cmd_monitor(switch: BaseSwitchClient, args: argparse.Namespace) -> None:
    """Run monitoring commands."""
    print("=== System Info ===")
    info = switch.monitoring.get_system_info()
    print(f"  Model:    {info.model}")
    print(f"  Hostname: {info.hostname}")
    print(f"  MAC:      {info.mac_address}")
    print(f"  Serial:   {info.serial_number}")
    print(f"  Firmware: {info.firmware_version} ({info.firmware_date})")
    print(f"  Uptime:   {info.uptime}s")

    print("\n=== Sensors ===")
    sensors = switch.monitoring.get_sensor_data()
    print(f"  Temperature: {sensors.temperature}°C (max: {sensors.max_temperature}°C)")
    print(f"  Fan Speed:   {sensors.fan_speed} RPM")

    print("\n=== Port Status ===")
    ports = switch.monitoring.get_port_status()
    for p in ports:
        status = "UP" if p.link_up else "DOWN"
        print(f"  {p.port:20s}  {status:5s}  {p.speed:>6s}  {p.duplex}")

    print("\n=== Port Statistics ===")
    stats = switch.monitoring.get_port_statistics()
    for s in stats:
        if s.link_up:
            print(f"  {s.port:20s}  TX: {s.tx_bytes:>12,} bytes  " f"RX: {s.rx_bytes:>12,} bytes")

    print("\n=== LACP Info ===")
    lacp = switch.monitoring.get_lacp_info()
    if lacp:
        for l in lacp:
            print(f"  Channel {l.port_channel_id}: {', '.join(l.member_ports)} [{l.status}]")
    else:
        print("  No LACP port-channels configured")


def cmd_vlan_create(switch: BaseSwitchClient, args: argparse.Namespace) -> None:
    """Create a VLAN."""
    if hasattr(switch, "enable"):
        switch.enable()
    switch.vlan.create_vlan(args.vlan_id, args.name or "")
    print(f"VLAN {args.vlan_id} created successfully")


def cmd_vlan_list(switch: BaseSwitchClient, args: argparse.Namespace) -> None:
    """List VLANs."""
    vlans = switch.vlan.list_vlans()
    if not vlans:
        print("No VLANs found (or unable to parse output)")
        return

    print(f"{'VLAN':>6s}  {'Name':20s}  {'Tagged':30s}  {'Untagged'}")
    print("-" * 90)
    for v in vlans:
        tagged = ", ".join(v.tagged_ports) or "-"
        untagged = ", ".join(v.untagged_ports) or "-"
        print(f"{v.vlan_id:>6d}  {v.name:20s}  {tagged:30s}  {untagged}")


def cmd_vlan_delete(switch: BaseSwitchClient, args: argparse.Namespace) -> None:
    """Delete a VLAN."""
    if hasattr(switch, "enable"):
        switch.enable()
    switch.vlan.delete_vlan(args.vlan_id)
    print(f"VLAN {args.vlan_id} deleted successfully")


def cmd_port_config(switch: BaseSwitchClient, args: argparse.Namespace) -> None:
    """Configure a port."""
    if hasattr(switch, "enable"):
        switch.enable()

    config = PortConfig(
        port=args.interface,
        speed=PortSpeed(args.speed) if args.speed else PortSpeed.AUTO,
        duplex=DuplexMode(args.duplex) if args.duplex else DuplexMode.AUTO,
        mode=PortMode(args.mode) if args.mode else PortMode.ACCESS,
        enabled=not args.shutdown,
        description=args.description or "",
        access_vlan=args.access_vlan,
    )
    switch.port.configure_port(config)
    print(f"Port {args.interface} configured successfully")


def cmd_example(switch: BaseSwitchClient, args: argparse.Namespace) -> None:
    """Run example operations."""
    print("=== Example: Monitoring ===")
    info = switch.monitoring.get_system_info()
    print(f"Connected to {info.model} ({info.hostname})")
    print(f"Firmware: {info.firmware_version}")

    sensors = switch.monitoring.get_sensor_data()
    print(f"Temperature: {sensors.temperature}°C")

    ports = switch.monitoring.get_port_status()
    up_count = sum(1 for p in ports if p.link_up)
    print(f"Ports: {up_count}/{len(ports)} up")

    print("\n=== Example complete ===")
    print("For configuration examples (VLAN, port), use the specific subcommands.")


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for switch management."""
    parser = argparse.ArgumentParser(
        prog="networkmgmt-switchctrl",
        description="Multi-vendor switch management — monitor, VLAN, and port configuration",
    )
    parser.add_argument(
        "--vendor",
        choices=list_vendors(),
        required=True,
        help="Switch vendor",
    )
    parser.add_argument("--host", required=True, help="Switch IP address or hostname")
    parser.add_argument("--password", required=True, help="Admin password")
    parser.add_argument("--username", default="admin", help="Username (default: admin)")
    parser.add_argument("--ssh-username", help="SSH username (vendor-specific default if omitted)")
    parser.add_argument("--ssh-password", help="SSH password (vendor-specific default if omitted)")
    parser.add_argument("--rest-port", type=int, help="REST API port")
    parser.add_argument("--ssh-port", type=int, help="SSH port")
    parser.add_argument(
        "--enable-password",
        help="Enable password (Cisco: required; QNAP: auto-generated if omitted)",
    )
    parser.add_argument("--verify-ssl", action="store_true", help="Verify SSL certificates")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # monitor
    subparsers.add_parser("monitor", help="Show monitoring information")

    # vlan
    vlan_parser = subparsers.add_parser("vlan", help="VLAN management")
    vlan_sub = vlan_parser.add_subparsers(dest="vlan_command", help="VLAN commands")

    vlan_create = vlan_sub.add_parser("create", help="Create a VLAN")
    vlan_create.add_argument("vlan_id", type=int, help="VLAN ID (2-4094)")
    vlan_create.add_argument("--name", help="VLAN name")

    vlan_sub.add_parser("list", help="List all VLANs")

    vlan_delete = vlan_sub.add_parser("delete", help="Delete a VLAN")
    vlan_delete.add_argument("vlan_id", type=int, help="VLAN ID to delete")

    # port
    port_parser = subparsers.add_parser("port", help="Port configuration")
    port_sub = port_parser.add_subparsers(dest="port_command", help="Port commands")

    port_config = port_sub.add_parser("config", help="Configure a port")
    port_config.add_argument(
        "interface",
        help="Interface name (e.g. gi1 for Cisco C1200, GigabitEthernet1/0/1 for QNAP)",
    )
    port_config.add_argument("--speed", choices=["auto", "10", "100", "1000", "10000"])
    port_config.add_argument("--duplex", choices=["auto", "full", "half"])
    port_config.add_argument("--mode", choices=["access", "trunk"])
    port_config.add_argument("--shutdown", action="store_true", help="Disable the port")
    port_config.add_argument("--description", help="Port description")
    port_config.add_argument("--access-vlan", type=int, help="Access VLAN ID")

    # example
    subparsers.add_parser("example", help="Run example operations")

    return parser


def main(args: list[str] | None = None) -> None:
    """Main entry point for switch management CLI."""
    parser = build_parser()
    parsed = parser.parse_args(args)

    if not parsed.command:
        parser.print_help()
        sys.exit(1)

    if parsed.verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(name)s %(levelname)s: %(message)s")
    else:
        logging.basicConfig(level=logging.WARNING)

    # Build vendor-specific kwargs
    kwargs: dict = {
        "password": parsed.password,
        "username": parsed.username,
        "verify_ssl": parsed.verify_ssl,
    }
    if parsed.ssh_username is not None:
        kwargs["ssh_username"] = parsed.ssh_username
    if parsed.ssh_password is not None:
        kwargs["ssh_password"] = parsed.ssh_password
    if parsed.rest_port is not None:
        kwargs["rest_port"] = parsed.rest_port
    if parsed.ssh_port is not None:
        kwargs["ssh_port"] = parsed.ssh_port
    if parsed.enable_password is not None:
        kwargs["enable_password"] = parsed.enable_password

    try:
        with create_switch(parsed.vendor, host=parsed.host, **kwargs) as switch:
            if parsed.command == "monitor":
                cmd_monitor(switch, parsed)
            elif parsed.command == "vlan":
                if parsed.vlan_command == "create":
                    cmd_vlan_create(switch, parsed)
                elif parsed.vlan_command == "list":
                    cmd_vlan_list(switch, parsed)
                elif parsed.vlan_command == "delete":
                    cmd_vlan_delete(switch, parsed)
                else:
                    print("Usage: networkmgmt-switchctrl ... vlan {create|list|delete}")
            elif parsed.command == "port":
                if parsed.port_command == "config":
                    cmd_port_config(switch, parsed)
                else:
                    print("Usage: networkmgmt-switchctrl ... port {config}")
            elif parsed.command == "example":
                cmd_example(switch, parsed)
    except SwitchError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
