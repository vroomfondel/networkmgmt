"""Tests for networkmgmt.snmp_vlan_dump.cli SNMP option handling."""

from __future__ import annotations

import pytest

from networkmgmt.snmp import SnmpCredentials, SnmpTimeoutError
from networkmgmt.snmp_vlan_dump import cli


def _creds(argv: list[str]) -> SnmpCredentials:
    parser = cli.build_parser()
    return cli.credentials_from_args(parser, parser.parse_args(argv))


class TestCredentialParsing:
    def test_default_is_v2c_public(self, monkeypatch):
        monkeypatch.delenv("SNMP_AUTH_KEY", raising=False)
        creds = _creds(["192.0.2.38"])
        assert creds == SnmpCredentials.v2c("public")
        assert cli.parse_args(["192.0.2.38"]).ip == "192.0.2.38"

    def test_ip_is_required(self):
        with pytest.raises(SystemExit):
            cli.parse_args([])

    def test_positional_community(self):
        assert _creds(["10.0.0.1", "private"]).community == "private"

    def test_v3_defaults(self):
        creds = _creds(["10.0.0.1", "--snmp-user", "admin", "--snmp-auth-key", "authkey12"])
        assert creds.version == "v3"
        assert creds.auth_protocol == "sha512"
        assert creds.priv_key is None
        assert creds.priv_protocol == "aes128"

    def test_v3_full(self):
        creds = _creds(
            [
                "192.0.2.38",
                "--snmp-user",
                "u",
                "--snmp-auth-key",
                "a" * 8,
                "--snmp-auth-protocol",
                "md5",
                "--snmp-priv-key",
                "p" * 8,
                "--snmp-priv-protocol",
                "des",
            ]
        )
        assert (creds.auth_protocol, creds.priv_key, creds.priv_protocol) == ("md5", "p" * 8, "des")

    def test_auth_key_from_env(self, monkeypatch):
        monkeypatch.setenv("SNMP_AUTH_KEY", "envkey123")
        assert _creds(["192.0.2.38", "--snmp-user", "u"]).auth_key == "envkey123"

    def test_user_without_key_errors(self, monkeypatch):
        monkeypatch.delenv("SNMP_AUTH_KEY", raising=False)
        with pytest.raises(SystemExit):
            _creds(["192.0.2.38", "--snmp-user", "u"])

    def test_invalid_protocol_rejected(self):
        with pytest.raises(SystemExit):
            cli.parse_args(["--snmp-auth-protocol", "sha1024"])


class TestMain:
    def test_snmp_failure_exits_1(self, monkeypatch):
        class Failing:
            def __init__(self, *a, **kw):
                self.kw = kw

            def collect(self):
                raise SnmpTimeoutError("timeout")

        monkeypatch.setattr(cli, "HAS_PYSNMP", True)
        monkeypatch.setattr("networkmgmt.snmp_vlan_dump.collector.VlanDataCollector", Failing)
        with pytest.raises(SystemExit) as exc:
            cli.main(["10.0.0.1", "--snmp-user", "u", "--snmp-auth-key", "authkey12"])
        assert exc.value.code == 1
