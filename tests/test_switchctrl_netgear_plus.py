"""Tests for Netgear Smart Managed Plus switch management (netgear_tool fully mocked)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import networkmgmt.switchctrl.vendors.netgear_plus  # noqa: F401
from networkmgmt.switchctrl import factory
from networkmgmt.switchctrl.exceptions import AuthenticationError, PortError, SwitchError, VLANError
from networkmgmt.switchctrl.models.port import DuplexMode, PortConfig, PortSpeed
from networkmgmt.switchctrl.models.vlan import TrunkConfig
from networkmgmt.switchctrl.vendors.netgear_plus import sdk
from networkmgmt.switchctrl.vendors.netgear_plus.client import NetgearPlusSwitch
from networkmgmt.switchctrl.vendors.netgear_plus.managers import (
    PortVlanSpec,
    plan_vlan_changes,
)


class FakeSwitch:
    """In-memory stand-in for ``netgear_tool.Switch`` that records writes."""

    def __init__(self, ports: int = 5, dot1q: bool = True) -> None:
        self._port_count = ports
        self.dot1q = dot1q
        self.membership = {1: "1" * ports, 101: "3" * ports}
        self.pvids = {p: 1 for p in range(1, ports + 1)}
        self.writes: list[tuple] = []
        self.logged_out = False
        self.speeds = {p: "No Speed" for p in range(1, ports + 1)}

    def logout(self) -> None:
        self.logged_out = True

    def get_switch_config(self):
        return SimpleNamespace(model="GS305E", name="sw", serial="S1", mac="aa:bb", firmware="V1.0.0.22")

    def get_port_settings(self):
        return [SimpleNamespace(port=p, description=f"d{p}", enabled=True, speed_act=s) for p, s in self.speeds.items()]

    def get_port_stats(self):
        return [SimpleNamespace(port=p, bytes_rx=10 * p, bytes_tx=20 * p, crc_errors=p) for p in self.speeds]

    def set_port(self, port, speed=None, fc_enabled=None, description=None):
        self.writes.append(("set_port", port, speed, description))

    def get_dot1q_enabled(self):
        return self.dot1q

    def get_vlan_ids(self):
        return sorted(self.membership)

    def get_vlan_membership(self, vid):
        return self.membership[vid]

    def set_vlan_membership(self, vid, membership):
        self.writes.append(("membership", vid, membership))
        self.membership[vid] = membership

    def add_vlan(self, vid):
        self.writes.append(("add", vid))
        self.membership[vid] = "3" * self._port_count

    def delete_vlan(self, vid):
        self.writes.append(("delete", vid))
        del self.membership[vid]

    def get_port_pvids(self):
        return dict(self.pvids)

    def set_port_pvid(self, port, pvid):
        self.writes.append(("pvid", port, pvid))
        self.pvids[port] = pvid


@pytest.fixture
def fake(monkeypatch):
    sw = FakeSwitch()
    monkeypatch.setattr(sdk, "HAS_NETGEAR_TOOL", True)
    monkeypatch.setattr(sdk, "_make_switch", lambda host, password, timeout: sw)
    return sw


@pytest.fixture
def switch(fake):
    client = NetgearPlusSwitch(host="192.0.2.1", password="pw")
    yield client
    client.disconnect()


class TestRegistry:
    def test_registered(self):
        assert "netgear_plus" in factory.list_vendors()

    def test_create_switch(self, fake):
        client = factory.create_switch("netgear_plus", "192.0.2.1", password="pw")
        assert isinstance(client, NetgearPlusSwitch)


class TestConnect:
    def test_missing_sdk_raises_clear_error(self, monkeypatch):
        monkeypatch.setattr(sdk, "HAS_NETGEAR_TOOL", False)
        monkeypatch.setattr(sdk, "_make_switch", None)
        with pytest.raises(SwitchError, match="netgear-tool"):
            NetgearPlusSwitch(host="h", password="pw").connect()

    @pytest.mark.parametrize("message", ["Login failed: password is invalid", "login failed: Unknown error"])
    def test_login_failure_maps_to_auth_error(self, monkeypatch, message):
        def boom(host, password, timeout):
            raise RuntimeError(message)

        monkeypatch.setattr(sdk, "HAS_NETGEAR_TOOL", True)
        monkeypatch.setattr(sdk, "_make_switch", boom)
        with pytest.raises(AuthenticationError):
            NetgearPlusSwitch(host="h", password="bad").connect()

    def test_other_failure_is_switch_error_not_auth(self, monkeypatch):
        def boom(host, password, timeout):
            raise ConnectionError("unreachable")

        monkeypatch.setattr(sdk, "HAS_NETGEAR_TOOL", True)
        monkeypatch.setattr(sdk, "_make_switch", boom)
        with pytest.raises(SwitchError) as exc:
            NetgearPlusSwitch(host="h", password="pw").connect()
        assert not isinstance(exc.value, AuthenticationError)

    def test_lazy_connect_and_disconnect(self, fake):
        client = NetgearPlusSwitch(host="h", password="pw")
        assert client.monitoring.get_system_info().model == "GS305E"
        client.disconnect()
        assert fake.logged_out


class TestMonitoring:
    def test_system_info(self, switch):
        info = switch.monitoring.get_system_info()
        assert (info.hostname, info.mac_address, info.firmware_version, info.serial_number) == (
            "sw",
            "aa:bb",
            "V1.0.0.22",
            "S1",
        )

    def test_port_status_link(self, switch, fake):
        fake.speeds[2] = "1000M"
        status = {s.port: s for s in switch.monitoring.get_port_status()}
        assert status["2"].link_up and status["2"].speed == "1000M"
        assert not status["1"].link_up

    def test_port_statistics(self, switch):
        stats = switch.monitoring.get_port_statistics()
        assert (stats[2].port, stats[2].rx_bytes, stats[2].tx_bytes, stats[2].rx_errors) == ("3", 30, 60, 3)

    def test_sensor_and_lacp_not_supported(self, switch):
        with pytest.raises(NotImplementedError):
            switch.monitoring.get_sensor_data()
        with pytest.raises(NotImplementedError):
            switch.monitoring.get_lacp_info()
        with pytest.raises(NotImplementedError):
            switch.lacp.get_port_channel_info()
        with pytest.raises(NotImplementedError):
            switch.lacp.create_port_channel(1, ["1", "2"])


class TestPlan:
    def test_add_and_remove(self):
        cur = {1: "11311", 101: "22333"}
        pvids = {p: 1 for p in range(1, 6)}
        plan = plan_vlan_changes(cur, pvids, [1, 101], {2: PortVlanSpec(untagged=1), 3: PortVlanSpec(tagged=[101])}, 5)
        assert plan.want_membership == {1: "11311", 101: "23233"}
        assert plan.edited == [101]
        assert not plan.pvid_changes

    def test_undeclared_ports_untouched(self):
        cur = {1: "11111", 101: "22333"}
        plan = plan_vlan_changes(cur, {p: 1 for p in range(1, 6)}, [1, 101], {}, 5)
        assert not plan.changed

    def test_pvid_change_only(self):
        cur = {1: "11111", 101: "21333"}
        plan = plan_vlan_changes(
            cur, {p: 1 for p in range(1, 6)}, [1, 101], {1: PortVlanSpec(tagged=[101], pvid=101)}, 5
        )
        assert plan.pvid_changes == {1: 101}

    def test_create_and_purge(self):
        cur = {1: "11111", 5: "33333"}
        plan = plan_vlan_changes(cur, {p: 1 for p in range(1, 6)}, [1, 7], {}, 5, purge_vlans=True)
        assert plan.created == [7] and plan.deleted == [5]

    def test_vlan1_never_purged(self):
        plan = plan_vlan_changes({1: "11111"}, {p: 1 for p in range(1, 6)}, [], {}, 5, purge_vlans=True)
        assert not plan.deleted

    @pytest.mark.parametrize(
        "spec",
        [
            PortVlanSpec(untagged=1, tagged=[1]),
            PortVlanSpec(untagged=999),
            PortVlanSpec(untagged=1, pvid=101),
            PortVlanSpec(tagged=[101], pvid=1),
        ],
    )
    def test_invalid_specs(self, spec):
        with pytest.raises(VLANError):
            plan_vlan_changes({1: "11111", 101: "33333"}, {p: 1 for p in range(1, 6)}, [1, 101], {1: spec}, 5)

    def test_port_out_of_range(self):
        with pytest.raises(VLANError):
            plan_vlan_changes({1: "11111"}, {}, [1], {9: PortVlanSpec(untagged=1)}, 5)


class TestReconcile:
    def test_writes_in_safe_order_and_readback(self, switch, fake):
        plan = switch.vlan.reconcile(
            [1, 101, 150], {1: PortVlanSpec(untagged=1, tagged=[101, 150]), 2: PortVlanSpec(untagged=101)}
        )
        assert plan.changed
        kinds = [w[0] for w in fake.writes]
        assert kinds.index("add") < kinds.index("pvid") < len(kinds) - 1
        assert fake.membership[101] == "21333"
        assert fake.pvids[2] == 101
        assert fake.membership[1][0] == "1" and fake.membership[1][1] == "3"

    def test_idempotent_second_run_no_writes(self, switch, fake):
        ports = {1: PortVlanSpec(untagged=1, tagged=[101]), 2: PortVlanSpec(untagged=101)}
        switch.vlan.reconcile([1, 101], ports)
        fake.writes.clear()
        plan = switch.vlan.reconcile([1, 101], ports)
        assert not plan.changed
        assert fake.writes == []

    def test_dry_run_does_not_write(self, switch, fake):
        plan = switch.vlan.reconcile([1, 101], {2: PortVlanSpec(untagged=101)}, dry_run=True)
        assert plan.changed and fake.writes == []
        assert "101" in plan.render(desired=True)

    def test_membership_removed_not_only_added(self, switch, fake):
        fake.membership[101] = "33233"
        fake.membership[1] = "11311"
        fake.pvids[3] = 101
        switch.vlan.reconcile([1, 101], {3: PortVlanSpec(untagged=1)})
        assert fake.membership[101][2] == "3"
        assert fake.membership[1][2] == "1"
        assert fake.pvids[3] == 1

    def test_purge_deletes_vlan(self, switch, fake):
        fake.membership[200] = "33333"
        switch.vlan.reconcile([1, 101], purge_vlans=True)
        assert ("delete", 200) in fake.writes and 200 not in fake.membership

    def test_readback_mismatch_raises(self, switch, fake):
        fake.set_vlan_membership = lambda vid, mem: None
        with pytest.raises(VLANError, match="did not apply"):
            switch.vlan.reconcile([1, 101], {2: PortVlanSpec(untagged=101)})

    def test_dot1q_disabled_fails_clearly(self, switch, fake):
        fake.dot1q = False
        with pytest.raises(VLANError, match="802.1Q"):
            switch.vlan.list_vlans()
        with pytest.raises(VLANError, match="802.1Q"):
            switch.vlan.reconcile([1])
        assert fake.writes == []


class TestVlanManager:
    def test_list_vlans(self, switch, fake):
        fake.membership[101] = "22133"
        vlans = {v.vlan_id: v for v in switch.vlan.list_vlans()}
        assert vlans[101].tagged_ports == ["1", "2"] and vlans[101].untagged_ports == ["3"]

    def test_create_vlan_idempotent(self, switch, fake):
        switch.vlan.create_vlan(150, "servers")
        assert fake.writes == [("add", 150)]
        fake.writes.clear()
        switch.vlan.create_vlan(150)
        assert fake.writes == []

    def test_delete_vlan(self, switch, fake):
        switch.vlan.delete_vlan(101)
        assert ("delete", 101) in fake.writes
        fake.writes.clear()
        switch.vlan.delete_vlan(101)
        assert fake.writes == []

    def test_delete_vlan1_refused(self, switch):
        with pytest.raises(VLANError):
            switch.vlan.delete_vlan(1)

    def test_assign_untagged_moves_port_and_sets_pvid(self, switch, fake):
        switch.vlan.assign_port_to_vlan("2", 101)
        assert fake.membership[101][1] == "1"
        assert fake.membership[1][1] == "3"
        assert fake.pvids[2] == 101

    def test_assign_tagged_keeps_untagged(self, switch, fake):
        switch.vlan.assign_port_to_vlan("2", 101, tagged=True)
        assert fake.membership[101][1] == "2"
        assert fake.membership[1][1] == "1"
        assert fake.pvids[2] == 1

    def test_assign_to_missing_vlan(self, switch):
        with pytest.raises(VLANError):
            switch.vlan.assign_port_to_vlan("2", 999)

    def test_assign_invalid_port(self, switch):
        with pytest.raises(VLANError):
            switch.vlan.assign_port_to_vlan("Gi1/0/1", 101)

    def test_configure_trunk_idempotent(self, switch, fake):
        trunk = TrunkConfig(port="1", native_vlan=1, allowed_vlans=[1, 101])
        switch.vlan.configure_trunk(trunk)
        assert fake.membership[101][0] == "2" and fake.membership[1][0] == "1"
        fake.writes.clear()
        switch.vlan.configure_trunk(trunk)
        assert fake.writes == []


class TestPortManager:
    def test_get_port_status_single(self, switch):
        assert [s.port for s in switch.port.get_port_status("3")] == ["3"]
        with pytest.raises(PortError):
            switch.port.get_port_status("9")

    def test_disable_enable_preserve_description(self, switch, fake):
        switch.port.disable_port("2")
        switch.port.enable_port("2")
        assert fake.writes == [("set_port", 2, 2, "d2"), ("set_port", 2, 1, "d2")]

    def test_configure_port_forced_speed(self, switch, fake):
        switch.port.configure_port(PortConfig(port="1", speed=PortSpeed.SPEED_100M, duplex=DuplexMode.FULL))
        assert fake.writes == [("set_port", 1, 6, "d1")]

    def test_configure_port_disabled(self, switch, fake):
        switch.port.configure_port(PortConfig(port="1", enabled=False, description="x"))
        assert fake.writes == [("set_port", 1, 2, "x")]

    def test_configure_port_unsupported_speed(self, switch):
        with pytest.raises(PortError):
            switch.port.configure_port(PortConfig(port="1", speed=PortSpeed.SPEED_10G))
