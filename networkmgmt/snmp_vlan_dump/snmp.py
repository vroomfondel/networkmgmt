"""OID constants and error reporting for VLAN dump queries."""

from __future__ import annotations

from loguru import logger

from networkmgmt.snmp import HAS_PYSNMP, SnmpAuthenticationError, SnmpCredentials, SnmpError, SnmpTimeoutError

__all__ = [
    "HAS_PYSNMP",
    "OID_SYS_DESCR",
    "OID_SYS_NAME",
    "OID_SYS_UPTIME",
    "OID_IF_DESCR",
    "OID_IF_NAME",
    "OID_IF_OPER_STATUS",
    "OID_BASE_PORT_IFINDEX",
    "OID_VLAN_STATIC_NAME",
    "OID_DOT1Q_PVID",
    "OID_VLAN_EGRESS_PORTS",
    "OID_VLAN_UNTAGGED_PORTS",
    "OID_VLAN_CURRENT_EGRESS",
    "OID_VLAN_CURRENT_UNTAG",
    "describe_snmp_error",
    "log_snmp_error",
]

OID_SYS_DESCR = "1.3.6.1.2.1.1.1.0"
OID_SYS_NAME = "1.3.6.1.2.1.1.5.0"
OID_SYS_UPTIME = "1.3.6.1.2.1.1.3.0"
OID_IF_DESCR = "1.3.6.1.2.1.2.2.1.2"  # IF-MIB::ifDescr
OID_IF_NAME = "1.3.6.1.2.1.31.1.1.1.1"  # IF-MIB::ifName
OID_IF_OPER_STATUS = "1.3.6.1.2.1.2.2.1.8"  # IF-MIB::ifOperStatus
OID_BASE_PORT_IFINDEX = "1.3.6.1.2.1.17.1.4.1.2"  # BRIDGE-MIB::dot1dBasePortIfIndex
OID_VLAN_STATIC_NAME = "1.3.6.1.2.1.17.7.1.4.3.1.1"  # Q-BRIDGE-MIB
OID_DOT1Q_PVID = "1.3.6.1.2.1.17.7.1.4.5.1.1"  # Q-BRIDGE-MIB
OID_VLAN_EGRESS_PORTS = "1.3.6.1.2.1.17.7.1.4.3.1.2"  # Q-BRIDGE-MIB
OID_VLAN_UNTAGGED_PORTS = "1.3.6.1.2.1.17.7.1.4.3.1.4"  # Q-BRIDGE-MIB
OID_VLAN_CURRENT_EGRESS = "1.3.6.1.2.1.17.7.1.4.2.1.4"  # Q-BRIDGE-MIB
OID_VLAN_CURRENT_UNTAG = "1.3.6.1.2.1.17.7.1.4.2.1.5"  # Q-BRIDGE-MIB


def describe_snmp_error(exc: SnmpError, host: str, credentials: SnmpCredentials) -> str:
    """Return an actionable one-line description of an SNMP failure.

    Args:
        exc: The failure raised by the SNMP core.
        host: Agent address, for the message.
        credentials: Credentials that were used.

    Returns:
        The message; authentication failures and v3 timeouts name the likely credential causes.
    """
    if isinstance(exc, SnmpAuthenticationError):
        who = f"user {credentials.user!r}" if credentials.user else "community"
        return f"SNMP authentication failed for {host} ({who}): {exc}"
    if isinstance(exc, SnmpTimeoutError) and credentials.user:
        return (
            f"SNMP timeout from {host}: unreachable, or SNMPv3 user {credentials.user!r} / auth key / "
            f"auth protocol ({credentials.auth_protocol}) / priv settings rejected (agents often stay silent): {exc}"
        )
    if isinstance(exc, SnmpTimeoutError):
        return f"SNMP timeout from {host}: unreachable or wrong community: {exc}"
    return f"SNMP error from {host}: {exc}"


def log_snmp_error(exc: SnmpError, host: str, credentials: SnmpCredentials) -> None:
    """Log :func:`describe_snmp_error` at error level."""
    logger.error(describe_snmp_error(exc, host, credentials))
