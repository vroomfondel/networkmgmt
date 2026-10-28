"""Vendor specific data models for Netgear Smart Managed Pro switches."""

from dataclasses import dataclass, field


@dataclass
class StackUnit:
    """One chassis of a stack (ENTITY-MIB).

    The serial number is deliberately absent: firmware 6.6.4.28 reports the serial of the management
    unit for every member.
    """

    name: str = ""
    model: str = ""
    firmware: str = ""


@dataclass
class StackPort:
    """A 10G port from agentInventoryStackPortTable.

    Attributes:
        name: ifName of the port (``2/xg27``) or ``<unit>/<tag>`` if unknown.
        unit: Stack unit number.
        configured_mode: ``stack``, ``ethernet`` or ``unknown``.
        running_mode: ``stack``, ``ethernet`` or ``unknown``.
        link_up: Link state.
        speed_gbps: Link speed.
        errors: Errors since boot.
    """

    name: str
    unit: int
    configured_mode: str
    running_mode: str
    link_up: bool
    speed_gbps: int
    errors: int


@dataclass
class RoutingInterface:
    """A VLAN routing interface.

    Attributes:
        vlan_id: VLAN the interface belongs to.
        ifindex: ifIndex of the routing interface.
        address: ``a.b.c.d/len`` or None if unnumbered.
        routing: Whether IP routing is enabled on the interface.
        mtu: IP MTU.
    """

    vlan_id: int
    ifindex: int
    address: str | None
    routing: bool
    mtu: int


@dataclass
class RouteEntry:
    """A row of the routing table read via SNMP (inetCidrRouteTable)."""

    dest: str
    next_hop: str
    proto: int
    metric: int


@dataclass
class RoutingState:
    """Global routing state."""

    enabled: bool
    interfaces: dict[int, RoutingInterface] = field(default_factory=dict)
    routes: list[RouteEntry] = field(default_factory=list)


@dataclass
class StaticRoute:
    """A static route as shown by the web UI.

    Attributes:
        dest: Network in CIDR notation, ``0.0.0.0/0`` for the default route.
        next_hop: Next hop address.
        preference: Route preference (1-255).
        description: Up to 31 characters of ``[A-Za-z0-9_-]``.
    """

    dest: str
    next_hop: str
    preference: int | None = None
    description: str = ""


@dataclass
class ManagementConfig:
    """Out-of-band style management address of the switch.

    Attributes:
        address: ``a.b.c.d/len``.
        gateway: Gateway or ``0.0.0.0``.
        protocol: agentNetworkConfigProtocol (1 = none, 2 = bootp, 3 = dhcp).
        vlan: Management VLAN.
    """

    address: str
    gateway: str
    protocol: int
    vlan: int


@dataclass
class WebSession:
    """A login session of the web UI (limit: agentHTTPMaxSessions)."""

    user: str
    address: str
    type: str
