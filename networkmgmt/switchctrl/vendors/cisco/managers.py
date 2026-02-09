"""Cisco Catalyst 1200 specific managers.

Overrides for C1200-8T-D:
- Monitoring entirely via SSH CLI (no REST API)
- VLAN creation via 'vlan database' mode (not 'configure terminal')
- C1200 'show interfaces status' output format
- LACP 'channel-group mode auto' instead of 'mode active'
"""

from __future__ import annotations

import logging
import re

from networkmgmt.switchctrl.base.managers import BaseMonitoringManager
from networkmgmt.switchctrl.exceptions import LACPError, VLANError
from networkmgmt.switchctrl.models.port import PortStatus
from networkmgmt.switchctrl.models.stats import PortStatistics
from networkmgmt.switchctrl.models.system import LACPInfo, SensorData, SystemInfo
from networkmgmt.switchctrl.vendors.common.cisco_cli import CiscoCLITransport
from networkmgmt.switchctrl.vendors.common.cisco_managers import (
    CiscoLACPManager,
    CiscoPortManager,
    CiscoVLANManager,
)

logger = logging.getLogger(__name__)


class CiscoCLIMonitoringManager(BaseMonitoringManager):
    """Monitoring via SSH CLI for Cisco Catalyst 1200 (no REST API)."""

    def __init__(self, transport: CiscoCLITransport):
        self._transport = transport

    def get_system_info(self) -> SystemInfo:
        """Get system info by parsing 'show version' output."""
        output = self._transport.send_command("show version")
        return self._parse_show_version(output)

    def get_port_status(self) -> list[PortStatus]:
        """Get port status by parsing 'show interfaces status' output."""
        output = self._transport.send_command("show interfaces status")
        return self._parse_interface_status(output)

    def get_port_statistics(self) -> list[PortStatistics]:
        """Get port counters by parsing 'show interfaces counters' output."""
        output = self._transport.send_command("show interfaces counters")
        return self._parse_interface_counters(output)

    def get_sensor_data(self) -> SensorData:
        """Get environment data by parsing 'show environment' output.

        The C1200-8T-D is fanless (PoE-powered), so fan_speed is always 0.
        """
        output = self._transport.send_command("show environment")
        return self._parse_environment(output)

    def get_lacp_info(self) -> list[LACPInfo]:
        """Get LACP info by parsing 'show etherchannel summary' output."""
        output = self._transport.send_command("show etherchannel summary")
        return CiscoLACPManager._parse_etherchannel(output)

    @staticmethod
    def _parse_show_version(output: str) -> SystemInfo:
        """Parse 'show version' CLI output into SystemInfo.

        Extracts hostname, MAC address, serial number, firmware version,
        model, and uptime from typical C1200 'show version' output.
        """
        hostname = ""
        mac = ""
        serial = ""
        firmware = ""
        model = ""
        uptime = 0

        for line in output.splitlines():
            line = line.strip()

            if not hostname:
                match = re.match(r"^(\S+)\s+uptime\s+is\s+(.*)", line, re.IGNORECASE)
                if match:
                    hostname = match.group(1)
                    uptime = _parse_uptime(match.group(2))
                    continue

            match = re.match(r"^System\s+image\s+file\s+is\s+\"?(.+?)\"?$", line, re.IGNORECASE)
            if match:
                firmware = match.group(1)
                continue

            match = re.match(r"^Base\s+Ethernet\s+MAC\s+Address\s*:\s*(\S+)", line, re.IGNORECASE)
            if not match:
                match = re.match(r"^Base\s+ethernet\s+MAC\s+Address\s*:\s*(\S+)", line)
            if match:
                mac = match.group(1)
                continue

            match = re.match(r"^System\s+serial\s+number\s*:\s*(\S+)", line, re.IGNORECASE)
            if match:
                serial = match.group(1)
                continue

            match = re.match(r"^[Cc]isco\s+(\S+)", line)
            if match and not model:
                model = match.group(1)
                continue

            # Software version line, e.g. "Cisco ... Software, Version 4.2.x.x"
            match = re.search(r"Version\s+(\S+)", line)
            if match and not firmware:
                firmware = match.group(1)
                continue

        return SystemInfo(
            hostname=hostname,
            mac_address=mac,
            serial_number=serial,
            firmware_version=firmware,
            model=model,
            uptime=uptime,
        )

    @staticmethod
    def _parse_interface_status(output: str) -> list[PortStatus]:
        """Parse C1200 'show interfaces status' output.

        Expected format::

            Port     Type         Duplex  Speed Neg      ctrl State       Pressure Mode
            -------- ------------ ------  ----- -------- ---- ----------- -------- -------
            gi1      1G-Copper    Full    1000  Enabled  Off  Up          Disabled Auto
            gi2      1G-Copper    --      --    Enabled  Off  Down        Disabled Auto
        """
        ports: list[PortStatus] = []
        for line in output.splitlines():
            line = line.strip()
            match = re.match(
                r"^(gi\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+\S+\s+\S+\s+(Up|Down)",
                line,
                re.IGNORECASE,
            )
            if not match:
                continue

            speed = match.group(4)
            duplex = match.group(3)

            ports.append(
                PortStatus(
                    port=match.group(1),
                    link_up=match.group(5).lower() == "up",
                    speed=speed if speed != "--" else "",
                    duplex=duplex if duplex != "--" else "",
                    media_type=match.group(2),
                )
            )

        return ports

    @staticmethod
    def _parse_interface_counters(output: str) -> list[PortStatistics]:
        """Parse 'show interfaces counters' output.

        The C1200 outputs two tables (InOctets/InUcastPkts/... and
        OutOctets/OutUcastPkts/...).  We merge them by port name.
        """
        stats_map: dict[str, dict[str, int]] = {}

        for line in output.splitlines():
            line = line.strip()
            # Match lines starting with a port name like gi1, gi2, ...
            match = re.match(r"^(gi\d+)\s+([\d\s]+)$", line, re.IGNORECASE)
            if not match:
                continue

            port = match.group(1)
            values = match.group(2).split()
            if port not in stats_map:
                stats_map[port] = {}

            current = stats_map[port]
            int_values = [int(v) for v in values]

            if "rx_bytes" not in current:
                # First table: In counters (Octets, UcastPkts, McastPkts, BcastPkts)
                if len(int_values) >= 1:
                    current["rx_bytes"] = int_values[0]
                if len(int_values) >= 2:
                    current["rx_packets"] = int_values[1]
                    # Add multicast and broadcast to total if present
                    for i in range(2, len(int_values)):
                        current["rx_packets"] += int_values[i]
            else:
                # Second table: Out counters
                if len(int_values) >= 1:
                    current["tx_bytes"] = int_values[0]
                if len(int_values) >= 2:
                    current["tx_packets"] = int_values[1]
                    for i in range(2, len(int_values)):
                        current["tx_packets"] += int_values[i]

        result: list[PortStatistics] = []
        for port in sorted(stats_map):
            s = stats_map[port]
            result.append(
                PortStatistics(
                    port=port,
                    rx_bytes=s.get("rx_bytes", 0),
                    tx_bytes=s.get("tx_bytes", 0),
                    rx_packets=s.get("rx_packets", 0),
                    tx_packets=s.get("tx_packets", 0),
                )
            )

        return result

    @staticmethod
    def _parse_environment(output: str) -> SensorData:
        """Parse 'show environment' output for temperature.

        C1200-8T-D is fanless (PoE-powered), so fan_speed is always 0.
        """
        temperature = 0.0
        max_temperature = 0.0

        for line in output.splitlines():
            line = line.strip()
            match = re.search(r"(\d+(?:\.\d+)?)\s*[Cc]", line)
            if match:
                temp = float(match.group(1))
                if temperature == 0.0:
                    temperature = temp
                else:
                    max_temperature = temp

        return SensorData(
            temperature=temperature,
            max_temperature=max_temperature,
            fan_speed=0,
        )


class CiscoCatalystVLANManager(CiscoVLANManager):
    """VLAN manager for Cisco Catalyst 1200.

    Uses 'vlan database' mode for VLAN creation/deletion instead of
    'configure terminal' → 'vlan {id}'.
    """

    def create_vlan(self, vlan_id: int, name: str = "") -> None:
        """Create a VLAN via 'vlan database' mode.

        Args:
            vlan_id: VLAN ID (2-4094).
            name: Optional VLAN name.
        """
        if not 2 <= vlan_id <= 4094:
            raise VLANError(f"Invalid VLAN ID: {vlan_id} (must be 2-4094)")

        self._transport.send_command("vlan database")

        cmd = f"vlan {vlan_id}"
        if name:
            cmd += f" name {name}"
        output = self._transport.send_command(cmd)

        exit_output = self._transport.send_command("exit")
        combined = output + "\n" + exit_output

        if "error" in combined.lower() or "invalid" in combined.lower():
            raise VLANError(f"Failed to create VLAN {vlan_id}: {combined}")

        logger.info("Created VLAN %d%s", vlan_id, f" ({name})" if name else "")

    def delete_vlan(self, vlan_id: int) -> None:
        """Delete a VLAN via 'vlan database' mode.

        Args:
            vlan_id: VLAN ID to delete.
        """
        if vlan_id == 1:
            raise VLANError("Cannot delete default VLAN 1")

        self._transport.send_command("vlan database")
        output = self._transport.send_command(f"no vlan {vlan_id}")
        exit_output = self._transport.send_command("exit")
        combined = output + "\n" + exit_output

        if "error" in combined.lower() or "not found" in combined.lower():
            raise VLANError(f"Failed to delete VLAN {vlan_id}: {combined}")

        logger.info("Deleted VLAN %d", vlan_id)


class CiscoCatalystPortManager(CiscoPortManager):
    """Port manager for Cisco Catalyst 1200.

    Overrides '_parse_interface_status()' for the C1200 output format.
    """

    def get_port_status(self, port: str | None = None) -> list[PortStatus]:
        """Get port status via CLI 'show interfaces status'.

        Args:
            port: Optional specific interface (e.g. "gi1"). If None, shows all.

        Returns:
            List of PortStatus objects.
        """
        if port:
            output = self._transport.send_command(f"show interfaces {port} status")
        else:
            output = self._transport.send_command("show interfaces status")

        return self._parse_interface_status(output)

    @staticmethod
    def _parse_interface_status(output: str) -> list[PortStatus]:
        """Parse C1200 'show interfaces status' output.

        Expected format::

            Port     Type         Duplex  Speed Neg      ctrl State       Pressure Mode
            -------- ------------ ------  ----- -------- ---- ----------- -------- -------
            gi1      1G-Copper    Full    1000  Enabled  Off  Up          Disabled Auto
            gi2      1G-Copper    --      --    Enabled  Off  Down        Disabled Auto
        """
        ports: list[PortStatus] = []
        for line in output.splitlines():
            line = line.strip()
            match = re.match(
                r"^(gi\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+\S+\s+\S+\s+(Up|Down)",
                line,
                re.IGNORECASE,
            )
            if not match:
                continue

            speed = match.group(4)
            duplex = match.group(3)

            ports.append(
                PortStatus(
                    port=match.group(1),
                    link_up=match.group(5).lower() == "up",
                    speed=speed if speed != "--" else "",
                    duplex=duplex if duplex != "--" else "",
                    media_type=match.group(2),
                )
            )

        return ports


class CiscoCatalystLACPManager(CiscoLACPManager):
    """LACP manager for Cisco Catalyst 1200.

    Uses 'channel-group {id} mode auto' instead of 'mode active'.
    """

    def create_port_channel(self, channel_id: int, member_ports: list[str]) -> None:
        """Create an LACP port-channel with 'mode auto'.

        Args:
            channel_id: Port-channel number (1-8).
            member_ports: List of interface names to add (e.g. ["gi1", "gi2"]).
        """
        if not 1 <= channel_id <= 8:
            raise LACPError(f"Invalid port-channel ID: {channel_id} (must be 1-8)")
        if not member_ports:
            raise LACPError("At least one member port is required")

        commands = []
        for port in member_ports:
            commands.extend(
                [
                    f"interface {port}",
                    f"channel-group {channel_id} mode auto",
                    "exit",
                ]
            )

        output = self._transport.send_config_commands(commands)
        if "error" in output.lower() or "invalid" in output.lower():
            raise LACPError(f"Failed to create port-channel {channel_id}: {output}")

        logger.info(
            "Created port-channel %d with members: %s",
            channel_id,
            ", ".join(member_ports),
        )


def _parse_uptime(uptime_str: str) -> int:
    """Parse Cisco uptime string into seconds.

    Examples:
        "1 day, 2 hours, 30 minutes" → 95400
        "5 minutes" → 300
    """
    total = 0

    days = re.search(r"(\d+)\s+day", uptime_str)
    if days:
        total += int(days.group(1)) * 86400

    hours = re.search(r"(\d+)\s+hour", uptime_str)
    if hours:
        total += int(hours.group(1)) * 3600

    minutes = re.search(r"(\d+)\s+minute", uptime_str)
    if minutes:
        total += int(minutes.group(1)) * 60

    seconds = re.search(r"(\d+)\s+second", uptime_str)
    if seconds:
        total += int(seconds.group(1))

    return total
