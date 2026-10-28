#!/usr/bin/python

DOCUMENTATION = r"""
module: netgear_plus_vlan
short_description: Reconcile 802.1Q VLAN membership and PVIDs on Netgear Smart Managed Plus switches
description:
  - Same per-port schema as C(qnap_qsw_vlan). A declared port is owned completely, undeclared ports stay untouched.
  - C(untagged) sets the untagged membership and the PVID. Without it the PVID comes from C(pvid) or stays as is.
  - Writes go through the C(netgear_tool) SDK (web scraping).
  - Order is create, widened membership, PVIDs, final membership, delete; the firmware rejects a PVID outside the port's VLANs.
requirements:
  - netgear-tool
options:
  host:
    description: Switch IP or hostname.
    required: true
    type: str
  password:
    description: Login password.
    required: true
    type: str
  timeout:
    description: HTTP timeout in seconds.
    type: float
    default: 10.0
  vlans:
    description: VLAN IDs that must exist.
    required: true
    type: list
    elements: int
  ports:
    description: Port number to C({untagged, tagged, pvid}).
    type: dict
    default: {}
  purge_vlans:
    description: Delete VLANs present on the switch but not in C(vlans). VLAN 1 is never deleted.
    type: bool
    default: false
"""

EXAMPLES = r"""
- name: Reconcile VLANs
  netgear_plus_vlan:
    host: "{{ ansible_host }}"
    password: "{{ netgear_password }}"
    vlans: [1, 101]
    ports:
      1: {untagged: 1, tagged: [101]}
      2: {untagged: 101}
"""

import warnings

from ansible.module_utils.basic import AnsibleModule

try:
    from netgear_tool import make_switch

    HAS_SDK = True
except ImportError:
    HAS_SDK = False

UNTAGGED, TAGGED, NONE = "1", "2", "3"


def _render(memberships, pvids):
    lines = []
    for vid in sorted(memberships):
        mem = memberships[vid]
        tagged = [str(i + 1) for i, c in enumerate(mem) if c == TAGGED]
        untagged = [str(i + 1) for i, c in enumerate(mem) if c == UNTAGGED]
        lines.append("VLAN %4d  tagged %-12s untagged %s" % (vid, ",".join(tagged) or "-", ",".join(untagged) or "-"))
    lines.append("PVID       " + " ".join("%d:%d" % (p, pvids[p]) for p in sorted(pvids)))
    return "\n".join(lines) + "\n"


def _normalize_ports(module, ports, vlans, port_count):
    out = {}
    for port, cfg in (ports or {}).items():
        cfg = cfg or {}
        unknown = set(cfg) - {"untagged", "tagged", "pvid"}
        if unknown:
            module.fail_json(msg="port %s: unknown keys %s" % (port, sorted(unknown)))
        port = int(port)
        if not 1 <= port <= port_count:
            module.fail_json(msg="port %d outside 1..%d" % (port, port_count))
        untagged = cfg.get("untagged")
        untagged = None if untagged is None else int(untagged)
        tagged = [int(v) for v in cfg.get("tagged") or []]
        pvid = cfg.get("pvid")
        pvid = untagged if pvid is None else int(pvid)
        if untagged is not None and pvid != untagged:
            module.fail_json(msg="port %d: pvid %d differs from untagged VLAN %d" % (port, pvid, untagged))
        for vid in tagged + [v for v in (untagged, pvid) if v is not None]:
            if vid not in vlans:
                module.fail_json(msg="port %d references VLAN %d, which is not in vlans" % (port, vid))
        if untagged in tagged:
            module.fail_json(msg="port %d: VLAN %d is both tagged and untagged" % (port, untagged))
        if pvid is not None and pvid != untagged and pvid not in tagged:
            module.fail_json(msg="port %d: pvid %d is not a VLAN of this port" % (port, pvid))
        out[port] = {"untagged": untagged, "tagged": tagged, "pvid": pvid}
    return out


def _read(sw):
    memberships = {vid: sw.get_vlan_membership(vid) for vid in sw.get_vlan_ids()}
    return memberships, sw.get_port_pvids()


def main():
    module = AnsibleModule(
        argument_spec=dict(
            host=dict(type="str", required=True),
            password=dict(type="str", required=True, no_log=True),
            timeout=dict(type="float", default=10.0),
            vlans=dict(type="list", elements="int", required=True),
            ports=dict(type="dict", default={}),
            purge_vlans=dict(type="bool", default=False),
        ),
        supports_check_mode=True,
    )
    if not HAS_SDK:
        module.fail_json(msg="netgear-tool is required on the controller")
    p = module.params
    vlans = set(p["vlans"])

    result = dict(changed=False)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            sw = make_switch(p["host"], password=p["password"], timeout=p["timeout"])
        with sw:
            n = sw._port_count
            ports = _normalize_ports(module, p["ports"], vlans, n)
            if not sw.get_dot1q_enabled():
                module.fail_json(msg="802.1Q mode is disabled on the switch; enabling it is not this module's job")

            cur_mem, cur_pvid = _read(sw)
            want_mem = {vid: list(mem) for vid, mem in cur_mem.items()}
            for vid in vlans:
                want_mem.setdefault(vid, [NONE] * n)
            if p["purge_vlans"]:
                for vid in [v for v in want_mem if v not in vlans and v != 1]:
                    del want_mem[vid]
            want_pvid = dict(cur_pvid)
            for port, cfg in ports.items():
                for mem in want_mem.values():
                    mem[port - 1] = NONE
                for vid in cfg["tagged"]:
                    want_mem[vid][port - 1] = TAGGED
                if cfg["untagged"] is not None:
                    want_mem[cfg["untagged"]][port - 1] = UNTAGGED
                if cfg["pvid"] is not None:
                    want_pvid[port] = cfg["pvid"]
            want_mem = {vid: "".join(mem) for vid, mem in want_mem.items()}

            to_create = sorted(v for v in want_mem if v not in cur_mem)
            to_delete = sorted(v for v in cur_mem if v not in want_mem)
            to_edit = sorted(v for v in want_mem if want_mem[v] != cur_mem.get(v, NONE * n))
            pvid_changes = {port: pvid for port, pvid in want_pvid.items() if cur_pvid.get(port) != pvid}

            result.update(
                created=to_create,
                edited=to_edit,
                deleted=to_delete,
                pvids=pvid_changes,
                unmanaged_vlans=sorted(v for v in cur_mem if v not in vlans and v in want_mem),
            )
            if not (to_create or to_delete or to_edit or pvid_changes):
                module.exit_json(**result)

            result["changed"] = True
            result["diff"] = dict(before=_render(cur_mem, cur_pvid), after=_render(want_mem, want_pvid))
            if module.check_mode:
                module.exit_json(**result)

            for vid in to_create:
                sw.add_vlan(vid)
            for vid in to_edit:
                cur = cur_mem.get(vid, NONE * n)
                widened = "".join(w if w != NONE else c for w, c in zip(want_mem[vid], cur))
                if widened != cur:
                    sw.set_vlan_membership(vid, widened)
            for port, pvid in sorted(pvid_changes.items()):
                sw.set_port_pvid(port, pvid)
            for vid in to_edit:
                sw.set_vlan_membership(vid, want_mem[vid])
            for vid in to_delete:
                sw.delete_vlan(vid)

            got_mem, got_pvid = _read(sw)
            if got_mem != want_mem or got_pvid != want_pvid:
                result["diff"] = dict(before=_render(want_mem, want_pvid), after=_render(got_mem, got_pvid))
                module.fail_json(msg="switch did not apply the VLAN table as written (diff: wanted -> read back)", **result)
    except Exception as e:
        module.fail_json(msg="switch operation failed: %s" % e, **result)

    module.exit_json(**result)


if __name__ == "__main__":
    main()
