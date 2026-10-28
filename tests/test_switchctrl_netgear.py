"""Tests for the Netgear Smart Managed Pro vendor (SNMP and web UI fully mocked)."""

import pytest

from networkmgmt.snmp import SnmpSession, SnmpWriteError, octets, portlist_to_ports, ports_to_portlist
from networkmgmt.snmp.errors import SnmpAuthenticationError
from networkmgmt.switchctrl.exceptions import (
    APIError,
    AuthenticationError,
    PortError,
    SwitchError,
    VLANError,
)
from networkmgmt.switchctrl.factory import create_switch, list_vendors
from networkmgmt.switchctrl.models.port import DuplexMode, PortConfig, PortMode, PortSpeed
from networkmgmt.switchctrl.models.vlan import TrunkConfig
from networkmgmt.switchctrl.vendors.netgear import oids
from networkmgmt.switchctrl.vendors.netgear.backend import NetgearBackend
from networkmgmt.switchctrl.vendors.netgear.client import NetgearSwitch
from networkmgmt.switchctrl.vendors.netgear.models import StaticRoute
from networkmgmt.switchctrl.vendors.netgear.routing import NetgearRoutingManager, parse_route_index
from networkmgmt.switchctrl.vendors.netgear.snmp_managers import (
    NetgearLACPManager,
    NetgearMonitoringManager,
    NetgearPortManager,
    NetgearVLANManager,
)
from networkmgmt.switchctrl.vendors.netgear.web import NetgearWeb, WebError

LENGTH = 2
ALL_BITS = set(range(1, 8 * LENGTH + 1))
NAMES = {1: "1/g1", 2: "1/g2", 3: "1/g3", 4: "1/g4", 5: "l1", 9: "1/stack1", 13: "cpu"}
BRIDGE_PORTS = {1, 2, 3, 4, 5}


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class FakeSwitch(SnmpSession):
    """In-memory GS108Tv2-like agent: delayed writes, one varbind per VLAN PDU, untagged-mask lock."""

    def __init__(self, delay=0, host="192.0.2.50"):
        self._host = host
        self.mib = {}
        self.delay = delay
        self.pending = []
        self.sets = []
        self.locked = set()
        self.vlans = {}
        self.save_status = oids.SAVE_COMPLETE
        self.fail_next_set = None
        self.add_vlan(1, "", {1, 2, 3, 4, 9}, {1, 2, 3, 4, 9}, static=False)
        self.add_vlan(10, "servers", {1}, {1})
        for idx, name in NAMES.items():
            self.mib[f"{oids.IF_NAME}.{idx}"] = name
            self.mib[f"{oids.IF_ADMIN_STATUS}.{idx}"] = 1
            self.mib[f"{oids.IF_OPER_STATUS}.{idx}"] = 1 if idx <= 2 else 2
            self.mib[f"{oids.IF_HIGH_SPEED}.{idx}"] = 1000
        for idx in BRIDGE_PORTS:
            self.mib[f"{oids.BASE_PORT_IFINDEX}.{idx}"] = idx
            self.mib[f"{oids.PVID}.{idx}"] = 1
            self.mib[f"{oids.SWITCHPORT_MODE}.{idx}"] = 3

    @property
    def host(self):
        return self._host

    @property
    def single_varbind(self):
        return False

    def add_vlan(self, vid, name, egress, untagged, static=True):
        self.vlans[vid] = dict(name=name, egress=set(egress), untagged=set(untagged), static=static)
        self._publish(vid)

    def _publish(self, vid):
        v = self.vlans[vid]
        raw_untagged = ports_to_portlist((ALL_BITS - v["egress"]) | v["untagged"], LENGTH)
        egress_raw = ports_to_portlist(v["egress"], LENGTH)
        self.mib[f"{oids.VLAN_STATIC_NAME}.{vid}"] = v["name"]
        if v["static"]:
            self.mib[f"{oids.VLAN_STATIC_EGRESS}.{vid}"] = egress_raw
            self.mib[f"{oids.VLAN_STATIC_UNTAGGED}.{vid}"] = raw_untagged
        else:
            self.mib.pop(f"{oids.VLAN_STATIC_NAME}.{vid}", None)
            self.mib[f"{oids.VLAN_CURRENT_EGRESS}.0.{vid}"] = egress_raw
            self.mib[f"{oids.VLAN_CURRENT_UNTAGGED}.0.{vid}"] = raw_untagged
        self.mib[f"{oids.VLAN_STATIC_ROWSTATUS}.{vid}"] = 1

    def _tick(self):
        due = [p for p in self.pending if p[0] <= 0]
        self.pending = [(n - 1, f) for n, f in self.pending if n > 0]
        for _, apply in due:
            apply()

    def get(self, oid):
        return str(self._get(oid))

    def get_int(self, oid):
        return int(self._get(oid))

    def get_octets(self, oid):
        return bytes(self._get(oid))

    def _get(self, oid):
        self._tick()
        if oid == oids.SAVE_STATUS:
            return self.save_status
        return self.mib[oid]

    def _rows(self, base):
        self._tick()
        return {k[len(base) + 1 :]: v for k, v in sorted(self.mib.items()) if k.startswith(base + ".")}

    def walk(self, base):
        return {k: v.decode() if isinstance(v, bytes) else str(v) for k, v in self._rows(base).items()}

    def walk_int(self, base):
        return {k: int(v) for k, v in self._rows(base).items()}

    def walk_octets(self, base):
        return {k: bytes(v) for k, v in self._rows(base).items()}

    def set(self, *pairs, single_varbind=None):
        if self.fail_next_set:
            err, self.fail_next_set = self.fail_next_set, None
            raise err
        single = self.single_varbind if single_varbind is None else single_varbind
        if single:
            for pair in pairs:
                self.set(pair, single_varbind=False)
            return
        self.sets.append(pairs)
        vlan_oids = (
            oids.VLAN_STATIC_NAME,
            oids.VLAN_STATIC_EGRESS,
            oids.VLAN_STATIC_UNTAGGED,
            oids.VLAN_STATIC_ROWSTATUS,
        )
        if len(pairs) > 1 and any(o.startswith(vlan_oids) for o, _ in pairs):
            raise SnmpWriteError("set: commitFailed", oid=pairs[0][0], status="commitFailed")
        for oid, value in pairs:
            self.pending.append((self.delay, lambda o=oid, v=value: self._apply(o, v)))
        if self.delay == 0:
            self._tick()

    def _apply(self, oid, value):
        base, _, tail = oid.rpartition(".")
        vid = int(tail) if tail.isdigit() else None
        if base == oids.VLAN_STATIC_ROWSTATUS:
            if value.value == oids.ROW_CREATE_AND_GO:
                self.add_vlan(vid, "", set(), set())
            else:
                self.vlans.pop(vid, None)
                for prefix in (
                    oids.VLAN_STATIC_NAME,
                    oids.VLAN_STATIC_EGRESS,
                    oids.VLAN_STATIC_UNTAGGED,
                    oids.VLAN_STATIC_ROWSTATUS,
                ):
                    self.mib.pop(f"{prefix}.{vid}", None)
        elif base == oids.VLAN_STATIC_NAME:
            self.vlans[vid]["name"] = value.value
            self._publish(vid)
        elif base == oids.VLAN_STATIC_EGRESS:
            self.vlans[vid]["egress"] = portlist_to_ports(value.value)
            self.vlans[vid]["untagged"] &= self.vlans[vid]["egress"]
            self._publish(vid)
        elif base == oids.VLAN_STATIC_UNTAGGED:
            v = self.vlans[vid]
            if vid in self.locked:
                raise SnmpWriteError("set: commitFailed", oid=oid, status="commitFailed")
            old = (ALL_BITS - v["egress"]) | v["untagged"]
            new = portlist_to_ports(value.value)
            if any(i in old and i not in new and i not in v["egress"] for i in ALL_BITS):
                self.locked.add(vid)
            v["untagged"] = new & v["egress"]
            self._publish(vid)
        elif base == oids.IP_VLAN_STATUS:
            if value.value == oids.ROW_CREATE_AND_GO:
                self.mib[f"{oids.IP_VLAN_IFINDEX}.{vid}"] = 100 + vid
            else:
                self.mib.pop(f"{oids.IP_VLAN_IFINDEX}.{vid}", None)
        elif base == oids.PVID:
            self.mib[oid] = value.value
        elif base == oids.IF_ADMIN_STATUS:
            self.mib[oid] = value.value
        elif oid == oids.SAVE_CONFIG:
            self.mib[oid] = value.value
        else:
            self.mib[oid] = value.value


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    clock = FakeClock()
    for module in (
        "networkmgmt.snmp.polling",
        "networkmgmt.switchctrl.vendors.netgear.backend",
        "networkmgmt.switchctrl.vendors.netgear.routing",
    ):
        monkeypatch.setattr(f"{module}.time", clock)
    return clock


def backend_for(fake, timeout=10.0):
    return NetgearBackend(fake, settle_timeout=timeout, settle_interval=1.0)


@pytest.fixture
def fake():
    return FakeSwitch()


@pytest.fixture
def vlan(fake):
    return NetgearVLANManager(backend_for(fake))


def vlan_sets(fake):
    return [p for p in fake.sets if p[0][0].startswith("1.3.6.1.2.1.17.7")]


class TestRegistration:
    def test_netgear_in_vendor_list(self):
        assert "netgear" in list_vendors()

    def test_create_switch_with_v2c(self, monkeypatch):
        monkeypatch.setattr("networkmgmt.switchctrl.vendors.netgear.client.SnmpSession", lambda *a, **k: FakeSwitch())
        switch = create_switch(vendor="netgear", host="10.0.0.1", community="public")
        assert isinstance(switch, NetgearSwitch)
        assert switch.host == "10.0.0.1"


class TestClientCredentials:
    @pytest.fixture
    def captured(self, monkeypatch):
        calls = []

        def fake_session(host, credentials, **kwargs):
            calls.append((host, credentials, kwargs))
            return FakeSwitch()

        monkeypatch.setattr("networkmgmt.switchctrl.vendors.netgear.client.SnmpSession", fake_session)
        return calls

    def test_v3_defaults(self, captured):
        NetgearSwitch(host="h", snmp_user="u", auth_key="k")
        creds = captured[0][1]
        assert (creds.version, creds.auth_protocol, creds.priv_protocol, creds.priv_key) == (
            "v3",
            "sha512",
            "aes128",
            None,
        )

    def test_gs108t_protocols(self, captured):
        NetgearSwitch(
            host="h", snmp_user="admin", auth_key="pw", auth_protocol="sha", priv_key="pk", priv_protocol="des"
        )
        creds = captured[0][1]
        assert (creds.auth_protocol, creds.priv_protocol) == ("sha", "des")

    def test_username_password_aliases(self, captured):
        NetgearSwitch(host="h", username="admin", password="pw", verify_ssl=False, extra_param=1)
        creds = captured[0][1]
        assert (creds.user, creds.auth_key) == ("admin", "pw")

    def test_v2c_fallback(self, captured):
        NetgearSwitch(host="h", community="public")
        assert captured[0][1].version == "v2c"

    def test_snmp_options_forwarded(self, captured):
        NetgearSwitch(host="h", community="c", port=1161, timeout=2.0, retries=4, single_varbind=True)
        assert captured[0][2] == {"port": 1161, "timeout": 2.0, "retries": 4, "single_varbind": True}

    def test_missing_credentials(self, captured):
        with pytest.raises(ValueError):
            NetgearSwitch(host="h")

    def test_pysnmp_missing_maps_to_switch_error(self, monkeypatch):
        monkeypatch.setattr("networkmgmt.snmp.session.HAS_PYSNMP", False)
        with pytest.raises(SwitchError):
            NetgearSwitch(host="h", community="public")

    def test_managers_are_lazy_and_cached(self, captured):
        switch = NetgearSwitch(host="h", community="public")
        assert switch.vlan is switch.vlan
        assert switch.monitoring is switch.monitoring
        assert switch.port is switch.port
        assert switch.lacp is switch.lacp
        assert switch.routing is switch.routing


class TestVlanListing:
    def test_merges_static_and_current_tables(self, fake):
        table = backend_for(fake).read_vlans()
        assert set(table.vlans) == {1, 10}
        assert table.vlans[1].static is False
        assert table.vlans[10].static is True
        assert table.length == LENGTH

    def test_untagged_masked_with_members(self, fake):
        table = backend_for(fake).read_vlans()
        assert table.vlans[10].untagged == {1}
        assert table.vlans[10].egress == {1}

    def test_factory_vlan_has_no_readable_name(self, fake):
        assert backend_for(fake).read_vlans().vlans[1].name == ""

    def test_list_vlans_omits_stack_links(self, vlan):
        by_id = {v.vlan_id: v for v in vlan.list_vlans()}
        assert by_id[1].untagged_ports == ["1/g1", "1/g2", "1/g3", "1/g4"]
        assert by_id[10].name == "servers"

    def test_tagged_ports_listed(self, fake, vlan):
        fake.add_vlan(20, "iot", {1, 2}, {1})
        assert next(v for v in vlan.list_vlans() if v.vlan_id == 20).tagged_ports == ["1/g2"]


class TestVlanCreateDelete:
    def test_create_uses_one_varbind_per_pdu(self, fake, vlan):
        vlan.create_vlan(20, "iot")
        assert [len(p) for p in vlan_sets(fake)] == [1, 1]
        assert vlan_sets(fake)[0][0][0] == f"{oids.VLAN_STATIC_ROWSTATUS}.20"
        assert vlan_sets(fake)[0][0][1].value == oids.ROW_CREATE_AND_GO
        assert vlan_sets(fake)[1][0][0] == f"{oids.VLAN_STATIC_NAME}.20"
        assert fake.vlans[20]["name"] == "iot"

    def test_create_existing_with_same_name_is_noop(self, fake, vlan):
        vlan.create_vlan(10, "servers")
        assert fake.sets == []

    def test_create_existing_renames(self, fake, vlan):
        vlan.create_vlan(10, "backend")
        assert fake.vlans[10]["name"] == "backend"
        assert len(fake.sets) == 1

    def test_create_rejects_invalid_id(self, vlan):
        with pytest.raises(VLANError):
            vlan.create_vlan(4095)

    def test_delete(self, fake, vlan):
        vlan.delete_vlan(10)
        assert 10 not in fake.vlans

    def test_delete_missing_is_noop(self, fake, vlan):
        vlan.delete_vlan(99)
        assert fake.sets == []

    def test_delete_vlan_1_refused(self, vlan):
        with pytest.raises(VLANError):
            vlan.delete_vlan(1)

    def test_agent_rejection_maps_to_vlan_error(self, fake, vlan):
        fake.fail_next_set = SnmpWriteError("set: commitFailed", status="commitFailed")
        with pytest.raises(VLANError, match="commitFailed"):
            vlan.create_vlan(30, "x")

    def test_authentication_error_maps(self, fake, vlan):
        fake.fail_next_set = SnmpAuthenticationError("bad digest")
        with pytest.raises(AuthenticationError):
            vlan.create_vlan(30, "x")


class TestVlanAssignment:
    def test_untagged_assignment_moves_port_and_sets_pvid(self, fake, vlan):
        vlan.assign_port_to_vlan("1/g2", 10)
        assert 2 in fake.vlans[10]["untagged"]
        assert 2 not in fake.vlans[1]["egress"]
        assert fake.mib[f"{oids.PVID}.2"] == 10

    def test_every_vlan_write_is_a_single_varbind(self, fake, vlan):
        vlan.assign_port_to_vlan("1/g2", 10)
        assert all(len(p) == 1 for p in vlan_sets(fake))

    def test_egress_and_untagged_are_separate_pdus(self, fake, vlan):
        vlan.assign_port_to_vlan("1/g2", 10)
        written = [p[0][0].rpartition(".")[0] for p in vlan_sets(fake)]
        assert oids.VLAN_STATIC_EGRESS in written and oids.VLAN_STATIC_UNTAGGED in written

    def test_untagged_mask_never_locks_the_vlan(self, fake, vlan):
        vlan.assign_port_to_vlan("1/g2", 10)
        vlan.assign_port_to_vlan("1/g3", 10, tagged=True)
        vlan.assign_port_to_vlan("1/g2", 1)
        assert fake.locked == set()

    def test_non_member_bits_are_preserved(self, fake, vlan):
        vlan.assign_port_to_vlan("1/g2", 10)
        raw = fake.mib[f"{oids.VLAN_STATIC_UNTAGGED}.10"]
        assert portlist_to_ports(raw) >= {13, 14, 15, 16, 3, 4}

    def test_full_mask_rebuild_would_lock_the_vlan(self, fake):
        fake.set((f"{oids.VLAN_STATIC_EGRESS}.10", octets(ports_to_portlist({1, 2}, LENGTH))))
        fake.set((f"{oids.VLAN_STATIC_UNTAGGED}.10", octets(ports_to_portlist({1, 2}, LENGTH))))
        assert 10 in fake.locked

    def test_tagged_assignment_keeps_pvid_and_other_vlans(self, fake, vlan):
        vlan.assign_port_to_vlan("1/g2", 10, tagged=True)
        assert 2 in fake.vlans[10]["egress"] and 2 not in fake.vlans[10]["untagged"]
        assert 2 in fake.vlans[1]["untagged"]
        assert fake.mib[f"{oids.PVID}.2"] == 1

    def test_assignment_is_idempotent(self, fake, vlan):
        vlan.assign_port_to_vlan("1/g2", 10)
        fake.sets.clear()
        vlan.assign_port_to_vlan("1/g2", 10)
        assert fake.sets == []

    def test_stack_link_rejected(self, fake, vlan):
        with pytest.raises(VLANError, match="bridge port"):
            vlan.assign_port_to_vlan("1/stack1", 10)
        assert fake.sets == []

    def test_unknown_port_rejected(self, vlan):
        with pytest.raises(VLANError, match="does not exist"):
            vlan.assign_port_to_vlan("9/g9", 10)

    def test_unknown_vlan_rejected(self, vlan):
        with pytest.raises(VLANError, match="VLAN 99"):
            vlan.assign_port_to_vlan("1/g2", 99)

    def test_port_not_in_general_mode_rejected(self, fake, vlan):
        fake.mib[f"{oids.SWITCHPORT_MODE}.2"] = 1
        with pytest.raises(VLANError, match="general"):
            vlan.assign_port_to_vlan("1/g2", 10)

    def test_trunk(self, fake, vlan):
        fake.add_vlan(20, "iot", set(), set())
        vlan.configure_trunk(TrunkConfig(port="1/g2", native_vlan=10, allowed_vlans=[20]))
        assert fake.vlans[10]["untagged"] >= {2}
        assert 2 in fake.vlans[20]["egress"] and 2 not in fake.vlans[20]["untagged"]
        assert 2 not in fake.vlans[1]["egress"]
        assert fake.mib[f"{oids.PVID}.2"] == 10
        assert fake.locked == set()

    def test_trunk_unknown_vlan(self, vlan):
        with pytest.raises(VLANError, match=r"\[77\]"):
            vlan.configure_trunk(TrunkConfig(port="1/g2", native_vlan=1, allowed_vlans=[77]))


class TestReadbackPolling:
    def test_delayed_write_is_polled(self, clock):
        fake = FakeSwitch(delay=3)
        NetgearVLANManager(backend_for(fake)).create_vlan(20, "iot")
        assert 20 in fake.vlans
        assert clock.sleeps

    def test_write_that_never_lands_raises(self, clock):
        fake = FakeSwitch(delay=10**6)
        with pytest.raises(VLANError, match="did not apply"):
            NetgearVLANManager(backend_for(fake, timeout=5)).create_vlan(20, "iot")
        assert sum(clock.sleeps) >= 5

    def test_membership_change_is_verified(self):
        fake = FakeSwitch(delay=2)
        NetgearVLANManager(backend_for(fake)).assign_port_to_vlan("1/g2", 10)
        assert fake.vlans[10]["egress"] >= {1, 2}

    def test_membership_that_does_not_land_raises(self):
        fake = FakeSwitch()
        manager = NetgearVLANManager(backend_for(fake, timeout=3))
        original = fake._apply
        fake._apply = lambda oid, value: None if oid.startswith(oids.VLAN_STATIC_EGRESS) else original(oid, value)
        with pytest.raises(VLANError, match="did not apply"):
            manager.assign_port_to_vlan("1/g2", 10)


class TestSaveConfig:
    def test_save_uses_4526_11(self, fake):
        backend_for(fake).save_config()
        assert fake.sets[0][0][0] == "1.3.6.1.4.1.4526.11.1.3.1.0"
        assert fake.sets[0][0][1].value == 1
        assert oids.SAVE_STATUS == "1.3.6.1.4.1.4526.11.1.3.11.0"

    def test_save_oids_are_not_the_librenms_mib(self):
        assert ".4526.10." not in oids.SAVE_CONFIG and ".4526.10." not in oids.SAVE_STATUS

    def test_save_failure(self, fake):
        fake.save_status = oids.SAVE_FAILED
        client = object.__new__(NetgearSwitch)
        client._backend = backend_for(fake)
        with pytest.raises(APIError, match="saving"):
            client.save_config()


class TestPortManager:
    def test_disable_and_enable(self, fake):
        port = NetgearPortManager(backend_for(fake))
        port.disable_port("1/g2")
        assert fake.mib[f"{oids.IF_ADMIN_STATUS}.2"] == oids.ADMIN_DOWN
        port.enable_port("1/g2")
        assert fake.mib[f"{oids.IF_ADMIN_STATUS}.2"] == oids.ADMIN_UP

    def test_enable_already_up_is_noop(self, fake):
        NetgearPortManager(backend_for(fake)).enable_port("1/g1")
        assert fake.sets == []

    def test_delayed_admin_change_polled(self, clock):
        fake = FakeSwitch(delay=2)
        NetgearPortManager(backend_for(fake)).disable_port("1/g2")
        assert fake.mib[f"{oids.IF_ADMIN_STATUS}.2"] == oids.ADMIN_DOWN
        assert clock.sleeps

    def test_admin_change_that_does_not_land(self):
        fake = FakeSwitch(delay=10**6)
        with pytest.raises(PortError, match="did not apply"):
            NetgearPortManager(backend_for(fake, timeout=3)).disable_port("1/g2")

    def test_stack_link_and_cpu_refused(self, fake):
        port = NetgearPortManager(backend_for(fake))
        with pytest.raises(PortError):
            port.disable_port("1/stack1")
        with pytest.raises(PortError):
            port.disable_port("cpu")

    def test_unknown_port(self, fake):
        with pytest.raises(PortError, match="does not exist"):
            NetgearPortManager(backend_for(fake)).enable_port("7/g7")

    def test_configure_port_access_vlan_and_description(self, fake):
        NetgearPortManager(backend_for(fake)).configure_port(
            PortConfig(port="1/g2", enabled=False, description="uplink", mode=PortMode.ACCESS, access_vlan=10)
        )
        assert fake.mib[f"{oids.IF_ALIAS}.2"] == "uplink"
        assert fake.mib[f"{oids.PVID}.2"] == 10
        assert fake.mib[f"{oids.IF_ADMIN_STATUS}.2"] == oids.ADMIN_DOWN

    def test_configure_port_speed_not_implemented(self, fake):
        with pytest.raises(NotImplementedError):
            NetgearPortManager(backend_for(fake)).configure_port(PortConfig(port="1/g2", speed=PortSpeed.SPEED_100M))
        with pytest.raises(NotImplementedError):
            NetgearPortManager(backend_for(fake)).configure_port(PortConfig(port="1/g2", duplex=DuplexMode.FULL))

    def test_get_port_status_filtered(self, fake):
        status = NetgearPortManager(backend_for(fake)).get_port_status("1/g1")
        assert [(p.port, p.link_up, p.speed) for p in status] == [("1/g1", True, "1000")]


class TestMonitoring:
    @pytest.fixture
    def mon(self, fake):
        m = fake.mib
        m[oids.SYS_NAME] = "stack"
        m[oids.SYS_DESCR] = "S3300-52X"
        m[oids.SYS_UPTIME] = 123456
        m[oids.BRIDGE_ADDRESS] = bytes.fromhex("0a0b0c0d0e0f")
        for i, (cls, name, model, sw) in enumerate(
            [(3, "Unit 1", "GSM4352", "6.6.4.28"), (10, "Fan", "", ""), (3, "Unit 2", "GSM4352", "6.6.4.28")]
        ):
            m[f"{oids.ENT_CLASS}.{100 + i}"] = cls
            m[f"{oids.ENT_NAME}.{100 + i}"] = name
            m[f"{oids.ENT_MODEL}.{100 + i}"] = model
            m[f"{oids.ENT_SOFTWARE_REV}.{100 + i}"] = sw
            m[f"1.3.6.1.2.1.47.1.1.1.1.11.{100 + i}"] = "SERIAL-OF-MGMT-UNIT"
        return NetgearMonitoringManager(backend_for(fake))

    def test_system_info(self, mon):
        info = mon.get_system_info()
        assert (info.hostname, info.model, info.firmware_version, info.uptime) == ("stack", "GSM4352", "6.6.4.28", 1234)
        assert info.mac_address == "0a:0b:0c:0d:0e:0f"

    def test_serial_is_not_reported(self, mon):
        assert mon.get_system_info().serial_number == ""

    def test_stack_units(self, mon):
        assert [u.name for u in mon.get_stack_units()] == ["Unit 1", "Unit 2"]

    def test_port_status_skips_cpu(self, mon):
        status = {p.port: p for p in mon.get_port_status()}
        assert "cpu" not in status
        assert status["1/g1"].link_up and not status["1/g3"].link_up
        assert status["1/g3"].speed == ""

    def test_statistics_use_hc_counters(self, fake, mon):
        fake.mib[f"{oids.IF_HC_IN_OCTETS}.1"] = 10**12
        fake.mib[f"{oids.IF_HC_OUT_OCTETS}.1"] = 5
        fake.mib[f"{oids.IF_HC_IN_UCAST_PKTS}.1"] = 3
        fake.mib[f"{oids.IF_HC_IN_MULTICAST_PKTS}.1"] = 2
        fake.mib[f"{oids.IF_HC_IN_BROADCAST_PKTS}.1"] = 1
        fake.mib[f"{oids.IF_IN_ERRORS}.1"] = 7
        stats = {s.port: s for s in mon.get_port_statistics()}
        assert (stats["1/g1"].rx_bytes, stats["1/g1"].tx_bytes, stats["1/g1"].rx_packets, stats["1/g1"].rx_errors) == (
            10**12,
            5,
            6,
            7,
        )

    def test_statistics_fall_back_to_32_bit(self, fake, mon):
        fake.mib[f"{oids.IF_IN_OCTETS}.1"] = 99
        fake.mib[f"{oids.IF_IN_UCAST_PKTS}.1"] = 4
        stats = {s.port: s for s in mon.get_port_statistics()}
        assert (stats["1/g1"].rx_bytes, stats["1/g1"].rx_packets) == (99, 4)

    def test_stack_ports(self, fake, mon):
        fake.mib.update(
            {
                f"{oids.STACK_PORT_UNIT}.1": 1,
                f"{oids.STACK_PORT_TAG}.1": "0/1",
                f"{oids.STACK_PORT_CONFIGURED_MODE}.1": 1,
                f"{oids.STACK_PORT_RUNNING_MODE}.1": 1,
                f"{oids.STACK_PORT_LINK}.1": 1,
                f"{oids.STACK_PORT_SPEED}.1": 10,
                f"{oids.STACK_PORT_ERRORS}.1": 2,
            }
        )
        [sp] = mon.get_stack_ports()
        assert (sp.unit, sp.configured_mode, sp.running_mode, sp.link_up, sp.speed_gbps, sp.errors) == (
            1,
            "stack",
            "stack",
            True,
            10,
            2,
        )

    def test_sensor_data_not_implemented(self, mon):
        with pytest.raises(NotImplementedError):
            mon.get_sensor_data()


class TestLACP:
    @pytest.fixture
    def lacp(self, fake):
        fake.mib[f"{oids.LAG_ATTACHED_AGG_ID}.3"] = 5
        fake.mib[f"{oids.LAG_ATTACHED_AGG_ID}.4"] = 5
        fake.mib[f"{oids.LAG_ATTACHED_AGG_ID}.1"] = 0
        fake.mib[f"{oids.LAG_ACTOR_ADMIN_KEY}.5"] = 7
        fake.mib[f"{oids.LAG_PARTNER_OPER_KEY}.5"] = 9
        return NetgearLACPManager(backend_for(fake))

    def test_reads_lags(self, lacp):
        [info] = lacp.get_port_channel_info()
        assert (info.port_channel_id, info.member_ports, info.admin_key, info.partner_key) == (
            1,
            ["1/g3", "1/g4"],
            7,
            9,
        )
        assert info.status == "down"

    def test_writes_not_implemented(self, lacp):
        with pytest.raises(NotImplementedError):
            lacp.create_port_channel(1, ["1/g3"])
        with pytest.raises(NotImplementedError):
            lacp.delete_port_channel(1)


def route_index(dest, prefix, nh):
    return f"1.4.{dest}.{prefix}.2.0.0.1.4.{nh}"


class FakeWeb:
    def __init__(self, routes=None, fail_login=False):
        self.routes = list(routes or [])
        self.fail_login = fail_login
        self.events = []

    def login(self):
        self.events.append("login")
        if self.fail_login:
            raise WebError("refused")

    def close(self):
        self.events.append("close")

    def read_static_routes(self):
        return list(self.routes)

    def add_static_route(self, dest, next_hop, preference=1, description=""):
        self.events.append(("add", dest, next_hop))
        self.routes.append(StaticRoute(dest, next_hop, preference, description))

    def delete_static_route(self, dest, next_hop):
        self.events.append(("delete", dest, next_hop))
        self.routes = [r for r in self.routes if (r.dest, r.next_hop) != (dest, next_hop)]


class TestRouting:
    @pytest.fixture
    def routed(self, fake):
        m = fake.mib
        m[oids.ROUTING_MODE] = 1
        m[f"{oids.IP_VLAN_IFINDEX}.10"] = 40
        m[f"{oids.IP_IF_ADDRESS}.40"] = "192.0.2.50"
        m[f"{oids.IP_IF_NETMASK}.40"] = "255.255.255.0"
        m[f"{oids.IP_IF_ROUTING}.40"] = 1
        m[f"{oids.IP_IF_MTU}.40"] = 1500
        m[f"{oids.CIDR_ROUTE}.{oids.CIDR_ROUTE_PROTO}.{route_index('10.1.0.0', 16, '192.0.2.1')}"] = 3
        m[f"{oids.CIDR_ROUTE}.{oids.CIDR_ROUTE_METRIC1}.{route_index('10.1.0.0', 16, '192.0.2.1')}"] = 1
        m[oids.MGMT_IP] = "198.51.100.5"
        m[oids.MGMT_MASK] = "255.255.255.0"
        m[oids.MGMT_GATEWAY] = "198.51.100.1"
        m[oids.MGMT_PROTOCOL] = 1
        m[oids.MGMT_VLAN] = 1
        m[oids.HTTP_MAX_SESSIONS] = 4
        return fake

    def manager(self, fake, web=None, wait=30.0):
        return NetgearRoutingManager(
            backend_for(fake), web_factory=(lambda: web) if web else None, web_wait=wait, web_poll_interval=10.0
        )

    def test_parse_route_index(self):
        assert parse_route_index(route_index("10.1.0.0", 16, "192.0.2.1")) == ("10.1.0.0", 16, "192.0.2.1")
        assert parse_route_index("2.16.1.2") is None

    def test_get_routing(self, routed):
        state = self.manager(routed).get_routing()
        assert state.enabled
        iface = state.interfaces[10]
        assert (iface.ifindex, iface.address, iface.routing, iface.mtu) == (40, "192.0.2.50/24", True, 1500)
        assert [(r.dest, r.next_hop, r.proto, r.metric) for r in state.routes] == [("10.1.0.0/16", "192.0.2.1", 3, 1)]

    def test_set_interface_creates_and_configures(self, routed):
        self.manager(routed).set_interface(20, "192.0.2.129/25", routing=True, mtu=1400)
        assert routed.mib[f"{oids.IP_VLAN_IFINDEX}.20"] == 120
        assert routed.mib[f"{oids.IP_IF_ADDRESS}.120"] == "192.0.2.129"
        assert routed.mib[f"{oids.IP_IF_NETMASK}.120"] == "255.255.255.128"
        assert routed.mib[f"{oids.IP_IF_MTU}.120"] == 1400

    def test_management_interface_is_protected(self, routed):
        with pytest.raises(SwitchError, match="management address"):
            self.manager(routed).set_interface(10, "192.0.2.50/25")
        with pytest.raises(SwitchError, match="management address"):
            self.manager(routed).remove_interface(10)

    def test_invalid_interface_address(self, routed):
        with pytest.raises(SwitchError):
            self.manager(routed).set_interface(20, "nonsense")

    def test_get_management(self, routed):
        m = self.manager(routed).get_management()
        assert (m.address, m.gateway, m.protocol, m.vlan) == ("198.51.100.5/24", "198.51.100.1", 1, 1)

    def test_management_unchanged_is_noop(self, routed):
        self.manager(routed).set_management("198.51.100.5/24", "198.51.100.1")
        assert routed.sets == []

    def test_management_change_over_itself_refused(self, routed, monkeypatch):
        monkeypatch.setattr("socket.gethostbyname", lambda host: "198.51.100.5")
        with pytest.raises(SwitchError, match="over itself"):
            self.manager(routed).set_management("198.51.100.6/24", "198.51.100.1")

    def test_management_change_sequence(self, routed, monkeypatch):
        monkeypatch.setattr("socket.gethostbyname", lambda host: "192.0.2.50")
        self.manager(routed).set_management("198.51.100.9/24", "198.51.100.254")
        assert [tuple(o for o, _ in p) for p in routed.sets] == [
            (oids.MGMT_GATEWAY,),
            (oids.MGMT_IP, oids.MGMT_MASK),
            (oids.MGMT_GATEWAY,),
        ]

    def test_management_gateway_outside_network(self, routed):
        with pytest.raises(SwitchError, match="not in"):
            self.manager(routed).set_management("198.51.100.9/24", "203.0.113.1")

    def test_management_from_dhcp_refused(self, routed, monkeypatch):
        monkeypatch.setattr("socket.gethostbyname", lambda host: "192.0.2.50")
        routed.mib[oids.MGMT_PROTOCOL] = 3
        with pytest.raises(SwitchError, match="dhcp"):
            self.manager(routed).set_management("198.51.100.9/24")

    def test_static_routes_need_web_password(self, routed):
        with pytest.raises(SwitchError, match="web UI password"):
            self.manager(routed).list_static_routes()

    def test_static_route_roundtrip_logs_out(self, routed):
        web = FakeWeb()
        mgr = self.manager(routed, web)
        mgr.add_static_route("10.9.0.0/16", "192.0.2.1", preference=5, description="lab")
        assert web.routes == [StaticRoute("10.9.0.0/16", "192.0.2.1", 5, "lab")]
        mgr.add_static_route("10.9.0.0/16", "192.0.2.1", preference=5, description="lab")
        assert [e for e in web.events if isinstance(e, tuple)] == [("add", "10.9.0.0/16", "192.0.2.1")]
        mgr.delete_static_route("10.9.0.0/16", "192.0.2.1")
        assert web.routes == []
        assert web.events.count("login") == web.events.count("close") == 3

    def test_web_logout_even_if_login_fails(self, routed):
        web = FakeWeb(fail_login=True)
        with pytest.raises(WebError):
            self.manager(routed, web).list_static_routes()
        assert web.events == ["login", "close"]

    def test_invalid_static_route_input(self, routed):
        mgr = self.manager(routed, FakeWeb())
        with pytest.raises(SwitchError):
            mgr.add_static_route("10.0.0.0/33", "192.0.2.1")
        with pytest.raises(SwitchError):
            mgr.add_static_route("10.0.0.0/8", "192.0.2.1", preference=0)
        with pytest.raises(SwitchError):
            mgr.add_static_route("10.0.0.0/8", "192.0.2.1", description="no spaces allowed")

    def test_waits_for_free_web_slot_then_gives_up(self, routed):
        for i in range(4):
            routed.mib[f"{oids.LOGIN_SESSION}.{oids.LOGIN_SESSION_TYPE}.{i + 1}"] = oids.LOGIN_TYPE_HTTP
            routed.mib[f"{oids.LOGIN_SESSION}.{oids.LOGIN_SESSION_USER}.{i + 1}"] = f"user{i}"
            routed.mib[f"{oids.LOGIN_SESSION}.{oids.LOGIN_SESSION_INET}.{i + 1}"] = bytes([192, 0, 2, i + 1])
        with pytest.raises(SwitchError, match="4 of 4 taken: user0 from 192.0.2.1"):
            self.manager(routed, FakeWeb(), wait=25.0).list_static_routes()

    def test_web_sessions(self, routed):
        routed.mib[f"{oids.LOGIN_SESSION}.{oids.LOGIN_SESSION_TYPE}.1"] = oids.LOGIN_TYPE_HTTPS
        routed.mib[f"{oids.LOGIN_SESSION}.{oids.LOGIN_SESSION_USER}.1"] = "admin"
        routed.mib[f"{oids.LOGIN_SESSION}.{oids.LOGIN_SESSION_INET}.1"] = bytes([192, 0, 2, 7])
        [s] = self.manager(routed).web_sessions()
        assert (s.user, s.address, s.type) == ("admin", "192.0.2.7", "https")


LOGIN_HTML = '<input NAME="CSRFToken" TYPE="hidden" VALUE="tok123">'
ROUTE_HTML = (
    LOGIN_HTML
    + 'NAME=1.1.1.v_1_1_1 VALUE="Static" NAME=1.1.1.v_1_1_2 VALUE="10.9.0.0" NAME=1.1.1.v_1_1_3 VALUE="255.255.0.0" '
    + 'NAME=1.1.1.v_1_1_5 VALUE="192.0.2.1" NAME=1.1.1.v_1_1_8 VALUE="5" NAME=1.1.1.v_1_1_10 VALUE="lab" name="1.1.1.gecb4" '
    + 'NAME=1.1.2.v_1_1_1 VALUE="Default" NAME=1.1.2.v_1_1_5 VALUE="192.0.2.254" NAME=1.1.2.v_1_1_8 VALUE="1" '
    + 'NAME=1.1.3.v_1_1_1 VALUE="Connected" NAME=1.1.3.v_1_1_5 VALUE="0.0.0.0"'
)


class FakeResponse:
    def __init__(self, text="", status=200):
        self.text = text
        self.status_code = status


class FakeHttp:
    def __init__(self, pages):
        self.pages = pages
        self.posts = []
        self.cookies = {"SID": "x"}

    def get(self, url, headers=None, timeout=None):
        return FakeResponse(self.pages["/" + url.split("//", 1)[1].partition("/")[2]])

    def post(self, url, data=None, headers=None, timeout=None):
        self.posts.append((url, data))
        return FakeResponse("")


class TestWeb:
    @pytest.fixture
    def http(self):
        return FakeHttp({"/": LOGIN_HTML, "/RouteConfig.html": ROUTE_HTML, "/base/status.html": LOGIN_HTML})

    def test_login_logout(self, http):
        web = NetgearWeb("192.0.2.50", "pw", session=http)
        web.login()
        assert http.posts[0][1]["pwd"] == "pw" and http.posts[0][1]["CSRFToken"] == "tok123"
        web.close()
        assert http.posts[-1][0].endswith("/base/status.html")
        assert not web.logged_in

    def test_login_refused_without_sid(self, http):
        http.cookies = {}
        with pytest.raises(WebError, match="refused"):
            NetgearWeb("192.0.2.50", "pw", session=http).login()

    def test_read_static_routes_skips_connected(self, http):
        routes = NetgearWeb("192.0.2.50", "pw", session=http).read_static_routes()
        assert routes == [
            StaticRoute("10.9.0.0/16", "192.0.2.1", 5, "lab"),
            StaticRoute("0.0.0.0/0", "192.0.2.254", 1, ""),
        ]

    def test_add_default_route_form(self, http):
        NetgearWeb("192.0.2.50", "pw", session=http).add_static_route("0.0.0.0/0", "192.0.2.254", 2, "gw")
        data = http.posts[-1][1]
        assert data["v_g_1_1_1"] == "Default" and data["v_g_1_1_11"] == "0.0.0.0" and data["v_g_1_1_5"] == "192.0.2.254"

    def test_delete_posts_checkbox_row(self, http):
        NetgearWeb("192.0.2.50", "pw", session=http).delete_static_route("10.9.0.0/16", "192.0.2.1")
        data = http.posts[-1][1]
        assert data["1.1.1.gecb4"] == "on" and data["v_4_3_1"] == "DELETE" and data["1.1.1.v_1_1_3"] == "255.255.0.0"

    def test_delete_unknown_route_is_noop(self, http):
        NetgearWeb("192.0.2.50", "pw", session=http).delete_static_route("10.77.0.0/16", "192.0.2.1")
        assert http.posts == []
