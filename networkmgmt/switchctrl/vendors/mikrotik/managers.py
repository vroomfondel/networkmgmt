"""MikroTik RouterOS managers for monitoring, VLAN, port, and LACP."""

from __future__ import annotations

import logging
import re

from networkmgmt.switchctrl.base.managers import (
    BaseLACPManager,
    BaseMonitoringManager,
    BasePortManager,
    BaseVLANManager,
)
from networkmgmt.switchctrl.exceptions import LACPError, PortError, VLANError
from networkmgmt.switchctrl.models.port import PortConfig, PortMode, PortSpeed, PortStatus
from networkmgmt.switchctrl.models.stats import PortStatistics
from networkmgmt.switchctrl.models.system import LACPInfo, SensorData, SystemInfo
from networkmgmt.switchctrl.models.vlan import VLAN, TrunkConfig
from networkmgmt.switchctrl.vendors.mikrotik.rest import MikroTikRESTTransport
from networkmgmt.switchctrl.vendors.mikrotik.ssh import RouterOSTransport

logger = logging.getLogger(__name__)


class MikroTikMonitoringManager(BaseMonitoringManager):
    """Monitoring operations via MikroTik REST API."""

    def __init__(self, transport: MikroTikRESTTransport):
        self._transport = transport

    def get_port_status(self) -> list[PortStatus]:
        """Get operational status of all ethernet ports."""
        interfaces = self._transport.get("interface/ethernet")
        ports: list[PortStatus] = []

        for iface in interfaces:
            ports.append(
                PortStatus(
                    port=iface.get("name", ""),
                    link_up=iface.get("running", False),
                    speed=iface.get("speed", ""),
                    duplex=iface.get("full-duplex", ""),
                    media_type="Ethernet",
                )
            )

        return ports

    def get_port_statistics(self) -> list[PortStatistics]:
        """Get traffic statistics for all ethernet ports."""
        interfaces = self._transport.get("interface")
        stats: list[PortStatistics] = []

        for iface in interfaces:
            stats.append(
                PortStatistics(
                    port=iface.get("name", ""),
                    tx_bytes=int(iface.get("tx-byte", 0)),
                    rx_bytes=int(iface.get("rx-byte", 0)),
                    tx_packets=int(iface.get("tx-packet", 0)),
                    rx_packets=int(iface.get("rx-packet", 0)),
                    tx_errors=int(iface.get("tx-error", 0)),
                    rx_errors=int(iface.get("rx-error", 0)),
                    link_up=iface.get("running", False),
                    speed=iface.get("speed", "") if iface.get("type") == "ether" else "",
                )
            )

        return stats

    def get_system_info(self) -> SystemInfo:
        """Get system information."""
        identity = self._transport.get("system/identity")
        resource = self._transport.get("system/resource")
        routerboard = self._transport.get("system/routerboard")

        hostname = ""
        if isinstance(identity, list) and identity:
            hostname = identity[0].get("name", "")
        elif isinstance(identity, dict):
            hostname = identity.get("name", "")

        res = resource[0] if isinstance(resource, list) and resource else resource or {}
        rb = routerboard[0] if isinstance(routerboard, list) and routerboard else routerboard or {}

        return SystemInfo(
            hostname=hostname,
            mac_address="",
            serial_number=rb.get("serial-number", ""),
            firmware_version=res.get("version", ""),
            firmware_date="",
            model=rb.get("model", ""),
            uptime=self._parse_uptime(res.get("uptime", "0s")),
        )

    def get_sensor_data(self) -> SensorData:
        """Get sensor data (temperature if available)."""
        try:
            health = self._transport.get("system/health")
            if isinstance(health, list):
                temp = 0.0
                for item in health:
                    if item.get("name") == "temperature":
                        temp = float(item.get("value", 0))
                return SensorData(temperature=temp)
            return SensorData()
        except Exception:
            return SensorData()

    def get_lacp_info(self) -> list[LACPInfo]:
        """Get LACP bonding information."""
        try:
            bonds = self._transport.get("interface/bonding")
            infos: list[LACPInfo] = []
            for i, bond in enumerate(bonds):
                slaves = bond.get("slaves", "")
                members = [s.strip() for s in slaves.split(",") if s.strip()] if slaves else []
                infos.append(
                    LACPInfo(
                        port_channel_id=i + 1,
                        member_ports=members,
                        status="active" if bond.get("running", False) else "inactive",
                    )
                )
            return infos
        except Exception:
            return []

    @staticmethod
    def _parse_uptime(uptime_str: str) -> int:
        """Parse RouterOS uptime string (e.g. '1d2h3m4s') to seconds."""
        total = 0
        for match in re.finditer(r"(\d+)([wdhms])", uptime_str):
            value = int(match.group(1))
            unit = match.group(2)
            if unit == "w":
                total += value * 604800
            elif unit == "d":
                total += value * 86400
            elif unit == "h":
                total += value * 3600
            elif unit == "m":
                total += value * 60
            elif unit == "s":
                total += value
        return total


class MikroTikVLANManager(BaseVLANManager):
    """VLAN management via MikroTik RouterOS SSH CLI."""

    def __init__(self, transport: RouterOSTransport):
        self._transport = transport

    def create_vlan(self, vlan_id: int, name: str = "") -> None:
        """Create a VLAN interface."""
        if not 2 <= vlan_id <= 4094:
            raise VLANError(f"Invalid VLAN ID: {vlan_id} (must be 2-4094)")

        vlan_name = name or f"vlan{vlan_id}"
        cmd = f"/interface vlan add name={vlan_name} vlan-id={vlan_id} interface=bridge"
        output = self._transport.send_command(cmd)
        if "failure" in output.lower() or "error" in output.lower():
            raise VLANError(f"Failed to create VLAN {vlan_id}: {output}")

        logger.info("Created VLAN %d (%s)", vlan_id, vlan_name)

    def delete_vlan(self, vlan_id: int) -> None:
        """Delete a VLAN interface."""
        if vlan_id == 1:
            raise VLANError("Cannot delete default VLAN 1")

        # Find the VLAN by vlan-id
        output = self._transport.send_command(f"/interface vlan print where vlan-id={vlan_id}")
        # Try to extract the item number
        match = re.search(r"^\s*(\d+)", output, re.MULTILINE)
        if not match:
            raise VLANError(f"VLAN {vlan_id} not found")

        item_num = match.group(1)
        remove_output = self._transport.send_command(f"/interface vlan remove {item_num}")
        if "failure" in remove_output.lower() or "error" in remove_output.lower():
            raise VLANError(f"Failed to delete VLAN {vlan_id}: {remove_output}")

        logger.info("Deleted VLAN %d", vlan_id)

    def list_vlans(self) -> list[VLAN]:
        """List all VLAN interfaces."""
        output = self._transport.send_command("/interface vlan print")
        return self._parse_vlan_print(output)

    def assign_port_to_vlan(self, port: str, vlan_id: int, tagged: bool = False) -> None:
        """Assign a port to a VLAN via bridge VLAN configuration."""
        if tagged:
            cmd = f"/interface bridge vlan add bridge=bridge tagged={port} vlan-ids={vlan_id}"
        else:
            cmd = f"/interface bridge vlan add bridge=bridge untagged={port} vlan-ids={vlan_id}"

        output = self._transport.send_command(cmd)
        if "failure" in output.lower() or "error" in output.lower():
            raise VLANError(f"Failed to assign {port} to VLAN {vlan_id}: {output}")

        mode = "tagged" if tagged else "untagged"
        logger.info("Assigned %s to VLAN %d (%s)", port, vlan_id, mode)

    def configure_trunk(self, config: TrunkConfig) -> None:
        """Configure trunk port with allowed VLANs."""
        for vlan_id in config.allowed_vlans:
            cmd = f"/interface bridge vlan add bridge=bridge tagged={config.port} vlan-ids={vlan_id}"
            output = self._transport.send_command(cmd)
            if "failure" in output.lower() or "error" in output.lower():
                raise VLANError(f"Failed to configure trunk VLAN {vlan_id} on {config.port}: {output}")

        # Set PVID (native VLAN)
        cmd = f"/interface bridge port set [find interface={config.port}] pvid={config.native_vlan}"
        self._transport.send_command(cmd)
        logger.info("Configured trunk on %s (native VLAN %d)", config.port, config.native_vlan)

    @staticmethod
    def _parse_vlan_print(output: str) -> list[VLAN]:
        """Parse '/interface vlan print' output."""
        vlans: list[VLAN] = []
        for line in output.splitlines():
            line = line.strip()
            # Match lines like: 0  name="vlan100" ... vlan-id=100 interface=bridge
            name_match = re.search(r'name="?(\S+)"?', line)
            id_match = re.search(r"vlan-id=(\d+)", line)
            if id_match:
                vlan_id = int(id_match.group(1))
                name = name_match.group(1).strip('"') if name_match else f"vlan{vlan_id}"
                vlans.append(VLAN(vlan_id=vlan_id, name=name))
        return vlans


class MikroTikPortManager(BasePortManager):
    """Port management via MikroTik RouterOS SSH CLI."""

    def __init__(self, transport: RouterOSTransport):
        self._transport = transport

    def configure_port(self, config: PortConfig) -> None:
        """Apply port configuration."""
        iface = config.port
        parts = [f"/interface ethernet set [find name={iface}]"]

        if config.speed != PortSpeed.AUTO:
            parts.append(f"speed={config.speed.value}Mbps")
        else:
            parts.append("speed=auto")

        if not config.enabled:
            parts.append("disabled=yes")
        else:
            parts.append("disabled=no")

        if config.description:
            parts.append(f"comment={config.description}")

        output = self._transport.send_command(" ".join(parts))
        if "failure" in output.lower() or "error" in output.lower():
            raise PortError(f"Failed to configure {iface}: {output}")

        logger.info("Configured port %s", iface)

    def enable_port(self, port: str) -> None:
        """Enable a port."""
        output = self._transport.send_command(f"/interface ethernet set [find name={port}] disabled=no")
        if "failure" in output.lower() or "error" in output.lower():
            raise PortError(f"Failed to enable {port}: {output}")
        logger.info("Enabled port %s", port)

    def disable_port(self, port: str) -> None:
        """Disable a port."""
        output = self._transport.send_command(f"/interface ethernet set [find name={port}] disabled=yes")
        if "failure" in output.lower() or "error" in output.lower():
            raise PortError(f"Failed to disable {port}: {output}")
        logger.info("Disabled port %s", port)

    def get_port_status(self, port: str | None = None) -> list[PortStatus]:
        """Get port status via CLI."""
        if port:
            output = self._transport.send_command(f"/interface ethernet print where name={port}")
        else:
            output = self._transport.send_command("/interface ethernet print")

        return self._parse_ethernet_print(output)

    @staticmethod
    def _parse_ethernet_print(output: str) -> list[PortStatus]:
        """Parse '/interface ethernet print' output."""
        ports: list[PortStatus] = []
        for line in output.splitlines():
            line = line.strip()
            name_match = re.search(r'name="?(\S+)"?', line)
            if not name_match:
                continue
            running = "R" in line.split()[0] if line and line[0].isdigit() else False
            speed_match = re.search(r"speed=(\S+)", line)
            ports.append(
                PortStatus(
                    port=name_match.group(1).strip('"'),
                    link_up=running,
                    speed=speed_match.group(1) if speed_match else "",
                    media_type="Ethernet",
                )
            )
        return ports


class MikroTikLACPManager(BaseLACPManager):
    """LACP / bonding management via MikroTik RouterOS SSH CLI."""

    def __init__(self, transport: RouterOSTransport):
        self._transport = transport

    def create_port_channel(self, channel_id: int, member_ports: list[str]) -> None:
        """Create a bonding interface with LACP."""
        if not member_ports:
            raise LACPError("At least one member port is required")

        slaves = ",".join(member_ports)
        bond_name = f"bond{channel_id}"
        cmd = f"/interface bonding add name={bond_name} " f"slaves={slaves} mode=802.3ad"
        output = self._transport.send_command(cmd)
        if "failure" in output.lower() or "error" in output.lower():
            raise LACPError(f"Failed to create bonding {bond_name}: {output}")

        logger.info("Created bonding %s with members: %s", bond_name, slaves)

    def delete_port_channel(self, channel_id: int) -> None:
        """Delete a bonding interface."""
        bond_name = f"bond{channel_id}"
        output = self._transport.send_command(f"/interface bonding remove [find name={bond_name}]")
        if "failure" in output.lower() or "error" in output.lower():
            raise LACPError(f"Failed to delete bonding {bond_name}: {output}")

        logger.info("Deleted bonding %s", bond_name)

    def get_port_channel_info(self) -> list[LACPInfo]:
        """Get bonding information."""
        output = self._transport.send_command("/interface bonding print")
        return self._parse_bonding_print(output)

    @staticmethod
    def _parse_bonding_print(output: str) -> list[LACPInfo]:
        """Parse '/interface bonding print' output."""
        channels: list[LACPInfo] = []
        for line in output.splitlines():
            line = line.strip()
            name_match = re.search(r'name="?(\S+)"?', line)
            slaves_match = re.search(r'slaves="?([^"]*)"?', line)
            if not name_match:
                continue

            bond_name = name_match.group(1).strip('"')
            # Extract channel ID from bond name (e.g. "bond1" -> 1)
            id_match = re.search(r"(\d+)$", bond_name)
            channel_id = int(id_match.group(1)) if id_match else 0

            slaves_str = slaves_match.group(1) if slaves_match else ""
            members = [s.strip() for s in slaves_str.split(",") if s.strip()]

            running = "R" in line.split()[0] if line and line[0].isdigit() else False

            channels.append(
                LACPInfo(
                    port_channel_id=channel_id,
                    member_ports=members,
                    status="active" if running else "inactive",
                )
            )

        return channels
