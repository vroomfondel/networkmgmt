"""SNMP sessions on top of pysnmp 7 (``pysnmp.hlapi.v3arch.asyncio``).

:class:`AsyncSnmpSession` is the asyncio primitive, :class:`SnmpSession` a synchronous wrapper that
runs its own event loop in a daemon thread and therefore also works when called from code that
already runs inside an event loop. ``pysnmp`` is an optional dependency; without it constructing a
session raises :class:`~networkmgmt.snmp.errors.SnmpUnavailableError`.
"""

import asyncio
import threading
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Literal, Self

from loguru import logger

from networkmgmt.snmp.credentials import SnmpCredentials
from networkmgmt.snmp.errors import (
    SnmpAuthenticationError,
    SnmpError,
    SnmpNoSuchObjectError,
    SnmpTimeoutError,
    SnmpUnavailableError,
    SnmpWriteError,
)

try:
    from pysnmp.hlapi.v3arch.asyncio import (
        CommunityData,
        ContextData,
        Integer,
        IpAddress,
        ObjectIdentity,
        ObjectType,
        OctetString,
        SnmpEngine,
        UdpTransportTarget,
        Unsigned32,
        UsmUserData,
        bulk_walk_cmd,
        get_cmd,
        set_cmd,
        usmAesCfb128Protocol,
        usmDESPrivProtocol,
        usmHMAC192SHA256AuthProtocol,
        usmHMAC384SHA512AuthProtocol,
        usmHMACMD5AuthProtocol,
        usmHMACSHAAuthProtocol,
    )

    HAS_PYSNMP = True
except ImportError:
    HAS_PYSNMP = False

SnmpAny = Any
"""Opaque pysnmp object (engine, auth data, target, varbind); pysnmp ships no usable type stubs."""

_AUTH_INDICATIONS = frozenset(
    {
        "WrongDigest",
        "UnknownUserName",
        "UnknownSecurityName",
        "UnknownCommunityName",
        "UnsupportedSecurityLevel",
        "UnsupportedAuthProtocol",
        "UnsupportedPrivProtocol",
        "DecryptionError",
        "AuthenticationFailure",
        "AuthenticationError",
    }
)
_MISSING_VALUES = frozenset({"NoSuchObject", "NoSuchInstance", "EndOfMibView"})


@dataclass(frozen=True)
class SnmpValue:
    """A typed value for a SET, independent of pysnmp.

    Attributes:
        kind: ``integer``, ``unsigned``, ``octets`` or ``ip``.
        value: ``int`` for integer/unsigned, ``bytes`` or ``str`` for octets, dotted quad for ip.
    """

    kind: Literal["integer", "unsigned", "octets", "ip"]
    value: int | bytes | str


def integer(value: int) -> SnmpValue:
    """Return an INTEGER value for :meth:`SnmpSession.set`."""
    return SnmpValue("integer", value)


def unsigned(value: int) -> SnmpValue:
    """Return an Unsigned32/Gauge32 value for :meth:`SnmpSession.set`."""
    return SnmpValue("unsigned", value)


def octets(value: bytes | str) -> SnmpValue:
    """Return an OCTET STRING value for :meth:`SnmpSession.set`."""
    return SnmpValue("octets", value)


def ip_address(value: str) -> SnmpValue:
    """Return an IpAddress value (dotted quad) for :meth:`SnmpSession.set`."""
    return SnmpValue("ip", value)


def build_auth_data(credentials: SnmpCredentials) -> SnmpAny:
    """Translate credentials into pysnmp ``CommunityData`` (v2c) or ``UsmUserData`` (v3).

    Args:
        credentials: The credentials.

    Returns:
        The pysnmp authentication object.
    """
    if not credentials.user:
        return CommunityData(credentials.community, mpModel=1)
    auth = {
        "md5": usmHMACMD5AuthProtocol,
        "sha": usmHMACSHAAuthProtocol,
        "sha256": usmHMAC192SHA256AuthProtocol,
        "sha512": usmHMAC384SHA512AuthProtocol,
    }[credentials.auth_protocol]
    if not credentials.priv_key:
        return UsmUserData(credentials.user, authKey=credentials.auth_key, authProtocol=auth)
    priv = {"des": usmDESPrivProtocol, "aes128": usmAesCfb128Protocol}[credentials.priv_protocol]
    return UsmUserData(
        credentials.user,
        authKey=credentials.auth_key,
        authProtocol=auth,
        privKey=credentials.priv_key,
        privProtocol=priv,
    )


def _to_pysnmp(value: SnmpValue) -> SnmpAny:
    if value.kind == "integer":
        return Integer(value.value)
    if value.kind == "unsigned":
        return Unsigned32(value.value)
    if value.kind == "ip":
        return IpAddress(value.value)
    return OctetString(value.value)


def _raise_indication(action: str, oid: str, indication: SnmpAny) -> None:
    message = f"{action} {oid}: {indication}"
    name = type(indication).__name__
    if name in _AUTH_INDICATIONS:
        raise SnmpAuthenticationError(message)
    if name == "RequestTimedOut":
        raise SnmpTimeoutError(message)
    raise SnmpError(message)


class AsyncSnmpSession:
    """Asyncio SNMP client for one agent (GET, bulk WALK, SET).

    Values are returned already converted: ``str`` (pysnmp ``prettyPrint``), ``int`` or ``bytes``.
    Walk results are keyed by the OID suffix below the walked base (``"7"``, ``"1.4.10.0.0.0"``).
    """

    def __init__(
        self,
        host: str,
        credentials: SnmpCredentials,
        *,
        port: int = 161,
        timeout: float = 5.0,
        retries: int = 1,
        single_varbind: bool = False,
        max_repetitions: int = 25,
    ):
        """Create the session; no network traffic happens before the first request.

        Args:
            host: Agent IP or hostname.
            credentials: v2c or v3 credentials.
            port: UDP port.
            timeout: Per-request timeout in seconds.
            retries: Retries per request.
            single_varbind: Send every SET as one PDU per varbind (GS108Tv2 rejects multi-varbind PDUs).
            max_repetitions: GETBULK max-repetitions for walks.

        Raises:
            SnmpUnavailableError: If pysnmp 7.x is not installed.
        """
        if not HAS_PYSNMP:
            raise SnmpUnavailableError("pysnmp >= 7 is required (pip install pysnmp)")
        self.host = host
        self.credentials = credentials
        self.port = port
        self.timeout = timeout
        self.retries = retries
        self.single_varbind = single_varbind
        self.max_repetitions = max_repetitions
        self._engine: SnmpAny = None
        self._auth: SnmpAny = None
        self._target: SnmpAny = None

    async def open(self) -> None:
        """Create the engine and transport target (idempotent)."""
        if self._target is not None:
            return
        self._engine = SnmpEngine()
        self._auth = build_auth_data(self.credentials)
        self._target = await UdpTransportTarget.create(
            (self.host, self.port), timeout=self.timeout, retries=self.retries
        )

    def close(self) -> None:
        """Close the dispatcher of the engine (idempotent)."""
        if self._engine is not None:
            self._engine.close_dispatcher()
        self._engine = None
        self._target = None

    async def __aenter__(self) -> Self:
        await self.open()
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: TracebackType | None
    ) -> None:
        self.close()

    async def _get_raw(self, oid: str) -> SnmpAny:
        await self.open()
        err_ind, err_stat, _, var_binds = await get_cmd(
            self._engine, self._auth, self._target, ContextData(), ObjectType(ObjectIdentity(oid))
        )
        if err_ind:
            _raise_indication("get", oid, err_ind)
        if err_stat:
            raise SnmpError(f"get {oid}: {err_stat.prettyPrint()}")
        value = var_binds[0][1]
        if type(value).__name__ in _MISSING_VALUES:
            raise SnmpNoSuchObjectError(f"get {oid}: {value.prettyPrint()}")
        return value

    async def get(self, oid: str) -> str:
        """GET one OID and return the pretty-printed value.

        Raises:
            SnmpNoSuchObjectError: If the OID does not exist on the agent.
            SnmpError: On any other failure.
        """
        return str((await self._get_raw(oid)).prettyPrint())

    async def get_int(self, oid: str) -> int:
        """GET one OID and return it as ``int`` (also Counter, Gauge, TimeTicks)."""
        return int(await self._get_raw(oid))

    async def get_octets(self, oid: str) -> bytes:
        """GET one OID and return the raw octets."""
        return bytes((await self._get_raw(oid)).asOctets())

    async def _walk(self, base: str, convert: Callable[[SnmpAny], Any]) -> dict[str, Any]:
        await self.open()
        rows: dict[str, Any] = {}
        async for err_ind, err_stat, _, var_binds in bulk_walk_cmd(
            self._engine,
            self._auth,
            self._target,
            ContextData(),
            0,
            self.max_repetitions,
            ObjectType(ObjectIdentity(base)),
            lexicographicMode=False,
        ):
            if err_ind:
                _raise_indication("walk", base, err_ind)
            if err_stat:
                raise SnmpError(f"walk {base}: {err_stat.prettyPrint()}")
            for oid, val in var_binds:
                rows[str(oid)[len(base) + 1 :]] = convert(val)
        return rows

    async def walk(self, base: str) -> dict[str, str]:
        """Walk a subtree and return ``{suffix: prettyPrint}``."""
        return await self._walk(base, lambda v: str(v.prettyPrint()))

    async def walk_int(self, base: str) -> dict[str, int]:
        """Walk a subtree and return ``{suffix: int}``."""
        return await self._walk(base, int)

    async def walk_octets(self, base: str) -> dict[str, bytes]:
        """Walk a subtree and return ``{suffix: raw octets}`` (PortLists, MAC addresses)."""
        return await self._walk(base, lambda v: bytes(v.asOctets()))

    async def set(self, *pairs: tuple[str, SnmpValue], single_varbind: bool | None = None) -> None:
        """SET one or more varbinds.

        Args:
            *pairs: ``(oid, value)`` tuples, values built with :func:`integer`, :func:`unsigned`,
                :func:`octets` or :func:`ip_address`.
            single_varbind: Force one PDU per varbind; None uses the session default.

        Raises:
            SnmpWriteError: If the agent rejects the SET.
            SnmpError: On transport failure.
        """
        await self.open()
        if single_varbind if single_varbind is not None else self.single_varbind:
            for pair in pairs:
                await self._set_pdu((pair,))
        else:
            await self._set_pdu(pairs)

    async def _set_pdu(self, pairs: tuple[tuple[str, SnmpValue], ...]) -> None:
        objs = [ObjectType(ObjectIdentity(oid), _to_pysnmp(value)) for oid, value in pairs]
        err_ind, err_stat, err_idx, _ = await set_cmd(self._engine, self._auth, self._target, ContextData(), *objs)
        first = pairs[0][0]
        if err_ind:
            _raise_indication("set", first, err_ind)
        if err_stat:
            index = int(err_idx)
            where = pairs[index - 1][0] if 0 < index <= len(pairs) else first
            status = str(err_stat.prettyPrint())
            raise SnmpWriteError(f"set {where}: {status}", oid=where, status=status)


class _LoopThread:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, name="snmp-session", daemon=True)
        self.thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def call[T](self, coro: Coroutine[Any, Any, T]) -> T:
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5)
        if not self.thread.is_alive():
            self.loop.close()


class SnmpSession:
    """Synchronous SNMP client; same operations as :class:`AsyncSnmpSession`.

    The session is opened lazily on first use and closed by :meth:`close` or the context manager.

    Usage::

        creds = SnmpCredentials.v3("admin", "secret", auth_protocol="sha", priv_key=None)
        with SnmpSession("192.0.2.10", creds) as snmp:
            names = snmp.walk("1.3.6.1.2.1.31.1.1.1.1")
            snmp.set(("1.3.6.1.2.1.2.2.1.7.5", integer(2)))
    """

    def __init__(
        self,
        host: str,
        credentials: SnmpCredentials,
        *,
        port: int = 161,
        timeout: float = 5.0,
        retries: int = 1,
        single_varbind: bool = False,
        max_repetitions: int = 25,
    ):
        """Create the session.

        Args:
            host: Agent IP or hostname.
            credentials: v2c or v3 credentials.
            port: UDP port.
            timeout: Per-request timeout in seconds.
            retries: Retries per request.
            single_varbind: Send every SET as one PDU per varbind.
            max_repetitions: GETBULK max-repetitions for walks.

        Raises:
            SnmpUnavailableError: If pysnmp 7.x is not installed.
        """
        self._async = AsyncSnmpSession(
            host,
            credentials,
            port=port,
            timeout=timeout,
            retries=retries,
            single_varbind=single_varbind,
            max_repetitions=max_repetitions,
        )
        self._runner: _LoopThread | None = None

    @property
    def host(self) -> str:
        """Agent host as given."""
        return self._async.host

    @property
    def credentials(self) -> SnmpCredentials:
        """Credentials in use."""
        return self._async.credentials

    @property
    def single_varbind(self) -> bool:
        """Whether SETs are sent as one PDU per varbind by default."""
        return self._async.single_varbind

    def _run[T](self, coro: Coroutine[Any, Any, T]) -> T:
        if self._runner is None:
            self._runner = _LoopThread()
        return self._runner.call(coro)

    def open(self) -> None:
        """Open the session eagerly (otherwise done on first request)."""
        self._run(self._async.open())

    def close(self) -> None:
        """Close the dispatcher and stop the loop thread (idempotent)."""
        if self._runner is None:
            return
        try:
            self._runner.call(self._close_async())
        finally:
            self._runner.stop()
            self._runner = None
        logger.debug(f"SNMP session to {self.host} closed")

    async def _close_async(self) -> None:
        self._async.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc_val: BaseException | None, exc_tb: TracebackType | None
    ) -> None:
        self.close()

    def get(self, oid: str) -> str:
        """GET one OID, pretty-printed. See :meth:`AsyncSnmpSession.get`."""
        return self._run(self._async.get(oid))

    def get_int(self, oid: str) -> int:
        """GET one OID as ``int``."""
        return self._run(self._async.get_int(oid))

    def get_octets(self, oid: str) -> bytes:
        """GET one OID as raw octets."""
        return self._run(self._async.get_octets(oid))

    def walk(self, base: str) -> dict[str, str]:
        """Walk a subtree, ``{suffix: prettyPrint}``."""
        return self._run(self._async.walk(base))

    def walk_int(self, base: str) -> dict[str, int]:
        """Walk a subtree, ``{suffix: int}``."""
        return self._run(self._async.walk_int(base))

    def walk_octets(self, base: str) -> dict[str, bytes]:
        """Walk a subtree, ``{suffix: raw octets}``."""
        return self._run(self._async.walk_octets(base))

    def set(self, *pairs: tuple[str, SnmpValue], single_varbind: bool | None = None) -> None:
        """SET varbinds. See :meth:`AsyncSnmpSession.set`."""
        self._run(self._async.set(*pairs, single_varbind=single_varbind))
