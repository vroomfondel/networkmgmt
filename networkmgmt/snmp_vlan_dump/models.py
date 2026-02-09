"""Pydantic models for VLAN dump data."""

from __future__ import annotations

from pydantic import BaseModel, Field


class UnitInfo(BaseModel):
    """Per-unit (stacking member) metadata parsed from ifDescr strings."""

    min_idx: int
    max_idx: int
    max_port: int
    ten_g_start: int | None = None


class PortVlans(BaseModel):
    """VLAN membership for a single physical port."""

    tagged: list[int] = Field(default_factory=list)
    untagged: list[int] = Field(default_factory=list)


class VlanDumpData(BaseModel):
    """Complete VLAN dump dataset collected from a switch via SNMP."""

    sys_descr: str = ""
    sys_name: str = ""
    sys_uptime: str = ""

    unit_info: dict[int, UnitInfo] = Field(default_factory=dict)
    port_map: dict[int, str] = Field(default_factory=dict)
    unit_ports: dict[int, list[int]] = Field(default_factory=dict)

    port_vlans: dict[int, PortVlans] = Field(default_factory=dict)
    vlan_names: dict[int, str] = Field(default_factory=dict)
    pvid_data: dict[int, int] = Field(default_factory=dict)

    oper_status: dict[int, int] = Field(default_factory=dict)
    egress_data: dict[int, bytes] = Field(default_factory=dict)
    untagged_data: dict[int, bytes] = Field(default_factory=dict)

    all_phys: list[int] = Field(default_factory=list)
