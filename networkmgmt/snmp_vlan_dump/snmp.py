"""OID constants and async SNMP wrappers for VLAN dump queries."""

from __future__ import annotations

from typing import Any

from loguru import logger

# Optional pysnmp import
try:
    from pysnmp.hlapi.asyncio import (
        CommunityData,
        ContextData,
        ObjectIdentity,
        ObjectType,
        SnmpEngine,
        UdpTransportTarget,
        bulk_walk_cmd,
        get_cmd,
    )

    HAS_PYSNMP = True
except ImportError:
    HAS_PYSNMP = False

# ── OID constants ──────────────────────────────────────────────────────
OID_SYS_DESCR = "1.3.6.1.2.1.1.1.0"
OID_SYS_NAME = "1.3.6.1.2.1.1.5.0"
OID_SYS_UPTIME = "1.3.6.1.2.1.1.3.0"
OID_IF_DESCR = "1.3.6.1.2.1.2.2.1.2"  # IF-MIB::ifDescr
OID_IF_OPER_STATUS = "1.3.6.1.2.1.2.2.1.8"  # IF-MIB::ifOperStatus
OID_VLAN_STATIC_NAME = "1.3.6.1.2.1.17.7.1.4.3.1.1"  # Q-BRIDGE-MIB
OID_DOT1Q_PVID = "1.3.6.1.2.1.17.7.1.4.5.1.1"  # Q-BRIDGE-MIB
OID_VLAN_EGRESS_PORTS = "1.3.6.1.2.1.17.7.1.4.3.1.2"  # Q-BRIDGE-MIB
OID_VLAN_UNTAGGED_PORTS = "1.3.6.1.2.1.17.7.1.4.3.1.4"  # Q-BRIDGE-MIB
OID_VLAN_CURRENT_EGRESS = "1.3.6.1.2.1.17.7.1.4.2.1.4"  # Q-BRIDGE-MIB (fallback)
OID_VLAN_CURRENT_UNTAG = "1.3.6.1.2.1.17.7.1.4.2.1.5"  # Q-BRIDGE-MIB (fallback)


async def snmp_get_scalars(
    engine: Any,
    auth: Any,
    target: Any,
    *oids: str,
    host: str = "",
) -> list:
    """GET one or more scalar OIDs, return list of values."""
    tag = f" [{host}]" if host else ""
    error_indication, error_status, _, var_binds = await get_cmd(
        engine,
        auth,
        target,
        ContextData(),
        *[ObjectType(ObjectIdentity(oid)) for oid in oids],
    )
    if error_indication:
        logger.warning(f"SNMP error{tag}: {error_indication}")
        return [None] * len(oids)
    if error_status:
        logger.warning(f"SNMP error{tag}: {error_status.prettyPrint()}")
        return [None] * len(oids)
    return [val for _, val in var_binds]


async def snmp_walk_table(
    engine: Any,
    auth: Any,
    target: Any,
    oid: str,
    index_len: int = 1,
    host: str = "",
) -> list[tuple]:
    """Bulk-walk an OID subtree.

    index_len=1: return (last_index, value) tuples.
    index_len=2: return ((idx[-2], idx[-1]), value) tuples (e.g. Current table).
    """
    tag = f" [{host}]" if host else ""
    results: list[tuple] = []
    async for error_indication, error_status, _, var_binds in bulk_walk_cmd(
        engine,
        auth,
        target,
        ContextData(),
        0,
        25,  # nonRepeaters, maxRepetitions
        ObjectType(ObjectIdentity(oid)),
        lexicographicMode=False,
    ):
        if error_indication:
            logger.warning(f"SNMP walk error{tag} on {oid}: {error_indication}")
            break
        if error_status:
            logger.warning(f"SNMP walk error{tag} on {oid}: {error_status.prettyPrint()}")
            break
        for var_bind_oid, val in var_binds:
            idx: int | tuple[int, ...]
            if index_len == 1:
                idx = int(var_bind_oid[-1])
            else:
                idx = tuple(int(var_bind_oid[-i]) for i in range(index_len, 0, -1))
            results.append((idx, val))
    return results
