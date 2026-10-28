"""Tests for QNAP REST pacing, endpoint handling and REST VLAN management."""

from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest
import requests

from networkmgmt.switchctrl.exceptions import APIError, VLANError
from networkmgmt.switchctrl.models.vlan import TrunkConfig
from networkmgmt.switchctrl.vendors.common.cisco_managers import CiscoVLANManager
from networkmgmt.switchctrl.vendors.qnap.client import QNAPSwitch
from networkmgmt.switchctrl.vendors.qnap.rest import (
    DEFAULT_ENDPOINTS,
    QNAPBlockedEndpointError,
    QNAPMonitoringManager,
    QNAPNonJSONError,
    QNAPRESTTransport,
)
from networkmgmt.switchctrl.vendors.qnap.vlan_rest import PortMembership, QNAPRESTVLANManager, parse_table

REST = "networkmgmt.switchctrl.vendors.qnap.rest"


def _resp(body=None, content=b"x", json_error=False, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.content = content
    if json_error:
        resp.json.side_effect = ValueError("no json")
    else:
        resp.json.return_value = body
    return resp


def _vlan_payload(table):
    return [
        {"key": str(vid), "val": [{"Port": p, "Tagged": t} for p, t in members.items()]}
        for vid, members in table.items()
    ]


@pytest.fixture()
def session():
    sess = MagicMock()
    sess.post.return_value = _resp({"result": "tok"})
    return sess


@pytest.fixture()
def transport(session):
    with patch(f"{REST}.requests.Session", return_value=session):
        t = QNAPRESTTransport(host="10.0.0.1", password="pw", delay=0.3)
        t.connect()
    return t


@pytest.fixture()
def vlan_transport():
    """Transport mock whose GET /vlan returns the current table and applies writes to it."""
    state = {"table": {1: {}}}
    t = MagicMock()

    def get(endpoint, **kwargs):
        return {"result": _vlan_payload(state["table"])}

    def put(endpoint, data=None):
        if endpoint.endswith("/vlan"):
            for entry in data["data"]:
                state["table"][int(entry["key"])] = {m["Port"]: m["Tagged"] for m in entry["val"]}
        return {}

    def post(endpoint, data=None):
        for entry in data["data"]:
            state["table"].setdefault(int(entry["key"]), {})
        return {}

    def delete(endpoint, data=None):
        for vid in data["idx"]:
            state["table"].pop(int(vid), None)
        return {}

    t.get.side_effect = get
    t.put.side_effect = put
    t.post.side_effect = post
    t.delete.side_effect = delete
    t.state = state
    return t


class TestPacing:
    def test_requests_are_spaced_by_delay(self, transport, session):
        session.get.return_value = _resp({"result": {}})
        with patch(f"{REST}.time") as fake_time:
            fake_time.monotonic.side_effect = [100.1, 100.1, 100.3, 100.3]
            transport._last_request = 100.0
            transport.get("api/v1/a")
            transport.get("api/v1/b")

        sleeps = [c.args[0] for c in fake_time.sleep.call_args_list]
        assert sleeps == [pytest.approx(0.2), pytest.approx(0.1)]

    def test_no_sleep_when_delay_elapsed(self, transport, session):
        session.get.return_value = _resp({"result": {}})
        with patch(f"{REST}.time") as fake_time:
            fake_time.monotonic.side_effect = [10.0, 10.0]
            transport._last_request = 1.0
            transport.get("api/v1/a")

        fake_time.sleep.assert_not_called()

    def test_zero_delay_never_sleeps(self, session):
        session.get.return_value = _resp({"result": {}})
        with patch(f"{REST}.requests.Session", return_value=session), patch(f"{REST}.time") as fake_time:
            fake_time.monotonic.return_value = 5.0
            t = QNAPRESTTransport(host="h", password="pw", delay=0)
            t.connect()
            t.get("api/v1/a")
            t.get("api/v1/b")

        fake_time.sleep.assert_not_called()

    def test_no_retry_on_failure(self, transport, session):
        session.get.side_effect = requests.ConnectionError("down")

        with pytest.raises(APIError):
            transport.get("api/v1/a")

        assert session.get.call_count == 1

    def test_default_delay(self):
        assert QNAPRESTTransport(host="h", password="pw").delay == 0.3


class TestEndpointHandling:
    def test_lldp_not_in_defaults(self):
        assert not any("lldp" in e for e in DEFAULT_ENDPOINTS)

    def test_lldp_blocked(self, transport, session):
        with pytest.raises(QNAPBlockedEndpointError):
            transport.get("api/v1/lldp/neighbors/status")

        session.get.assert_not_called()

    def test_lldp_allowed_explicitly(self, transport, session):
        session.get.return_value = _resp({"result": []})

        assert transport.get("api/v1/lldp/neighbors/status", allow_blocked=True) == {"result": []}

    def test_non_json_raises_dedicated_error(self, transport, session):
        session.get.return_value = _resp(json_error=True)

        with pytest.raises(QNAPNonJSONError):
            transport.get("api/v1/ports/config")

    def test_error_code_raises(self, transport, session):
        session.get.return_value = _resp({"error_code": 500, "error_message": "boom"})

        with pytest.raises(APIError, match="boom"):
            transport.get("api/v1/vlan")

    def test_empty_body_is_empty_dict(self, transport, session):
        session.put.return_value = _resp(content=b"")

        assert transport.put("api/v1/system/save", {}) == {}

    def test_facts_collect_unavailable_instead_of_failing(self):
        t = MagicMock()

        def get(endpoint, allow_blocked=False):
            if endpoint.endswith("ports/config"):
                raise QNAPNonJSONError("no json")
            return {"result": {"ok": endpoint}}

        t.get.side_effect = get

        facts = QNAPMonitoringManager(t).get_facts(["system/board", "ports/config", "vlan"])

        assert facts["facts"] == {
            "system_board": {"ok": "api/v1/system/board"},
            "vlan": {"ok": "api/v1/vlan"},
        }
        assert list(facts["unavailable"]) == ["ports/config"]

    def test_facts_default_endpoints_without_lldp(self):
        t = MagicMock()
        t.get.return_value = {"result": {}}

        QNAPMonitoringManager(t).get_facts()

        queried = [c.args[0] for c in t.get.call_args_list]
        assert queried == [f"api/v1/{e}" for e in DEFAULT_ENDPOINTS]
        assert all(c.kwargs.get("allow_blocked") is False for c in t.get.call_args_list)

    def test_facts_lldp_only_when_requested(self):
        t = MagicMock()
        t.get.return_value = {"result": {}}

        QNAPMonitoringManager(t).get_facts(["vlan"], include_lldp=True)

        assert t.get.call_args_list[-1] == call("api/v1/lldp/neighbors/status", allow_blocked=True)

    def test_facts_lldp_in_list_without_flag_is_unavailable(self):
        t = MagicMock()
        t.get.side_effect = QNAPBlockedEndpointError("blocked")

        facts = QNAPMonitoringManager(t).get_facts(["lldp/neighbors/status"])

        assert facts["facts"] == {}
        assert "lldp/neighbors/status" in facts["unavailable"]


class TestVlanApply:
    def test_no_change_no_writes(self, vlan_transport):
        vlan_transport.state["table"] = {1: {"3": False}, 101: {"3": True}}
        manager = QNAPRESTVLANManager(vlan_transport)

        result = manager.apply_port_membership([1, 101], {"3": PortMembership(untagged=1, tagged=[101])})

        assert result.changed is False
        vlan_transport.put.assert_not_called()
        vlan_transport.post.assert_not_called()

    def test_add_and_remove_membership(self, vlan_transport):
        vlan_transport.state["table"] = {1: {"3": False}, 101: {}, 188: {"3": True, "4": True}}
        manager = QNAPRESTVLANManager(vlan_transport)

        result = manager.apply_port_membership([1, 101, 188], {"3": PortMembership(untagged=1, tagged=[101])})

        assert result.changed is True
        assert result.edited == [101, 188]
        assert vlan_transport.state["table"] == {1: {"3": False}, 101: {"3": True}, 188: {"4": True}}
        assert "VLAN  101" in result.diff_after

    def test_untagged_unique_per_port(self, vlan_transport):
        vlan_transport.state["table"] = {1: {"5": False}, 101: {}}
        manager = QNAPRESTVLANManager(vlan_transport)

        manager.apply_port_membership([1, 101], {"5": PortMembership(untagged=101)})

        assert vlan_transport.state["table"] == {1: {}, 101: {"5": False}}

    def test_declared_port_untagged_and_tagged_same_vlan_rejected(self, vlan_transport):
        manager = QNAPRESTVLANManager(vlan_transport)

        with pytest.raises(VLANError, match="both tagged and untagged"):
            manager.apply_port_membership([1], {"3": PortMembership(untagged=1, tagged=[1])})

    def test_unknown_vlan_rejected(self, vlan_transport):
        manager = QNAPRESTVLANManager(vlan_transport)

        with pytest.raises(VLANError, match="not in the VLAN list"):
            manager.apply_port_membership([1], {"3": PortMembership(tagged=[999])})

    def test_undeclared_ports_untouched(self, vlan_transport):
        vlan_transport.state["table"] = {1: {"lag1": False, "3": False}, 101: {"lag1": True}}
        manager = QNAPRESTVLANManager(vlan_transport)

        manager.apply_port_membership([1, 101], {"3": PortMembership(tagged=[101])})

        assert vlan_transport.state["table"] == {1: {"lag1": False}, 101: {"lag1": True, "3": True}}

    def test_create_new_vlan_posts_then_puts(self, vlan_transport):
        manager = QNAPRESTVLANManager(vlan_transport)

        result = manager.apply_port_membership([1, 150], {"2": PortMembership(tagged=[150])})

        assert result.created == [150]
        vlan_transport.post.assert_called_once_with("api/v1/vlan", {"data": [{"key": "150", "val": []}]})
        vlan_transport.put.assert_any_call(
            "api/v1/vlan", {"data": [{"key": "150", "val": [{"Port": "2", "Tagged": True}]}]}
        )

    def test_one_put_per_changed_vlan(self, vlan_transport):
        vlan_transport.state["table"] = {1: {}, 101: {}, 102: {}, 103: {}}
        manager = QNAPRESTVLANManager(vlan_transport)

        manager.apply_port_membership([1, 101, 102, 103], {"2": PortMembership(tagged=[101, 102])}, save=False)

        assert vlan_transport.put.call_count == 2

    def test_purge_deletes_but_never_vlan_1(self, vlan_transport):
        vlan_transport.state["table"] = {1: {}, 101: {}, 999: {}}
        manager = QNAPRESTVLANManager(vlan_transport)

        result = manager.apply_port_membership([101], {}, purge_vlans=True)

        assert result.deleted == [999]
        vlan_transport.delete.assert_called_once_with("api/v1/vlan", {"idx": ["999"]})
        assert 1 in vlan_transport.state["table"]

    def test_without_purge_foreign_vlans_reported_unmanaged(self, vlan_transport):
        vlan_transport.state["table"] = {1: {}, 999: {}}
        manager = QNAPRESTVLANManager(vlan_transport)

        result = manager.apply_port_membership([1], {})

        assert result.unmanaged_vlans == [999]
        assert result.changed is False

    def test_protected_port_mgmt_vlan_change_refused(self, vlan_transport):
        vlan_transport.state["table"] = {1: {"11": False}, 101: {}}
        manager = QNAPRESTVLANManager(vlan_transport)

        with pytest.raises(VLANError, match="protected port 11"):
            manager.apply_port_membership([1, 101], {"11": PortMembership(untagged=101)}, protect_ports=["11"])

        vlan_transport.put.assert_not_called()

    def test_protected_port_other_vlan_change_allowed(self, vlan_transport):
        vlan_transport.state["table"] = {1: {"11": False}, 101: {}}
        manager = QNAPRESTVLANManager(vlan_transport)

        manager.apply_port_membership([1, 101], {"11": PortMembership(untagged=1, tagged=[101])}, protect_ports=["11"])

        assert vlan_transport.state["table"][101] == {"11": True}

    def test_check_mode_writes_nothing(self, vlan_transport):
        manager = QNAPRESTVLANManager(vlan_transport)

        result = manager.apply_port_membership([1, 101], {"3": PortMembership(tagged=[101])}, check_mode=True)

        assert result.changed is True
        assert result.saved is False
        vlan_transport.put.assert_not_called()
        vlan_transport.post.assert_not_called()

    def test_save_after_change(self, vlan_transport):
        manager = QNAPRESTVLANManager(vlan_transport)

        result = manager.apply_port_membership([1, 101], {"3": PortMembership(tagged=[101])})

        assert result.saved is True
        assert vlan_transport.put.call_args_list[-1] == call("api/v1/system/save", {})

    def test_no_save_without_change(self, vlan_transport):
        manager = QNAPRESTVLANManager(vlan_transport)

        result = manager.apply_port_membership([1], {})

        assert result.saved is False
        vlan_transport.put.assert_not_called()

    def test_save_can_be_disabled(self, vlan_transport):
        manager = QNAPRESTVLANManager(vlan_transport)

        manager.apply_port_membership([1, 101], {"3": PortMembership(tagged=[101])}, save=False)

        assert all(c.args[0] != "api/v1/system/save" for c in vlan_transport.put.call_args_list)

    def test_readback_mismatch_is_error_and_not_saved(self, vlan_transport):
        def ignore_put(endpoint, data=None):
            return {}

        vlan_transport.put.side_effect = ignore_put
        vlan_transport.state["table"] = {1: {}, 101: {}}
        manager = QNAPRESTVLANManager(vlan_transport)

        with pytest.raises(VLANError, match="did not apply"):
            manager.apply_port_membership([1, 101], {"3": PortMembership(tagged=[101])})

        assert all(c.args[0] != "api/v1/system/save" for c in vlan_transport.put.call_args_list)

    def test_save_failure_is_vlan_error(self, vlan_transport):
        original = vlan_transport.put.side_effect

        def put(endpoint, data=None):
            if endpoint.endswith("system/save"):
                raise APIError("save failed")
            return original(endpoint, data)

        vlan_transport.put.side_effect = put
        manager = QNAPRESTVLANManager(vlan_transport)

        with pytest.raises(VLANError, match="system/save"):
            manager.apply_port_membership([1, 101], {"3": PortMembership(tagged=[101])})

    def test_parse_table_rejects_non_list(self):
        with pytest.raises(VLANError):
            parse_table({"a": 1})


class TestVlanManagerInterface:
    def test_list_vlans(self, vlan_transport):
        vlan_transport.state["table"] = {1: {"2": False, "10": True, "3": True}, 101: {}}

        vlans = QNAPRESTVLANManager(vlan_transport).list_vlans()

        assert [v.vlan_id for v in vlans] == [1, 101]
        assert vlans[0].tagged_ports == ["3", "10"]
        assert vlans[0].untagged_ports == ["2"]

    def test_assign_untagged_replaces_old_untagged(self, vlan_transport):
        vlan_transport.state["table"] = {1: {"4": False}, 101: {}, 188: {"4": True}}

        QNAPRESTVLANManager(vlan_transport).assign_port_to_vlan("4", 101)

        assert vlan_transport.state["table"] == {1: {}, 101: {"4": False}, 188: {"4": True}}

    def test_assign_tagged_keeps_untagged(self, vlan_transport):
        vlan_transport.state["table"] = {1: {"4": False}, 101: {}}

        QNAPRESTVLANManager(vlan_transport).assign_port_to_vlan("4", 101, tagged=True)

        assert vlan_transport.state["table"] == {1: {"4": False}, 101: {"4": True}}

    def test_configure_trunk(self, vlan_transport):
        vlan_transport.state["table"] = {1: {"4": False}, 101: {}, 102: {}}

        QNAPRESTVLANManager(vlan_transport).configure_trunk(
            TrunkConfig(port="4", native_vlan=1, allowed_vlans=[101, 102])
        )

        assert vlan_transport.state["table"] == {1: {"4": False}, 101: {"4": True}, 102: {"4": True}}

    def test_create_existing_vlan_is_noop(self, vlan_transport):
        QNAPRESTVLANManager(vlan_transport).create_vlan(1)

        vlan_transport.post.assert_not_called()
        vlan_transport.put.assert_not_called()

    def test_create_vlan(self, vlan_transport):
        QNAPRESTVLANManager(vlan_transport).create_vlan(200, "servers")

        assert 200 in vlan_transport.state["table"]

    def test_delete_vlan_1_refused(self, vlan_transport):
        with pytest.raises(VLANError):
            QNAPRESTVLANManager(vlan_transport).delete_vlan(1)

    def test_delete_vlan(self, vlan_transport):
        vlan_transport.state["table"] = {1: {}, 200: {}}

        QNAPRESTVLANManager(vlan_transport).delete_vlan(200)

        assert vlan_transport.state["table"] == {1: {}}


class TestClientVlanBackend:
    @patch("networkmgmt.switchctrl.vendors.qnap.client.CiscoCLITransport")
    @patch("networkmgmt.switchctrl.vendors.qnap.client.QNAPRESTTransport")
    def test_default_is_rest(self, rest_cls, ssh_cls):
        rest_cls.return_value.is_connected.return_value = True

        client = QNAPSwitch(host="h", password="pw")

        assert isinstance(client.vlan, QNAPRESTVLANManager)
        assert client.rest_vlan is client.vlan
        ssh_cls.return_value.connect.assert_not_called()

    @pytest.mark.parametrize("kwargs", [{"ssh_password": "x"}, {"ssh_username": "debug"}, {"enable_password": "e"}])
    @patch("networkmgmt.switchctrl.vendors.qnap.client.CiscoCLITransport")
    @patch("networkmgmt.switchctrl.vendors.qnap.client.QNAPRESTTransport")
    def test_explicit_ssh_params_select_ssh(self, rest_cls, ssh_cls, kwargs):
        ssh_cls.return_value.is_connected.return_value = True

        client = QNAPSwitch(host="h", password="pw", **kwargs)

        assert isinstance(client.vlan, CiscoVLANManager)

    @patch("networkmgmt.switchctrl.vendors.qnap.client.CiscoCLITransport")
    @patch("networkmgmt.switchctrl.vendors.qnap.client.QNAPRESTTransport")
    def test_explicit_backend_wins(self, rest_cls, ssh_cls):
        ssh_cls.return_value.is_connected.return_value = True

        client = QNAPSwitch(host="h", password="pw", ssh_password="x", vlan_backend="rest")
        rest_cls.return_value.is_connected.return_value = True

        assert isinstance(client.vlan, QNAPRESTVLANManager)

    @patch("networkmgmt.switchctrl.vendors.qnap.client.CiscoCLITransport")
    @patch("networkmgmt.switchctrl.vendors.qnap.client.QNAPRESTTransport")
    def test_ssh_defaults_and_rest_delay_forwarded(self, rest_cls, ssh_cls):
        QNAPSwitch(host="h", password="pw", rest_delay=1.5)

        assert rest_cls.call_args.kwargs["delay"] == 1.5
        assert ssh_cls.call_args.kwargs["username"] == "guest"
        assert ssh_cls.call_args.kwargs["password"] == "guest123"
