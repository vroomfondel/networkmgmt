#!/usr/bin/python

DOCUMENTATION = r"""
module: netgear_pro_facts
short_description: Read stack, port, VLAN and LLDP state from Netgear Smart Managed Pro switches via SNMP
description:
  - Walks standard MIBs (IF-MIB, Q-BRIDGE-MIB, LLDP-MIB, ENTITY-MIB) and returns C(netgear_pro) facts keyed by ifName (C(1/g1), C(2/xg27)).
  - C(routing) holds the global routing mode, VLAN routing interfaces (NETGEAR-ROUTING-MIB) and the route table (IP-FORWARD-MIB inetCidrRouteTable).
  - Ports are bridge ports whose index equals the ifIndex, as on the S3300 stack.
  - VLANs missing from dot1qVlanStaticTable (GS108T factory VLANs) are read from dot1qVlanCurrentTable.
  - SNMPv3 when C(snmp_user) is set, otherwise v2c with C(community).
  - No unit serials; FW 6.6.4.28 reports the management unit's serial for every stack member.
  - C(stack_ports) holds the 10G ports from agentInventoryStackPortTable (mode, link, speed, errors since boot); empty on standalone switches.
requirements:
  - pysnmp >= 7
options:
  host:
    description: Switch IP or hostname.
    required: true
    type: str
  port:
    description: SNMP UDP port.
    type: int
    default: 161
  timeout:
    description: Per-request timeout in seconds.
    type: float
    default: 5.0
  retries:
    description: Retries per request.
    type: int
    default: 1
  community:
    description: v2c community, used when C(snmp_user) is unset.
    type: str
  snmp_user:
    description: SNMPv3 user name.
    type: str
  auth_key:
    description: SNMPv3 authentication key.
    type: str
  auth_protocol:
    description: SNMPv3 authentication protocol.
    type: str
    choices: [md5, sha, sha256, sha512]
    default: sha512
  priv_key:
    description: SNMPv3 privacy key; unset means authNoPriv.
    type: str
  priv_protocol:
    description: SNMPv3 privacy protocol.
    type: str
    choices: [des, aes128]
    default: aes128
"""

EXAMPLES = r"""
- name: Gather stack facts
  netgear_pro_facts:
    host: "{{ ansible_host }}"
    snmp_user: "{{ netgear_snmp_user }}"
    auth_key: "{{ netgear_snmp_auth_key }}"
"""

import asyncio
import collections

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.netgear_snmp import (
    CONNECTION_ARGS,
    CONNECTION_CONSTRAINTS,
    HAS_PYSNMP,
    SnmpSession,
    read_routing,
    read_vlans,
)

TABLES = {
    "sysdescr": "1.3.6.1.2.1.1.1",
    "sysname": "1.3.6.1.2.1.1.5",
    "ent_class": "1.3.6.1.2.1.47.1.1.1.1.5",
    "ent_name": "1.3.6.1.2.1.47.1.1.1.1.7",
    "ent_sw": "1.3.6.1.2.1.47.1.1.1.1.10",
    "ent_model": "1.3.6.1.2.1.47.1.1.1.1.13",
    "ifname": "1.3.6.1.2.1.31.1.1.1.1",
    "ifalias": "1.3.6.1.2.1.31.1.1.1.18",
    "ifhighspeed": "1.3.6.1.2.1.31.1.1.1.15",
    "ifadmin": "1.3.6.1.2.1.2.2.1.7",
    "ifoper": "1.3.6.1.2.1.2.2.1.8",
    "pvid": "1.3.6.1.2.1.17.7.1.4.5.1.1",
    "lag_attached": "1.2.840.10006.300.43.1.2.1.1.13",
    "lldp_rem_portdesc": "1.0.8802.1.1.2.1.4.1.1.8",
    "lldp_rem_sysname": "1.0.8802.1.1.2.1.4.1.1.9",
    "stack_unit": "1.3.6.1.4.1.4526.11.13.7.2.1.2",
    "stack_tag": "1.3.6.1.4.1.4526.11.13.7.2.1.3",
    "stack_cfg_mode": "1.3.6.1.4.1.4526.11.13.7.2.1.4",
    "stack_run_mode": "1.3.6.1.4.1.4526.11.13.7.2.1.5",
    "stack_link": "1.3.6.1.4.1.4526.11.13.7.2.1.6",
    "stack_speed": "1.3.6.1.4.1.4526.11.13.7.2.1.7",
    "stack_errors": "1.3.6.1.4.1.4526.11.13.7.2.1.10",
}
STACK_MODE = {"1": "stack", "2": "ethernet"}


async def _walk_all(p):
    session = SnmpSession(p)
    try:
        await session.open()
        raw = {key: await session.walk(base) for key, base in TABLES.items()}
        raw["vlans"], _ = await read_vlans(session)
        raw["routing"] = await read_routing(session)
        return raw
    finally:
        session.close()


def _facts(raw):
    ifname = {int(k): v for k, v in raw["ifname"].items()}
    units = []
    for idx, cls in raw["ent_class"].items():
        if cls == "3":
            units.append(
                dict(
                    name=raw["ent_name"].get(idx, ""),
                    model=raw["ent_model"].get(idx, ""),
                    firmware=raw["ent_sw"].get(idx, ""),
                )
            )

    membership = collections.defaultdict(dict)
    for vid, v in raw["vlans"].items():
        for ifx in v["egress"]:
            membership[ifx][vid] = ifx not in v["untagged"]

    neighbors = collections.defaultdict(list)
    for suffix, sysname in raw["lldp_rem_sysname"].items():
        _, local_port, _ = suffix.split(".")
        neighbors[int(local_port)].append(dict(sysname=sysname, port=raw["lldp_rem_portdesc"].get(suffix, "")))

    ports = {}
    for ifx, name in sorted(ifname.items()):
        if name.lower().startswith(("vlan", "cpu")):
            continue
        k = str(ifx)
        lag = int(raw["lag_attached"].get(k, "0") or 0)
        mem = membership.get(ifx, {})
        ports[name] = dict(
            ifindex=ifx,
            alias=raw["ifalias"].get(k, ""),
            admin_up=raw["ifadmin"].get(k) == "1",
            link=raw["ifoper"].get(k) == "1",
            speed_mbps=int(raw["ifhighspeed"].get(k, "0") or 0),
            pvid=int(raw["pvid"][k]) if k in raw["pvid"] else None,
            untagged=sorted(v for v, tagged in mem.items() if not tagged),
            tagged=sorted(v for v, tagged in mem.items() if tagged),
            lag=ifname.get(lag) if lag and lag != ifx else None,
            lldp=neighbors.get(ifx, []),
        )

    by_unit_port = {}
    for name in ifname.values():
        unit, _, rest = name.partition("/")
        num = rest.lstrip("abcdefghijklmnopqrstuvwxyz")
        if unit.isdigit() and num.isdigit():
            by_unit_port[(unit, num)] = name
    stack_ports = {}
    for idx, unit in raw["stack_unit"].items():
        num = raw["stack_tag"].get(idx, "").rpartition("/")[2]
        stack_ports[by_unit_port.get((unit, num), "%s/%s" % (unit, num))] = dict(
            unit=int(unit),
            configured_mode=STACK_MODE.get(raw["stack_cfg_mode"].get(idx), "unknown"),
            running_mode=STACK_MODE.get(raw["stack_run_mode"].get(idx), "unknown"),
            link=raw["stack_link"].get(idx) == "1",
            speed_gbps=int(raw["stack_speed"].get(idx, "0") or 0),
            errors=int(raw["stack_errors"].get(idx, "0") or 0),
        )

    return dict(
        sysname=raw["sysname"].get("0", ""),
        sysdescr=raw["sysdescr"].get("0", ""),
        units=units,
        vlans={vid: raw["vlans"][vid]["name"] for vid in sorted(raw["vlans"])},
        ports=ports,
        routing=raw["routing"],
        stack_ports=stack_ports,
    )


def main():
    module = AnsibleModule(
        argument_spec=CONNECTION_ARGS,
        **CONNECTION_CONSTRAINTS,
        supports_check_mode=True,
    )
    if not HAS_PYSNMP:
        module.fail_json(msg="pysnmp >= 7 is required on the controller")
    try:
        raw = asyncio.run(_walk_all(module.params))
    except Exception as e:
        module.fail_json(msg="SNMP walk of %s failed: %s" % (module.params["host"], e))
    module.exit_json(changed=False, ansible_facts={"netgear_pro": _facts(raw)})


if __name__ == "__main__":
    main()
