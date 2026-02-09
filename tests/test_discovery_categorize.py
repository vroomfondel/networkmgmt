"""Tests for networkmgmt/discovery/categorize.py"""

import pytest

from networkmgmt.discovery.categorize import _categorize_host
from networkmgmt.discovery.models import DeviceCategory, DiscoveredHost


@pytest.fixture
def sample_discovered_host():
    """Fixture providing a sample DiscoveredHost."""
    return lambda **kwargs: DiscoveredHost(ip="192.168.1.10", **kwargs)


class TestCategorizeHost:
    """Tests for _categorize_host function."""

    def test_vendor_netgear_returns_infrastructure(self, sample_discovered_host):
        """Test NETGEAR vendor is categorized as infrastructure."""
        host = sample_discovered_host(vendor="NETGEAR")
        assert _categorize_host(host) == DeviceCategory.INFRASTRUCTURE

    def test_vendor_espressif_returns_iot(self, sample_discovered_host):
        """Test Espressif vendor is categorized as IoT."""
        host = sample_discovered_host(vendor="Espressif Inc.")
        assert _categorize_host(host) == DeviceCategory.IOT

    def test_vendor_grandstream_returns_phone(self, sample_discovered_host):
        """Test Grandstream vendor is categorized as phone."""
        host = sample_discovered_host(vendor="Grandstream Networks, Inc.")
        assert _categorize_host(host) == DeviceCategory.PHONE

    def test_hostname_switch_returns_infrastructure(self, sample_discovered_host):
        """Test hostname containing 'switch' is categorized as infrastructure."""
        host = sample_discovered_host(hostname="switch1")
        assert _categorize_host(host) == DeviceCategory.INFRASTRUCTURE

    def test_hostname_chromecast_returns_media(self, sample_discovered_host):
        """Test hostname containing 'chromecast' is categorized as media."""
        host = sample_discovered_host(hostname="chromecast-abc")
        assert _categorize_host(host) == DeviceCategory.MEDIA

    def test_case_insensitive_vendor_matching(self, sample_discovered_host):
        """Test case-insensitive vendor matching."""
        host = sample_discovered_host(vendor="netgear")
        assert _categorize_host(host) == DeviceCategory.INFRASTRUCTURE

    def test_case_insensitive_hostname_matching(self, sample_discovered_host):
        """Test case-insensitive hostname matching."""
        host = sample_discovered_host(hostname="SWITCH1")
        assert _categorize_host(host) == DeviceCategory.INFRASTRUCTURE

    def test_unknown_vendor_and_hostname_returns_other(self, sample_discovered_host):
        """Test unknown vendor and hostname returns OTHER."""
        host = sample_discovered_host(vendor="UnknownCorp", hostname="randomhost")
        assert _categorize_host(host) == DeviceCategory.OTHER

    def test_vendor_match_takes_priority_over_hostname(self, sample_discovered_host):
        """Test vendor match takes priority over hostname match."""
        # NETGEAR (INFRASTRUCTURE) should match before 'server' hostname pattern (SERVER)
        host = sample_discovered_host(vendor="NETGEAR", hostname="server1")
        assert _categorize_host(host) == DeviceCategory.INFRASTRUCTURE

    def test_empty_vendor_and_hostname_returns_other(self, sample_discovered_host):
        """Test empty vendor and hostname returns OTHER."""
        host = sample_discovered_host(vendor="", hostname="")
        assert _categorize_host(host) == DeviceCategory.OTHER

    def test_partial_vendor_match(self, sample_discovered_host):
        """Test partial vendor substring matching."""
        host = sample_discovered_host(vendor="Google, Inc.")
        assert _categorize_host(host) == DeviceCategory.MEDIA

    def test_partial_hostname_match(self, sample_discovered_host):
        """Test partial hostname substring matching."""
        host = sample_discovered_host(hostname="my-printer-office")
        assert _categorize_host(host) == DeviceCategory.COMPUTER
