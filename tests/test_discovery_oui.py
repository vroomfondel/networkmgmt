"""Tests for networkmgmt/discovery/oui.py"""

import subprocess
from pathlib import Path
from unittest.mock import Mock, mock_open, patch

import pytest

from networkmgmt.discovery.oui import _abbreviate_vendor, load_oui_db, lookup_vendor


class TestAbbreviateVendor:
    """Tests for _abbreviate_vendor function."""

    def test_abbreviates_known_vendor(self):
        """Test abbreviation of known vendor."""
        assert _abbreviate_vendor("NETGEAR") == "Netgear"

    def test_returns_unknown_vendor_as_is(self):
        """Test unknown vendor is returned unchanged."""
        assert _abbreviate_vendor("Unknown Vendor Corp") == "Unknown Vendor Corp"


class TestLookupVendor:
    """Tests for lookup_vendor function."""

    def test_lookup_vendor_with_matching_prefix(self):
        """Test lookup with matching OUI prefix."""
        oui_db = {"AA:BB:CC": "TestVendor"}
        result = lookup_vendor("aa:bb:cc:dd:ee:ff", oui_db)
        assert result == "TestVendor"

    def test_lookup_vendor_case_insensitive(self):
        """Test lookup is case-insensitive."""
        oui_db = {"AA:BB:CC": "TestVendor"}
        result = lookup_vendor("AA:BB:CC:DD:EE:FF", oui_db)
        assert result == "TestVendor"

    def test_lookup_vendor_missing_prefix_returns_empty(self):
        """Test missing prefix returns empty string."""
        oui_db = {"AA:BB:CC": "TestVendor"}
        result = lookup_vendor("11:22:33:44:55:66", oui_db)
        assert result == ""


class TestLoadOuiDb:
    """Tests for load_oui_db function."""

    @patch("networkmgmt.discovery.oui.Path.exists")
    @patch("builtins.open", new_callable=mock_open)
    def test_cache_exists_reads_and_parses_file(self, mock_file, mock_exists):
        """Test loading OUI database from cache file."""
        mock_exists.return_value = True
        mock_file.return_value.read_text = Mock(
            return_value=(
                "AA-BB-CC   (hex)\t\tTestVendor\n" "11-22-33   (hex)\t\tAnotherVendor\n" "Random line without hex\n"
            )
        )

        # Mock the file reading in the context manager
        mock_file.return_value.__enter__.return_value = [
            "AA-BB-CC   (hex)\t\tTestVendor\n",
            "11-22-33   (hex)\t\tAnotherVendor\n",
            "Random line without hex\n",
        ]

        result = load_oui_db()

        assert result == {
            "AA:BB:CC": "TestVendor",
            "11:22:33": "AnotherVendor",
        }

    @patch("networkmgmt.discovery.oui.Path.exists")
    @patch("networkmgmt.discovery.oui.subprocess.run")
    @patch("builtins.open", new_callable=mock_open)
    def test_cache_missing_downloads_and_parses(self, mock_file, mock_run, mock_exists):
        """Test downloading and parsing OUI database when cache missing."""
        # First call to exists() returns False (cache doesn't exist)
        # Subsequent calls during parsing would return True after download
        mock_exists.return_value = False

        mock_result = Mock()
        mock_result.returncode = 0
        mock_run.return_value = mock_result

        # Mock file reading after download
        mock_file.return_value.__enter__.return_value = [
            "AA-BB-CC   (hex)\t\tTestVendor\n",
        ]

        result = load_oui_db()

        # Verify curl was called
        mock_run.assert_called_once()
        call_args = mock_run.call_args[0][0]
        assert call_args[0] == "curl"
        assert "-sL" in call_args
        assert "https://standards-oui.ieee.org/oui/oui.txt" in call_args

        # Verify parsing happened
        assert result == {"AA:BB:CC": "TestVendor"}

    @patch("networkmgmt.discovery.oui.Path.exists")
    @patch("networkmgmt.discovery.oui.subprocess.run")
    def test_curl_failure_returns_empty_dict(self, mock_run, mock_exists):
        """Test curl failure returns empty dictionary."""
        mock_exists.return_value = False

        mock_result = Mock()
        mock_result.returncode = 1
        mock_result.stderr = "curl error"
        mock_run.return_value = mock_result

        result = load_oui_db()

        assert result == {}

    @patch("networkmgmt.discovery.oui.Path.exists")
    @patch("networkmgmt.discovery.oui.subprocess.run")
    def test_timeout_returns_empty_dict(self, mock_run, mock_exists):
        """Test timeout during download returns empty dictionary."""
        mock_exists.return_value = False
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="curl", timeout=30)

        result = load_oui_db()

        assert result == {}

    @patch("networkmgmt.discovery.oui.Path.exists")
    @patch("networkmgmt.discovery.oui.subprocess.run")
    def test_file_not_found_returns_empty_dict(self, mock_run, mock_exists):
        """Test FileNotFoundError returns empty dictionary."""
        mock_exists.return_value = False
        mock_run.side_effect = FileNotFoundError()

        result = load_oui_db()

        assert result == {}
