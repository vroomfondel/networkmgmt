"""Routing extras of Netgear Smart Managed Pro switches: VLAN routing interfaces, management IP, static routes.

Interfaces and the management address are written via SNMP. Static routes need the web UI because the
firmware answers ``inetCidrRouteTable`` writes with ``commitFailed``. A Telnet debug CLI exists on port
60000 (web UI, Remote Diagnostics) and is intentionally not used.
"""

import ipaddress
import re
import socket
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from loguru import logger

from networkmgmt.snmp import (
    SnmpError,
    SnmpSettleTimeoutError,
    integer,
    ip_address,
    poll_until,
    to_switch_error,
    unsigned,
)
from networkmgmt.switchctrl.exceptions import APIError, SwitchError
from networkmgmt.switchctrl.vendors.netgear import oids
from networkmgmt.switchctrl.vendors.netgear.backend import NetgearBackend
from networkmgmt.switchctrl.vendors.netgear.models import (
    ManagementConfig,
    RouteEntry,
    RoutingInterface,
    RoutingState,
    StaticRoute,
    WebSession,
)
from networkmgmt.switchctrl.vendors.netgear.web import NetgearWeb

DESCRIPTION_RE = re.compile(r"^[A-Za-z0-9_-]{0,31}$")


def mask_to_prefix(mask: str) -> int:
    """Convert a dotted netmask to its prefix length."""
    return sum(bin(int(o)).count("1") for o in mask.split("."))


def prefix_to_mask(prefix: int) -> str:
    """Convert a prefix length to a dotted netmask."""
    bits = (0xFFFFFFFF << (32 - prefix)) & 0xFFFFFFFF
    return ".".join(str((bits >> s) & 0xFF) for s in (24, 16, 8, 0))


def parse_route_index(suffix: str) -> tuple[str, int, str] | None:
    """Parse an inetCidrRouteTable index into ``(dest, prefix, next_hop)``; None for non-IPv4 rows."""
    parts = [int(x) for x in suffix.split(".")]
    if parts[:2] != [1, 4]:
        return None
    dest = ".".join(map(str, parts[2:6]))
    prefix = parts[6]
    rest = parts[7:]
    rest = rest[1 + rest[0] :]
    if rest[:2] != [1, 4]:
        return None
    return dest, prefix, ".".join(map(str, rest[2:6]))


class NetgearRoutingManager:
    """VLAN routing interfaces, management address and static routes."""

    def __init__(
        self,
        backend: NetgearBackend,
        web_factory: Callable[[], NetgearWeb] | None = None,
        web_wait: float = 120.0,
        web_poll_interval: float = 10.0,
    ):
        """Create the manager.

        Args:
            backend: Shared SNMP backend.
            web_factory: Creates an unauthenticated :class:`NetgearWeb`; None disables static routes.
            web_wait: Seconds to wait for a free web UI session slot.
            web_poll_interval: Seconds between two checks for a free slot.
        """
        self._b = backend
        self._web_factory = web_factory
        self._web_wait = web_wait
        self._web_poll_interval = web_poll_interval

    def get_routing(self) -> RoutingState:
        """Read the routing mode, VLAN routing interfaces and the SNMP route table."""
        try:
            return self._read_routing()
        except SnmpError as exc:
            raise to_switch_error(exc, APIError) from exc

    def _read_routing(self) -> RoutingState:
        s = self._b.session
        mode = s.walk(oids.ROUTING_MODE.rsplit(".", 1)[0])
        vlan_ifindex = {int(k): int(v) for k, v in s.walk(oids.IP_VLAN_IFINDEX).items()}
        addr, mask = s.walk(oids.IP_IF_ADDRESS), s.walk(oids.IP_IF_NETMASK)
        rmode, mtu = s.walk(oids.IP_IF_ROUTING), s.walk(oids.IP_IF_MTU)
        interfaces = {}
        for vid, idx in sorted(vlan_ifindex.items()):
            k = str(idx)
            address = addr.get(k, "0.0.0.0")
            interfaces[vid] = RoutingInterface(
                vlan_id=vid,
                ifindex=idx,
                address=None if address == "0.0.0.0" else f"{address}/{mask_to_prefix(mask.get(k, '0.0.0.0'))}",
                routing=rmode.get(k) == str(oids.ENABLE),
                mtu=int(mtu.get(k, "0")),
            )
        proto = s.walk(f"{oids.CIDR_ROUTE}.{oids.CIDR_ROUTE_PROTO}")
        metric = s.walk(f"{oids.CIDR_ROUTE}.{oids.CIDR_ROUTE_METRIC1}")
        routes = []
        for suffix, p in proto.items():
            parsed = parse_route_index(suffix)
            if parsed:
                dest, prefix, next_hop = parsed
                routes.append(
                    RouteEntry(
                        dest=f"{dest}/{prefix}", next_hop=next_hop, proto=int(p), metric=int(metric.get(suffix, "0"))
                    )
                )
        routes.sort(key=lambda r: (r.dest, r.next_hop))
        return RoutingState(enabled=mode.get("0") == str(oids.ENABLE), interfaces=interfaces, routes=routes)

    def set_routing_enabled(self) -> None:
        """Enable global IP routing. Disabling is refused: it would cut the remote session."""
        try:
            self._b.session.set((oids.ROUTING_MODE, integer(oids.ENABLE)))
            self._settle(lambda: self._read_routing().enabled, lambda v: v, "global routing mode")
        except SnmpError as exc:
            raise to_switch_error(exc, APIError) from exc

    def set_interface(self, vlan_id: int, address: str, routing: bool = True, mtu: int = 1500) -> None:
        """Create or update a VLAN routing interface.

        Args:
            vlan_id: VLAN of the interface.
            address: ``a.b.c.d/len``.
            routing: Enable IP routing on the interface.
            mtu: IP MTU.

        Raises:
            SwitchError: If the address is invalid, the interface carries the management address, or the
                switch does not apply the change.
        """
        try:
            iface = ipaddress.IPv4Interface(address)
        except ValueError as exc:
            raise SwitchError(f"address must be a.b.c.d/len ({exc})") from exc
        want = RoutingInterface(vlan_id=vlan_id, ifindex=0, address=str(iface), routing=routing, mtu=mtu)
        try:
            state = self._read_routing()
            cur = state.interfaces.get(vlan_id)
            self._guard_management_interface(cur, want)
            if cur is None:
                self._b.session.set((f"{oids.IP_VLAN_STATUS}.{vlan_id}", integer(oids.ROW_CREATE_AND_GO)))
                created = self._settle(
                    lambda: self._read_routing().interfaces.get(vlan_id),
                    lambda v: v is not None,
                    f"VLAN {vlan_id} interface",
                )
                assert created is not None
                cur = RoutingInterface(vlan_id=vlan_id, ifindex=created.ifindex, address=None, routing=False, mtu=0)
            if cur.address != want.address:
                self._b.session.set(
                    (f"{oids.IP_IF_ADDRESS}.{cur.ifindex}", ip_address(str(iface.ip))),
                    (f"{oids.IP_IF_NETMASK}.{cur.ifindex}", ip_address(prefix_to_mask(iface.network.prefixlen))),
                )
            if cur.routing != routing:
                self._b.session.set(
                    (f"{oids.IP_IF_ROUTING}.{cur.ifindex}", integer(oids.ENABLE if routing else oids.DISABLE))
                )
            if cur.mtu != mtu:
                self._b.session.set((f"{oids.IP_IF_MTU}.{cur.ifindex}", unsigned(mtu)))

            def done(s: RoutingState) -> bool:
                got = s.interfaces.get(vlan_id)
                return got is not None and (got.address, got.routing, got.mtu) == (want.address, routing, mtu)

            self._settle(self._read_routing, done, f"routing interface of VLAN {vlan_id}")
        except SnmpError as exc:
            raise to_switch_error(exc, APIError) from exc

    def remove_interface(self, vlan_id: int) -> None:
        """Delete a VLAN routing interface; a missing one is a no-op.

        Raises:
            SwitchError: If the interface carries the management address.
        """
        try:
            cur = self._read_routing().interfaces.get(vlan_id)
            if cur is None:
                return
            self._guard_management_interface(cur, None)
            self._b.session.set((f"{oids.IP_VLAN_STATUS}.{vlan_id}", integer(oids.ROW_DESTROY)))
            self._settle(
                lambda: vlan_id in self._read_routing().interfaces,
                lambda present: not present,
                f"removal of VLAN {vlan_id}",
            )
        except SnmpError as exc:
            raise to_switch_error(exc, APIError) from exc

    def _guard_management_interface(self, cur: RoutingInterface | None, want: RoutingInterface | None) -> None:
        if cur is None or not cur.address:
            return
        if want is not None and (cur.address, cur.routing, cur.mtu) == (want.address, want.routing, want.mtu):
            return
        if cur.address.split("/")[0] == socket.gethostbyname(self._b.session.host):
            raise SwitchError(
                f"refusing to change VLAN {cur.vlan_id} routing interface {cur.address}: it carries the management address"
            )

    def get_management(self) -> ManagementConfig:
        """Read the management address, gateway, address source and management VLAN."""
        s = self._b.session
        try:
            ip, mask, gw = (s.get(oid) for oid in (oids.MGMT_IP, oids.MGMT_MASK, oids.MGMT_GATEWAY))
            return ManagementConfig(
                address=f"{ip}/{mask_to_prefix(mask)}",
                gateway=gw,
                protocol=s.get_int(oids.MGMT_PROTOCOL),
                vlan=s.get_int(oids.MGMT_VLAN),
            )
        except SnmpError as exc:
            raise to_switch_error(exc, APIError) from exc

    def set_management(self, address: str, gateway: str = "0.0.0.0") -> None:
        """Set the management address and gateway.

        The agent commits one varbind at a time and rejects an address outside the current gateway's
        network, so the gateway is cleared first and set last.

        Args:
            address: ``a.b.c.d/len``.
            gateway: Gateway inside ``address``'s network, or ``0.0.0.0``.

        Raises:
            SwitchError: If the input is invalid, the address comes from DHCP/BOOTP, the switch is
                addressed through the management IP itself, or the switch does not apply the change.
        """
        try:
            iface = ipaddress.IPv4Interface(address)
            gw = ipaddress.IPv4Address(gateway)
        except ValueError as exc:
            raise SwitchError(f"address must be a.b.c.d/len, gateway a.b.c.d ({exc})") from exc
        if str(gw) != "0.0.0.0" and gw not in iface.network:
            raise SwitchError(f"gateway {gw} is not in {iface.network}")
        want = ManagementConfig(address=str(iface), gateway=str(gw), protocol=oids.MGMT_PROTOCOL_NONE, vlan=0)
        cur = self.get_management()
        if (cur.address, cur.gateway) == (want.address, want.gateway):
            return
        if cur.protocol != oids.MGMT_PROTOCOL_NONE:
            raise SwitchError(
                f"management address comes from bootp/dhcp (agentNetworkConfigProtocol={cur.protocol}), set it to none first"
            )
        host_ip = socket.gethostbyname(self._b.session.host)
        if host_ip in (cur.address.split("/")[0], str(iface.ip)):
            raise SwitchError(
                f"refusing to change the management address over itself ({host_ip}); use a routing interface address as host"
            )
        s = self._b.session
        try:
            if cur.gateway != "0.0.0.0":
                s.set((oids.MGMT_GATEWAY, ip_address("0.0.0.0")))
            if cur.address != want.address:
                s.set(
                    (oids.MGMT_IP, ip_address(str(iface.ip))),
                    (oids.MGMT_MASK, ip_address(prefix_to_mask(iface.network.prefixlen))),
                )
            if str(gw) != "0.0.0.0":
                s.set((oids.MGMT_GATEWAY, ip_address(str(gw))))
        except SnmpError as exc:
            raise to_switch_error(exc, APIError) from exc
        self._settle(
            self._management_pair,
            lambda v: v == (want.address, want.gateway),
            "the management address",
        )

    def _management_pair(self) -> tuple[str, str]:
        m = self.get_management()
        return m.address, m.gateway

    def web_sessions(self) -> list[WebSession]:
        """Return the open web UI sessions (HTTP and HTTPS)."""
        s = self._b.session
        try:
            types = s.walk_int(f"{oids.LOGIN_SESSION}.{oids.LOGIN_SESSION_TYPE}")
            users = s.walk(f"{oids.LOGIN_SESSION}.{oids.LOGIN_SESSION_USER}")
            inet = s.walk_octets(f"{oids.LOGIN_SESSION}.{oids.LOGIN_SESSION_INET}")
        except SnmpError as exc:
            raise to_switch_error(exc, APIError) from exc
        return [
            WebSession(
                user=users.get(k, "?"),
                address=_inet_text(inet.get(k, b"")),
                type="https" if v == oids.LOGIN_TYPE_HTTPS else "http",
            )
            for k, v in sorted(types.items())
            if v in (oids.LOGIN_TYPE_HTTP, oids.LOGIN_TYPE_HTTPS)
        ]

    @contextmanager
    def _web(self) -> Iterator[NetgearWeb]:
        if self._web_factory is None:
            raise SwitchError("static routes need the web UI password (web_password)")
        self._wait_web_slot()
        web = self._web_factory()
        try:
            web.login()
            yield web
        finally:
            web.close()

    def _wait_web_slot(self) -> None:
        deadline = time.monotonic() + self._web_wait
        while True:
            try:
                limit = self._b.session.get_int(oids.HTTP_MAX_SESSIONS)
            except SnmpError as exc:
                raise to_switch_error(exc, APIError) from exc
            sessions = self.web_sessions()
            if len(sessions) < limit:
                return
            if time.monotonic() + self._web_poll_interval > deadline:
                held = ", ".join(f"{x.user} from {x.address} ({x.type})" for x in sessions)
                raise SwitchError(
                    f"no free web session slot after {self._web_wait:g}s, {len(sessions)} of {limit} taken: {held}"
                )
            logger.debug(f"web UI sessions exhausted ({len(sessions)}/{limit}), waiting")
            time.sleep(self._web_poll_interval)

    def list_static_routes(self) -> list[StaticRoute]:
        """Return the static and default routes from the web UI."""
        with self._web() as web:
            return web.read_static_routes()

    def add_static_route(self, dest: str, next_hop: str, preference: int = 1, description: str = "") -> None:
        """Add a static route (``0.0.0.0/0`` is the default route) and verify by reading back.

        Args:
            dest: Network in CIDR notation.
            next_hop: Next hop address.
            preference: 1-255.
            description: Up to 31 characters of ``[A-Za-z0-9_-]``.

        Raises:
            SwitchError: For invalid input or if the route does not show up.
        """
        try:
            net = ipaddress.IPv4Network(dest)
            nh = ipaddress.IPv4Address(next_hop)
        except ValueError as exc:
            raise SwitchError(f"static route {dest} via {next_hop}: {exc}") from exc
        if not 1 <= preference <= 255 or not DESCRIPTION_RE.match(description):
            raise SwitchError("static route: preference must be 1-255, description up to 31 of [A-Za-z0-9_-]")
        want = StaticRoute(dest=str(net), next_hop=str(nh), preference=preference, description=description)
        with self._web() as web:
            if want in web.read_static_routes():
                return
            web.add_static_route(want.dest, want.next_hop, preference, description)
            self._settle(web.read_static_routes, lambda routes: want in routes, f"static route {dest}")

    def delete_static_route(self, dest: str, next_hop: str) -> None:
        """Delete a static route; a missing one is a no-op."""
        with self._web() as web:
            if not any(r.dest == dest and r.next_hop == next_hop for r in web.read_static_routes()):
                return
            web.delete_static_route(dest, next_hop)
            self._settle(
                web.read_static_routes,
                lambda routes: not any(r.dest == dest and r.next_hop == next_hop for r in routes),
                f"deletion of static route {dest}",
            )

    def _settle[T](self, read: Callable[[], T], done: Callable[[T], bool], what: str) -> T:
        try:
            return poll_until(read, done, timeout=self._b.settle_timeout, interval=self._b.settle_interval, what=what)
        except SnmpSettleTimeoutError as exc:
            raise APIError(str(exc)) from exc


def _inet_text(raw: bytes) -> str:
    if len(raw) in (4, 16):
        return socket.inet_ntop(socket.AF_INET if len(raw) == 4 else socket.AF_INET6, raw)
    return raw.hex() or "?"
