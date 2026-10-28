"""CLI entry point for multi-vendor switch management — standalone-capable.

Supported vendors:
  - cisco     Cisco Catalyst 1200 (C1200-8T-D) — SSH CLI only, no REST API
  - mikrotik  MikroTik RouterOS switches — REST API + SSH CLI
  - netgear   Netgear Smart Managed Pro (S3300, GS108Tv2, M4300) — SNMPv3 (v2c fallback), web UI for static routes
  - netgear_plus  Netgear Smart Managed Plus (GS105E/GS108E/GS305E) — web UI via netgear-tool
  - qnap      QNAP QSW managed switches — REST API + SSH CLI (Cisco-style)

Examples:
  # Cisco Catalyst 1200 — enable-password is required
  networkmgmt-switchctrl --vendor cisco --host 192.168.1.254 \\
      --username admin --password <PW> --enable-password <EN> monitor

  # QNAP QSW — enable-password auto-generated from serial if omitted
  networkmgmt-switchctrl --vendor qnap --host 192.168.1.1 --password <PW> monitor

  # Netgear Smart Managed Pro — SNMPv3
  networkmgmt-switchctrl --vendor netgear --host 192.168.1.38 --snmp-user monitor \\
      --auth-key <AUTH> --priv-key <PRIV> vlan list

  # Netgear Smart Managed Plus — web password
  networkmgmt-switchctrl --vendor netgear_plus --host 192.168.1.5 --password <PW> vlan list

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
from typing import Any, Callable, TypeVar

from networkmgmt.switchctrl import SwitchError, create_switch, list_vendors
from networkmgmt.switchctrl.base.client import BaseSwitchClient
from networkmgmt.switchctrl.models import LACPInfo, PortStatistics, PortStatus, SensorData, SystemInfo
from networkmgmt.switchctrl.models.port import DuplexMode, PortConfig, PortMode, PortSpeed

T = TypeVar("T")

_NETGEAR_ONLY = {
    "community": "--community",
    "snmp_user": "--snmp-user",
    "auth_key": "--auth-key",
    "auth_protocol": "--auth-protocol",
    "priv_key": "--priv-key",
    "priv_protocol": "--priv-protocol",
    "web_password": "--web-password",
}
_QNAP_ONLY = {"vlan_backend": "--vlan-backend", "rest_delay": "--rest-delay"}


def _section(title: str, fetch: Callable[[], T], render: Callable[[T], None]) -> None:
    print(f"=== {title} ===")
    try:
        data = fetch()
    except NotImplementedError as e:
        print(f"  Not supported by this switch: {e}\n")
        return
    render(data)
    print()


def cmd_monitor(switch: BaseSwitchClient, args: argparse.Namespace) -> None:
    """Run monitoring commands; sections the vendor does not implement are reported and skipped."""

    def system(info: SystemInfo) -> None:
        print(f"  Model:    {info.model}")
        print(f"  Hostname: {info.hostname}")
        print(f"  MAC:      {info.mac_address}")
        print(f"  Serial:   {info.serial_number}")
        print(f"  Firmware: {info.firmware_version} ({info.firmware_date})")
        print(f"  Uptime:   {info.uptime}s")

    def sensors(data: SensorData) -> None:
        print(f"  Temperature: {data.temperature}°C (max: {data.max_temperature}°C)")
        print(f"  Fan Speed:   {data.fan_speed} RPM")

    def port_status(ports: list[PortStatus]) -> None:
        for p in ports:
            status = "UP" if p.link_up else "DOWN"
            print(f"  {p.port:20s}  {status:5s}  {p.speed:>6s}  {p.duplex}")

    def port_stats(stats: list[PortStatistics]) -> None:
        for s in stats:
            if s.link_up:
                print(f"  {s.port:20s}  TX: {s.tx_bytes:>12,} bytes  " f"RX: {s.rx_bytes:>12,} bytes")

    def lacp_info(lacp: list[LACPInfo]) -> None:
        if not lacp:
            print("  No LACP port-channels configured")
            return
        for entry in lacp:
            print(f"  Channel {entry.port_channel_id}: {', '.join(entry.member_ports)} [{entry.status}]")

    _section("System Info", switch.monitoring.get_system_info, system)
    _section("Sensors", switch.monitoring.get_sensor_data, sensors)
    _section("Port Status", switch.monitoring.get_port_status, port_status)
    _section("Port Statistics", switch.monitoring.get_port_statistics, port_stats)
    _section("LACP Info", switch.monitoring.get_lacp_info, lacp_info)


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

    try:
        print(f"Temperature: {switch.monitoring.get_sensor_data().temperature}°C")
    except NotImplementedError:
        print("Temperature: not supported by this switch")

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
    parser.add_argument(
        "--password",
        help="Admin password (required except for netgear with --community/--snmp-user)",
    )
    parser.add_argument("--username", help="Username (default: admin; netgear: alias for --snmp-user)")
    parser.add_argument("--ssh-username", help="SSH username (vendor-specific default if omitted)")
    parser.add_argument("--ssh-password", help="SSH password (vendor-specific default if omitted)")
    parser.add_argument("--rest-port", type=int, help="REST API port")
    parser.add_argument("--ssh-port", type=int, help="SSH port")
    parser.add_argument(
        "--enable-password",
        help="Enable password (Cisco: required; QNAP: auto-generated if omitted)",
    )
    netgear = parser.add_argument_group("netgear (Smart Managed Pro, SNMP)")
    netgear.add_argument("--community", help="SNMPv2c community (v2c fallback when no --snmp-user is given)")
    netgear.add_argument("--snmp-user", help="SNMPv3 user")
    netgear.add_argument("--auth-key", help="SNMPv3 authentication passphrase (default: --password)")
    netgear.add_argument(
        "--auth-protocol",
        choices=["md5", "sha", "sha256", "sha512"],
        help="SNMPv3 authentication protocol (default: sha512; GS108Tv2: md5 or sha)",
    )
    netgear.add_argument("--priv-key", help="SNMPv3 privacy passphrase (omit for authNoPriv)")
    netgear.add_argument(
        "--priv-protocol",
        choices=["des", "aes128"],
        help="SNMPv3 privacy protocol (default: aes128; GS108Tv2: des)",
    )
    netgear.add_argument("--web-password", help="Web UI password for static routes (default: --password)")

    qnap = parser.add_argument_group("qnap")
    qnap.add_argument(
        "--vlan-backend", choices=["rest", "ssh"], help="VLAN backend (default: rest, ssh if SSH options given)"
    )
    qnap.add_argument("--rest-delay", type=float, help="Seconds between REST requests (default: 0.3)")

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


def build_kwargs(parsed: argparse.Namespace) -> dict[str, Any]:
    """Translate parsed options into the keyword arguments of the selected vendor's constructor.

    Raises:
        ValueError: If a required credential is missing or an option does not belong to the vendor.
    """
    vendor = parsed.vendor
    for attrs, owner in ((_NETGEAR_ONLY, "netgear"), (_QNAP_ONLY, "qnap")):
        for attr, flag in attrs.items():
            if getattr(parsed, attr) is not None and vendor != owner:
                raise ValueError(f"{flag} is only valid with --vendor {owner}")

    if vendor == "netgear":
        kwargs: dict[str, Any] = {
            attr: getattr(parsed, attr) for attr in _NETGEAR_ONLY if getattr(parsed, attr) is not None
        }
        if parsed.password is not None:
            kwargs["password"] = parsed.password
        if parsed.username is not None:
            kwargs["username"] = parsed.username
        if not any(k in kwargs for k in ("community", "snmp_user", "username")):
            raise ValueError("netgear needs --community (SNMPv2c) or --snmp-user/--username (SNMPv3)")
        if "community" not in kwargs and not any(k in kwargs for k in ("auth_key", "password")):
            raise ValueError("netgear SNMPv3 needs --auth-key or --password")
        return kwargs

    if not parsed.password:
        raise ValueError("--password is required")
    if vendor == "netgear_plus":
        return {"password": parsed.password}

    kwargs = {
        "password": parsed.password,
        "username": parsed.username or "admin",
        "verify_ssl": parsed.verify_ssl,
    }
    for attr in ("ssh_username", "ssh_password", "rest_port", "ssh_port", "enable_password"):
        if getattr(parsed, attr) is not None:
            kwargs[attr] = getattr(parsed, attr)
    if vendor == "qnap":
        for attr in _QNAP_ONLY:
            if getattr(parsed, attr) is not None:
                kwargs[attr] = getattr(parsed, attr)
    return kwargs


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

    try:
        kwargs = build_kwargs(parsed)
    except ValueError as e:
        parser.error(str(e))

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
