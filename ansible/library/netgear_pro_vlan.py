#!/usr/bin/python

DOCUMENTATION = r"""
module: netgear_pro_vlan
short_description: Reconcile 802.1Q VLANs, port membership and PVIDs on Netgear Smart Managed Pro switches via SNMP
description:
  - Writes Q-BRIDGE-MIB (dot1qVlanStaticTable, dot1qPvid) into the running config; C(save) or C(netgear_pro_save) persists it.
  - Every write is a single-varbind PDU (GS108T rejects or silently drops combined ones); VLANs missing from the static table are read from dot1qVlanCurrentTable.
  - Ports are keyed by ifName (C(1/g1), C(2/xg27)). A declared port is owned completely; undeclared ports stay untouched.
  - C(untagged) is a list; FASTPATH allows several untagged VLANs per port. C(pvid) is independent and left as is when omitted.
  - Every declared port must be a bridge port (stack links are not) in switchport mode general, the only mode where Q-BRIDGE membership is free-form.
  - Order is create, widened membership, PVIDs, final membership, delete, polled readback (writes land with a delay), optional save.
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
  vlans:
    description: VLAN ID to name. Every listed VLAN must exist with that name.
    required: true
    type: dict
  ports:
    description: ifName to C({pvid, untagged, tagged}).
    type: dict
    default: {}
  purge_vlans:
    description: Delete VLANs present on the switch but not in C(vlans). VLAN 1 is never deleted.
    type: bool
    default: false
  save:
    description: Persist running to startup config after a change. Playbooks notify a C(netgear_pro_save) handler instead.
    type: bool
    default: false
  settle_wait:
    description: Seconds to poll until the read-back table matches; FASTPATH applies sets and destroys with a delay.
    type: float
    default: 30
"""

EXAMPLES = r"""
- name: Reconcile stack VLANs
  netgear_pro_vlan:
    host: "{{ ansible_host }}"
    snmp_user: "{{ netgear_snmp_user }}"
    auth_key: "{{ netgear_snmp_auth_key }}"
    priv_key: "{{ netgear_snmp_priv_key }}"
    vlans: {1: Default, 101: lab}
    ports:
      "1/g22": {pvid: 101, untagged: [101]}
      "2/xg27": {pvid: 1, untagged: [1], tagged: [101]}
"""

import asyncio
import re
import time

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.netgear_snmp import (
    CONNECTION_ARGS,
    CONNECTION_CONSTRAINTS,
    HAS_PYSNMP,
    SnmpError,
    SnmpSession,
    integer,
    octets,
    portlist_encode,
    portlist_patch,
    read_vlans,
    save_config,
    unsigned,
)

IFNAME = "1.3.6.1.2.1.31.1.1.1.1"
BASE_PORT_IFINDEX = "1.3.6.1.2.1.17.1.4.1.2"
VLAN_NAME = "1.3.6.1.2.1.17.7.1.4.3.1.1"
VLAN_EGRESS = "1.3.6.1.2.1.17.7.1.4.3.1.2"
VLAN_UNTAGGED = "1.3.6.1.2.1.17.7.1.4.3.1.4"
VLAN_ROWSTATUS = "1.3.6.1.2.1.17.7.1.4.3.1.5"
PVID = "1.3.6.1.2.1.17.7.1.4.5.1.1"
SWITCHPORT_MODE = "1.3.6.1.4.1.4526.11.1.2.8.37.1.2"
CREATE_AND_GO, DESTROY = 4, 6
MODE_GENERAL = "3"


class Fail(Exception):
    def __init__(self, msg, **result):
        super().__init__(msg)
        self.result = result


def _fmt_ports(ifindexes, names):
    groups = {}
    for idx in sorted(ifindexes):
        m = re.match(r"^(\d+/[a-z]+)(\d+)$", names.get(idx, ""))
        key, num = (m.group(1), int(m.group(2))) if m else (names.get(idx, str(idx)), None)
        groups.setdefault(key, []).append(num)
    out = []
    for key, nums in groups.items():
        if nums == [None]:
            out.append(key)
            continue
        runs = []
        for n in nums:
            if runs and runs[-1][1] == n - 1:
                runs[-1][1] = n
            else:
                runs.append([n, n])
        out += ["%s%d" % (key, a) if a == b else "%s%d-%d" % (key, a, b) for a, b in runs]
    return ",".join(out) or "-"


def _render(state, names, pvid_ports):
    lines = []
    for vid in sorted(state["vlans"]):
        v = state["vlans"][vid]
        tagged = v["egress"] - v["untagged"]
        lines.append("VLAN %4d %-14s tagged %s  untagged %s" % (vid, v["name"], _fmt_ports(tagged, names), _fmt_ports(v["untagged"], names)))
    pv = ["%s:%s" % (names[i], state["pvid"].get(i)) for i in sorted(pvid_ports)]
    lines.append("PVID " + " ".join(pv))
    return "\n".join(lines) + "\n"


async def _read(s):
    vlans, length = await read_vlans(s)
    pvid = {int(k): int(v) for k, v in (await s.walk(PVID)).items()}
    return dict(vlans=vlans, pvid=pvid), length


def _normalize(params, ifindex):
    vlans = {int(k): str(v) for k, v in params["vlans"].items()}
    ports = {}
    for name, cfg in (params["ports"] or {}).items():
        cfg = cfg or {}
        unknown = set(cfg) - {"pvid", "untagged", "tagged"}
        if unknown:
            raise Fail("port %s: unknown keys %s" % (name, sorted(unknown)))
        if name not in ifindex:
            raise Fail("port %s does not exist on the switch" % name)
        untagged = {int(v) for v in cfg.get("untagged") or []}
        tagged = {int(v) for v in cfg.get("tagged") or []}
        pvid = cfg.get("pvid")
        pvid = None if pvid is None else int(pvid)
        for vid in untagged | tagged | ({pvid} if pvid else set()):
            if vid not in vlans:
                raise Fail("port %s references VLAN %d, which is not in vlans" % (name, vid))
        if untagged & tagged:
            raise Fail("port %s: VLANs %s are both tagged and untagged" % (name, sorted(untagged & tagged)))
        ports[ifindex[name]] = dict(name=name, untagged=untagged, tagged=tagged, pvid=pvid)
    return vlans, ports


def _desired(current, vlans, ports, purge):
    want = {vid: dict(name=v["name"], egress=set(v["egress"]), untagged=set(v["untagged"]), static=v["static"]) for vid, v in current["vlans"].items()}
    for vid, name in vlans.items():
        entry = want.setdefault(vid, dict(name=name, egress=set(), untagged=set(), static=True))
        # names of non-static (GS108T factory) VLANs are not readable via SNMP
        if entry["static"]:
            entry["name"] = name
    if purge:
        for vid in [v for v in want if v not in vlans and v != 1]:
            del want[vid]
    pvid = dict(current["pvid"])
    for idx, cfg in ports.items():
        for v in want.values():
            v["egress"].discard(idx)
            v["untagged"].discard(idx)
        for vid in cfg["tagged"]:
            want[vid]["egress"].add(idx)
        for vid in cfg["untagged"]:
            want[vid]["egress"].add(idx)
            want[vid]["untagged"].add(idx)
        if cfg["pvid"] is not None:
            pvid[idx] = cfg["pvid"]
    return dict(vlans=want, pvid=pvid)


async def _run(params, check_mode):
    s = SnmpSession(params)
    result = dict(changed=False)
    try:
        await s.open()
        names = {int(k): v for k, v in (await s.walk(IFNAME)).items()}
        ifindex = {v: k for k, v in names.items()}
        vlans, ports = _normalize(params, ifindex)

        bridge_ports = {int(v) for v in (await s.walk(BASE_PORT_IFINDEX)).values()}
        not_bridged = [ports[i]["name"] for i in sorted(ports) if i not in bridge_ports]
        if not_bridged:
            raise Fail("not bridge ports (stack links?): %s" % ", ".join(not_bridged))

        modes = await s.walk(SWITCHPORT_MODE)
        wrong = [ports[i]["name"] for i in sorted(ports) if modes.get(str(i), MODE_GENERAL) != MODE_GENERAL]
        if wrong:
            raise Fail("ports not in switchport mode general: %s" % ", ".join(wrong))

        current, length = await _read(s)
        want = _desired(current, vlans, ports, params["purge_vlans"])

        cur_v, want_v = current["vlans"], want["vlans"]
        to_create = sorted(v for v in want_v if v not in cur_v)
        to_delete = sorted(v for v in cur_v if v not in want_v)
        to_rename = sorted(v for v in want_v if v in cur_v and want_v[v]["name"] != cur_v[v]["name"])
        to_edit = sorted(
            v for v in want_v if (want_v[v]["egress"], want_v[v]["untagged"]) != (cur_v.get(v, {}).get("egress", set()), cur_v.get(v, {}).get("untagged", set()))
        )
        pvid_changes = {i: want["pvid"][i] for i in ports if want["pvid"].get(i) != current["pvid"].get(i)}

        result.update(
            created=to_create,
            deleted=to_delete,
            renamed=to_rename,
            edited=to_edit,
            pvids={names[i]: p for i, p in sorted(pvid_changes.items())},
            unmanaged_vlans=sorted(v for v in cur_v if v not in vlans and v in want_v),
        )
        if not (to_create or to_delete or to_rename or to_edit or pvid_changes):
            return result

        pvid_ports = set(ports)
        result["changed"] = True
        result["diff"] = dict(before=_render(current, names, pvid_ports), after=_render(want, names, pvid_ports))
        if check_mode:
            return result

        raw_untagged = {vid: v["untagged_raw"] for vid, v in cur_v.items()}

        # GS108T drops the egress list when both go in one PDU
        async def set_members(vid, egress, untagged):
            cur = cur_v.get(vid, dict(egress=set(), untagged=set()))
            moved = {i for i in egress | untagged | cur["egress"] | cur["untagged"] if (i in egress, i in untagged) != (i in cur["egress"], i in cur["untagged"])}
            await s.set(("%s.%d" % (VLAN_EGRESS, vid), octets(portlist_encode(egress, length))))
            # GS108T sets untagged bits of non-members (incl. CPU port) and refuses edits once one is cleared
            bits = portlist_patch(raw_untagged.get(vid, b""), length, {i: i in untagged for i in moved})
            await s.set(("%s.%d" % (VLAN_UNTAGGED, vid), octets(bits)))

        for vid in to_create:
            # GS108T answers commitFailed to createAndGo + name in one PDU
            await s.set(("%s.%d" % (VLAN_ROWSTATUS, vid), integer(CREATE_AND_GO)))
            await s.set(("%s.%d" % (VLAN_NAME, vid), octets(want_v[vid]["name"])))
        if to_create:
            fresh, _ = await read_vlans(s)
            raw_untagged.update({vid: fresh[vid]["untagged_raw"] for vid in to_create if vid in fresh})
        for vid in to_rename:
            await s.set(("%s.%d" % (VLAN_NAME, vid), octets(want_v[vid]["name"])))
        for vid in to_edit:
            cur = cur_v.get(vid, dict(egress=set(), untagged=set()))
            egress = cur["egress"] | want_v[vid]["egress"]
            untagged = cur["untagged"] | want_v[vid]["untagged"]
            if (egress, untagged) != (cur["egress"], cur["untagged"]):
                await set_members(vid, egress, untagged)
        for idx, pvid in sorted(pvid_changes.items()):
            await s.set(("%s.%d" % (PVID, idx), unsigned(pvid)))
        for vid in to_edit:
            await set_members(vid, want_v[vid]["egress"], want_v[vid]["untagged"])
        for vid in to_delete:
            await s.set(("%s.%d" % (VLAN_ROWSTATUS, vid), integer(DESTROY)))
        want_cmp = {v: (d["name"], d["egress"], d["untagged"]) for v, d in want_v.items()}
        deadline = time.monotonic() + params["settle_wait"]
        while True:
            got, _ = await _read(s)
            got_cmp = {v: (d["name"], d["egress"], d["untagged"]) for v, d in got["vlans"].items()}
            if got_cmp == want_cmp and all(got["pvid"].get(i) == want["pvid"].get(i) for i in ports):
                break
            if time.monotonic() > deadline:
                result["diff"] = dict(before=_render(want, names, pvid_ports), after=_render(got, names, pvid_ports))
                raise Fail("switch did not apply the VLAN table within %ss (diff: wanted -> read back)" % params["settle_wait"], **result)
            await asyncio.sleep(1)

        if params["save"]:
            await save_config(s)
            result["saved"] = True
        return result
    except SnmpError as e:
        raise Fail(str(e), **result)
    finally:
        s.close()


def main():
    module = AnsibleModule(
        argument_spec=dict(
            CONNECTION_ARGS,
            vlans=dict(type="dict", required=True),
            ports=dict(type="dict", default={}),
            purge_vlans=dict(type="bool", default=False),
            save=dict(type="bool", default=False),
            settle_wait=dict(type="float", default=30),
        ),
        supports_check_mode=True,
        **CONNECTION_CONSTRAINTS,
    )
    if not HAS_PYSNMP:
        module.fail_json(msg="pysnmp >= 7 is required on the controller")
    try:
        result = asyncio.run(_run(module.params, module.check_mode))
    except Fail as e:
        module.fail_json(msg=str(e), **e.result)
    module.exit_json(**result)


if __name__ == "__main__":
    main()
