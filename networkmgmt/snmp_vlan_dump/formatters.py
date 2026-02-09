"""Terminal and Markdown formatters for VLAN dump data."""

from __future__ import annotations

from networkmgmt.snmp_vlan_dump._util import (
    decode_portlist,
    port_is_active,
    status_str,
    unit_summary_str,
)
from networkmgmt.snmp_vlan_dump.mermaid import VlanMermaidGenerator
from networkmgmt.snmp_vlan_dump.models import VlanDumpData


class TerminalFormatter:
    """Format VlanDumpData as plain-text terminal output."""

    def __init__(self, data: VlanDumpData) -> None:
        self.data = data

    def format(self) -> str:
        """Return the complete terminal output as a string."""
        d = self.data
        lines: list[str] = []

        # ── System info ────────────────────────────────────────────────
        lines.append(f"  Switch:  {d.sys_descr}")
        lines.append(f"  Name:    {d.sys_name}")
        lines.append(f"  Uptime:  {d.sys_uptime}")
        lines.append(f"  Units:   {len(d.unit_info)}")
        for u in sorted(d.unit_info):
            ui = d.unit_info[u]
            summary = unit_summary_str(ui)
            lines.append(f"           Unit {u}: {summary}" f" (ifIndex {ui.min_idx}-{ui.max_idx})")

        # ── Per-unit tables ────────────────────────────────────────────
        for unit_num in sorted(d.unit_ports):
            ports = d.unit_ports[unit_num]
            ui = d.unit_info[unit_num]
            summary = unit_summary_str(ui)

            lines.append(f"\n{'=' * 85}")
            lines.append(f"  UNIT {unit_num}  ({summary})")
            lines.append(f"{'=' * 85}")
            lines.append(f"{'Port':<10} {'Link':<6} {'PVID':>5}  " f"{'Untagged VLANs':<25} {'Tagged VLANs'}")
            lines.append("-" * 85)

            for p in ports:
                if not port_is_active(p, d.port_vlans, d.oper_status):
                    continue
                info = d.port_vlans[p]
                st = status_str(d.oper_status.get(p, 0))
                pv = d.pvid_data.get(p, "-")
                untag_str = ", ".join(str(v) for v in info.untagged) or "-"
                tag_str = ", ".join(str(v) for v in info.tagged) or "-"
                name = d.port_map.get(p, f"p{p}")
                lines.append(f"{name:<10} {st:<6} {str(pv):>5}  {untag_str:<25} {tag_str}")

        # ── VLAN summary ──────────────────────────────────────────────
        lines.append(f"\n\n{'=' * 85}")
        lines.append("  VLAN-ZUSAMMENFASSUNG")
        lines.append(f"{'=' * 85}\n")

        for vlan_id in sorted(d.egress_data):
            egress = decode_portlist(d.egress_data[vlan_id])
            untag = decode_portlist(d.untagged_data.get(vlan_id, b""))
            member_ports = sorted(p for p in d.all_phys if p in egress)

            name = d.vlan_names.get(vlan_id, "")
            tagged = [d.port_map.get(p, f"p{p}") for p in member_ports if p not in untag]
            untagged = [d.port_map.get(p, f"p{p}") for p in member_ports if p in untag]

            lines.append(f"VLAN {vlan_id} ({name}):")
            if untagged:
                lines.append(f"  Untagged: {', '.join(untagged)}")
            if tagged:
                lines.append(f"  Tagged:   {', '.join(tagged)}")
            if not member_ports:
                lines.append("  (keine Ports)")
            lines.append("")

        return "\n".join(lines)


class MarkdownFormatter:
    """Format VlanDumpData as Markdown with optional Mermaid diagram."""

    def __init__(
        self,
        data: VlanDumpData,
        diagram_style: str = "aggregated",
    ) -> None:
        self.data = data
        self.diagram_style = diagram_style

    def format(self) -> str:
        """Return the complete Markdown document as a string."""
        d = self.data
        lines: list[str] = []

        # ── Title + system info ────────────────────────────────────────
        lines.append(f"# VLAN Dump: {d.sys_name}\n")
        lines.append(f"- **Switch:** {d.sys_descr}")
        lines.append(f"- **Name:** {d.sys_name}")
        lines.append(f"- **Uptime:** {d.sys_uptime}")
        lines.append(f"- **Units:** {len(d.unit_info)}")
        for u in sorted(d.unit_info):
            ui = d.unit_info[u]
            summary = unit_summary_str(ui)
            lines.append(f"  - Unit {u}: {summary} (ifIndex {ui.min_idx}-{ui.max_idx})")
        lines.append("")

        # ── Per-unit tables ────────────────────────────────────────────
        for unit_num in sorted(d.unit_ports):
            ports = d.unit_ports[unit_num]
            ui = d.unit_info[unit_num]
            summary = unit_summary_str(ui)

            lines.append(f"## Unit {unit_num} ({summary})\n")
            lines.append("| Port | Link | PVID | Untagged VLANs | Tagged VLANs |")
            lines.append("|------|------|-----:|----------------|--------------|")

            for p in ports:
                if not port_is_active(p, d.port_vlans, d.oper_status):
                    continue
                info = d.port_vlans[p]
                st = status_str(d.oper_status.get(p, 0))
                pv = d.pvid_data.get(p, "-")
                untag_str = ", ".join(str(v) for v in info.untagged) or "-"
                tag_str = ", ".join(str(v) for v in info.tagged) or "-"
                name = d.port_map.get(p, f"p{p}")
                lines.append(f"| {name} | {st} | {pv} | {untag_str} | {tag_str} |")

            lines.append("")

        # ── VLAN summary ──────────────────────────────────────────────
        lines.append("## VLAN Summary\n")

        for vlan_id in sorted(d.egress_data):
            egress = decode_portlist(d.egress_data[vlan_id])
            untag = decode_portlist(d.untagged_data.get(vlan_id, b""))
            member_ports = sorted(p for p in d.all_phys if p in egress)

            name = d.vlan_names.get(vlan_id, "")
            tagged = [d.port_map.get(p, f"p{p}") for p in member_ports if p not in untag]
            untagged = [d.port_map.get(p, f"p{p}") for p in member_ports if p in untag]

            lines.append(f"**VLAN {vlan_id} ({name}):**")
            if untagged:
                lines.append(f"- Untagged: {', '.join(untagged)}")
            if tagged:
                lines.append(f"- Tagged: {', '.join(tagged)}")
            if not member_ports:
                lines.append("- (no ports)")
            lines.append("")

        # ── Mermaid diagram ────────────────────────────────────────────
        lines.append("## Topology\n")
        generator = VlanMermaidGenerator(d, style=self.diagram_style)
        lines.extend(generator.generate().splitlines())

        return "\n".join(lines)
