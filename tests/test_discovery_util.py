"""Tests for networkmgmt/discovery/_util.py"""

import subprocess
from unittest.mock import Mock, patch

import pytest

from networkmgmt.discovery._util import (
    _run_cmd,
    _strip_hostname_suffix,
    _validate_interface_name,
    _validate_ip,
)


class TestStripHostnameSuffix:
    """Tests for _strip_hostname_suffix function."""

    def test_strips_fritz_box_suffix(self):
        """Test stripping .fritz.box suffix."""
        assert _strip_hostname_suffix("router.fritz.box") == "router"

    def test_strips_local_suffix(self):
        """Test stripping .local suffix."""
        assert _strip_hostname_suffix("host.local") == "host"

    def test_strips_lan_suffix(self):
        """Test stripping .lan suffix."""
        assert _strip_hostname_suffix("host.lan") == "host"

    def test_no_suffix_match_returns_original(self):
        """Test hostname without suffix is returned as-is."""
        assert _strip_hostname_suffix("hostname") == "hostname"

    def test_case_insensitive_matching_preserves_original_case(self):
        """Test case-insensitive suffix matching preserves original case."""
        assert _strip_hostname_suffix("ROUTER.FRITZ.BOX") == "ROUTER"


class TestValidateInterfaceName:
    """Tests for _validate_interface_name function."""

    def test_valid_interface_names(self):
        """Test valid interface names."""
        assert _validate_interface_name("eth0") is True
        assert _validate_interface_name("wlan0") is True
        assert _validate_interface_name("br0.1") is True
        assert _validate_interface_name("eno1") is True
        assert _validate_interface_name("enp0s3") is True

    def test_invalid_interface_names(self):
        """Test invalid interface names."""
        assert _validate_interface_name("; rm -rf /") is False
        assert _validate_interface_name("../etc") is False
        assert _validate_interface_name("") is False
        assert _validate_interface_name("eth 0") is False


class TestValidateIp:
    """Tests for _validate_ip function."""

    def test_valid_ipv4_addresses(self):
        """Test valid IPv4 addresses."""
        assert _validate_ip("192.168.1.1") is True
        assert _validate_ip("10.0.0.1") is True

    def test_valid_ipv6_addresses(self):
        """Test valid IPv6 addresses."""
        assert _validate_ip("::1") is True

    def test_invalid_ip_addresses(self):
        """Test invalid IP addresses."""
        assert _validate_ip("300.0.0.1") is False
        assert _validate_ip("not-an-ip") is False
        assert _validate_ip("") is False


class TestRunCmd:
    """Tests for _run_cmd function."""

    @patch("networkmgmt.discovery._util.subprocess.run")
    def test_successful_command_returns_stdout(self, mock_run):
        """Test successful command returns stdout."""
        mock_result = Mock()
        mock_result.stdout = "command output"
        mock_run.return_value = mock_result

        result = _run_cmd(["echo", "test"], timeout=30)

        assert result == "command output"
        mock_run.assert_called_once_with(["echo", "test"], capture_output=True, text=True, timeout=30)

    @patch("networkmgmt.discovery._util.subprocess.run")
    def test_timeout_expired_returns_empty_string(self, mock_run):
        """Test TimeoutExpired exception returns empty string."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="test", timeout=30)

        result = _run_cmd(["sleep", "100"], timeout=1)

        assert result == ""

    @patch("networkmgmt.discovery._util.subprocess.run")
    def test_file_not_found_returns_empty_string(self, mock_run):
        """Test FileNotFoundError returns empty string."""
        mock_run.side_effect = FileNotFoundError()

        result = _run_cmd(["nonexistent_command"], timeout=30)

        assert result == ""
