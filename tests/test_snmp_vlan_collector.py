"""Tests for networkmgmt.snmp_vlan_dump.collector with a fake SNMP session."""

from __future__ import annotations

import pytest
from loguru import logger

from networkmgmt.snmp import SnmpAuthenticationError, SnmpCredentials, SnmpTimeoutError, ports_to_portlist
from networkmgmt.snmp_vlan_dump import collector as collector_mod
from networkmgmt.snmp_vlan_dump import snmp as oids
from networkmgmt.snmp_vlan_dump._util import classify_port, ifname_to_descr
from networkmgmt.snmp_vlan_dump.collector import VlanDataCollector
from networkmgmt.snmp_vlan_dump.formatters import MarkdownFormatter, TerminalFormatter


class FakeSession:
    """Stands in for AsyncSnmpSession; tables map base OID to suffix-keyed rows."""

    tables: dict[str, dict] = {}
    scalars: dict[str, object] = {}
    error: Exception | None = None
    created: list[tuple[str, SnmpCredentials]] = []

    def __init__(self, host, credentials, **kwargs):
        FakeSession.created.append((host, credentials))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def get(self, oid):
        if FakeSession.error:
            raise FakeSession.error
        return str(self.scalars[oid])

    async def get_int(self, oid):
        return int(self.scalars[oid])

    async def _walk(self, base):
        return dict(self.tables.get(base, {}))

    walk = walk_int = walk_octets = _walk


def _gs108t_tables() -> dict[str, dict]:
    pl = ports_to_portlist
    return {
        oids.OID_IF_DESCR: {str(i): f"Slot: 0 Port: {i} Gigabit - Level" for i in range(1, 9)},
        oids.OID_IF_OPER_STATUS: {str(i): 1 if i < 3 else 2 for i in range(1, 9)},
        oids.OID_BASE_PORT_IFINDEX: {str(i): i for i in range(1, 9)},
        oids.OID_VLAN_STATIC_NAME: {"10": "servers"},
        oids.OID_DOT1Q_PVID: {"1": 1, "2": 10, "3": 1},
        oids.OID_VLAN_EGRESS_PORTS: {"10": pl({2, 3}, 2)},
        oids.OID_VLAN_UNTAGGED_PORTS: {"10": pl({2}, 2)},
        oids.OID_VLAN_CURRENT_EGRESS: {"0.1": pl({1, 3, 8}, 2), "0.10": pl({9}, 2)},
        oids.OID_VLAN_CURRENT_UNTAG: {"0.1": pl({1}, 2), "0.10": pl(set(), 2)},
    }


@pytest.fixture
def fake_session(monkeypatch):
    FakeSession.tables = _gs108t_tables()
    FakeSession.scalars = {oids.OID_SYS_DESCR: "GS108Tv2", oids.OID_SYS_NAME: "sw", oids.OID_SYS_UPTIME: 8640100}
    FakeSession.error = None
    FakeSession.created = []
    monkeypatch.setattr(collector_mod, "AsyncSnmpSession", FakeSession)
    return FakeSession


class TestMerge:
    def test_static_preferred_current_adds_factory_vlans(self, fake_session):
        data = VlanDataCollector("10.0.0.1").collect()

        assert sorted(data.egress_data) == [1, 10]
        assert data.egress_data[10] == ports_to_portlist({2, 3}, 2)
        assert data.port_vlans[1].untagged == [1]
        assert data.port_vlans[3].tagged == [1, 10]
        assert data.port_vlans[2].untagged == [10]
        assert data.vlan_names == {10: "servers"}

    def test_pvid_collected(self, fake_session):
        data = VlanDataCollector("10.0.0.1").collect()

        assert data.pvid_data == {1: 1, 2: 10, 3: 1}
        assert "PVID" in TerminalFormatter(data).format()
        assert "PVID" in MarkdownFormatter(data).format()

    def test_uptime_formatted(self, fake_session):
        assert VlanDataCollector("10.0.0.1").collect().sys_uptime == "1 days, 00:00:01"


class TestStackPorts:
    def _stack_tables(self) -> dict[str, dict]:
        tables = _gs108t_tables()
        tables[oids.OID_IF_DESCR] = {"1": "unit 1 port 1 Gigabit - Level", "2": "unit 1 port 2 Gigabit - Level"}
        tables[oids.OID_IF_NAME] = {"1": "1/g1", "2": "1/g2", "3": "1/xg51", "4": "l1", "5": "cpu"}
        tables[oids.OID_BASE_PORT_IFINDEX] = {"1": 1, "2": 2, "4": 4}
        tables[oids.OID_VLAN_EGRESS_PORTS] = {}
        tables[oids.OID_VLAN_UNTAGGED_PORTS] = {}
        tables[oids.OID_VLAN_CURRENT_EGRESS] = {"0.1": ports_to_portlist({1, 2, 3}, 1)}
        tables[oids.OID_VLAN_CURRENT_UNTAG] = {"0.1": ports_to_portlist({1, 2, 3}, 1)}
        return tables

    def test_stack_link_not_member(self, fake_session):
        fake_session.tables = self._stack_tables()

        data = VlanDataCollector("10.0.0.1").collect()

        assert data.stack_ports == [3]
        assert data.port_kind[3] == "stack"
        assert data.port_kind[4] == "lag"
        assert data.port_kind[5] == "cpu"
        assert data.all_phys == [1, 2]
        assert 3 not in data.port_vlans
        assert data.unit_info[1].max_port == 2
        assert "Stack links" in TerminalFormatter(data).format()
        assert "Stack links" in MarkdownFormatter(data).format()

    def test_classify(self):
        assert classify_port("1/g3", "", True) == "front"
        assert classify_port("2/xg27", "", False) == "stack"
        assert classify_port("2/xg27", "", None) == "front"
        assert classify_port("l3", "", True) == "lag"
        assert classify_port("cpu", "", True) == "cpu"
        assert classify_port("", "Slot: 0 Port: 3 Gigabit - Level", True) == "front"
        assert classify_port("vlan1", "", True) == "other"

    def test_ifname_to_descr(self):
        assert ifname_to_descr("2/xg27") == "unit 2 port 27 10G - Level"
        assert ifname_to_descr("l1") is None


class TestErrors:
    def _messages(self):
        messages: list[str] = []
        logger.enable("networkmgmt")
        sink = logger.add(messages.append, level="ERROR", format="{message}")
        return messages, sink

    def test_auth_error_logged_and_raised(self, fake_session):
        fake_session.error = SnmpAuthenticationError("get 1.3.6: WrongDigest")
        messages, sink = self._messages()
        creds = SnmpCredentials.v3("admin", "secret123", auth_protocol="sha")
        try:
            with pytest.raises(SnmpAuthenticationError):
                VlanDataCollector("10.0.0.1", credentials=creds).collect()
        finally:
            logger.remove(sink)
            logger.disable("networkmgmt")

        assert any("authentication failed" in m and "admin" in m for m in messages)
        assert not any("secret123" in m for m in messages)

    def test_v3_timeout_hints_credentials(self, fake_session):
        fake_session.error = SnmpTimeoutError("get: RequestTimedOut")
        messages, sink = self._messages()
        try:
            with pytest.raises(SnmpTimeoutError):
                VlanDataCollector("10.0.0.1", credentials=SnmpCredentials.v3("admin", "secret123")).collect()
        finally:
            logger.remove(sink)
            logger.disable("networkmgmt")

        assert any("SNMPv3 user" in m and "sha512" in m for m in messages)

    def test_credentials_passed_to_session(self, fake_session):
        creds = SnmpCredentials.v3("u", "authkey12", priv_key="privkey12")
        VlanDataCollector("10.0.0.9", credentials=creds).collect()

        assert fake_session.created == [("10.0.0.9", creds)]
