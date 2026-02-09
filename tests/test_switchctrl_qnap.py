"""Tests for QNAP QSW switch management."""

from __future__ import annotations

import hashlib
from unittest.mock import MagicMock, patch

import pytest
import requests

from networkmgmt.switchctrl.exceptions import AuthenticationError
from networkmgmt.switchctrl.models.port import PortStatus
from networkmgmt.switchctrl.models.system import SensorData, SystemInfo
from networkmgmt.switchctrl.vendors.qnap.client import QNAPSwitch
from networkmgmt.switchctrl.vendors.qnap.rest import QNAPMonitoringManager, QNAPRESTTransport
from networkmgmt.switchctrl.vendors.qnap.utils import generate_enable_password

# ── generate_enable_password utility ───────────────────────────────────


class TestGenerateEnablePassword:
    """Test generate_enable_password function."""

    def test_known_serial(self):
        """Generate password from known serial returns deterministic hash."""
        serial = "TEST123"
        expected = hashlib.sha512(serial.encode()).hexdigest()[-8:]

        result = generate_enable_password(serial)

        assert result == expected
        assert len(result) == 8
        # Verify it's hex
        assert all(c in "0123456789abcdef" for c in result)

    def test_same_input_same_output(self):
        """Same serial number produces same password."""
        serial = "ABC123XYZ"
        result1 = generate_enable_password(serial)
        result2 = generate_enable_password(serial)

        assert result1 == result2

    def test_always_8_characters(self):
        """Generated password is always 8 characters."""
        test_serials = ["SHORT", "VERYLONGSERIAL123456789", "X", "1234567890"]

        for serial in test_serials:
            result = generate_enable_password(serial)
            assert len(result) == 8


# ── QNAPRESTTransport ──────────────────────────────────────────────────


class TestQNAPRESTTransport:
    """Test QNAPRESTTransport with mocked requests.Session."""

    @patch("networkmgmt.switchctrl.vendors.qnap.rest.requests.Session")
    def test_connect(self, mock_session_class):
        """connect() base64-encodes password, POSTs login, sets Bearer token."""
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"result": "token123"}
        mock_session.post.return_value = mock_response
        mock_session_class.return_value = mock_session

        transport = QNAPRESTTransport(host="10.0.0.1", password="admin123")
        transport.connect()

        # Verify session was created
        mock_session_class.assert_called_once()

        # Verify SSL verification was set
        assert mock_session.verify is False

        # Verify login POST was called with encoded password
        mock_session.post.assert_called_once()
        call_args = mock_session.post.call_args
        assert "api/v1/users/login" in call_args[0][0]

        posted_data = call_args[1]["json"]
        assert posted_data["username"] == "admin"
        # Password should be base64-encoded
        import base64

        expected_pw = base64.b64encode(b"admin123").decode()
        assert posted_data["password"] == expected_pw

        # Verify Authorization header was set via __setitem__
        mock_session.headers.__setitem__.assert_called_with("Authorization", "Bearer token123")
        assert transport._token == "token123"

    @patch("networkmgmt.switchctrl.vendors.qnap.rest.requests.Session")
    def test_disconnect(self, mock_session_class):
        """disconnect() POSTs to logout endpoint."""
        mock_session = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"result": "token123"}
        mock_session.post.return_value = mock_response
        mock_session_class.return_value = mock_session

        transport = QNAPRESTTransport(host="10.0.0.1", password="admin123")
        transport.connect()
        transport.disconnect()

        # Verify logout was called
        calls = [call[0][0] for call in mock_session.post.call_args_list]
        assert any("api/v1/users/exit" in call for call in calls)

        # Verify session was closed
        mock_session.close.assert_called_once()
        assert transport._token is None
        assert transport._session is None

    @patch("networkmgmt.switchctrl.vendors.qnap.rest.requests.Session")
    def test_get(self, mock_session_class):
        """get() sends GET request and returns JSON response."""
        mock_session = MagicMock()
        mock_login_response = MagicMock()
        mock_login_response.json.return_value = {"result": "token123"}
        mock_get_response = MagicMock()
        mock_get_response.json.return_value = {"result": {"key": "value"}}

        mock_session.post.return_value = mock_login_response
        mock_session.get.return_value = mock_get_response
        mock_session_class.return_value = mock_session

        transport = QNAPRESTTransport(host="10.0.0.1", password="admin123")
        transport.connect()
        result = transport.get("api/v1/test")

        mock_session.get.assert_called_once()
        assert "https://10.0.0.1:443/api/v1/test" in mock_session.get.call_args[0][0]
        assert result == {"result": {"key": "value"}}

    @patch("networkmgmt.switchctrl.vendors.qnap.rest.requests.Session")
    def test_post(self, mock_session_class):
        """post() sends POST request with data and returns JSON response."""
        mock_session = MagicMock()
        mock_login_response = MagicMock()
        mock_login_response.json.return_value = {"result": "token123"}
        mock_post_response = MagicMock()
        mock_post_response.json.return_value = {"result": "success"}

        # First POST is login, second is our test POST
        mock_session.post.side_effect = [mock_login_response, mock_post_response]
        mock_session_class.return_value = mock_session

        transport = QNAPRESTTransport(host="10.0.0.1", password="admin123")
        transport.connect()
        result = transport.post("api/v1/test", {"key": "value"})

        assert mock_session.post.call_count == 2
        # Check the second POST call (first is login)
        second_call = mock_session.post.call_args_list[1]
        assert "https://10.0.0.1:443/api/v1/test" in second_call[0][0]
        assert second_call[1]["json"] == {"key": "value"}
        assert result == {"result": "success"}

    @patch("networkmgmt.switchctrl.vendors.qnap.rest.requests.Session")
    def test_auth_failure(self, mock_session_class):
        """Auth failure raises AuthenticationError."""
        mock_session = MagicMock()
        mock_session.post.side_effect = requests.RequestException("Connection failed")
        mock_session_class.return_value = mock_session

        transport = QNAPRESTTransport(host="10.0.0.1", password="admin123")

        with pytest.raises(AuthenticationError, match="REST login failed"):
            transport.connect()

        # connect() does not call close() on auth failure — session remains set
        assert transport._session is mock_session
        assert transport._token is None


# ── QNAPMonitoringManager ──────────────────────────────────────────────


class TestQNAPMonitoringManager:
    """Test QNAPMonitoringManager with mocked transport."""

    def test_get_port_status(self, mock_qnap_rest):
        """get_port_status returns list[PortStatus] from REST API."""
        mock_qnap_rest.get.return_value = {
            "result": {
                "port1": {
                    "linkStatus": 1,
                    "speed": "1000",
                    "duplex": "full",
                    "mediaType": "copper",
                    "maxSpeed": "1000",
                },
                "port2": {
                    "linkStatus": 0,
                    "speed": "0",
                    "duplex": "unknown",
                    "mediaType": "copper",
                    "maxSpeed": "1000",
                },
            }
        }

        manager = QNAPMonitoringManager(mock_qnap_rest)
        ports = manager.get_port_status()

        assert len(ports) == 2
        assert isinstance(ports[0], PortStatus)
        assert ports[0].port == "port1"
        assert ports[0].link_up is True
        assert ports[0].speed == "1000"
        assert ports[0].duplex == "full"
        assert ports[0].media_type == "copper"
        assert ports[0].max_speed == "1000"

        assert ports[1].port == "port2"
        assert ports[1].link_up is False

        mock_qnap_rest.get.assert_called_once_with("api/v1/ports/status")

    def test_get_system_info(self, mock_qnap_rest):
        """get_system_info returns SystemInfo from REST API."""
        mock_qnap_rest.get.return_value = {
            "result": {
                "hostname": "qsw",
                "macAddr": "AA:BB:CC:DD:EE:FF",
                "serialNum": "SER123456",
                "fwVer": "1.0.0",
                "fwDate": "2024-01-15",
                "modelName": "QSW-M408",
                "uptime": 86400,
            }
        }

        manager = QNAPMonitoringManager(mock_qnap_rest)
        info = manager.get_system_info()

        assert isinstance(info, SystemInfo)
        assert info.hostname == "qsw"
        assert info.mac_address == "AA:BB:CC:DD:EE:FF"
        assert info.serial_number == "SER123456"
        assert info.firmware_version == "1.0.0"
        assert info.firmware_date == "2024-01-15"
        assert info.model == "QSW-M408"
        assert info.uptime == 86400

        mock_qnap_rest.get.assert_called_once_with("api/v1/system/board")

    def test_get_sensor_data(self, mock_qnap_rest):
        """get_sensor_data returns SensorData from REST API."""
        mock_qnap_rest.get.return_value = {
            "result": {
                "tempVal": 45.5,
                "tempMax": 70.0,
                "fanSpeed": 3000,
            }
        }

        manager = QNAPMonitoringManager(mock_qnap_rest)
        sensor = manager.get_sensor_data()

        assert isinstance(sensor, SensorData)
        assert sensor.temperature == 45.5
        assert sensor.max_temperature == 70.0
        assert sensor.fan_speed == 3000

        mock_qnap_rest.get.assert_called_once_with("api/v1/system/sensor")


# ── QNAPSwitch client ──────────────────────────────────────────────────


class TestQNAPSwitch:
    """Test QNAPSwitch client."""

    @patch("networkmgmt.switchctrl.vendors.qnap.client.CiscoCLITransport")
    @patch("networkmgmt.switchctrl.vendors.qnap.client.QNAPRESTTransport")
    def test_enable_auto_generates_password(self, mock_rest_class, mock_ssh_class):
        """enable() auto-generates password from serial when not provided."""
        mock_rest = MagicMock()
        mock_ssh = MagicMock()
        mock_ssh.is_connected.return_value = True
        mock_rest.is_connected.return_value = False
        mock_rest_class.return_value = mock_rest
        mock_ssh_class.return_value = mock_ssh

        # Mock get_system_info to return a SystemInfo with serial
        mock_monitoring = MagicMock()
        test_serial = "TEST123SERIAL"
        mock_monitoring.get_system_info.return_value = SystemInfo(serial_number=test_serial)

        client = QNAPSwitch(host="192.168.1.1", password="admin123")
        # Inject the mocked monitoring manager
        client._monitoring = mock_monitoring

        # Call enable without password
        client.enable()

        # Verify enter_enable_mode was called with generated password
        expected_password = generate_enable_password(test_serial)
        mock_ssh.enter_enable_mode.assert_called_once_with(expected_password)

    @patch("networkmgmt.switchctrl.vendors.qnap.client.CiscoCLITransport")
    @patch("networkmgmt.switchctrl.vendors.qnap.client.QNAPRESTTransport")
    def test_enable_with_provided_password(self, mock_rest_class, mock_ssh_class):
        """enable(password) uses provided password instead of generating."""
        mock_rest = MagicMock()
        mock_ssh = MagicMock()
        mock_ssh.is_connected.return_value = True
        mock_rest_class.return_value = mock_rest
        mock_ssh_class.return_value = mock_ssh

        client = QNAPSwitch(host="192.168.1.1", password="admin123")

        # Call enable with explicit password
        client.enable(password="mypassword123")

        # Verify enter_enable_mode was called with provided password
        mock_ssh.enter_enable_mode.assert_called_once_with("mypassword123")

    @patch("networkmgmt.switchctrl.vendors.qnap.client.CiscoCLITransport")
    @patch("networkmgmt.switchctrl.vendors.qnap.client.QNAPRESTTransport")
    def test_enable_with_constructor_password(self, mock_rest_class, mock_ssh_class):
        """enable() uses enable_password from constructor if set."""
        mock_rest = MagicMock()
        mock_ssh = MagicMock()
        mock_ssh.is_connected.return_value = True
        mock_rest_class.return_value = mock_rest
        mock_ssh_class.return_value = mock_ssh

        client = QNAPSwitch(
            host="192.168.1.1",
            password="admin123",
            enable_password="constructor_pw",
        )

        # Call enable without explicit password
        client.enable()

        # Verify enter_enable_mode was called with constructor password
        mock_ssh.enter_enable_mode.assert_called_once_with("constructor_pw")
