"""Tests for the switchctrl CLI option handling and monitor degradation."""

from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from networkmgmt.switchctrl import cli
from networkmgmt.switchctrl.models import LACPInfo, PortStatistics, PortStatus, SystemInfo


def parse(*argv: str):
    return cli.build_parser().parse_args(list(argv))


def make_switch(sensors_error: bool = False, lacp_error: bool = False) -> MagicMock:
    switch = MagicMock()
    mon = switch.monitoring
    mon.get_system_info.return_value = SystemInfo(
        model="M", hostname="h", mac_address="aa", serial_number="1", firmware_version="1", firmware_date="d", uptime=5
    )
    if sensors_error:
        mon.get_sensor_data.side_effect = NotImplementedError("no sensors")
    if lacp_error:
        mon.get_lacp_info.side_effect = NotImplementedError("no lacp")
    else:
        mon.get_lacp_info.return_value = [LACPInfo(port_channel_id=1, member_ports=["1", "2"], status="up")]
    mon.get_port_status.return_value = [PortStatus(port="1", link_up=True, speed="1000", duplex="full")]
    mon.get_port_statistics.return_value = [PortStatistics(port="1", link_up=True, tx_bytes=1, rx_bytes=2)]
    return switch


@contextmanager
def fake_factory(switch: MagicMock):
    switch.__enter__.return_value = switch
    with patch.object(cli, "create_switch", return_value=switch) as factory:
        yield factory


class TestBuildKwargs:
    def test_netgear_v3_options(self):
        parsed = parse(
            "--vendor", "netgear", "--host", "h", "--snmp-user", "u", "--auth-key", "a", "--auth-protocol", "md5",
            "--priv-key", "p", "--priv-protocol", "des", "--web-password", "w", "monitor",
        )  # fmt: skip
        assert cli.build_kwargs(parsed) == {
            "snmp_user": "u",
            "auth_key": "a",
            "auth_protocol": "md5",
            "priv_key": "p",
            "priv_protocol": "des",
            "web_password": "w",
        }

    def test_netgear_community_without_password(self):
        parsed = parse("--vendor", "netgear", "--host", "h", "--community", "public", "monitor")
        assert cli.build_kwargs(parsed) == {"community": "public"}

    def test_netgear_does_not_inject_default_username(self):
        parsed = parse("--vendor", "netgear", "--host", "h", "--community", "c", "--password", "pw", "monitor")
        assert "username" not in cli.build_kwargs(parsed)

    def test_netgear_without_credentials(self):
        parsed = parse("--vendor", "netgear", "--host", "h", "monitor")
        with pytest.raises(ValueError, match="--community"):
            cli.build_kwargs(parsed)

    def test_netgear_v3_without_key(self):
        parsed = parse("--vendor", "netgear", "--host", "h", "--snmp-user", "u", "monitor")
        with pytest.raises(ValueError, match="--auth-key"):
            cli.build_kwargs(parsed)

    def test_netgear_plus_only_password(self):
        parsed = parse("--vendor", "netgear_plus", "--host", "h", "--password", "pw", "--verify-ssl", "monitor")
        assert cli.build_kwargs(parsed) == {"password": "pw"}

    def test_password_required_for_other_vendors(self):
        parsed = parse("--vendor", "cisco", "--host", "h", "monitor")
        with pytest.raises(ValueError, match="--password"):
            cli.build_kwargs(parsed)

    def test_qnap_options(self):
        parsed = parse(
            "--vendor", "qnap", "--host", "h", "--password", "pw", "--vlan-backend", "ssh", "--rest-delay", "0.5", "monitor"
        )  # fmt: skip
        kwargs = cli.build_kwargs(parsed)
        assert kwargs["vlan_backend"] == "ssh"
        assert kwargs["rest_delay"] == 0.5
        assert kwargs["username"] == "admin"

    def test_qnap_defaults_omit_new_options(self):
        parsed = parse("--vendor", "qnap", "--host", "h", "--password", "pw", "monitor")
        kwargs = cli.build_kwargs(parsed)
        assert "vlan_backend" not in kwargs
        assert "rest_delay" not in kwargs

    def test_netgear_option_rejected_for_other_vendor(self):
        parsed = parse("--vendor", "cisco", "--host", "h", "--password", "pw", "--community", "c", "monitor")
        with pytest.raises(ValueError, match="only valid with --vendor netgear"):
            cli.build_kwargs(parsed)

    def test_qnap_option_rejected_for_other_vendor(self):
        parsed = parse("--vendor", "netgear_plus", "--host", "h", "--password", "pw", "--rest-delay", "1", "monitor")
        with pytest.raises(ValueError, match="only valid with --vendor qnap"):
            cli.build_kwargs(parsed)

    @pytest.mark.parametrize("flag,value", [("--auth-protocol", "bogus"), ("--priv-protocol", "3des")])
    def test_invalid_protocol_choice(self, flag, value):
        with pytest.raises(SystemExit):
            parse("--vendor", "netgear", "--host", "h", flag, value, "monitor")


class TestMain:
    def test_passes_kwargs_to_factory(self):
        with fake_factory(make_switch()) as factory:
            cli.main(["--vendor", "netgear", "--host", "h", "--snmp-user", "u", "--auth-key", "a", "monitor"])
        factory.assert_called_once_with("netgear", host="h", snmp_user="u", auth_key="a")

    def test_missing_credentials_is_usage_error(self):
        with pytest.raises(SystemExit) as exc:
            cli.main(["--vendor", "netgear", "--host", "h", "monitor"])
        assert exc.value.code == 2

    def test_monitor_survives_missing_sensors(self, capsys):
        with fake_factory(make_switch(sensors_error=True)):
            cli.main(["--vendor", "netgear", "--host", "h", "--community", "c", "monitor"])
        out = capsys.readouterr().out
        assert "no sensors" in out
        assert "=== Port Status ===" in out
        assert "Channel 1: 1, 2 [up]" in out

    def test_monitor_survives_missing_lacp(self, capsys):
        with fake_factory(make_switch(lacp_error=True)):
            cli.main(["--vendor", "netgear_plus", "--host", "h", "--password", "pw", "monitor"])
        out = capsys.readouterr().out
        assert "no lacp" in out
        assert "TX:" in out

    def test_example_survives_missing_sensors(self, capsys):
        with fake_factory(make_switch(sensors_error=True)):
            cli.main(["--vendor", "netgear_plus", "--host", "h", "--password", "pw", "example"])
        out = capsys.readouterr().out
        assert "not supported" in out
        assert "Ports: 1/1 up" in out
