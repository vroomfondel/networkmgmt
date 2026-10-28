"""Tests for the shared SNMP core (pysnmp fully mocked)."""

import asyncio
from types import SimpleNamespace

import pytest

import networkmgmt.snmp.session as session_mod
from networkmgmt.snmp import (
    AsyncSnmpSession,
    SnmpAuthenticationError,
    SnmpCredentials,
    SnmpError,
    SnmpNoSuchObjectError,
    SnmpSession,
    SnmpSettleTimeoutError,
    SnmpTimeoutError,
    SnmpUnavailableError,
    SnmpWriteError,
    integer,
    ip_address,
    octets,
    poll_until,
    portlist_patch,
    portlist_to_ports,
    ports_to_portlist,
    to_switch_error,
    unsigned,
)
from networkmgmt.switchctrl.exceptions import APIError, AuthenticationError, VLANError


class FakeValue:
    def __init__(self, value):
        self.value = value

    def prettyPrint(self):
        return self.value.decode() if isinstance(self.value, bytes) else str(self.value)

    def asOctets(self):
        return self.value

    def __int__(self):
        return int(self.value)


class NoSuchObject(FakeValue):
    pass


class RequestTimedOut:
    def __str__(self):
        return "No SNMP response received before timeout"


class WrongDigest:
    def __str__(self):
        return "Wrong SNMP PDU digest"


class FakeStatus:
    def __init__(self, text):
        self.text = text

    def __bool__(self):
        return True

    def prettyPrint(self):
        return self.text


@pytest.fixture
def fake_pysnmp(monkeypatch):
    state = SimpleNamespace(
        mib={},
        indication=None,
        set_error=None,
        set_pdus=[],
        auth_calls=[],
        engines=[],
        targets=[],
    )
    protocols = {
        n: f"<{n}>"
        for n in (
            "usmHMACMD5AuthProtocol",
            "usmHMACSHAAuthProtocol",
            "usmHMAC192SHA256AuthProtocol",
            "usmHMAC384SHA512AuthProtocol",
            "usmDESPrivProtocol",
            "usmAesCfb128Protocol",
        )
    }

    class Engine:
        def __init__(self):
            self.closed = 0
            state.engines.append(self)

        def close_dispatcher(self):
            self.closed += 1

    class Target:
        @classmethod
        async def create(cls, address, timeout, retries):
            state.targets.append((address, timeout, retries))
            return cls()

    async def get_cmd(engine, auth, target, ctx, *objs):
        if state.indication:
            return state.indication, 0, 0, []
        oid = objs[0][0]
        value = state.mib.get(oid, NoSuchObject("No Such Object"))
        return None, 0, 0, [(oid, value if isinstance(value, FakeValue) else FakeValue(value))]

    async def bulk_walk_cmd(engine, auth, target, ctx, nr, mr, obj, lexicographicMode):
        if state.indication:
            yield state.indication, 0, 0, []
            return
        base = obj[0]
        rows = [(f"{k}", FakeValue(v)) for k, v in sorted(state.mib.items()) if k.startswith(base + ".")]
        for i in range(0, len(rows), 2):
            yield None, 0, 0, rows[i : i + 2]

    async def set_cmd(engine, auth, target, ctx, *objs):
        state.set_pdus.append([(o[0], o[1]) for o in objs])
        if state.set_error:
            return None, FakeStatus(state.set_error[1]), state.set_error[0], []
        return None, 0, 0, []

    def record(name):
        def make(*args, **kwargs):
            state.auth_calls.append((name, args, kwargs))
            return (name, args, kwargs)

        return make

    attrs = {
        "HAS_PYSNMP": True,
        "SnmpEngine": Engine,
        "UdpTransportTarget": Target,
        "ContextData": lambda: "ctx",
        "ObjectIdentity": lambda oid: oid,
        "ObjectType": lambda ident, value=None: (ident, value),
        "Integer": lambda v: ("Integer", v),
        "Unsigned32": lambda v: ("Unsigned32", v),
        "OctetString": lambda v: ("OctetString", v),
        "IpAddress": lambda v: ("IpAddress", v),
        "CommunityData": record("CommunityData"),
        "UsmUserData": record("UsmUserData"),
        "get_cmd": get_cmd,
        "bulk_walk_cmd": bulk_walk_cmd,
        "set_cmd": set_cmd,
        **protocols,
    }
    for name, value in attrs.items():
        monkeypatch.setattr(session_mod, name, value, raising=False)
    state.protocols = protocols
    return state


class TestPortlist:
    def test_to_ports_msb_first(self):
        assert portlist_to_ports(b"\x80\x01") == {1, 16}

    def test_roundtrip(self):
        assert portlist_to_ports(ports_to_portlist({1, 5, 9, 13}, 2)) == {1, 5, 9, 13}

    def test_encode_length(self):
        assert ports_to_portlist({1}, 3) == b"\x80\x00\x00"

    def test_patch_touches_only_given_bits(self):
        raw = b"\xff\xf0"
        assert portlist_patch(raw, 2, {2: False}) == b"\xbf\xf0"
        assert portlist_patch(raw, 2, {16: True}) == b"\xff\xf1"

    def test_patch_keeps_non_member_ones_which_full_rebuild_would_clear(self):
        raw = b"\x7f\xff"
        rebuilt = ports_to_portlist({1}, 2)
        patched = portlist_patch(raw, 2, {1: True})
        assert patched == b"\xff\xff"
        assert rebuilt != patched

    def test_patch_pads_and_truncates(self):
        assert portlist_patch(b"\x80", 2, {9: True}) == b"\x80\x80"
        assert portlist_patch(b"\x80\x80\x80", 1, {}) == b"\x80"


class TestCredentials:
    def test_requires_community_or_user(self):
        with pytest.raises(ValueError):
            SnmpCredentials()

    def test_v3_requires_auth_key(self):
        with pytest.raises(ValueError):
            SnmpCredentials(user="u")

    def test_unknown_protocols_rejected(self):
        with pytest.raises(ValueError):
            SnmpCredentials.v3("u", "k", auth_protocol="sha384")
        with pytest.raises(ValueError):
            SnmpCredentials.v3("u", "k", priv_key="p", priv_protocol="aes256")

    def test_versions(self):
        assert SnmpCredentials.v2c("public").version == "v2c"
        assert SnmpCredentials.v3("u", "k").version == "v3"

    def test_defaults_sha512_aes128(self):
        c = SnmpCredentials.v3("u", "k")
        assert (c.auth_protocol, c.priv_protocol) == ("sha512", "aes128")

    def test_repr_hides_secrets(self):
        text = repr(SnmpCredentials.v3("u", "topsecret", priv_key="privsecret"))
        assert "topsecret" not in text and "privsecret" not in text


class TestBuildAuthData:
    def test_v2c_uses_mp_model_1(self, fake_pysnmp):
        session_mod.build_auth_data(SnmpCredentials.v2c("public"))
        assert fake_pysnmp.auth_calls[-1] == ("CommunityData", ("public",), {"mpModel": 1})

    @pytest.mark.parametrize(
        ("name", "const"),
        [
            ("md5", "usmHMACMD5AuthProtocol"),
            ("sha", "usmHMACSHAAuthProtocol"),
            ("sha256", "usmHMAC192SHA256AuthProtocol"),
            ("sha512", "usmHMAC384SHA512AuthProtocol"),
        ],
    )
    def test_auth_protocol_selection(self, fake_pysnmp, name, const):
        session_mod.build_auth_data(SnmpCredentials.v3("u", "k", auth_protocol=name))
        _, args, kwargs = fake_pysnmp.auth_calls[-1]
        assert args == ("u",)
        assert kwargs == {"authKey": "k", "authProtocol": fake_pysnmp.protocols[const]}

    @pytest.mark.parametrize(("name", "const"), [("des", "usmDESPrivProtocol"), ("aes128", "usmAesCfb128Protocol")])
    def test_priv_protocol_selection(self, fake_pysnmp, name, const):
        session_mod.build_auth_data(SnmpCredentials.v3("u", "k", auth_protocol="sha", priv_key="p", priv_protocol=name))
        kwargs = fake_pysnmp.auth_calls[-1][2]
        assert kwargs["privKey"] == "p"
        assert kwargs["privProtocol"] == fake_pysnmp.protocols[const]

    def test_auth_no_priv_has_no_priv_arguments(self, fake_pysnmp):
        session_mod.build_auth_data(SnmpCredentials.v3("u", "k"))
        assert "privKey" not in fake_pysnmp.auth_calls[-1][2]


@pytest.mark.asyncio
class TestAsyncSession:
    def make(self, **kwargs):
        return AsyncSnmpSession("192.0.2.1", SnmpCredentials.v2c("public"), **kwargs)

    async def test_open_passes_target_arguments(self, fake_pysnmp):
        s = self.make(port=1161, timeout=2.0, retries=3)
        await s.open()
        await s.open()
        assert fake_pysnmp.targets == [(("192.0.2.1", 1161), 2.0, 3)]
        s.close()
        assert fake_pysnmp.engines[0].closed == 1

    async def test_get_variants(self, fake_pysnmp):
        fake_pysnmp.mib = {"1.2.3.0": b"name", "1.2.4.0": 42}
        s = self.make()
        assert await s.get("1.2.3.0") == "name"
        assert await s.get_int("1.2.4.0") == 42
        assert await s.get_octets("1.2.3.0") == b"name"

    async def test_get_missing_raises_no_such_object(self, fake_pysnmp):
        with pytest.raises(SnmpNoSuchObjectError):
            await self.make().get("1.3.6.1.4.1.4526.10.1.3.1.0")

    async def test_walk_returns_suffixes(self, fake_pysnmp):
        fake_pysnmp.mib = {"1.2.1": "a", "1.2.2": "b", "1.2.3": "c", "1.9.1": "x"}
        assert await self.make().walk("1.2") == {"1": "a", "2": "b", "3": "c"}

    async def test_walk_int_and_octets(self, fake_pysnmp):
        fake_pysnmp.mib = {"1.2.1": 5, "1.2.2": 6}
        s = self.make()
        assert await s.walk_int("1.2") == {"1": 5, "2": 6}
        fake_pysnmp.mib = {"1.2.1": b"\x80\x01"}
        assert await s.walk_octets("1.2") == {"1": b"\x80\x01"}

    async def test_timeout_is_mapped(self, fake_pysnmp):
        fake_pysnmp.indication = RequestTimedOut()
        with pytest.raises(SnmpTimeoutError):
            await self.make().get("1.2.3.0")
        with pytest.raises(SnmpTimeoutError):
            await self.make().walk("1.2")

    async def test_auth_failure_is_mapped(self, fake_pysnmp):
        fake_pysnmp.indication = WrongDigest()
        with pytest.raises(SnmpAuthenticationError):
            await self.make().get("1.2.3.0")

    async def test_set_converts_value_types(self, fake_pysnmp):
        await self.make().set(
            ("1.1", integer(1)), ("1.2", unsigned(2)), ("1.3", octets(b"\x80")), ("1.4", ip_address("10.0.0.1"))
        )
        assert fake_pysnmp.set_pdus == [
            [
                ("1.1", ("Integer", 1)),
                ("1.2", ("Unsigned32", 2)),
                ("1.3", ("OctetString", b"\x80")),
                ("1.4", ("IpAddress", "10.0.0.1")),
            ]
        ]

    async def test_multi_varbind_is_one_pdu_by_default(self, fake_pysnmp):
        await self.make().set(("1.1", integer(1)), ("1.2", integer(2)))
        assert len(fake_pysnmp.set_pdus) == 1

    async def test_session_wide_single_varbind_mode(self, fake_pysnmp):
        await self.make(single_varbind=True).set(("1.1", integer(1)), ("1.2", integer(2)))
        assert [len(p) for p in fake_pysnmp.set_pdus] == [1, 1]

    async def test_per_call_single_varbind_overrides_default(self, fake_pysnmp):
        await self.make().set(("1.1", integer(1)), ("1.2", integer(2)), single_varbind=True)
        assert [len(p) for p in fake_pysnmp.set_pdus] == [1, 1]
        fake_pysnmp.set_pdus.clear()
        await self.make(single_varbind=True).set(("1.1", integer(1)), ("1.2", integer(2)), single_varbind=False)
        assert [len(p) for p in fake_pysnmp.set_pdus] == [2]

    async def test_set_error_names_the_failing_oid(self, fake_pysnmp):
        fake_pysnmp.set_error = (2, "commitFailed")
        with pytest.raises(SnmpWriteError) as exc_info:
            await self.make().set(("1.1", integer(1)), ("1.2", integer(2)))
        assert exc_info.value.oid == "1.2"
        assert exc_info.value.status == "commitFailed"


class TestUnavailable:
    def test_session_requires_pysnmp(self, monkeypatch):
        monkeypatch.setattr(session_mod, "HAS_PYSNMP", False)
        with pytest.raises(SnmpUnavailableError):
            AsyncSnmpSession("192.0.2.1", SnmpCredentials.v2c("public"))
        with pytest.raises(SnmpUnavailableError):
            SnmpSession("192.0.2.1", SnmpCredentials.v2c("public"))


class TestSyncSession:
    def test_blocking_calls(self, fake_pysnmp):
        fake_pysnmp.mib = {"1.2.1": 7}
        with SnmpSession("192.0.2.1", SnmpCredentials.v2c("public")) as s:
            assert s.walk_int("1.2") == {"1": 7}
            s.set(("1.1", integer(1)))
        assert fake_pysnmp.set_pdus == [[("1.1", ("Integer", 1))]]
        assert fake_pysnmp.engines[0].closed == 1

    def test_usable_inside_running_event_loop(self, fake_pysnmp):
        fake_pysnmp.mib = {"1.2.3.0": 5}

        async def caller():
            s = SnmpSession("192.0.2.1", SnmpCredentials.v2c("public"))
            try:
                return s.get_int("1.2.3.0")
            finally:
                s.close()

        assert asyncio.run(caller()) == 5

    def test_close_without_use_is_noop(self, fake_pysnmp):
        SnmpSession("192.0.2.1", SnmpCredentials.v2c("public")).close()


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class TestPolling:
    @pytest.fixture
    def clock(self, monkeypatch):
        clock = FakeClock()
        monkeypatch.setattr("networkmgmt.snmp.polling.time", clock)
        return clock

    def test_returns_when_done(self, clock):
        values = iter([1, 1, 2])
        assert poll_until(lambda: next(values), lambda v: v == 2, interval=0.5) == 2
        assert clock.sleeps == [0.5, 0.5]

    def test_times_out(self, clock):
        with pytest.raises(SnmpSettleTimeoutError, match="the thing"):
            poll_until(lambda: 0, lambda v: v == 1, timeout=3, interval=1, what="the thing")
        assert sum(clock.sleeps) >= 3

    def test_first_read_needs_no_sleep(self, clock):
        poll_until(lambda: 1, lambda v: True)
        assert clock.sleeps == []


class TestErrorMapping:
    def test_authentication_maps_to_authentication_error(self):
        assert isinstance(to_switch_error(SnmpAuthenticationError("x")), AuthenticationError)

    def test_others_map_to_fallback(self):
        assert isinstance(to_switch_error(SnmpTimeoutError("x")), APIError)
        assert isinstance(to_switch_error(SnmpError("x"), VLANError), VLANError)

    def test_message_is_kept(self):
        assert str(to_switch_error(SnmpWriteError("set 1.2: commitFailed"))) == "set 1.2: commitFailed"
