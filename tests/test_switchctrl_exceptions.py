"""Tests for switchctrl exception hierarchy."""

import pytest

from networkmgmt.switchctrl.exceptions import (
    APIError,
    AuthenticationError,
    LACPError,
    PortError,
    SSHError,
    SwitchError,
    VLANError,
)


class TestExceptionHierarchy:
    """Test exception inheritance and structure."""

    def test_switch_error_inherits_from_exception(self):
        """SwitchError should inherit from Exception."""
        assert issubclass(SwitchError, Exception)
        exc = SwitchError("test")
        assert isinstance(exc, Exception)
        assert str(exc) == "test"

    def test_authentication_error_inherits_from_switch_error(self):
        """AuthenticationError should inherit from SwitchError."""
        assert issubclass(AuthenticationError, SwitchError)
        assert issubclass(AuthenticationError, Exception)
        exc = AuthenticationError("auth failed")
        assert isinstance(exc, SwitchError)
        assert str(exc) == "auth failed"

    def test_api_error_inherits_from_switch_error(self):
        """APIError should inherit from SwitchError."""
        assert issubclass(APIError, SwitchError)
        assert issubclass(APIError, Exception)

    def test_ssh_error_inherits_from_switch_error(self):
        """SSHError should inherit from SwitchError."""
        assert issubclass(SSHError, SwitchError)
        assert issubclass(SSHError, Exception)
        exc = SSHError("ssh failed")
        assert isinstance(exc, SwitchError)
        assert str(exc) == "ssh failed"

    def test_vlan_error_inherits_from_switch_error(self):
        """VLANError should inherit from SwitchError."""
        assert issubclass(VLANError, SwitchError)
        assert issubclass(VLANError, Exception)
        exc = VLANError("vlan failed")
        assert isinstance(exc, SwitchError)
        assert str(exc) == "vlan failed"

    def test_port_error_inherits_from_switch_error(self):
        """PortError should inherit from SwitchError."""
        assert issubclass(PortError, SwitchError)
        assert issubclass(PortError, Exception)
        exc = PortError("port failed")
        assert isinstance(exc, SwitchError)
        assert str(exc) == "port failed"

    def test_lacp_error_inherits_from_switch_error(self):
        """LACPError should inherit from SwitchError."""
        assert issubclass(LACPError, SwitchError)
        assert issubclass(LACPError, Exception)
        exc = LACPError("lacp failed")
        assert isinstance(exc, SwitchError)
        assert str(exc) == "lacp failed"


class TestAPIError:
    """Test APIError specific functionality."""

    def test_api_error_with_status_code(self):
        """APIError should store status_code."""
        exc = APIError("API failed", status_code=404)
        assert exc.status_code == 404
        assert str(exc) == "API failed"

    def test_api_error_without_status_code(self):
        """APIError should allow status_code=None."""
        exc = APIError("API failed")
        assert exc.status_code is None
        assert str(exc) == "API failed"

    def test_api_error_with_explicit_none(self):
        """APIError should accept explicit None for status_code."""
        exc = APIError("API failed", status_code=None)
        assert exc.status_code is None
        assert str(exc) == "API failed"

    def test_api_error_preserves_message(self):
        """APIError should preserve the message string."""
        message = "Request failed with detailed error"
        exc = APIError(message, status_code=500)
        assert str(exc) == message
        assert exc.status_code == 500
