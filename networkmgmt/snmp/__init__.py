"""Shared SNMP core: credentials, sessions, PortList helpers and read-back polling.

Requires the optional dependency ``pysnmp`` (>= 7) for any network access; everything else
(credentials, PortList helpers, polling, error mapping) works without it.
"""

from networkmgmt.snmp.credentials import AUTH_PROTOCOLS, PRIV_PROTOCOLS, SnmpCredentials
from networkmgmt.snmp.errors import (
    SnmpAuthenticationError,
    SnmpError,
    SnmpNoSuchObjectError,
    SnmpSettleTimeoutError,
    SnmpTimeoutError,
    SnmpUnavailableError,
    SnmpWriteError,
    to_switch_error,
)
from networkmgmt.snmp.polling import poll_until
from networkmgmt.snmp.portlist import portlist_patch, portlist_to_ports, ports_to_portlist
from networkmgmt.snmp.session import (
    HAS_PYSNMP,
    AsyncSnmpSession,
    SnmpSession,
    SnmpValue,
    integer,
    ip_address,
    octets,
    unsigned,
)

__all__ = [
    "AUTH_PROTOCOLS",
    "PRIV_PROTOCOLS",
    "HAS_PYSNMP",
    "SnmpCredentials",
    "SnmpSession",
    "AsyncSnmpSession",
    "SnmpValue",
    "integer",
    "unsigned",
    "octets",
    "ip_address",
    "SnmpError",
    "SnmpUnavailableError",
    "SnmpAuthenticationError",
    "SnmpTimeoutError",
    "SnmpNoSuchObjectError",
    "SnmpWriteError",
    "SnmpSettleTimeoutError",
    "to_switch_error",
    "poll_until",
    "portlist_to_ports",
    "ports_to_portlist",
    "portlist_patch",
]
