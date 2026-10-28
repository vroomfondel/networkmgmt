"""Netgear Smart Managed Pro / FASTPATH switch client (SNMPv3 preferred, v2c fallback)."""

from typing import Any

from loguru import logger

from networkmgmt.snmp import SnmpCredentials, SnmpError, SnmpSession, SnmpUnavailableError, to_switch_error
from networkmgmt.switchctrl.base.client import BaseSwitchClient
from networkmgmt.switchctrl.base.managers import (
    BaseLACPManager,
    BaseMonitoringManager,
    BasePortManager,
    BaseVLANManager,
)
from networkmgmt.switchctrl.exceptions import APIError, SwitchError
from networkmgmt.switchctrl.factory import register_vendor
from networkmgmt.switchctrl.vendors.netgear.backend import NetgearBackend
from networkmgmt.switchctrl.vendors.netgear.routing import NetgearRoutingManager
from networkmgmt.switchctrl.vendors.netgear.snmp_managers import (
    NetgearLACPManager,
    NetgearMonitoringManager,
    NetgearPortManager,
    NetgearVLANManager,
)
from networkmgmt.switchctrl.vendors.netgear.web import NetgearWeb


@register_vendor("netgear")
class NetgearSwitch(BaseSwitchClient):
    """Client for Netgear Smart Managed Pro switches (S3300 stack, GS108Tv2, M4300 class) over SNMP.

    Monitoring, VLAN, port and LACP (read-only) managers use SNMP; :attr:`routing` additionally uses the
    web UI for static routes. Writes take effect with delay and are verified by polling the read-back.

    The GS108Tv2 uses its web login as SNMPv3 user (``admin``, auth key = web password) and supports only
    ``md5``/``sha`` and ``des``; the S3300 supports ``sha512`` and ``aes128`` (the defaults).

    Usage::

        with NetgearSwitch(host="192.0.2.38", snmp_user="monitor", auth_key="...", priv_key="...") as sw:
            print(sw.monitoring.get_system_info())
            sw.vlan.create_vlan(20, "iot")
            sw.vlan.assign_port_to_vlan("1/g5", 20)
            sw.save_config()

        # GS108Tv2: user and key are the web login, no privacy
        sw = NetgearSwitch(host="192.0.2.150", username="admin", password="...", auth_protocol="sha")
    """

    def __init__(
        self,
        host: str,
        *,
        community: str | None = None,
        snmp_user: str | None = None,
        auth_key: str | None = None,
        auth_protocol: str = "sha512",
        priv_key: str | None = None,
        priv_protocol: str = "aes128",
        username: str | None = None,
        password: str | None = None,
        web_password: str | None = None,
        port: int = 161,
        timeout: float = 5.0,
        retries: int = 1,
        single_varbind: bool = False,
        settle_timeout: float = 30.0,
        settle_interval: float = 1.0,
        web_timeout: float = 20.0,
        web_wait: float = 120.0,
        **kwargs: Any,
    ):
        """Create the client; no traffic happens before the first operation.

        SNMPv3 is used when ``snmp_user`` (or ``username``) is set, otherwise v2c with ``community``.
        ``username``/``password`` are the generic switchctrl CLI arguments and fill ``snmp_user``,
        ``auth_key`` and ``web_password`` when those are unset.

        Args:
            host: Switch address.
            community: SNMPv2c community.
            snmp_user: SNMPv3 user.
            auth_key: SNMPv3 authentication passphrase.
            auth_protocol: ``md5``, ``sha``, ``sha256`` or ``sha512``.
            priv_key: SNMPv3 privacy passphrase; None means authNoPriv.
            priv_protocol: ``des`` or ``aes128``.
            username: Alias for ``snmp_user``.
            password: Alias for ``auth_key`` and ``web_password``.
            web_password: Web UI password for static routes.
            port: SNMP UDP port.
            timeout: SNMP request timeout in seconds.
            retries: SNMP retries per request.
            single_varbind: Send every SET as one PDU per varbind (the VLAN writes always do).
            settle_timeout: Seconds to poll for a write to appear on read-back.
            settle_interval: Seconds between read-backs.
            web_timeout: HTTP timeout of the web UI client.
            web_wait: Seconds to wait for a free web UI session slot.
            **kwargs: Ignored (generic CLI arguments such as ``verify_ssl``).

        Raises:
            ValueError: If the credentials are incomplete or a protocol is unknown.
            SwitchError: If pysnmp is not installed.
        """
        super().__init__(host)
        user = snmp_user or username
        if user:
            credentials = SnmpCredentials.v3(
                user,
                auth_key or password or "",
                auth_protocol=auth_protocol,
                priv_key=priv_key,
                priv_protocol=priv_protocol,
            )
        else:
            credentials = SnmpCredentials(community=community)
        if kwargs:
            logger.debug(f"NetgearSwitch ignores unsupported arguments: {sorted(kwargs)}")
        self._web_password = web_password or password
        self._web_timeout = web_timeout
        self._web_wait = web_wait
        try:
            self._session = SnmpSession(
                host, credentials, port=port, timeout=timeout, retries=retries, single_varbind=single_varbind
            )
        except SnmpUnavailableError as exc:
            raise to_switch_error(exc, SwitchError) from exc
        self._backend = NetgearBackend(self._session, settle_timeout=settle_timeout, settle_interval=settle_interval)
        self._monitoring: NetgearMonitoringManager | None = None
        self._vlan: NetgearVLANManager | None = None
        self._port: NetgearPortManager | None = None
        self._lacp: NetgearLACPManager | None = None
        self._routing: NetgearRoutingManager | None = None

    @property
    def monitoring(self) -> BaseMonitoringManager:
        """Access monitoring (system, ports, statistics, stack)."""
        if self._monitoring is None:
            self._monitoring = NetgearMonitoringManager(self._backend)
        return self._monitoring

    @property
    def vlan(self) -> BaseVLANManager:
        """Access VLAN management."""
        if self._vlan is None:
            self._vlan = NetgearVLANManager(self._backend)
        return self._vlan

    @property
    def port(self) -> BasePortManager:
        """Access port administration."""
        if self._port is None:
            self._port = NetgearPortManager(self._backend)
        return self._port

    @property
    def lacp(self) -> BaseLACPManager:
        """Access LACP (read-only)."""
        if self._lacp is None:
            self._lacp = NetgearLACPManager(self._backend)
        return self._lacp

    @property
    def routing(self) -> NetgearRoutingManager:
        """Access VLAN routing interfaces, management address and static routes."""
        if self._routing is None:
            self._routing = NetgearRoutingManager(
                self._backend,
                web_factory=self._make_web if self._web_password else None,
                web_wait=self._web_wait,
            )
        return self._routing

    def _make_web(self) -> NetgearWeb:
        return NetgearWeb(self.host, self._web_password or "", timeout=self._web_timeout)

    def save_config(self) -> None:
        """Copy the running config to the startup config (lost on reboot otherwise).

        Raises:
            APIError: If the switch reports failure.
        """
        try:
            self._backend.save_config()
        except SnmpError as exc:
            raise to_switch_error(exc, APIError) from exc

    def connect(self) -> None:
        """Open the SNMP session eagerly."""
        try:
            self._session.open()
        except SnmpError as exc:
            raise to_switch_error(exc, APIError) from exc

    def disconnect(self) -> None:
        """Close the SNMP session."""
        self._session.close()
