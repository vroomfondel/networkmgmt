"""Cisco-style CLI managers shared by QNAP, Netgear, etc."""

from __future__ import annotations

import logging
import re

from networkmgmt.switchctrl.base.managers import BaseLACPManager, BasePortManager, BaseVLANManager
from networkmgmt.switchctrl.exceptions import LACPError, PortError, VLANError
from networkmgmt.switchctrl.models.port import DuplexMode, PortConfig, PortMode, PortSpeed, PortStatus
from networkmgmt.switchctrl.models.system import LACPInfo
from networkmgmt.switchctrl.models.vlan import VLAN, TrunkConfig
from networkmgmt.switchctrl.vendors.common.cisco_cli import CiscoCLITransport

logger = logging.getLogger(__name__)


class CiscoVLANManager(BaseVLANManager):
    """VLAN configuration via Cisco-style CLI over SSH."""

    def __init__(self, transport: CiscoCLITransport):
        self._transport = transport

    def create_vlan(self, vlan_id: int, name: str = "") -> None:
        """Create a new VLAN.

        Args:
            vlan_id: VLAN ID (2-4094).
            name: Optional VLAN name.
        """
        if not 2 <= vlan_id <= 4094:
            raise VLANError(f"Invalid VLAN ID: {vlan_id} (must be 2-4094)")

        commands = [f"vlan {vlan_id}"]
        if name:
            commands.append(f"name {name}")
        commands.append("exit")

        output = self._transport.send_config_commands(commands)
        if "error" in output.lower() or "invalid" in output.lower():
            raise VLANError(f"Failed to create VLAN {vlan_id}: {output}")

        logger.info("Created VLAN %d%s", vlan_id, f" ({name})" if name else "")

    def delete_vlan(self, vlan_id: int) -> None:
        """Delete a VLAN.

        Args:
            vlan_id: VLAN ID to delete.
        """
        if vlan_id == 1:
            raise VLANError("Cannot delete default VLAN 1")

        output = self._transport.send_config_commands([f"no vlan {vlan_id}"])
        if "error" in output.lower() or "not found" in output.lower():
            raise VLANError(f"Failed to delete VLAN {vlan_id}: {output}")

        logger.info("Deleted VLAN %d", vlan_id)

    def list_vlans(self) -> list[VLAN]:
        """List all configured VLANs by parsing 'show vlan' output."""
        output = self._transport.send_command("show vlan")
        return self._parse_show_vlan(output)

    def assign_port_to_vlan(
        self,
        port: str,
        vlan_id: int,
        tagged: bool = False,
    ) -> None:
        """Assign a port to a VLAN.

        Args:
            port: Interface name (e.g. "GigabitEthernet1/0/1").
            vlan_id: VLAN ID to assign.
            tagged: If True, add as tagged (trunk); otherwise untagged (access).
        """
        if tagged:
            commands = [
                f"interface {port}",
                "switchport mode trunk",
                f"switchport trunk allowed vlan add {vlan_id}",
                "exit",
            ]
        else:
            commands = [
                f"interface {port}",
                "switchport mode access",
                f"switchport access vlan {vlan_id}",
                "exit",
            ]

        output = self._transport.send_config_commands(commands)
        if "error" in output.lower() or "invalid" in output.lower():
            raise VLANError(f"Failed to assign port {port} to VLAN {vlan_id}: {output}")

        mode = "tagged" if tagged else "untagged"
        logger.info("Assigned %s to VLAN %d (%s)", port, vlan_id, mode)

    def configure_trunk(self, config: TrunkConfig) -> None:
        """Configure a port as trunk with allowed VLANs.

        Args:
            config: TrunkConfig with port, native VLAN, and allowed VLANs.
        """
        vlan_list = ",".join(str(v) for v in config.allowed_vlans) if config.allowed_vlans else "all"
        commands = [
            f"interface {config.port}",
            "switchport mode trunk",
            f"switchport trunk native vlan {config.native_vlan}",
            f"switchport trunk allowed vlan {vlan_list}",
            "exit",
        ]

        output = self._transport.send_config_commands(commands)
        if "error" in output.lower() or "invalid" in output.lower():
            raise VLANError(f"Failed to configure trunk on {config.port}: {output}")

        logger.info("Configured trunk on %s (native VLAN %d)", config.port, config.native_vlan)

    @staticmethod
    def _parse_show_vlan(output: str) -> list[VLAN]:
        """Parse 'show vlan' CLI output into VLAN objects.

        Expected format:
        VLAN  Name          Tagged Ports          Untagged Ports
        ----  ----          ------------          --------------
        1     default                             Gi1/0/1, Gi1/0/2
        10    management    Gi1/0/8               Gi1/0/3
        """
        vlans: list[VLAN] = []
        # Match lines that start with a VLAN ID
        for line in output.splitlines():
            line = line.strip()
            match = re.match(r"^(\d+)\s+(\S+)\s*(.*)", line)
            if not match:
                continue

            vlan_id = int(match.group(1))
            name = match.group(2)
            rest = match.group(3).strip()

            # Try to split remaining into tagged/untagged port columns
            tagged_ports: list[str] = []
            untagged_ports: list[str] = []

            if rest:
                parts = re.split(r"\s{2,}", rest, maxsplit=1)
                if len(parts) >= 1 and parts[0]:
                    tagged_ports = [p.strip() for p in parts[0].split(",") if p.strip()]
                if len(parts) >= 2 and parts[1]:
                    untagged_ports = [p.strip() for p in parts[1].split(",") if p.strip()]

            vlans.append(
                VLAN(
                    vlan_id=vlan_id,
                    name=name,
                    tagged_ports=tagged_ports,
                    untagged_ports=untagged_ports,
                )
            )

        return vlans


class CiscoPortManager(BasePortManager):
    """Port configuration via Cisco-style CLI over SSH."""

    def __init__(self, transport: CiscoCLITransport):
        self._transport = transport

    def configure_port(self, config: PortConfig) -> None:
        """Apply a full port configuration.

        Args:
            config: PortConfig with desired settings.
        """
        commands = [f"interface {config.port}"]

        if not config.enabled:
            commands.append("shutdown")
        else:
            commands.append("no shutdown")

        if config.speed != PortSpeed.AUTO:
            commands.append(f"speed {config.speed.value}")
        else:
            commands.append("speed auto")

        if config.duplex != DuplexMode.AUTO:
            commands.append(f"duplex {config.duplex.value}")
        else:
            commands.append("duplex auto")

        if config.description:
            commands.append(f"description {config.description}")

        if config.mode == PortMode.TRUNK:
            commands.append("switchport mode trunk")
        else:
            commands.append("switchport mode access")
            if config.access_vlan is not None:
                commands.append(f"switchport access vlan {config.access_vlan}")

        commands.append("exit")

        output = self._transport.send_config_commands(commands)
        if "error" in output.lower() or "invalid" in output.lower():
            raise PortError(f"Failed to configure {config.port}: {output}")

        logger.info("Configured port %s", config.port)

    def enable_port(self, port: str) -> None:
        """Enable (no shutdown) a port.

        Args:
            port: Interface name (e.g. "GigabitEthernet1/0/1").
        """
        output = self._transport.send_config_commands(
            [
                f"interface {port}",
                "no shutdown",
                "exit",
            ]
        )
        if "error" in output.lower():
            raise PortError(f"Failed to enable {port}: {output}")

        logger.info("Enabled port %s", port)

    def disable_port(self, port: str) -> None:
        """Disable (shutdown) a port.

        Args:
            port: Interface name (e.g. "GigabitEthernet1/0/1").
        """
        output = self._transport.send_config_commands(
            [
                f"interface {port}",
                "shutdown",
                "exit",
            ]
        )
        if "error" in output.lower():
            raise PortError(f"Failed to disable {port}: {output}")

        logger.info("Disabled port %s", port)

    def get_port_status(self, port: str | None = None) -> list[PortStatus]:
        """Get port status via CLI 'show interfaces status'.

        Args:
            port: Optional specific interface. If None, shows all.

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
        """Parse 'show interfaces status' output.

        Expected format:
        Port       Link    Speed    Duplex   Type
        Gi1/0/1    Up      1000     Full     Copper
        Gi1/0/2    Down    Auto     Auto     Copper
        """
        ports: list[PortStatus] = []
        for line in output.splitlines():
            line = line.strip()
            match = re.match(
                r"^(\S+)\s+(Up|Down)\s+(\S+)\s+(\S+)\s+(\S+)",
                line,
                re.IGNORECASE,
            )
            if not match:
                continue

            ports.append(
                PortStatus(
                    port=match.group(1),
                    link_up=match.group(2).lower() == "up",
                    speed=match.group(3),
                    duplex=match.group(4),
                    media_type=match.group(5),
                )
            )

        return ports


class CiscoLACPManager(BaseLACPManager):
    """LACP port-channel management via Cisco-style CLI over SSH."""

    def __init__(self, transport: CiscoCLITransport):
        self._transport = transport

    def create_port_channel(self, channel_id: int, member_ports: list[str]) -> None:
        """Create an LACP port-channel and add member ports.

        Args:
            channel_id: Port-channel number (1-8).
            member_ports: List of interface names to add.
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
                    f"channel-group {channel_id} mode active",
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

    def delete_port_channel(self, channel_id: int) -> None:
        """Delete a port-channel.

        Args:
            channel_id: Port-channel number to delete.
        """
        output = self._transport.send_config_commands(
            [
                f"no interface port-channel {channel_id}",
            ]
        )
        if "error" in output.lower() or "not found" in output.lower():
            raise LACPError(f"Failed to delete port-channel {channel_id}: {output}")

        logger.info("Deleted port-channel %d", channel_id)

    def get_port_channel_info(self) -> list[LACPInfo]:
        """Get port-channel information via CLI.

        Returns:
            List of LACPInfo objects.
        """
        output = self._transport.send_command("show etherchannel summary")
        return self._parse_etherchannel(output)

    @staticmethod
    def _parse_etherchannel(output: str) -> list[LACPInfo]:
        """Parse 'show etherchannel summary' output.

        Expected format:
        Group  Port-channel  Protocol  Ports
        -----  ------------  --------  -----
        1      Po1(SU)       LACP      Gi1/0/1(P) Gi1/0/2(P)
        """
        channels: list[LACPInfo] = []
        for line in output.splitlines():
            line = line.strip()
            match = re.match(r"^(\d+)\s+\S+\((\w+)\)\s+(\w+)\s+(.*)", line)
            if not match:
                continue

            channel_id = int(match.group(1))
            status = match.group(2)
            ports_str = match.group(4)

            # Extract port names from patterns like "Gi1/0/1(P)"
            members = re.findall(r"(\S+)\(\w+\)", ports_str)

            channels.append(
                LACPInfo(
                    port_channel_id=channel_id,
                    member_ports=members,
                    status=status,
                )
            )

        return channels
