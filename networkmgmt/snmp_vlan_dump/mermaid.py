"""Mermaid diagram generation for VLAN-port topology."""

from __future__ import annotations

from collections import defaultdict

from networkmgmt.snmp_vlan_dump._util import (
    format_port_range,
    port_is_active,
    unit_summary_str,
)
from networkmgmt.snmp_vlan_dump.models import VlanDumpData


class VlanMermaidGenerator:
    """Generate Mermaid flowchart diagrams from VLAN dump data.

    Supports three diagram styles:
      - aggregated: all ports grouped by identical VLAN pattern, per unit
      - trunks: trunk ports only, with access port counts
      - vlan: VLAN-centric subgraphs with ports inside
    """

    DIAGRAM_STYLES = ("aggregated", "trunks", "vlan")

    def __init__(self, data: VlanDumpData, style: str = "aggregated") -> None:
        self.data = data
        self.style = style
        self._id_counter = 0

    def _next_id(self, prefix: str = "n") -> str:
        self._id_counter += 1
        return f"{prefix}{self._id_counter}"

    def generate(self) -> str:
        """Generate a Mermaid diagram string. Dispatches to the selected style."""
        dispatch = {
            "aggregated": self._generate_aggregated,
            "trunks": self._generate_trunks,
            "vlan": self._generate_vlan_centric,
        }
        lines = dispatch[self.style]()
        return "\n".join(lines)

    # ── Aggregated style ──────────────────────────────────────────────

    def _generate_aggregated(self) -> list[str]:
        """All ports grouped by identical VLAN pattern, per unit."""
        d = self.data
        lines: list[str] = []
        lines.append("```mermaid")
        lines.append("flowchart TD")

        # Group active ports by (unit, vlan_pattern)
        port_groups: dict[tuple, list[int]] = defaultdict(list)
        for unit_num in sorted(d.unit_ports):
            for p in d.unit_ports[unit_num]:
                if not port_is_active(p, d.port_vlans, d.oper_status):
                    continue
                info = d.port_vlans[p]
                key = (unit_num, tuple(info.untagged), tuple(info.tagged))
                port_groups[key].append(p)

        # Assign node IDs and build labels
        group_nodes: dict[tuple, tuple[str, str]] = {}
        node_counter = 0
        for key, ports in sorted(port_groups.items()):
            unit_num = key[0]
            node_id = f"grp{node_counter}"
            node_counter += 1
            names = [d.port_map[p] for p in ports]
            if len(ports) == 1:
                label = names[0]
            else:
                range_str = format_port_range(names)
                label = f"U{unit_num}/{range_str}<br/>({len(ports)} ports)"
            group_nodes[key] = (node_id, label)

        # Collect active VLANs
        active_vlans: set[int] = set()
        for _, untagged, tagged in port_groups:
            active_vlans.update(untagged)
            active_vlans.update(tagged)

        # Unit subgraphs with port group nodes
        groups_by_unit: dict[int, list[tuple]] = defaultdict(list)
        for key in port_groups:
            groups_by_unit[key[0]].append(key)

        for unit_num in sorted(groups_by_unit):
            ui = d.unit_info[unit_num]
            summary = unit_summary_str(ui)
            lines.append(f'    subgraph unit{unit_num}["Unit {unit_num} ({summary})"]')
            for key in sorted(groups_by_unit[unit_num]):
                node_id, label = group_nodes[key]
                lines.append(f'        {node_id}["{label}"]')
            lines.append("    end")

        # VLAN nodes (double circle for visual distinction)
        for vlan_id in sorted(active_vlans):
            name = d.vlan_names.get(vlan_id, "")
            label = f"VLAN {vlan_id}" + (f"<br/>{name}" if name else "")
            lines.append(f'    vlan{vlan_id}(("{label}"))')

        # Edges: solid for untagged, dashed for tagged
        for key in sorted(port_groups):
            _, untagged, tagged = key
            node_id, _ = group_nodes[key]
            for vid in untagged:
                lines.append(f"    {node_id} --> vlan{vid}")
            for vid in tagged:
                lines.append(f"    {node_id} -.-> vlan{vid}")

        lines.append("```")
        lines.append("")
        return lines

    # ── Trunks style ──────────────────────────────────────────────────

    def _generate_trunks(self) -> list[str]:
        """Trunk ports only, with access port counts."""
        d = self.data
        lines: list[str] = []
        lines.append("```mermaid")
        lines.append("flowchart TD")

        # Group trunk ports (have tagged VLANs) by (unit, vlan_pattern)
        trunk_groups: dict[tuple, list[int]] = defaultdict(list)
        for unit_num in sorted(d.unit_ports):
            for p in d.unit_ports[unit_num]:
                if not port_is_active(p, d.port_vlans, d.oper_status):
                    continue
                info = d.port_vlans[p]
                if not info.tagged:
                    continue
                key = (unit_num, tuple(info.untagged), tuple(info.tagged))
                trunk_groups[key].append(p)

        # Count access ports per VLAN (ports with untagged only, no tagged)
        access_count: dict[int, int] = defaultdict(int)
        for unit_num in sorted(d.unit_ports):
            for p in d.unit_ports[unit_num]:
                if not port_is_active(p, d.port_vlans, d.oper_status):
                    continue
                info = d.port_vlans[p]
                if info.tagged:
                    continue
                for vid in info.untagged:
                    access_count[vid] += 1

        # Assign node IDs and build labels
        group_nodes: dict[tuple, tuple[str, str]] = {}
        node_counter = 0
        for key, ports in sorted(trunk_groups.items()):
            unit_num = key[0]
            node_id = f"t{node_counter}"
            node_counter += 1
            names = [d.port_map[p] for p in ports]
            if len(ports) == 1:
                label = names[0]
            else:
                range_str = format_port_range(names)
                label = f"U{unit_num}/{range_str}<br/>({len(ports)} ports)"
            group_nodes[key] = (node_id, label)

        # Collect active VLANs (from trunk ports only)
        active_vlans: set[int] = set()
        for _, untagged, tagged in trunk_groups:
            active_vlans.update(untagged)
            active_vlans.update(tagged)

        # Unit subgraphs (only units with trunk ports)
        groups_by_unit: dict[int, list[tuple]] = defaultdict(list)
        for key in trunk_groups:
            groups_by_unit[key[0]].append(key)

        for unit_num in sorted(groups_by_unit):
            ui = d.unit_info[unit_num]
            summary = unit_summary_str(ui)
            lines.append(f'    subgraph unit{unit_num}["Unit {unit_num} ({summary})"]')
            for key in sorted(groups_by_unit[unit_num]):
                node_id, label = group_nodes[key]
                lines.append(f'        {node_id}["{label}"]')
            lines.append("    end")

        # VLAN nodes with access port count annotation
        for vlan_id in sorted(active_vlans):
            name = d.vlan_names.get(vlan_id, "")
            label = f"VLAN {vlan_id}" + (f"<br/>{name}" if name else "")
            ac = access_count.get(vlan_id, 0)
            if ac:
                label += f"<br/>(+{ac} access)"
            lines.append(f'    vlan{vlan_id}(("{label}"))')

        # Edges: solid for untagged, dashed for tagged
        for key in sorted(trunk_groups):
            _, untagged, tagged = key
            node_id, _ = group_nodes[key]
            for vid in untagged:
                lines.append(f"    {node_id} --> vlan{vid}")
            for vid in tagged:
                lines.append(f"    {node_id} -.-> vlan{vid}")

        lines.append("```")
        lines.append("")
        return lines

    # ── VLAN-centric style ────────────────────────────────────────────

    def _generate_vlan_centric(self) -> list[str]:
        """VLAN subgraphs with access ports inside, trunk ports separate."""
        d = self.data
        lines: list[str] = []
        lines.append("```mermaid")
        lines.append("flowchart TD")

        # Classify ports into access (no tagged VLANs) and trunk (has tagged VLANs)
        access_ports: list[tuple[int, int]] = []  # (port_idx, unit_num)
        trunk_ports: list[tuple[int, int]] = []  # (port_idx, unit_num)
        for unit_num in sorted(d.unit_ports):
            for p in d.unit_ports[unit_num]:
                if not port_is_active(p, d.port_vlans, d.oper_status):
                    continue
                info = d.port_vlans[p]
                if info.tagged:
                    trunk_ports.append((p, unit_num))
                else:
                    access_ports.append((p, unit_num))

        # Group access ports by (pvid, unit) for placement inside VLAN subgraphs
        access_by_vlan_unit: dict[int, dict[int, list[int]]] = defaultdict(
            lambda: defaultdict(list),
        )
        # Track extra untagged VLANs beyond PVID (need edges)
        access_extra_untagged: list[tuple[str, int]] = []  # (node_id, vlan_id)
        for p, unit_num in access_ports:
            pvid = d.pvid_data.get(p, 0)
            access_by_vlan_unit[pvid][unit_num].append(p)

        # Group trunk ports by (unit, vlan_pattern)
        trunk_groups: dict[tuple, list[int]] = defaultdict(list)
        for p, unit_num in trunk_ports:
            info = d.port_vlans[p]
            key = (unit_num, tuple(info.untagged), tuple(info.tagged))
            trunk_groups[key].append(p)

        # Collect all VLANs that have access port members
        access_vlans = set(access_by_vlan_unit.keys())

        # Collect all VLANs referenced by trunk ports
        trunk_vlans: set[int] = set()
        for _, untagged, tagged in trunk_groups:
            trunk_vlans.update(untagged)
            trunk_vlans.update(tagged)

        all_vlans = access_vlans | trunk_vlans

        # Build VLAN subgraphs with access ports inside
        node_counter = 0
        access_nodes: dict[tuple, str] = {}

        for vlan_id in sorted(all_vlans):
            name = d.vlan_names.get(vlan_id, "")
            vlan_label = f"VLAN {vlan_id}"
            if name:
                vlan_label += f" — {name}"
            lines.append(f'    subgraph v{vlan_id}["{vlan_label}"]')

            if vlan_id in access_by_vlan_unit:
                for unit_num in sorted(access_by_vlan_unit[vlan_id]):
                    ports = access_by_vlan_unit[vlan_id][unit_num]
                    names = [d.port_map[p] for p in ports]
                    node_id = f"v{vlan_id}a{node_counter}"
                    node_counter += 1

                    if len(ports) == 1:
                        label = names[0]
                    else:
                        range_str = format_port_range(names)
                        label = f"U{unit_num}/{range_str} ({len(ports)} ports)"
                    lines.append(f'        {node_id}["{label}"]')
                    access_nodes[(vlan_id, unit_num, tuple(ports))] = node_id

                    # Check for extra untagged VLANs beyond PVID
                    for p in ports:
                        info = d.port_vlans[p]
                        for vid in info.untagged:
                            if vid != vlan_id:
                                access_extra_untagged.append((node_id, vid))

            lines.append("    end")

        # Trunk Ports subgraph
        trunk_node_map: dict[tuple, str] = {}
        if trunk_groups:
            lines.append('    subgraph trunks["Trunk Ports"]')
            t_counter = 0
            for key, ports in sorted(trunk_groups.items()):
                unit_num = key[0]
                node_id = f"t{t_counter}"
                t_counter += 1
                names = [d.port_map[p] for p in ports]
                if len(ports) == 1:
                    label = names[0]
                else:
                    range_str = format_port_range(names)
                    label = f"U{unit_num}/{range_str} ({len(ports)})"
                lines.append(f'        {node_id}["{label}"]')
                trunk_node_map[key] = node_id
            lines.append("    end")

        # Edges from trunk ports: solid for untagged, dashed for tagged
        for key in sorted(trunk_groups):
            _, untagged, tagged = key
            node_id = trunk_node_map[key]
            for vid in untagged:
                lines.append(f"    {node_id} --> v{vid}")
            for vid in tagged:
                lines.append(f"    {node_id} -.-> v{vid}")

        # Edges from access ports with extra untagged VLANs (deduplicated)
        seen_edges: set[tuple[str, int]] = set()
        for node_id, vid in access_extra_untagged:
            edge_key = (node_id, vid)
            if edge_key not in seen_edges:
                seen_edges.add(edge_key)
                lines.append(f"    {node_id} --> v{vid}")

        lines.append("```")
        lines.append("")
        return lines
