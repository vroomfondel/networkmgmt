"""Shared SNMP access layer of the Netgear managers: interface names, VLAN tables, save."""

import time
from dataclasses import dataclass

from loguru import logger

from networkmgmt.snmp import SnmpError, SnmpSession, integer, portlist_to_ports
from networkmgmt.switchctrl.vendors.netgear import oids


@dataclass
class VlanState:
    """One row of the VLAN table.

    Attributes:
        name: VLAN name; empty for factory VLANs, whose names are not readable.
        egress: ifIndexes that are members.
        untagged: ifIndexes that are untagged members (non-members masked out).
        untagged_raw: Raw untagged bitmap as read; only bits of moving ports may be written back.
        static: False for factory VLANs that exist only in dot1qVlanCurrentTable.
    """

    name: str
    egress: set[int]
    untagged: set[int]
    untagged_raw: bytes
    static: bool


@dataclass
class VlanTable:
    """VLAN table plus the PortList length in octets."""

    vlans: dict[int, VlanState]
    length: int


class NetgearBackend:
    """SNMP access to one switch, shared by all managers of a client."""

    def __init__(self, session: SnmpSession, settle_timeout: float = 30.0, settle_interval: float = 1.0):
        """Create the backend.

        Args:
            session: Open or lazily opening SNMP session.
            settle_timeout: Seconds to poll for a write to show up on read-back.
            settle_interval: Seconds between two read-backs.
        """
        self.session = session
        self.settle_timeout = settle_timeout
        self.settle_interval = settle_interval

    def ifnames(self) -> dict[int, str]:
        """Return ``{ifIndex: ifName}`` (``1/g1``, ``2/xg27``, ``l1``, ``cpu``)."""
        return {int(k): v for k, v in self.session.walk(oids.IF_NAME).items()}

    def ifindex_by_name(self) -> dict[str, int]:
        """Return ``{ifName: ifIndex}``."""
        return {name: idx for idx, name in self.ifnames().items()}

    def bridge_ports(self) -> set[int]:
        """Return ifIndexes that are bridge ports; stack links are not."""
        return set(self.session.walk_int(oids.BASE_PORT_IFINDEX).values())

    def resolve_port(self, port: str, names: dict[int, str] | None = None) -> int:
        """Resolve an ifName (or a numeric ifIndex string) to the ifIndex.

        Args:
            port: Port name such as ``1/g3``.
            names: Pre-fetched ``ifnames()``.

        Returns:
            The ifIndex.

        Raises:
            KeyError: If the port does not exist.
        """
        names = names if names is not None else self.ifnames()
        for idx, name in names.items():
            if name == port:
                return idx
        if port.isdigit() and int(port) in names:
            return int(port)
        raise KeyError(port)

    def read_vlans(self) -> VlanTable:
        """Read the VLAN table, merging dot1qVlanStaticTable and dot1qVlanCurrentTable.

        Factory VLANs (1, 2 voice, 3 auto-video on the GS108Tv2) exist only in the current table.
        """
        names = self.session.walk(oids.VLAN_STATIC_NAME)
        egress = self.session.walk_octets(oids.VLAN_STATIC_EGRESS)
        untagged = self.session.walk_octets(oids.VLAN_STATIC_UNTAGGED)
        static = set(egress)
        for table, base in ((egress, oids.VLAN_CURRENT_EGRESS), (untagged, oids.VLAN_CURRENT_UNTAGGED)):
            for key, value in self.session.walk_octets(base).items():
                vid = key.rsplit(".", 1)[1]
                if vid not in static:
                    table[vid] = value
        vlans: dict[int, VlanState] = {}
        for vid, raw in egress.items():
            members = portlist_to_ports(raw)
            vlans[int(vid)] = VlanState(
                name=names.get(vid, ""),
                egress=members,
                untagged=portlist_to_ports(untagged.get(vid, b"")) & members,
                untagged_raw=untagged.get(vid, b""),
                static=vid in static,
            )
        length = len(next(iter(egress.values()))) if egress else 0
        return VlanTable(vlans=vlans, length=length)

    def read_pvids(self) -> dict[int, int]:
        """Return ``{ifIndex: PVID}``."""
        return {int(k): v for k, v in self.session.walk_int(oids.PVID).items()}

    def switchport_modes(self) -> dict[int, int]:
        """Return ``{ifIndex: switchport mode}``; 3 is general."""
        return {int(k): v for k, v in self.session.walk_int(oids.SWITCHPORT_MODE).items()}

    def save_config(self) -> None:
        """Copy the running config to the startup config.

        Uses agentSaveConfig under ``4526.11`` (``4526.10`` is the LibreNMS MIB and answers noSuchObject).
        The status OID reads savingComplete before and after, so it proves nothing; it is polled anyway
        to catch an explicit failure.

        Raises:
            SnmpError: If the agent reports failure or the poll times out.
        """
        self.session.set((oids.SAVE_CONFIG, integer(oids.SAVE_REQUEST)))
        deadline = time.monotonic() + 60.0
        while True:
            time.sleep(1.0)
            status = self.session.get_int(oids.SAVE_STATUS)
            if status == oids.SAVE_COMPLETE:
                logger.debug(f"[{self.session.host}] configuration saved")
                return
            if status == oids.SAVE_FAILED or time.monotonic() > deadline:
                raise SnmpError(f"saving the configuration failed (agentSaveConfigStatus={status})")
