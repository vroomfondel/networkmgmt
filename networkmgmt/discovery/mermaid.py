"""Mermaid flowchart diagram generation from NetworkTopology data."""

from __future__ import annotations

import ipaddress
import json
import socket

from loguru import logger

from networkmgmt.discovery._util import _strip_hostname_suffix
from networkmgmt.discovery.models import (
    DeviceCategory,
    DiscoveredHost,
    L2TopologyEntry,
    NetworkTopology,
    SubnetScan,
)
from networkmgmt.discovery.oui import _abbreviate_vendor


class MermaidGenerator:
    DIAGRAM_STYLES = ("auto", "flat", "categorized", "hierarchical")

    def __init__(
        self,
        topology: NetworkTopology,
        direction: str = "",
        diagram_style: str = "auto",
        elk: bool = False,
    ):
        self.topology = topology
        self.direction = direction  # "LR", "TD", or "" (auto)
        self.diagram_style = diagram_style
        self.elk = elk
        self._id_counter = 0
        # Build IP -> L2TopologyEntry lookup for port labels
        self._l2_by_ip: dict[str, L2TopologyEntry] = {e.host_ip: e for e in topology.l2_topology}
        # Per-subnet L2 lookups
        self._subnet_l2: dict[str, dict[str, L2TopologyEntry]] = {}
        for subnet in topology.subnets:
            iface_key = subnet.interface.name
            self._subnet_l2[iface_key] = {e.host_ip: e for e in subnet.l2_topology}

    def _next_id(self, prefix: str = "n") -> str:
        self._id_counter += 1
        return f"{prefix}{self._id_counter}"

    def _sanitize(self, text: str) -> str:
        """Sanitize text for Mermaid labels."""
        return text.replace('"', "'").replace("<", "&lt;").replace(">", "&gt;")

    def _host_label(
        self,
        host: DiscoveredHost,
        multiline: bool = True,
        compact: bool = False,
    ) -> str:
        """Build a display label for a host.

        Args:
            multiline: Use <br/> separators (True) or spaces (False).
            compact: If True, omit vendor when hostname is present (for dense diagrams).
        """
        parts = [host.ip]
        if host.hostname:
            parts.append(self._sanitize(_strip_hostname_suffix(host.hostname)))
        vendor = _abbreviate_vendor(host.vendor) if host.vendor else ""
        if vendor:
            # In compact mode, only show vendor when there's no hostname
            if not compact or not host.hostname:
                parts.append(self._sanitize(vendor))
        if host.services and not compact:
            parts.extend(self._sanitize(s) for s in host.services[:3])

        sep = "<br/>" if multiline else " "
        return sep.join(parts)

    def _port_label(self, host_ip: str, l2_lookup: dict[str, L2TopologyEntry] | None = None) -> str:
        """Return the switch port name for a host, or empty string if not available."""
        lookup = l2_lookup or self._l2_by_ip
        entry = lookup.get(host_ip)
        if entry and entry.switch.port_name:
            return entry.switch.port_name
        return ""

    def _has_hierarchy(self) -> bool:
        """Check if topology_tree has non-trivial hierarchy (not all direct)."""
        tree = self.topology.topology_tree
        if not tree:
            return False
        gw_ip = self.topology.gateway.ip if self.topology.gateway else ""
        # Non-trivial if any host's parent is NOT the gateway
        return any(parent != gw_ip for parent in tree.values())

    def _resolve_style(self) -> str:
        """Resolve 'auto' to the effective diagram style."""
        if self.diagram_style != "auto":
            return self.diagram_style
        if len(self.topology.subnets) > 1:
            return "categorized"
        if self._has_hierarchy():
            return "hierarchical"
        return "flat"

    def _wrap_mermaid(self, body: str) -> str:
        """Wrap raw mermaid body in fenced code block with optional config preamble."""
        fc_cfg: dict[str, object] = {}
        if self.elk:
            fc_cfg["defaultRenderer"] = "elk"
        total = sum(len(s.hosts) for s in self.topology.subnets)
        if total > 30:
            fc_cfg["nodeSpacing"] = 30
            fc_cfg["rankSpacing"] = 30

        preamble = ""
        if fc_cfg:
            inner = ", ".join(f'"{k}": {json.dumps(v)}' for k, v in fc_cfg.items())
            preamble = f'%%{{init: {{"flowchart": {{{inner}}}}}}}%%\n'

        return "```mermaid\n" + preamble + body + "\n```"

    def generate(self) -> str:
        """Generate Mermaid flowchart string.

        Diagram styles:
        - auto: categorized for multi-subnet, hierarchical if trace data
                available, flat otherwise
        - flat: all hosts listed directly under subnet (original layout)
        - categorized: hosts grouped by device category (IoT, Servers, etc.)
        - hierarchical: infrastructure subgraphs from traceroute topology tree
        """
        effective = self._resolve_style()
        total = sum(len(s.hosts) for s in self.topology.subnets)
        logger.info(
            f"Diagram style: {effective}"
            + (f" (from auto)" if self.diagram_style == "auto" else "")
            + f", {total} hosts, direction: {self.direction or 'auto'}"
            + (", renderer: elk" if self.elk else "")
        )

        dispatch = {
            "flat": self._generate_flat,
            "categorized": self._generate_categorized_single,
            "hierarchical": self._generate_hierarchical,
        }
        # Multi-subnet always uses _generate_multi_subnet; inner grouping
        # respects self.diagram_style
        if len(self.topology.subnets) > 1:
            return self._generate_multi_subnet()
        return dispatch[effective]()

    @staticmethod
    def _normalize_hostname(hostname: str) -> str:
        """Normalize hostname for cross-subnet comparison."""
        return _strip_hostname_suffix(hostname).lower().strip()

    def _detect_cross_subnet_hosts(
        self,
    ) -> list[tuple[DiscoveredHost, DiscoveredHost]]:
        """Find hosts appearing on multiple subnets by matching hostnames."""
        if len(self.topology.subnets) < 2:
            return []

        # Build per-subnet maps: normalized_hostname -> host
        subnet_maps: list[dict[str, DiscoveredHost]] = []
        for subnet in self.topology.subnets:
            name_map: dict[str, DiscoveredHost] = {}
            for host in subnet.hosts:
                if host.hostname and not host.is_gateway:
                    norm = self._normalize_hostname(host.hostname)
                    if norm:
                        name_map[norm] = host
            subnet_maps.append(name_map)

        pairs: list[tuple[DiscoveredHost, DiscoveredHost]] = []
        # Compare each pair of subnets
        for i in range(len(subnet_maps)):
            for j in range(i + 1, len(subnet_maps)):
                common = set(subnet_maps[i].keys()) & set(subnet_maps[j].keys())
                for name in sorted(common):
                    pairs.append((subnet_maps[i][name], subnet_maps[j][name]))

        return pairs

    def _generate_multi_subnet(self) -> str:
        """Generate Mermaid flowchart with categorized subgraphs per subnet."""
        subnets = self.topology.subnets
        total_hosts = sum(len(s.hosts) for s in subnets)
        compact = total_hosts > 40

        # Auto-select direction: TD for large diagrams, LR otherwise
        if self.direction:
            direction = self.direction
        else:
            direction = "TD" if total_hosts > 40 else "LR"

        lines: list[str] = [f"flowchart {direction}"]

        # Track IP -> mermaid ID across all subnets for cross-subnet linking
        ip_to_mid: dict[str, str] = {}

        # Local host subgraph — show all interfaces
        local_id = self._next_id("local")
        lines.append('    subgraph localbox["Local Host"]')
        hostname = socket.gethostname()
        iface_parts = [hostname]
        for subnet in subnets:
            iface = subnet.interface
            iface_parts.append(f"{iface.name}: {iface.ip}")
        local_label = "<br/>".join(iface_parts)
        lines.append(f'        {local_id}["{local_label}"]')
        lines.append("    end")
        lines.append("")

        # Per-subnet subgraphs
        subnet_gw_ids: list[tuple[SubnetScan, str]] = []
        # Collect connections to emit after all subgraphs are closed
        connections: list[str] = []

        for subnet in subnets:
            iface = subnet.interface
            network = ipaddress.IPv4Network(f"{iface.ip}/{iface.netmask}", strict=False)
            tree = subnet.topology_tree
            gw_ip = subnet.gateway.ip if subnet.gateway else ""

            lan_id = self._next_id("lan")
            lines.append(f'    subgraph {lan_id}["{network} ({iface.name})"]')

            # Gateway node
            gw_id = ""
            if subnet.gateway:
                gw = subnet.gateway
                gw_id = self._next_id("gw")
                gw_label = self._host_label(gw, compact=compact)
                lines.append(f'        {gw_id}{{{{"{gw_label}"}}}}')
                ip_to_mid[gw.ip] = gw_id

            non_gw_hosts = [h for h in subnet.hosts if not h.is_gateway]

            # Check if we have non-trivial hierarchy for this subnet
            has_hierarchy = bool(tree) and any(v != gw_ip for v in tree.values())

            # Per-subnet L2 lookup for port labels
            subnet_l2 = self._subnet_l2.get(iface.name, {})

            # Determine inner rendering style
            style = self.diagram_style
            if style == "flat":
                self._render_flat_hosts(
                    lines,
                    connections,
                    non_gw_hosts,
                    gw_id,
                    ip_to_mid,
                    compact,
                    indent=2,
                )
            elif style == "hierarchical" and has_hierarchy:
                self._render_hierarchical_subnet(
                    lines,
                    connections,
                    non_gw_hosts,
                    tree,
                    gw_ip,
                    gw_id,
                    ip_to_mid,
                    compact,
                    indent=2,
                    l2_lookup=subnet_l2,
                )
            elif style == "categorized":
                self._render_categorized_hosts(
                    lines,
                    connections,
                    non_gw_hosts,
                    gw_id,
                    ip_to_mid,
                    compact,
                    indent=2,
                )
            else:
                # auto: prefer hierarchy if available, else categorized
                if has_hierarchy:
                    self._render_hierarchical_subnet(
                        lines,
                        connections,
                        non_gw_hosts,
                        tree,
                        gw_ip,
                        gw_id,
                        ip_to_mid,
                        compact,
                        indent=2,
                        l2_lookup=subnet_l2,
                    )
                else:
                    self._render_categorized_hosts(
                        lines,
                        connections,
                        non_gw_hosts,
                        gw_id,
                        ip_to_mid,
                        compact,
                        indent=2,
                    )

            lines.append("    end")
            lines.append("")

            if gw_id:
                subnet_gw_ids.append((subnet, gw_id))

        # Connect local host to each gateway
        for subnet, gw_id in subnet_gw_ids:
            lines.append(f"    {local_id} -->|{subnet.interface.name}| {gw_id}")
        lines.append("")

        # Emit deferred connections (gateway->hosts, infra->children)
        for conn in connections:
            lines.append(conn)
        if connections:
            lines.append("")

        # Cross-subnet links
        cross_pairs = self._detect_cross_subnet_hosts()
        if cross_pairs:
            lines.append("    %% Cross-subnet hosts (same device)")
            for host_a, host_b in cross_pairs:
                mid_a = ip_to_mid.get(host_a.ip)
                mid_b = ip_to_mid.get(host_b.ip)
                if mid_a and mid_b:
                    short_name = _strip_hostname_suffix(host_a.hostname)
                    lines.append(f'    {mid_a} <-.->|"same: {self._sanitize(short_name)}"| {mid_b}')
            lines.append("")

        # Traceroute subgraphs
        first_gw_id = subnet_gw_ids[0][1] if subnet_gw_ids else ""
        for trace in self.topology.traceroute_paths:
            if not trace.hops:
                continue

            trace_label = self._sanitize(trace.target)
            trace_sg_id = self._next_id("trace")
            lines.append(f'    subgraph {trace_sg_id}["Traceroute: {trace_label}"]')

            hop_ids: list[str] = []
            for hop in trace.hops:
                hid = self._next_id("t")
                hop_ids.append(hid)

                if hop.is_timeout:
                    timeout_label = (
                        f"{hop.ip}<br/>UNREACHABLE" if hop.hostname == "UNREACHABLE" else f"Hop {hop.hop_number}: * * *"
                    )
                    lines.append(f'        {hid}["{timeout_label}"]')
                else:
                    hop_label = f"Hop {hop.hop_number}: {hop.ip}"
                    if hop.hostname:
                        hop_label += f"<br/>{self._sanitize(hop.hostname)}"
                    if hop.rtt_ms:
                        hop_label += f"<br/>{hop.rtt_ms:.1f}ms"
                    lines.append(f'        {hid}["{hop_label}"]')

            lines.append("    end")

            for i in range(len(hop_ids) - 1):
                lines.append(f"    {hop_ids[i]} --> {hop_ids[i + 1]}")

            if first_gw_id and hop_ids:
                lines.append(f"    {first_gw_id} --> {hop_ids[0]}")

            lines.append("")

        return self._wrap_mermaid("\n".join(lines))

    def _render_flat_hosts(
        self,
        lines: list[str],
        connections: list[str],
        hosts: list[DiscoveredHost],
        gw_id: str,
        ip_to_mid: dict[str, str],
        compact: bool,
        indent: int = 2,
    ) -> None:
        """Render hosts as a flat list without grouping."""
        pad = "    " * indent
        for host in hosts:
            hid = self._next_id("h")
            ip_to_mid[host.ip] = hid
            label = self._host_label(host, compact=compact)
            lines.append(f'{pad}{hid}["{label}"]')
            if gw_id:
                connections.append(f"    {gw_id} -.-> {hid}")

    def _render_categorized_hosts(
        self,
        lines: list[str],
        connections: list[str],
        hosts: list[DiscoveredHost],
        gw_id: str,
        ip_to_mid: dict[str, str],
        compact: bool,
        indent: int = 2,
    ) -> None:
        """Render hosts grouped by device category as nested subgraphs."""
        pad = "    " * indent

        # Group hosts by category
        by_category: dict[str, list[DiscoveredHost]] = {}
        for host in hosts:
            cat = host.category or DeviceCategory.OTHER.value
            by_category.setdefault(cat, []).append(host)

        # Separate singletons (categories with 1 member) from groups
        singletons: list[DiscoveredHost] = []
        groups: dict[str, list[DiscoveredHost]] = {}
        for cat, cat_hosts in by_category.items():
            if len(cat_hosts) < 2:
                singletons.extend(cat_hosts)
            else:
                groups[cat] = cat_hosts

        # Render grouped categories as nested subgraphs
        for cat in sorted(groups.keys()):
            cat_hosts = groups[cat]
            sg_id = self._next_id("cat")
            lines.append(f'{pad}subgraph {sg_id}["{cat} ({len(cat_hosts)})"]')

            for host in cat_hosts:
                hid = self._next_id("h")
                ip_to_mid[host.ip] = hid
                label = self._host_label(host, compact=compact)
                lines.append(f'{pad}    {hid}["{label}"]')

            lines.append(f"{pad}end")

            # Connect gateway to category hosts
            if gw_id:
                for host in cat_hosts:
                    mid = ip_to_mid.get(host.ip)
                    if mid:
                        connections.append(f"    {gw_id} -.-> {mid}")

        # Render singletons directly in the subnet subgraph
        for host in singletons:
            hid = self._next_id("h")
            ip_to_mid[host.ip] = hid
            label = self._host_label(host, compact=compact)
            lines.append(f'{pad}{hid}["{label}"]')
            if gw_id:
                connections.append(f"    {gw_id} -.-> {hid}")

    def _render_hierarchical_subnet(
        self,
        lines: list[str],
        connections: list[str],
        hosts: list[DiscoveredHost],
        tree: dict[str, str],
        gw_ip: str,
        gw_id: str,
        ip_to_mid: dict[str, str],
        compact: bool,
        indent: int = 2,
        l2_lookup: dict[str, L2TopologyEntry] | None = None,
    ) -> None:
        """Render hosts using hierarchy from topology_tree within a subnet subgraph."""
        pad = "    " * indent
        host_by_ip = {h.ip: h for h in hosts}

        # Identify infrastructure nodes
        infra_ips: set[str] = set()
        for parent_ip in tree.values():
            if parent_ip != gw_ip:
                infra_ips.add(parent_ip)

        # Group children by parent
        children_of: dict[str, list[str]] = {}
        for host_ip, parent_ip in tree.items():
            children_of.setdefault(parent_ip, []).append(host_ip)
        for parent_ip in children_of:
            children_of[parent_ip].sort(key=lambda ip: ipaddress.IPv4Address(ip))

        # Infrastructure subgraphs
        sorted_infra = sorted(infra_ips, key=lambda ip: ipaddress.IPv4Address(ip))
        for infra_ip in sorted_infra:
            infra_host = host_by_ip.get(infra_ip)
            sg_id = self._next_id("sw")

            if infra_host:
                parts = []
                if infra_host.hostname:
                    parts.append(self._sanitize(_strip_hostname_suffix(infra_host.hostname)))
                if infra_host.vendor:
                    parts.append(self._sanitize(_abbreviate_vendor(infra_host.vendor)))
                last_octet = infra_ip.rsplit(".", 1)[-1]
                parts.append(f".{last_octet}")
                sg_label = " ".join(parts)
            else:
                sg_label = infra_ip

            lines.append(f'{pad}subgraph {sg_id}["{sg_label}"]')

            # The infrastructure node itself
            infra_node_id = self._next_id("sw")
            if infra_host:
                infra_label = self._host_label(infra_host, compact=compact)
                lines.append(f'{pad}    {infra_node_id}{{{{"{infra_label}"}}}}')
            else:
                lines.append(f'{pad}    {infra_node_id}{{{{"{infra_ip}"}}}}')
            ip_to_mid[infra_ip] = infra_node_id

            # Children of this infrastructure node
            for child_ip in children_of.get(infra_ip, []):
                if child_ip in infra_ips:
                    continue
                child_host = host_by_ip.get(child_ip)
                child_id = self._next_id("h")
                ip_to_mid[child_ip] = child_id
                if child_host:
                    label = self._host_label(child_host, compact=compact)
                    lines.append(f'{pad}    {child_id}["{label}"]')
                else:
                    lines.append(f'{pad}    {child_id}["{child_ip}"]')

            lines.append(f"{pad}end")

        # Gateway -> infra connections (with optional port labels)
        if gw_id:
            for infra_ip in sorted_infra:
                infra_parent: str | None = tree.get(infra_ip, gw_ip)
                if infra_parent == gw_ip and infra_ip in ip_to_mid:
                    port = self._port_label(infra_ip, l2_lookup)
                    if port:
                        connections.append(f"    {gw_id} -->|{port}| {ip_to_mid[infra_ip]}")
                    else:
                        connections.append(f"    {gw_id} --> {ip_to_mid[infra_ip]}")

            # Infra -> child infra (nested switches, with port labels)
            for infra_ip in sorted_infra:
                infra_parent = tree.get(infra_ip)
                if infra_parent and infra_parent != gw_ip and infra_parent in ip_to_mid:
                    port = self._port_label(infra_ip, l2_lookup)
                    if port:
                        connections.append(f"    {ip_to_mid[infra_parent]} -->|{port}| {ip_to_mid[infra_ip]}")
                    else:
                        connections.append(f"    {ip_to_mid[infra_parent]} --> {ip_to_mid[infra_ip]}")

        # Infra -> leaf host connections (with port labels)
        if l2_lookup:
            for infra_ip in sorted_infra:
                for child_ip in children_of.get(infra_ip, []):
                    if child_ip in infra_ips:
                        continue
                    if child_ip in ip_to_mid and infra_ip in ip_to_mid:
                        port = self._port_label(child_ip, l2_lookup)
                        if port:
                            connections.append(f"    {ip_to_mid[infra_ip]} -->|{port}| {ip_to_mid[child_ip]}")

        # Direct hosts (parent = gateway, or not in tree) — group by category
        gateway_children = children_of.get(gw_ip, [])
        all_direct_ips = [ip for ip in gateway_children if ip not in infra_ips]
        # Add hosts not in tree
        hosts_in_tree = set(tree.keys()) | infra_ips
        for h in hosts:
            if (
                not h.is_gateway
                and not h.is_infrastructure
                and h.ip not in hosts_in_tree
                and h.ip not in all_direct_ips
            ):
                all_direct_ips.append(h.ip)
        all_direct_ips.sort(key=lambda ip: ipaddress.IPv4Address(ip))

        direct_hosts = [host_by_ip[ip] for ip in all_direct_ips if ip in host_by_ip]

        # Use category grouping for direct hosts
        self._render_categorized_hosts(
            lines,
            connections,
            direct_hosts,
            gw_id,
            ip_to_mid,
            compact,
            indent=indent,
        )

    def _generate_categorized_single(self) -> str:
        """Generate single-subnet Mermaid flowchart with category subgraphs."""
        total_hosts = len(self.topology.local_hosts)
        compact = total_hosts > 40
        if self.direction:
            direction = self.direction
        else:
            direction = "TD" if total_hosts > 40 else "LR"

        lines: list[str] = [f"flowchart {direction}"]
        iface = self.topology.local_interface
        network = ipaddress.IPv4Network(f"{iface.ip}/{iface.netmask}", strict=False)
        ip_to_mid: dict[str, str] = {}

        # Local host subgraph
        local_id = self._next_id("local")
        lines.append('    subgraph localbox["Local Host"]')
        local_label = f"{iface.ip}<br/>{socket.gethostname()}<br/>{iface.name}"
        if iface.mac:
            local_label += f"<br/>{iface.mac}"
        lines.append(f'        {local_id}["{local_label}"]')
        lines.append("    end")
        lines.append("")

        # LAN subgraph
        gw_id = ""
        lines.append(f'    subgraph lan["{network}"]')

        if self.topology.gateway:
            gw = self.topology.gateway
            gw_id = self._next_id("gw")
            gw_label = self._host_label(gw, compact=compact)
            lines.append(f'        {gw_id}{{{{"{gw_label}"}}}}')
            ip_to_mid[gw.ip] = gw_id

        non_gw_hosts = [h for h in self.topology.local_hosts if not h.is_gateway]
        connections: list[str] = []
        self._render_categorized_hosts(
            lines,
            connections,
            non_gw_hosts,
            gw_id,
            ip_to_mid,
            compact,
            indent=2,
        )

        lines.append("    end")
        lines.append("")

        # Connections
        if gw_id:
            lines.append(f"    {local_id} -->|default route| {gw_id}")
        for conn in connections:
            lines.append(conn)
        lines.append("")

        # Traceroute subgraphs
        for trace in self.topology.traceroute_paths:
            if not trace.hops:
                continue

            trace_label = self._sanitize(trace.target)
            trace_sg_id = self._next_id("trace")
            lines.append(f'    subgraph {trace_sg_id}["Traceroute: {trace_label}"]')

            hop_ids: list[str] = []
            for hop in trace.hops:
                hid = self._next_id("t")
                hop_ids.append(hid)

                if hop.is_timeout:
                    timeout_label = (
                        f"{hop.ip}<br/>UNREACHABLE" if hop.hostname == "UNREACHABLE" else f"Hop {hop.hop_number}: * * *"
                    )
                    lines.append(f'        {hid}["{timeout_label}"]')
                else:
                    hop_label = f"Hop {hop.hop_number}: {hop.ip}"
                    if hop.hostname:
                        hop_label += f"<br/>{self._sanitize(hop.hostname)}"
                    if hop.rtt_ms:
                        hop_label += f"<br/>{hop.rtt_ms:.1f}ms"
                    lines.append(f'        {hid}["{hop_label}"]')

            lines.append("    end")

            for i in range(len(hop_ids) - 1):
                lines.append(f"    {hop_ids[i]} --> {hop_ids[i + 1]}")

            if gw_id and hop_ids:
                lines.append(f"    {gw_id} --> {hop_ids[0]}")

            lines.append("")

        return self._wrap_mermaid("\n".join(lines))

    def _generate_flat(self) -> str:
        """Generate flat Mermaid flowchart (original layout)."""
        lines: list[str] = ["flowchart LR"]
        iface = self.topology.local_interface
        network = ipaddress.IPv4Network(f"{iface.ip}/{iface.netmask}", strict=False)

        # Local host subgraph
        local_id = self._next_id("local")
        lines.append('    subgraph localbox["Local Host"]')
        local_label = f"{iface.ip}<br/>{socket.gethostname()}<br/>{iface.name}"
        if iface.mac:
            local_label += f"<br/>{iface.mac}"
        lines.append(f'        {local_id}["{local_label}"]')
        lines.append("    end")
        lines.append("")

        # LAN subgraph
        gw_id = ""
        host_ids: list[str] = []
        non_gw_hosts = [h for h in self.topology.local_hosts if not h.is_gateway]

        lines.append(f'    subgraph lan["{network}"]')

        if self.topology.gateway:
            gw = self.topology.gateway
            gw_id = self._next_id("gw")
            gw_label = self._host_label(gw)
            lines.append(f'        {gw_id}{{{{"{gw_label}"}}}}')

        for host in non_gw_hosts:
            hid = self._next_id("h")
            host_ids.append(hid)
            label = self._host_label(host)
            lines.append(f'        {hid}["{label}"]')

        lines.append("    end")
        lines.append("")

        # Connections
        if gw_id:
            lines.append(f"    {local_id} -->|default route| {gw_id}")
            for hid in host_ids:
                lines.append(f"    {gw_id} -.-> {hid}")
        lines.append("")

        # Traceroute subgraphs
        for trace in self.topology.traceroute_paths:
            if not trace.hops:
                continue

            trace_label = self._sanitize(trace.target)
            trace_sg_id = self._next_id("trace")
            lines.append(f'    subgraph {trace_sg_id}["Traceroute: {trace_label}"]')

            hop_ids: list[str] = []
            for hop in trace.hops:
                hid = self._next_id("t")
                hop_ids.append(hid)

                if hop.is_timeout:
                    timeout_label = (
                        f"{hop.ip}<br/>UNREACHABLE" if hop.hostname == "UNREACHABLE" else f"Hop {hop.hop_number}: * * *"
                    )
                    lines.append(f'        {hid}["{timeout_label}"]')
                else:
                    hop_label = f"Hop {hop.hop_number}: {hop.ip}"
                    if hop.hostname:
                        hop_label += f"<br/>{self._sanitize(hop.hostname)}"
                    if hop.rtt_ms:
                        hop_label += f"<br/>{hop.rtt_ms:.1f}ms"
                    lines.append(f'        {hid}["{hop_label}"]')

            lines.append("    end")

            # Chain hops
            for i in range(len(hop_ids) - 1):
                lines.append(f"    {hop_ids[i]} --> {hop_ids[i + 1]}")

            # Connect gateway to first hop
            if gw_id and hop_ids:
                lines.append(f"    {gw_id} --> {hop_ids[0]}")

            lines.append("")

        return self._wrap_mermaid("\n".join(lines))

    def _generate_hierarchical(self) -> str:
        """Generate hierarchical Mermaid flowchart with infrastructure subgraphs."""
        lines: list[str] = ["flowchart LR"]
        iface = self.topology.local_interface
        network = ipaddress.IPv4Network(f"{iface.ip}/{iface.netmask}", strict=False)
        tree = self.topology.topology_tree
        gw_ip = self.topology.gateway.ip if self.topology.gateway else ""

        # Build lookup: ip -> host
        host_by_ip: dict[str, DiscoveredHost] = {h.ip: h for h in self.topology.local_hosts}

        # Identify infrastructure nodes (parents that are not the gateway)
        infra_ips: set[str] = set()
        for parent_ip in tree.values():
            if parent_ip != gw_ip:
                infra_ips.add(parent_ip)

        # Group children by parent
        children_of: dict[str, list[str]] = {}
        for host_ip, parent_ip in tree.items():
            children_of.setdefault(parent_ip, []).append(host_ip)

        # Sort children by IP for deterministic output
        for parent_ip in children_of:
            children_of[parent_ip].sort(key=lambda ip: ipaddress.IPv4Address(ip))

        # Hosts not in the tree at all (gateway, local, or missed)
        direct_hosts = [
            h
            for h in self.topology.local_hosts
            if not h.is_gateway and not h.is_infrastructure and h.ip not in tree and h.ip != iface.ip
        ]

        # Track mermaid IDs for connections
        ip_to_mermaid_id: dict[str, str] = {}

        # Local host subgraph
        local_id = self._next_id("local")
        lines.append('    subgraph localbox["Local Host"]')
        local_label = f"{iface.ip}<br/>{socket.gethostname()}<br/>{iface.name}"
        if iface.mac:
            local_label += f"<br/>{iface.mac}"
        lines.append(f'        {local_id}["{local_label}"]')
        lines.append("    end")
        lines.append("")

        # LAN subgraph
        lines.append(f'    subgraph lan["{network}"]')

        # Gateway node
        gw_id = ""
        if self.topology.gateway:
            gw = self.topology.gateway
            gw_id = self._next_id("gw")
            gw_label = self._host_label(gw)
            lines.append(f'        {gw_id}{{{{"{gw_label}"}}}}')
            ip_to_mermaid_id[gw_ip] = gw_id

        # Infrastructure subgraphs with their children
        sorted_infra = sorted(infra_ips, key=lambda ip: ipaddress.IPv4Address(ip))
        for infra_ip in sorted_infra:
            infra_host = host_by_ip.get(infra_ip)
            sg_id = self._next_id("sw")

            # Build subgraph label from host info
            if infra_host:
                parts = []
                if infra_host.hostname:
                    parts.append(self._sanitize(infra_host.hostname))
                if infra_host.vendor:
                    parts.append(self._sanitize(infra_host.vendor))
                last_octet = infra_ip.rsplit(".", 1)[-1]
                parts.append(f".{last_octet}")
                sg_label = " ".join(parts)
            else:
                sg_label = infra_ip

            lines.append(f'        subgraph {sg_id}["{sg_label}"]')

            # The infrastructure node itself
            infra_node_id = self._next_id("sw")
            if infra_host:
                infra_label = self._host_label(infra_host)
                lines.append(f'            {infra_node_id}{{{{"{infra_label}"}}}}')
            else:
                lines.append(f'            {infra_node_id}{{{{"{infra_ip}"}}}}')
            ip_to_mermaid_id[infra_ip] = infra_node_id

            # Children of this infrastructure node
            for child_ip in children_of.get(infra_ip, []):
                # Skip if child is itself an infrastructure node (rendered as own subgraph)
                if child_ip in infra_ips:
                    continue
                child_host = host_by_ip.get(child_ip)
                child_id = self._next_id("h")
                ip_to_mermaid_id[child_ip] = child_id
                if child_host:
                    label = self._host_label(child_host)
                    lines.append(f'            {child_id}["{label}"]')
                else:
                    lines.append(f'            {child_id}["{child_ip}"]')

            lines.append("        end")

        # Direct hosts (parent = gateway, or not in tree)
        gateway_children = children_of.get(gw_ip, [])
        all_direct = [ip for ip in gateway_children if ip not in infra_ips]
        # Add hosts not in tree at all
        for h in direct_hosts:
            if h.ip not in all_direct:
                all_direct.append(h.ip)
        all_direct.sort(key=lambda ip: ipaddress.IPv4Address(ip))

        for host_ip in all_direct:
            host = host_by_ip.get(host_ip)
            hid = self._next_id("h")
            ip_to_mermaid_id[host_ip] = hid
            if host:
                label = self._host_label(host)
                lines.append(f'        {hid}["{label}"]')
            else:
                lines.append(f'        {hid}["{host_ip}"]')

        lines.append("    end")
        lines.append("")

        # Connections (with port labels from L2 topology if available)
        l2 = self._l2_by_ip
        if gw_id:
            lines.append(f"    {local_id} -->|default route| {gw_id}")

            # Gateway -> infrastructure nodes
            for infra_ip in sorted_infra:
                # Only connect if this infra node's parent is the gateway
                infra_parent: str | None = tree.get(infra_ip, gw_ip)
                if infra_parent == gw_ip and infra_ip in ip_to_mermaid_id:
                    port = self._port_label(infra_ip, l2)
                    if port:
                        lines.append(f"    {gw_id} -->|{port}| {ip_to_mermaid_id[infra_ip]}")
                    else:
                        lines.append(f"    {gw_id} --> {ip_to_mermaid_id[infra_ip]}")

            # Infra -> child infra (nested switches)
            for infra_ip in sorted_infra:
                infra_parent = tree.get(infra_ip)
                if infra_parent and infra_parent != gw_ip and infra_parent in ip_to_mermaid_id:
                    port = self._port_label(infra_ip, l2)
                    if port:
                        lines.append(f"    {ip_to_mermaid_id[infra_parent]} -->|{port}| {ip_to_mermaid_id[infra_ip]}")
                    else:
                        lines.append(f"    {ip_to_mermaid_id[infra_parent]} --> {ip_to_mermaid_id[infra_ip]}")

            # Infra -> leaf host connections (with port labels)
            if l2:
                for infra_ip in sorted_infra:
                    for child_ip in children_of.get(infra_ip, []):
                        if child_ip in infra_ips:
                            continue
                        if child_ip in ip_to_mermaid_id and infra_ip in ip_to_mermaid_id:
                            port = self._port_label(child_ip, l2)
                            if port:
                                lines.append(
                                    f"    {ip_to_mermaid_id[infra_ip]} -->|{port}| {ip_to_mermaid_id[child_ip]}"
                                )

            # Gateway -> direct hosts
            for host_ip in all_direct:
                if host_ip in ip_to_mermaid_id:
                    lines.append(f"    {gw_id} -.-> {ip_to_mermaid_id[host_ip]}")

        lines.append("")

        # Traceroute subgraphs (same as flat)
        for trace in self.topology.traceroute_paths:
            if not trace.hops:
                continue

            trace_label = self._sanitize(trace.target)
            trace_sg_id = self._next_id("trace")
            lines.append(f'    subgraph {trace_sg_id}["Traceroute: {trace_label}"]')

            hop_ids: list[str] = []
            for hop in trace.hops:
                hid = self._next_id("t")
                hop_ids.append(hid)

                if hop.is_timeout:
                    timeout_label = (
                        f"{hop.ip}<br/>UNREACHABLE" if hop.hostname == "UNREACHABLE" else f"Hop {hop.hop_number}: * * *"
                    )
                    lines.append(f'        {hid}["{timeout_label}"]')
                else:
                    hop_label = f"Hop {hop.hop_number}: {hop.ip}"
                    if hop.hostname:
                        hop_label += f"<br/>{self._sanitize(hop.hostname)}"
                    if hop.rtt_ms:
                        hop_label += f"<br/>{hop.rtt_ms:.1f}ms"
                    lines.append(f'        {hid}["{hop_label}"]')

            lines.append("    end")

            for i in range(len(hop_ids) - 1):
                lines.append(f"    {hop_ids[i]} --> {hop_ids[i + 1]}")

            if gw_id and hop_ids:
                lines.append(f"    {gw_id} --> {hop_ids[0]}")

            lines.append("")

        return self._wrap_mermaid("\n".join(lines))
