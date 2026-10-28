#!/usr/bin/python

DOCUMENTATION = r"""
module: netgear_pro_mgmt
short_description: Set the management interface address of a Netgear Smart Managed Pro switch via SNMP
description:
  - Static management IP, mask and default gateway via NETGEAR-SWITCHING-MIB C(agentNetworkConfigGroup); the management VLAN stays as it is.
  - Three PDUs, gateway cleared first: the agent rejects a new address while the old gateway lies outside its subnet (commitFailed).
  - Refuses to run when C(host) is the current or the new management address, the change would cut the session; use a routing interface address.
  - The firmware rejects a VLAN routing interface in the management subnet, so moving the management IP away frees that subnet for routing.
  - Writes only the running config. Playbooks persist it with a C(netgear_pro_save) handler.
requirements:
  - pysnmp >= 7
options:
  host:
    description: Switch IP or hostname; must not be the management address itself.
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
    description: v2c community, used when C(snmp_user) is unset (needs a read-write community).
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
  address:
    description: Management address as a.b.c.d/len.
    required: true
    type: str
  gateway:
    description: Default gateway of the management interface; 0.0.0.0 for none.
    type: str
    default: 0.0.0.0
  settle_wait:
    description: Seconds to poll until the read-back state matches.
    type: float
    default: 30
"""

EXAMPLES = r"""
- name: Management address
  netgear_pro_mgmt:
    host: 192.0.2.38
    snmp_user: "{{ netgear_snmp_user }}"
    auth_key: "{{ netgear_snmp_auth_key }}"
    priv_key: "{{ netgear_snmp_priv_key }}"
    address: 198.51.100.38/24
"""

import asyncio
import ipaddress
import socket
import time

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.netgear_snmp import (
    CONNECTION_ARGS,
    CONNECTION_CONSTRAINTS,
    HAS_PYSNMP,
    SnmpError,
    SnmpSession,
    mask_to_prefix,
    prefix_to_mask,
)

try:
    from pysnmp.hlapi.v3arch.asyncio import IpAddress

    HAS_IPADDRESS = True
except ImportError:
    HAS_IPADDRESS = False

NETWORK = "1.3.6.1.4.1.4526.11.1.2.3"
MGMT_IP, MGMT_MASK, MGMT_GATEWAY, MGMT_PROTOCOL, MGMT_VLAN = ("%s.%d.0" % (NETWORK, i) for i in (1, 2, 3, 7, 11))
PROTOCOL_NONE = 1


class Fail(Exception):
    def __init__(self, msg, **result):
        super().__init__(msg)
        self.result = result


async def _read(s):
    ip, mask, gw = [(await s.get(oid)).prettyPrint() for oid in (MGMT_IP, MGMT_MASK, MGMT_GATEWAY)]
    return dict(address="%s/%d" % (ip, mask_to_prefix(mask)), gateway=gw), int(await s.get(MGMT_PROTOCOL)), int(await s.get(MGMT_VLAN))


def _render(state):
    return "address %s\ngateway %s\n" % (state["address"], state["gateway"])


async def _run(params, check_mode):
    try:
        iface = ipaddress.IPv4Interface(params["address"])
        gateway = str(ipaddress.IPv4Address(params["gateway"]))
    except ValueError as e:
        raise Fail("address must be a.b.c.d/len, gateway a.b.c.d (%s)" % e)
    want = dict(address=str(iface), gateway=gateway)
    if gateway != "0.0.0.0" and ipaddress.IPv4Address(gateway) not in iface.network:
        raise Fail("gateway %s is not in %s" % (gateway, iface.network))

    s = SnmpSession(params)
    result = dict(changed=False)
    try:
        await s.open()
        current, protocol, vlan = await _read(s)
        result.update(management_vlan=vlan, before=current)
        if current == want:
            return result
        if protocol != PROTOCOL_NONE:
            raise Fail("management address comes from bootp/dhcp (agentNetworkConfigProtocol=%d), set it to none first" % protocol, **result)
        host_ip = socket.gethostbyname(params["host"])
        if host_ip in (current["address"].split("/")[0], str(iface.ip)):
            raise Fail("refusing to change the management address over itself (%s), use a routing interface address as host" % host_ip, **result)

        result.update(changed=True, diff=dict(before=_render(current), after=_render(want)))
        if check_mode:
            return result
        # the agent commits one varbind at a time and rejects an address the current gateway is not in
        if current["gateway"] != "0.0.0.0":
            await s.set((MGMT_GATEWAY, IpAddress("0.0.0.0")))
        if current["address"] != want["address"]:
            await s.set((MGMT_IP, IpAddress(str(iface.ip))), (MGMT_MASK, IpAddress(prefix_to_mask(iface.network.prefixlen))))
        if gateway != "0.0.0.0":
            await s.set((MGMT_GATEWAY, IpAddress(gateway)))
        deadline = time.monotonic() + params["settle_wait"]
        while True:
            got = (await _read(s))[0]
            if got == want:
                return result
            if time.monotonic() > deadline:
                result["diff"] = dict(before=_render(want), after=_render(got))
                raise Fail("switch did not apply the management address within %ss (diff: wanted -> read back)" % params["settle_wait"], **result)
            await asyncio.sleep(1)
    except SnmpError as e:
        raise Fail(str(e), **result)
    finally:
        s.close()


def main():
    module = AnsibleModule(
        argument_spec=dict(
            CONNECTION_ARGS,
            address=dict(type="str", required=True),
            gateway=dict(type="str", default="0.0.0.0"),
            settle_wait=dict(type="float", default=30),
        ),
        supports_check_mode=True,
        **CONNECTION_CONSTRAINTS,
    )
    if not HAS_PYSNMP or not HAS_IPADDRESS:
        module.fail_json(msg="pysnmp >= 7 is required on the controller")
    try:
        result = asyncio.run(_run(module.params, module.check_mode))
    except Fail as e:
        module.fail_json(msg=str(e), **e.result)
    module.exit_json(**result)


if __name__ == "__main__":
    main()
