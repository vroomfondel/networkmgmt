#!/usr/bin/python

DOCUMENTATION = r"""
module: netgear_pro_routing
short_description: Reconcile IPv4 VLAN routing and static routes on Netgear Smart Managed Pro switches
description:
  - Global routing mode, VLAN routing interfaces and per-interface address/routing/MTU via NETGEAR-ROUTING-MIB.
  - Static routes via the web UI (Route Configuration page), because inetCidrRouteTable writes fail with commitFailed on the S3300.
  - Routes of type Static and Default (C(dest: 0.0.0.0/0)) are managed; the web UI is not touched unless C(static_routes) or C(purge_static_routes) is set.
  - Declared VLAN interfaces are owned completely; undeclared ones stay unless C(purge_interfaces).
  - The interface carrying C(host) is protected; changing or removing it would cut the management session.
  - Writes only the running config. Playbooks persist it with a C(netgear_pro_save) handler.
requirements:
  - pysnmp >= 7
  - requests (static routes only)
options:
  host:
    description: Switch IP or hostname. Its routing interface is never changed.
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
  password:
    description: Web UI password, required for static routes. The switch allows only 4 web sessions; the module logs out again.
    type: str
  enabled:
    description: Global IPv4 routing mode.
    type: bool
    default: true
  interfaces:
    description: VLAN ID to C({address: a.b.c.d/len, routing: bool (default true), mtu: int (default 1500)}).
    type: dict
    default: {}
  static_routes:
    description: List of C({dest: a.b.c.d/len, next_hop: a.b.c.d, preference: 1-255 (default 1), description: str (default "")}).
    type: list
    elements: dict
    default: []
  purge_interfaces:
    description: Remove VLAN routing interfaces not in C(interfaces).
    type: bool
    default: false
  purge_static_routes:
    description: Remove static routes not in C(static_routes).
    type: bool
    default: false
  settle_wait:
    description: Seconds to poll until the read-back state matches.
    type: float
    default: 30
  web_wait:
    description:
      - Seconds to wait for a free web session slot before the web login, polling agentLoginSessionTable every 10 seconds.
      - Sessions are only counted, never disconnected. On expiry the module fails and names the sessions holding the slots.
    type: int
    default: 120
  web_soft_timeout:
    description:
      - Web session idle timeout in minutes (agentHTTPSoftTimeout), 1-60.
      - Unset leaves the switch value alone. Applied via SNMP before the web part, independent of C(static_routes).
    type: int
"""

EXAMPLES = r"""
- name: Reconcile stack routing
  netgear_pro_routing:
    host: "{{ ansible_host }}"
    snmp_user: "{{ netgear_snmp_user }}"
    auth_key: "{{ netgear_snmp_auth_key }}"
    priv_key: "{{ netgear_snmp_priv_key }}"
    password: "{{ netgear_password }}"
    interfaces:
      101: {address: 192.0.2.38/24}
    static_routes:
      - {dest: 203.0.113.0/24, next_hop: 192.0.2.1, description: lab}
    web_wait: 300
    web_soft_timeout: 2
"""

RETURN = r"""
web_soft_timeout:
  description: Web session idle timeout in minutes after the run (only when C(web_soft_timeout) is set).
  type: int
  returned: when web_soft_timeout is set
web_sessions:
  description: Web (http/https) sessions of C({user, address, type}) seen when the module waited for a free slot.
  type: list
  elements: dict
  returned: when the module had to wait
"""

import asyncio
import ipaddress
import re
import socket
import time

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.netgear_snmp import (
    CONNECTION_ARGS,
    CONNECTION_CONSTRAINTS,
    HAS_PYSNMP,
    HTTP_MAX_SESSIONS,
    HTTP_SOFT_TIMEOUT,
    IP_IF_ADDRESS,
    IP_IF_MTU,
    IP_IF_NETMASK,
    IP_IF_ROUTING,
    IP_VLAN_IFINDEX,
    IP_VLAN_STATUS,
    ROUTING_MODE,
    SnmpError,
    SnmpSession,
    integer,
    prefix_to_mask,
    read_routing,
    read_web_sessions,
    unsigned,
)
from ansible.module_utils.netgear_web import HAS_REQUESTS, NetgearWeb, WebError

try:
    from pysnmp.hlapi.v3arch.asyncio import IpAddress

    HAS_IPADDRESS = True
except ImportError:
    HAS_IPADDRESS = False

CREATE_AND_GO, DESTROY = 4, 6
ENABLE, DISABLE = 1, 2
DESCRIPTION_RE = re.compile(r"^[A-Za-z0-9_-]{0,31}$")


class Fail(Exception):
    def __init__(self, msg, **result):
        super().__init__(msg)
        self.result = result


def _normalize(params):
    interfaces = {}
    for vid, cfg in (params["interfaces"] or {}).items():
        cfg = cfg or {}
        unknown = set(cfg) - {"address", "routing", "mtu"}
        if unknown:
            raise Fail("interface %s: unknown keys %s" % (vid, sorted(unknown)))
        try:
            iface = ipaddress.IPv4Interface(cfg["address"])
        except (KeyError, ValueError) as e:
            raise Fail("interface %s: address must be a.b.c.d/len (%s)" % (vid, e))
        interfaces[int(vid)] = dict(address=str(iface), routing=bool(cfg.get("routing", True)), mtu=int(cfg.get("mtu", 1500)))
    routes = {}
    for r in params["static_routes"] or []:
        unknown = set(r) - {"dest", "next_hop", "preference", "description"}
        if unknown:
            raise Fail("static route %s: unknown keys %s" % (r, sorted(unknown)))
        try:
            net = ipaddress.IPv4Network(r["dest"])
            nh = ipaddress.IPv4Address(r["next_hop"])
        except (KeyError, ValueError) as e:
            raise Fail("static route %s: %s" % (r, e))
        pref, desc = int(r.get("preference", 1)), str(r.get("description", ""))
        if not 1 <= pref <= 255 or not DESCRIPTION_RE.match(desc):
            raise Fail("static route %s: preference must be 1-255, description up to 31 of [A-Za-z0-9_-]" % r)
        routes[(str(net), str(nh))] = dict(preference=pref, description=desc)
    return interfaces, routes


def _render(state):
    lines = ["routing %s" % ("enabled" if state["enabled"] else "disabled")]
    for vid in sorted(state["interfaces"]):
        i = state["interfaces"][vid]
        lines.append("vlan %4d  %-18s routing %-3s mtu %d" % (vid, i["address"] or "-", "on" if i["routing"] else "off", i["mtu"]))
    for (dest, nh), r in sorted(state["routes"].items()):
        lines.append("route %-18s via %-15s preference %d %s" % (dest, nh, r["preference"], r["description"]))
    return "\n".join(lines) + "\n"


def _state(raw, web_routes):
    return dict(
        enabled=raw["enabled"],
        interfaces={vid: dict(address=i["address"], routing=i["routing"], mtu=i["mtu"]) for vid, i in raw["interfaces"].items()},
        routes={(r["dest"], r["next_hop"]): dict(preference=r["preference"], description=r["description"]) for r in web_routes},
    )


async def _wait_web_slot(s, wait, result):
    deadline = time.monotonic() + wait
    waited = False
    while True:
        limit = int(await s.get(HTTP_MAX_SESSIONS))
        sessions = await read_web_sessions(s)
        if len(sessions) < limit:
            return
        if waited is False:
            result["web_sessions"] = sessions
        waited = True
        if time.monotonic() + 10 > deadline:
            held = ", ".join("%s from %s (%s)" % (x["user"], x["address"], x["type"]) for x in sessions)
            raise Fail("no free web session slot after %ss, %d of %d taken: %s" % (wait, len(sessions), limit, held), **result)
        await asyncio.sleep(10)


async def _run(params, check_mode):
    s = SnmpSession(params)
    web = None
    result = dict(changed=False)
    try:
        await s.open()
        interfaces, routes = _normalize(params)
        want_timeout = params["web_soft_timeout"]
        timeout_diff = ""
        if want_timeout is not None:
            if not 1 <= want_timeout <= 60:
                raise Fail("web_soft_timeout must be 1-60 minutes")
            have_timeout = int(await s.get(HTTP_SOFT_TIMEOUT))
            result["web_soft_timeout"] = have_timeout
            if have_timeout != want_timeout:
                result["changed"] = True
                timeout_diff = ("web soft timeout %d min\n" % have_timeout, "web soft timeout %d min\n" % want_timeout)
                if not check_mode:
                    await s.set((HTTP_SOFT_TIMEOUT, integer(want_timeout)))
                    result["web_soft_timeout"] = int(await s.get(HTTP_SOFT_TIMEOUT))
                    if result["web_soft_timeout"] != want_timeout:
                        raise Fail("switch did not apply web_soft_timeout (read back %d)" % result["web_soft_timeout"], **result)
        if routes or params["purge_static_routes"]:
            if not params["password"]:
                raise Fail("static routes need the web UI password (password)")
            await _wait_web_slot(s, params["web_wait"], result)
            web = NetgearWeb(params["host"], params["password"])
            web.login()
        raw = await read_routing(s)
        current = _state(raw, web.read_static_routes() if web else [])
        if web:
            result["static_routes"] = [dict(dest=d, next_hop=n, **r) for (d, n), r in sorted(current["routes"].items())]

        want = dict(enabled=params["enabled"], interfaces=dict(current["interfaces"]), routes=dict(current["routes"]))
        want["interfaces"].update(interfaces)
        if params["purge_interfaces"]:
            want["interfaces"] = {v: i for v, i in want["interfaces"].items() if v in interfaces}
        want["routes"].update(routes)
        if params["purge_static_routes"]:
            want["routes"] = {k: m for k, m in want["routes"].items() if k in routes}

        mgmt_ip = socket.gethostbyname(params["host"])
        for vid, i in current["interfaces"].items():
            if i["address"] and i["address"].split("/")[0] == mgmt_ip and want["interfaces"].get(vid) != i:
                raise Fail("refusing to change VLAN %d routing interface %s, it carries the management address" % (vid, i["address"]))
        if not want["enabled"] and current["enabled"]:
            raise Fail("refusing to disable routing globally from a remote session")

        cur_if, want_if = current["interfaces"], want["interfaces"]
        if_create = sorted(v for v in want_if if v not in cur_if)
        if_delete = sorted(v for v in cur_if if v not in want_if)
        if_edit = sorted(v for v in want_if if v in cur_if and want_if[v] != cur_if[v])
        rt_add = sorted(k for k in want["routes"] if current["routes"].get(k) != want["routes"][k])
        rt_delete = sorted(k for k in current["routes"] if current["routes"][k] != want["routes"].get(k))

        result.update(
            routing_enabled=want["enabled"],
            interfaces_created=if_create,
            interfaces_deleted=if_delete,
            interfaces_edited=if_edit,
            routes_added=["%s via %s" % k for k in rt_add],
            routes_deleted=["%s via %s" % k for k in rt_delete],
        )
        if not (if_create or if_delete or if_edit or rt_add or rt_delete or want["enabled"] != current["enabled"]):
            if timeout_diff:
                result["diff"] = dict(before=timeout_diff[0], after=timeout_diff[1])
            return result

        result["changed"] = True
        pre = timeout_diff or ("", "")
        result["diff"] = dict(before=pre[0] + _render(current), after=pre[1] + _render(want))
        if check_mode:
            return result

        if want["enabled"] and not current["enabled"]:
            await s.set((ROUTING_MODE, integer(ENABLE)))

        # routes before interfaces, interfaces before creates: a route must not outlive its interface, an address may move VLANs
        for dest, nh in rt_delete:
            web.delete_static_route(dest, nh)
        for vid in if_delete:
            await s.set(("%s.%d" % (IP_VLAN_STATUS, vid), integer(DESTROY)))

        for vid in if_create:
            await s.set(("%s.%d" % (IP_VLAN_STATUS, vid), integer(CREATE_AND_GO)))
        ifindex = {vid: i["ifindex"] for vid, i in raw["interfaces"].items()}
        deadline = time.monotonic() + params["settle_wait"]
        while if_create:
            found = {int(k): int(v) for k, v in (await s.walk(IP_VLAN_IFINDEX)).items()}
            if all(v in found for v in if_create):
                ifindex.update(found)
                break
            if time.monotonic() > deadline:
                raise Fail("VLAN routing interfaces %s did not appear" % [v for v in if_create if v not in found], **result)
            await asyncio.sleep(1)

        # edits before creates: an address may move from an existing interface to a new one
        for vid in if_edit + if_create:
            idx, w, c = ifindex[vid], want_if[vid], cur_if.get(vid, {})
            if w["address"] != c.get("address"):
                iface = ipaddress.IPv4Interface(w["address"])
                await s.set(
                    ("%s.%d" % (IP_IF_ADDRESS, idx), IpAddress(str(iface.ip))),
                    ("%s.%d" % (IP_IF_NETMASK, idx), IpAddress(prefix_to_mask(iface.network.prefixlen))),
                )
            if w["routing"] != c.get("routing"):
                await s.set(("%s.%d" % (IP_IF_ROUTING, idx), integer(ENABLE if w["routing"] else DISABLE)))
            if w["mtu"] != c.get("mtu"):
                await s.set(("%s.%d" % (IP_IF_MTU, idx), unsigned(w["mtu"])))

        for dest, nh in rt_add:
            web.add_static_route(dest, nh, **want["routes"][(dest, nh)])

        deadline = time.monotonic() + params["settle_wait"]
        while True:
            got = _state(await read_routing(s), web.read_static_routes() if web else [])
            if got == want:
                break
            if time.monotonic() > deadline:
                result["diff"] = dict(before=_render(want), after=_render(got))
                raise Fail("switch did not apply the routing config within %ss (diff: wanted -> read back)" % params["settle_wait"], **result)
            await asyncio.sleep(1)
        return result
    except (SnmpError, WebError) as e:
        raise Fail(str(e), **result)
    finally:
        s.close()
        if web:
            web.close()


def main():
    module = AnsibleModule(
        argument_spec=dict(
            CONNECTION_ARGS,
            password=dict(type="str", no_log=True),
            enabled=dict(type="bool", default=True),
            interfaces=dict(type="dict", default={}),
            static_routes=dict(type="list", elements="dict", default=[]),
            purge_interfaces=dict(type="bool", default=False),
            purge_static_routes=dict(type="bool", default=False),
            settle_wait=dict(type="float", default=30),
            web_wait=dict(type="int", default=120),
            web_soft_timeout=dict(type="int"),
        ),
        supports_check_mode=True,
        **CONNECTION_CONSTRAINTS,
    )
    if not HAS_PYSNMP or not HAS_IPADDRESS:
        module.fail_json(msg="pysnmp >= 7 is required on the controller")
    if not HAS_REQUESTS and (module.params["static_routes"] or module.params["purge_static_routes"]):
        module.fail_json(msg="requests is required on the controller for static routes")
    try:
        result = asyncio.run(_run(module.params, module.check_mode))
    except Fail as e:
        module.fail_json(msg=str(e), **e.result)
    module.exit_json(**result)


if __name__ == "__main__":
    main()
