"""Tests for Netgear switch client (stub implementation)."""

import pytest

from networkmgmt.switchctrl.factory import create_switch, list_vendors
from networkmgmt.switchctrl.vendors.netgear.client import NetgearSwitch


class TestNetgearRegistration:
    """Test Netgear vendor registration."""

    def test_netgear_in_vendor_list(self):
        """Netgear should be registered in vendor list."""
        vendors = list_vendors()
        assert "netgear" in vendors

    def test_create_netgear_switch(self):
        """create_switch should return NetgearSwitch for netgear vendor."""
        switch = create_switch(vendor="netgear", host="10.0.0.1")
        assert isinstance(switch, NetgearSwitch)
        assert switch.host == "10.0.0.1"


class TestNetgearSwitch:
    """Test NetgearSwitch client stub."""

    def test_monitoring_not_implemented(self):
        """monitoring property should raise NotImplementedError."""
        switch = NetgearSwitch(host="10.0.0.1")
        with pytest.raises(NotImplementedError) as exc_info:
            _ = switch.monitoring
        assert "not yet implemented" in str(exc_info.value).lower()

    def test_vlan_not_implemented(self):
        """vlan property should raise NotImplementedError."""
        switch = NetgearSwitch(host="10.0.0.1")
        with pytest.raises(NotImplementedError) as exc_info:
            _ = switch.vlan
        assert "not yet implemented" in str(exc_info.value).lower()

    def test_port_not_implemented(self):
        """port property should raise NotImplementedError."""
        switch = NetgearSwitch(host="10.0.0.1")
        with pytest.raises(NotImplementedError) as exc_info:
            _ = switch.port
        assert "not yet implemented" in str(exc_info.value).lower()

    def test_lacp_not_implemented(self):
        """lacp property should raise NotImplementedError."""
        switch = NetgearSwitch(host="10.0.0.1")
        with pytest.raises(NotImplementedError) as exc_info:
            _ = switch.lacp
        assert "not yet implemented" in str(exc_info.value).lower()

    def test_connect_not_implemented(self):
        """connect method should raise NotImplementedError."""
        switch = NetgearSwitch(host="10.0.0.1")
        with pytest.raises(NotImplementedError) as exc_info:
            switch.connect()
        assert "not yet implemented" in str(exc_info.value).lower()

    def test_disconnect_does_not_raise(self):
        """disconnect method should not raise (pass implementation)."""
        switch = NetgearSwitch(host="10.0.0.1")
        # Should not raise
        switch.disconnect()

    def test_netgear_switch_accepts_kwargs(self):
        """NetgearSwitch should accept arbitrary kwargs."""
        switch = NetgearSwitch(
            host="10.0.0.1",
            username="admin",
            password="secret",
            extra_param="value",
        )
        assert switch.host == "10.0.0.1"
