"""Tests for networkmgmt.snmp_vlan_dump.models Pydantic models."""

from __future__ import annotations

import pytest

from networkmgmt.snmp_vlan_dump.models import PortVlans, UnitInfo, VlanDumpData


class TestUnitInfo:
    """Test UnitInfo model."""

    def test_required_fields(self):
        """All required fields are set correctly."""
        ui = UnitInfo(min_idx=1, max_idx=48, max_port=48)
        assert ui.min_idx == 1
        assert ui.max_idx == 48
        assert ui.max_port == 48
        assert ui.ten_g_start is None

    def test_optional_ten_g_start(self):
        """Optional ten_g_start field."""
        ui = UnitInfo(min_idx=1, max_idx=48, max_port=48, ten_g_start=45)
        assert ui.ten_g_start == 45

    def test_model_dump(self):
        """Model can be dumped to dict."""
        ui = UnitInfo(min_idx=1, max_idx=48, max_port=48, ten_g_start=49)
        data = ui.model_dump()
        assert data["min_idx"] == 1
        assert data["max_idx"] == 48
        assert data["max_port"] == 48
        assert data["ten_g_start"] == 49

    def test_model_validate(self):
        """Model can be validated from dict."""
        data = {"min_idx": 10, "max_idx": 20, "max_port": 15, "ten_g_start": None}
        ui = UnitInfo.model_validate(data)
        assert ui.min_idx == 10
        assert ui.max_idx == 20


class TestPortVlans:
    """Test PortVlans model."""

    def test_default_empty_lists(self):
        """Default factory creates empty lists."""
        pv = PortVlans()
        assert pv.tagged == []
        assert pv.untagged == []

    def test_list_isolation(self):
        """Two instances don't share list references."""
        pv1 = PortVlans()
        pv2 = PortVlans()
        pv1.tagged.append(10)
        pv1.untagged.append(20)
        assert pv2.tagged == []
        assert pv2.untagged == []

    def test_with_values(self):
        """Can initialize with values."""
        pv = PortVlans(tagged=[10, 20], untagged=[1])
        assert pv.tagged == [10, 20]
        assert pv.untagged == [1]

    def test_model_dump(self):
        """Model can be dumped to dict."""
        pv = PortVlans(tagged=[10], untagged=[1])
        data = pv.model_dump()
        assert data["tagged"] == [10]
        assert data["untagged"] == [1]

    def test_model_validate(self):
        """Model can be validated from dict."""
        data = {"tagged": [10, 20], "untagged": [1, 2]}
        pv = PortVlans.model_validate(data)
        assert pv.tagged == [10, 20]
        assert pv.untagged == [1, 2]


class TestVlanDumpData:
    """Test VlanDumpData model."""

    def test_defaults_to_empty(self):
        """All fields default to empty collections."""
        data = VlanDumpData()
        assert data.sys_descr == ""
        assert data.sys_name == ""
        assert data.sys_uptime == ""
        assert data.unit_info == {}
        assert data.port_map == {}
        assert data.unit_ports == {}
        assert data.port_vlans == {}
        assert data.vlan_names == {}
        assert data.pvid_data == {}
        assert data.oper_status == {}
        assert data.egress_data == {}
        assert data.untagged_data == {}
        assert data.all_phys == []

    def test_string_fields(self):
        """String fields can be set."""
        data = VlanDumpData(
            sys_descr="Netgear GS308T",
            sys_name="switch01",
            sys_uptime="1 day",
        )
        assert data.sys_descr == "Netgear GS308T"
        assert data.sys_name == "switch01"
        assert data.sys_uptime == "1 day"

    def test_nested_models(self):
        """Can contain nested UnitInfo models."""
        ui = UnitInfo(min_idx=1, max_idx=8, max_port=8)
        data = VlanDumpData(unit_info={1: ui})
        assert 1 in data.unit_info
        assert data.unit_info[1].max_port == 8

    def test_port_vlans_dict(self):
        """Can contain PortVlans dict."""
        pv = PortVlans(tagged=[10], untagged=[1])
        data = VlanDumpData(port_vlans={1: pv})
        assert 1 in data.port_vlans
        assert data.port_vlans[1].tagged == [10]

    def test_bytes_fields(self):
        """Can store bytes in egress_data and untagged_data."""
        data = VlanDumpData(
            egress_data={1: b"\xff"},
            untagged_data={1: b"\xc0"},
        )
        assert data.egress_data[1] == b"\xff"
        assert data.untagged_data[1] == b"\xc0"

    def test_model_dump(self):
        """Model can be dumped to dict."""
        ui = UnitInfo(min_idx=1, max_idx=8, max_port=8)
        pv = PortVlans(tagged=[10])
        data = VlanDumpData(
            sys_name="test",
            unit_info={1: ui},
            port_vlans={1: pv},
        )
        dumped = data.model_dump()
        assert dumped["sys_name"] == "test"
        assert 1 in dumped["unit_info"]
        assert dumped["unit_info"][1]["max_port"] == 8

    def test_json_round_trip(self):
        """Model survives JSON round-trip via model_dump/model_validate."""
        ui = UnitInfo(min_idx=1, max_idx=8, max_port=8, ten_g_start=None)
        pv = PortVlans(tagged=[10, 20], untagged=[1])
        original = VlanDumpData(
            sys_descr="Test Switch",
            sys_name="switch01",
            sys_uptime="1 day",
            unit_info={1: ui},
            port_map={1: "U1/g1", 2: "U1/g2"},
            unit_ports={1: [1, 2]},
            port_vlans={1: pv},
            vlan_names={1: "default", 10: "servers"},
            pvid_data={1: 1, 2: 10},
            oper_status={1: 1, 2: 2},
            all_phys=[1, 2],
        )

        # Dump to dict
        dumped = original.model_dump()

        # Validate back to model
        restored = VlanDumpData.model_validate(dumped)

        # Verify key fields
        assert restored.sys_descr == "Test Switch"
        assert restored.sys_name == "switch01"
        assert restored.unit_info[1].max_port == 8
        assert restored.port_map[1] == "U1/g1"
        assert restored.port_vlans[1].tagged == [10, 20]
        assert restored.vlan_names[10] == "servers"
        assert restored.all_phys == [1, 2]

    def test_collection_isolation(self):
        """Multiple instances don't share collection references."""
        data1 = VlanDumpData()
        data2 = VlanDumpData()

        data1.all_phys.append(1)
        data1.port_map[1] = "U1/g1"

        assert data2.all_phys == []
        assert data2.port_map == {}
