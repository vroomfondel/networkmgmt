"""Tests for switchctrl vendor factory."""

import pytest

from networkmgmt.switchctrl.factory import (
    create_switch,
    list_vendors,
    register_vendor,
)
from networkmgmt.switchctrl.vendors.cisco.client import CiscoSwitch


class TestListVendors:
    """Test list_vendors function."""

    def test_list_vendors_returns_sorted_list(self):
        """list_vendors should return a sorted list of registered vendors."""
        vendors = list_vendors()
        assert isinstance(vendors, list)
        # The vendors should be sorted
        assert vendors == sorted(vendors)

    def test_list_vendors_contains_required_vendors(self):
        """list_vendors should contain at least cisco, mikrotik, qnap, netgear."""
        vendors = list_vendors()
        assert "cisco" in vendors
        assert "mikrotik" in vendors
        assert "qnap" in vendors
        assert "netgear" in vendors


class TestCreateSwitch:
    """Test create_switch factory function."""

    def test_create_cisco_switch(self):
        """create_switch should create CiscoSwitch for cisco vendor."""
        switch = create_switch(
            vendor="cisco",
            host="10.0.0.1",
            username="admin",
            password="x",
            enable_password="y",
        )
        assert isinstance(switch, CiscoSwitch)
        assert switch.host == "10.0.0.1"

    def test_create_switch_case_insensitive(self):
        """create_switch should be case-insensitive for vendor name."""
        switch = create_switch(
            vendor="CISCO",
            host="10.0.0.1",
            username="admin",
            password="x",
            enable_password="y",
        )
        assert isinstance(switch, CiscoSwitch)

    def test_create_switch_unknown_vendor(self):
        """create_switch should raise ValueError for unknown vendor."""
        with pytest.raises(ValueError) as exc_info:
            create_switch(vendor="unknown_vendor", host="10.0.0.1")

        assert "Unknown vendor" in str(exc_info.value)
        assert "unknown_vendor" in str(exc_info.value)

    def test_create_switch_unknown_vendor_shows_available(self):
        """create_switch error should show available vendors."""
        with pytest.raises(ValueError) as exc_info:
            create_switch(vendor="invalid", host="10.0.0.1")

        error_msg = str(exc_info.value)
        assert "Available:" in error_msg


class TestRegisterVendor:
    """Test register_vendor decorator."""

    def test_register_vendor_normalizes_to_lowercase(self):
        """register_vendor should normalize vendor names to lowercase."""
        from networkmgmt.switchctrl.base.client import BaseSwitchClient

        # Create a temporary test class
        @register_vendor("TestVendor")
        class TestSwitch(BaseSwitchClient):
            def __init__(self, host: str, **kwargs):
                super().__init__(host)

            def connect(self):
                pass

            def disconnect(self):
                pass

            @property
            def monitoring(self):
                raise NotImplementedError

            @property
            def vlan(self):
                raise NotImplementedError

            @property
            def port(self):
                raise NotImplementedError

            @property
            def lacp(self):
                raise NotImplementedError

        # Verify it's registered with lowercase
        vendors = list_vendors()
        assert "testvendor" in vendors

        # Verify we can create it with any case
        switch = create_switch(vendor="testvendor", host="10.0.0.1")
        assert isinstance(switch, TestSwitch)

        switch2 = create_switch(vendor="TESTVENDOR", host="10.0.0.1")
        assert isinstance(switch2, TestSwitch)

    def test_register_vendor_returns_class(self):
        """register_vendor decorator should return the class unchanged."""
        from networkmgmt.switchctrl.base.client import BaseSwitchClient

        @register_vendor("another_test")
        class AnotherTestSwitch(BaseSwitchClient):
            def __init__(self, host: str, **kwargs):
                super().__init__(host)

            def connect(self):
                pass

            def disconnect(self):
                pass

            @property
            def monitoring(self):
                raise NotImplementedError

            @property
            def vlan(self):
                raise NotImplementedError

            @property
            def port(self):
                raise NotImplementedError

            @property
            def lacp(self):
                raise NotImplementedError

        # The decorator should return the class
        assert AnotherTestSwitch.__name__ == "AnotherTestSwitch"
