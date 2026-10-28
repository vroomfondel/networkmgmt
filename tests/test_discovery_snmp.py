"""Tests for networkmgmt/discovery/snmp.py"""

import asyncio

import pytest

from networkmgmt.discovery.models import DiscoveredHost, SwitchPortMapping
from networkmgmt.discovery.snmp import SnmpBridgeDiscovery, _build_port_name_map
from networkmgmt.snmp import SnmpAuthenticationError, SnmpCredentials, SnmpTimeoutError


class TestBuildPortNameMap:
    """Tests for _build_port_name_map function."""

    def test_netgear_unit_port_format(self):
        """Test parsing Netgear 'unit X port Y' format."""
        if_descrs = {1: "unit 1 port 5 Gigabit - Level"}

        result = _build_port_name_map(if_descrs)

        assert result[1] == "U1/g5"

    def test_netgear_slot_port_format(self):
        """Test parsing Netgear 'Slot: X Port: Y' format."""
        if_descrs = {2: "Slot: 0 Port: 3 Gigabit - Level"}

        result = _build_port_name_map(if_descrs)

        assert result[2] == "U0/g3"

    def test_lag_format(self):
        """Test parsing LAG format."""
        if_descrs = {3: "lag 2"}

        result = _build_port_name_map(if_descrs)

        assert result[3] == "LAG2"

    def test_10g_port_detection(self):
        """Test 10G port speed detection."""
        if_descrs = {4: "unit 1 port 49 10G - Level"}

        result = _build_port_name_map(if_descrs)

        assert result[4] == "U1/x49"

    def test_non_netgear_fallback(self):
        """Test non-Netgear format uses description directly."""
        if_descrs = {5: "GigabitEthernet1/0/1"}

        result = _build_port_name_map(if_descrs)

        assert result[5] == "GigabitEthernet1/0/1"

    def test_multiple_entries(self):
        """Test parsing multiple interface descriptions."""
        if_descrs = {
            1: "unit 1 port 5 Gigabit - Level",
            2: "lag 2",
            3: "Slot: 0 Port: 3 10G - Level",
        }

        result = _build_port_name_map(if_descrs)

        assert result[1] == "U1/g5"
        assert result[2] == "LAG2"
        assert result[3] == "U0/x3"


class TestBuildL2Topology:
    """Tests for SnmpBridgeDiscovery.build_l2_topology static method."""

    def test_host_directly_connected(self):
        """Test host directly connected to switch."""
        hosts = [
            DiscoveredHost(ip="192.168.1.10", mac="aa:bb:cc:dd:ee:ff"),
        ]
        mac_table = {
            "aa:bb:cc:dd:ee:ff": [
                SwitchPortMapping(
                    switch_ip="192.168.1.1",
                    switch_name="switch1",
                    port_index=5,
                    port_name="U1/g5",
                )
            ]
        }
        switch_ips = {"192.168.1.1"}

        l2_entries, topology_tree = SnmpBridgeDiscovery.build_l2_topology(hosts, mac_table, switch_ips)

        assert len(l2_entries) == 1
        assert l2_entries[0].host_ip == "192.168.1.10"
        assert l2_entries[0].host_mac == "aa:bb:cc:dd:ee:ff"
        assert l2_entries[0].switch.switch_ip == "192.168.1.1"
        assert l2_entries[0].switch.port_name == "U1/g5"
        assert l2_entries[0].source == "snmp"
        assert topology_tree["192.168.1.10"] == "192.168.1.1"

    def test_uplink_port_detection(self):
        """Test uplink port detection."""
        hosts = [
            DiscoveredHost(ip="192.168.1.1", mac="11:11:11:11:11:11"),
            DiscoveredHost(ip="192.168.1.2", mac="22:22:22:22:22:22"),
            DiscoveredHost(ip="192.168.1.10", mac="aa:bb:cc:dd:ee:ff"),
        ]
        # Switch1's MAC appears in switch2's table - that port is an uplink
        mac_table = {
            "11:11:11:11:11:11": [
                SwitchPortMapping(
                    switch_ip="192.168.1.2",
                    switch_name="switch2",
                    port_index=10,
                    port_name="U0/g10",
                )
            ],
            "aa:bb:cc:dd:ee:ff": [
                # Host appears on both switches
                SwitchPortMapping(
                    switch_ip="192.168.1.1",
                    switch_name="switch1",
                    port_index=5,
                    port_name="U1/g5",
                ),
                SwitchPortMapping(
                    switch_ip="192.168.1.2",
                    switch_name="switch2",
                    port_index=10,
                    port_name="U0/g10",
                ),
            ],
        }
        switch_ips = {"192.168.1.1", "192.168.1.2"}

        l2_entries, topology_tree = SnmpBridgeDiscovery.build_l2_topology(hosts, mac_table, switch_ips)

        # Host should be placed on switch1 (non-uplink port)
        host_entry = [e for e in l2_entries if e.host_ip == "192.168.1.10"][0]
        assert host_entry.switch.switch_ip == "192.168.1.1"
        assert host_entry.switch.port_name == "U1/g5"

    def test_prefers_non_uplink_ports(self):
        """Test preference for non-uplink ports."""
        hosts = [
            DiscoveredHost(ip="192.168.1.1", mac="11:11:11:11:11:11"),
            DiscoveredHost(ip="192.168.1.10", mac="aa:bb:cc:dd:ee:ff"),
        ]
        # Host MAC on both switches, but one port is uplink
        mac_table = {
            "11:11:11:11:11:11": [
                SwitchPortMapping(
                    switch_ip="192.168.1.2",
                    switch_name="switch2",
                    port_index=10,
                    port_name="U0/g10",
                )
            ],
            "aa:bb:cc:dd:ee:ff": [
                SwitchPortMapping(
                    switch_ip="192.168.1.1",
                    switch_name="switch1",
                    port_index=5,
                    port_name="U1/g5",
                ),
                SwitchPortMapping(
                    switch_ip="192.168.1.2",
                    switch_name="switch2",
                    port_index=10,
                    port_name="U0/g10",
                ),
            ],
        }
        switch_ips = {"192.168.1.1", "192.168.1.2"}

        l2_entries, topology_tree = SnmpBridgeDiscovery.build_l2_topology(hosts, mac_table, switch_ips)

        # Should prefer switch1 (non-uplink)
        host_entry = [e for e in l2_entries if e.host_ip == "192.168.1.10"][0]
        assert host_entry.switch.switch_ip == "192.168.1.1"

    def test_switch_to_switch_hierarchy(self):
        """Test switch-to-switch hierarchy in topology_tree."""
        hosts = [
            DiscoveredHost(ip="192.168.1.1", mac="11:11:11:11:11:11"),
            DiscoveredHost(ip="192.168.1.2", mac="22:22:22:22:22:22"),
        ]
        # Switch1's MAC on switch2's port means switch1 is behind switch2
        mac_table = {
            "11:11:11:11:11:11": [
                SwitchPortMapping(
                    switch_ip="192.168.1.2",
                    switch_name="switch2",
                    port_index=10,
                    port_name="U0/g10",
                )
            ],
        }
        switch_ips = {"192.168.1.1", "192.168.1.2"}

        l2_entries, topology_tree = SnmpBridgeDiscovery.build_l2_topology(hosts, mac_table, switch_ips)

        # Switch1 is behind switch2
        assert topology_tree["192.168.1.1"] == "192.168.1.2"
        # L2 entry for switch-to-switch connection
        switch_entry = [e for e in l2_entries if e.host_ip == "192.168.1.1"][0]
        assert switch_entry.switch.switch_ip == "192.168.1.2"
        assert switch_entry.source == "snmp"

    def test_gateway_host_skipped(self):
        """Test gateway hosts are skipped."""
        hosts = [
            DiscoveredHost(ip="192.168.1.254", mac="aa:bb:cc:dd:ee:ff", is_gateway=True),
        ]
        mac_table = {
            "aa:bb:cc:dd:ee:ff": [
                SwitchPortMapping(
                    switch_ip="192.168.1.1",
                    switch_name="switch1",
                    port_index=1,
                    port_name="U1/g1",
                )
            ]
        }
        switch_ips = {"192.168.1.1"}

        l2_entries, topology_tree = SnmpBridgeDiscovery.build_l2_topology(hosts, mac_table, switch_ips)

        assert len(l2_entries) == 0
        assert len(topology_tree) == 0

    def test_host_without_mac_skipped(self):
        """Test hosts without MAC are skipped."""
        hosts = [
            DiscoveredHost(ip="192.168.1.10", mac=""),
        ]
        mac_table = {}
        switch_ips = {"192.168.1.1"}

        l2_entries, topology_tree = SnmpBridgeDiscovery.build_l2_topology(hosts, mac_table, switch_ips)

        assert len(l2_entries) == 0


def _query(discovery, ip, credentials):
    return asyncio.run(discovery._query_switch(ip, credentials))


class FakeSession:
    """Stands in for AsyncSnmpSession in SnmpBridgeDiscovery._query_switch."""

    tables: dict = {}
    error: Exception | None = None
    created: list = []

    def __init__(self, host, credentials, **kwargs):
        FakeSession.created.append((host, credentials, kwargs))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def get(self, oid):
        if FakeSession.error:
            raise FakeSession.error
        return "sw1"

    async def _walk(self, base):
        return dict(FakeSession.tables.get(base, {}))

    walk = walk_int = _walk


@pytest.fixture
def fake_session(monkeypatch):
    from networkmgmt.discovery import snmp as snmp_mod

    FakeSession.error = None
    FakeSession.created = []
    FakeSession.tables = {
        snmp_mod._OID_IF_DESCR: {"5": "unit 1 port 5 Gigabit - Level"},
        snmp_mod._OID_DOT1D_BASE_PORT_IF_INDEX: {"5": 5},
    }
    monkeypatch.setattr(snmp_mod, "AsyncSnmpSession", FakeSession)
    return FakeSession


class TestQuerySwitch:
    def test_qbridge_suffix_keys(self, fake_session):
        from networkmgmt.discovery import snmp as snmp_mod

        fake_session.tables[snmp_mod._OID_DOT1Q_TP_FDB_PORT] = {"10.0.17.34.51.68.85": 5, "20.0.17.34.51.68.85": 5}

        result = _query(SnmpBridgeDiscovery([]), "10.0.0.1", "public")

        assert list(result) == ["00:11:22:33:44:55"]
        assert result["00:11:22:33:44:55"].port_name == "U1/g5"
        assert result["00:11:22:33:44:55"].switch_name == "sw1"

    def test_bridge_mib_fallback(self, fake_session):
        from networkmgmt.discovery import snmp as snmp_mod

        fake_session.tables[snmp_mod._OID_DOT1D_TP_FDB_PORT] = {"0.17.34.51.68.85": 5, "0.0.0.0.0.1": 0}

        result = _query(SnmpBridgeDiscovery([]), "10.0.0.1", "public")

        assert list(result) == ["00:11:22:33:44:55"]

    def test_v3_credentials_reach_session(self, fake_session):
        creds = SnmpCredentials.v3("admin", "authkey12", priv_key="privkey12")

        _query(SnmpBridgeDiscovery([], timeout=2.0, retries=0), "10.0.0.1", creds)

        host, used, kwargs = fake_session.created[0]
        assert used is creds
        assert kwargs == {"timeout": 2.0, "retries": 0}

    def test_string_means_v2c(self, fake_session):
        _query(SnmpBridgeDiscovery([]), "10.0.0.1", "private")

        assert fake_session.created[0][1] == SnmpCredentials.v2c("private")

    def test_auth_failure_logged_not_silent(self, fake_session):
        from loguru import logger

        fake_session.error = SnmpAuthenticationError("WrongDigest")
        messages: list[str] = []
        logger.enable("networkmgmt")
        sink = logger.add(messages.append, level="ERROR", format="{message}")
        try:
            result = _query(SnmpBridgeDiscovery([]), "10.0.0.1", SnmpCredentials.v3("admin", "authkey12"))
        finally:
            logger.remove(sink)
            logger.disable("networkmgmt")

        assert result == {}
        assert any("authentication failed" in m and "admin" in m for m in messages)
        assert not any("authkey12" in m for m in messages)

    def test_discover_mixed_switches(self, fake_session):
        from networkmgmt.discovery import snmp as snmp_mod

        fake_session.tables[snmp_mod._OID_DOT1D_TP_FDB_PORT] = {"0.17.34.51.68.85": 5}
        creds = SnmpCredentials.v3("admin", "authkey12")

        merged = SnmpBridgeDiscovery([("10.0.0.1", "public"), ("10.0.0.2", creds)]).discover()

        assert len(merged["00:11:22:33:44:55"]) == 2
        assert [c for _, c, _ in fake_session.created] == [SnmpCredentials.v2c("public"), creds]


def test_describe_snmp_error_v3_timeout():
    from networkmgmt.discovery.snmp import describe_snmp_error

    msg = describe_snmp_error(SnmpTimeoutError("t"), "h", SnmpCredentials.v3("admin", "authkey12"))

    assert "SNMPv3 user 'admin'" in msg
