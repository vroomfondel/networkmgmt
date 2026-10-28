"""Netgear Smart Managed Plus switch client."""

from networkmgmt.switchctrl.base.client import BaseSwitchClient
from networkmgmt.switchctrl.base.managers import (
    BaseLACPManager,
    BaseMonitoringManager,
    BasePortManager,
    BaseVLANManager,
)
from networkmgmt.switchctrl.exceptions import SwitchError
from networkmgmt.switchctrl.factory import register_vendor
from networkmgmt.switchctrl.vendors.netgear_plus import sdk
from networkmgmt.switchctrl.vendors.netgear_plus.managers import (
    NetgearPlusLACPManager,
    NetgearPlusMonitoringManager,
    NetgearPlusPortManager,
    NetgearPlusVLANManager,
)


@register_vendor("netgear_plus")
class NetgearPlusSwitch(BaseSwitchClient):
    """Client for Netgear Smart Managed Plus switches (GS105E, GS108E, GS305E ...).

    Talks to the web UI through the optional ``netgear-tool`` package. The switch
    allows a single web session, so use the client as a context manager.

    Usage::

        with NetgearPlusSwitch(host="192.168.1.5", password="secret") as switch:
            print(switch.monitoring.get_system_info().model)
            switch.vlan.configure_trunk(TrunkConfig(port="1", native_vlan=1, allowed_vlans=[1, 101]))
    """

    def __init__(self, host: str, password: str, timeout: float = 10.0) -> None:
        super().__init__(host)
        self._password = password
        self._timeout = timeout
        self._session: sdk.NetgearPlusSession | None = None
        self._monitoring: NetgearPlusMonitoringManager | None = None
        self._vlan: NetgearPlusVLANManager | None = None
        self._port: NetgearPlusPortManager | None = None
        self._lacp: NetgearPlusLACPManager | None = None

    def _require_session(self) -> sdk.NetgearPlusSession:
        if self._session is None:
            self.connect()
        if self._session is None:
            raise SwitchError("not connected")
        return self._session

    @property
    def monitoring(self) -> BaseMonitoringManager:
        """Access monitoring operations."""
        if self._monitoring is None:
            self._monitoring = NetgearPlusMonitoringManager(self._require_session())
        return self._monitoring

    @property
    def vlan(self) -> BaseVLANManager:
        """Access VLAN management; the returned manager also offers ``reconcile`` and ``ensure_*``."""
        return self._vlan_manager()

    def _vlan_manager(self) -> NetgearPlusVLANManager:
        if self._vlan is None:
            self._vlan = NetgearPlusVLANManager(self._require_session())
        return self._vlan

    @property
    def port(self) -> BasePortManager:
        """Access port configuration."""
        if self._port is None:
            self._port = NetgearPlusPortManager(self._require_session(), self._vlan_manager())
        return self._port

    @property
    def lacp(self) -> BaseLACPManager:
        """Access LACP management (always raises NotImplementedError)."""
        if self._lacp is None:
            self._lacp = NetgearPlusLACPManager()
        return self._lacp

    def connect(self) -> None:
        """Log in to the web UI.

        Raises:
            SwitchError: ``netgear-tool`` is not installed.
            AuthenticationError: The password was rejected.
        """
        if self._session is None:
            self._session = sdk.open_session(self.host, self._password, self._timeout)

    def disconnect(self) -> None:
        """Log out and drop all managers."""
        self._monitoring = None
        self._vlan = None
        self._port = None
        self._lacp = None
        if self._session is not None:
            self._session.logout()
            self._session = None
