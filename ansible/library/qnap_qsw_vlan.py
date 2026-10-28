#!/usr/bin/python

DOCUMENTATION = r"""
module: qnap_qsw_vlan
short_description: Reconcile VLAN membership on QNAP QSW switches (QSS firmware, REST API v1)
description:
  - Declarative per-port VLAN membership. A declared port is owned completely, memberships it does not list are removed.
  - Undeclared ports are left untouched in every VLAN.
  - QSS has no PVID setting; the untagged membership is the port's native/access VLAN, so a port gets at most one.
  - One write per changed VLAN, serialised with C(delay); the result is read back and compared.
requirements:
  - requests
options:
  host:
    description: Switch IP or hostname.
    required: true
    type: str
  username:
    description: Login user.
    type: str
    default: admin
  password:
    description: Login password.
    required: true
    type: str
  port:
    description: HTTPS port.
    type: int
    default: 443
  validate_certs:
    description: Verify the TLS certificate.
    type: bool
    default: false
  timeout:
    description: Per-request timeout in seconds.
    type: float
    default: 10.0
  delay:
    description: Pause between write requests in seconds (the backend forks per request).
    type: float
    default: 0.3
  vlans:
    description: VLAN IDs that must exist.
    required: true
    type: list
    elements: int
  ports:
    description:
      - Port name (as the switch reports it, e.g. C("3")) to C({untagged, tagged}).
      - C(untagged) is one VLAN ID or omitted, C(tagged) a list of VLAN IDs.
    type: dict
    default: {}
  purge_vlans:
    description: Delete VLANs present on the switch but not in C(vlans). VLAN 1 is never deleted.
    type: bool
    default: false
  mgmt_vlan:
    description: VLAN carrying the management IP.
    type: int
    default: 1
  protect_ports:
    description: Ports whose membership in C(mgmt_vlan) must not change (the path the controller reaches the switch on).
    type: list
    elements: str
    default: []
  save:
    description: Persist running to startup config after a change (C(PUT /system/save)).
    type: bool
    default: true
"""

EXAMPLES = r"""
- name: Reconcile VLANs
  qnap_qsw_vlan:
    host: "{{ ansible_host }}"
    password: "{{ qnap_password }}"
    vlans: [1, 101, 188]
    ports:
      "3": {untagged: 1, tagged: [101, 188]}
      "12": {untagged: 188, tagged: [101]}
    protect_ports: ["11"]
"""

import time

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.qnap_qss import HAS_REQUESTS, QssClient, QssError

if HAS_REQUESTS:
    import requests


def _port_key(p):
    return (0, int(p), "") if str(p).isdigit() else (1, 0, str(p))


def _fmt_ports(ports):
    nums = sorted(int(p) for p in ports if str(p).isdigit())
    parts = []
    for n in nums:
        if parts and parts[-1][1] == n - 1:
            parts[-1][1] = n
        else:
            parts.append([n, n])
    out = ["%d" % a if a == b else "%d-%d" % (a, b) for a, b in parts]
    out += sorted(str(p) for p in ports if not str(p).isdigit())
    return ",".join(out) or "-"


def _render(table):
    lines = []
    for vid in sorted(table, key=int):
        members = table[vid]
        tagged = [p for p, t in members.items() if t]
        untagged = [p for p, t in members.items() if not t]
        lines.append("VLAN %4s  tagged %-24s untagged %s" % (vid, _fmt_ports(tagged), _fmt_ports(untagged)))
    return "\n".join(lines) + "\n"


def _read_table(client):
    return {str(v["key"]): {str(m["Port"]): bool(m["Tagged"]) for m in v.get("val") or []} for v in client.get("vlan") or []}


def _desired_table(current, vlans, ports, purge):
    desired = {vid: dict(members) for vid, members in current.items()}
    for vid in vlans:
        desired.setdefault(vid, {})
    if purge:
        for vid in list(desired):
            if vid not in vlans and vid != "1":
                del desired[vid]
    for port, cfg in ports.items():
        for members in desired.values():
            members.pop(port, None)
        for vid in cfg["tagged"]:
            desired[vid][port] = True
        if cfg["untagged"] is not None:
            desired[cfg["untagged"]][port] = False
    return desired


def _normalize_ports(module, ports, vlans):
    out = {}
    for port, cfg in (ports or {}).items():
        cfg = cfg or {}
        unknown = set(cfg) - {"untagged", "tagged"}
        if unknown:
            module.fail_json(msg="port %s: unknown keys %s" % (port, sorted(unknown)))
        untagged = cfg.get("untagged")
        untagged = None if untagged is None else str(untagged)
        tagged = [str(v) for v in cfg.get("tagged") or []]
        for vid in tagged + ([untagged] if untagged else []):
            if vid not in vlans:
                module.fail_json(msg="port %s references VLAN %s, which is not in vlans" % (port, vid))
        if untagged in tagged:
            module.fail_json(msg="port %s: VLAN %s is both tagged and untagged" % (port, untagged))
        out[str(port)] = {"untagged": untagged, "tagged": tagged}
    return out


def main():
    module = AnsibleModule(
        argument_spec=dict(
            host=dict(type="str", required=True),
            username=dict(type="str", default="admin"),
            password=dict(type="str", required=True, no_log=True),
            port=dict(type="int", default=443),
            validate_certs=dict(type="bool", default=False),
            timeout=dict(type="float", default=10.0),
            delay=dict(type="float", default=0.3),
            vlans=dict(type="list", elements="int", required=True),
            ports=dict(type="dict", default={}),
            purge_vlans=dict(type="bool", default=False),
            mgmt_vlan=dict(type="int", default=1),
            protect_ports=dict(type="list", elements="str", default=[]),
            save=dict(type="bool", default=True),
        ),
        supports_check_mode=True,
    )
    if not HAS_REQUESTS:
        module.fail_json(msg="python requests is required on the controller")
    p = module.params
    vlans = [str(v) for v in p["vlans"]]
    ports = _normalize_ports(module, p["ports"], vlans)
    mgmt = str(p["mgmt_vlan"])

    client = QssClient(p["host"], p["port"], p["validate_certs"], p["timeout"])
    try:
        client.login(p["username"], p["password"])
    except (QssError, requests.RequestException) as e:
        module.fail_json(msg="login to %s failed: %s" % (p["host"], e))

    result = dict(changed=False)
    try:
        current = _read_table(client)
        desired = _desired_table(current, vlans, ports, p["purge_vlans"])

        for port in p["protect_ports"]:
            before = current.get(mgmt, {}).get(port)
            after = desired.get(mgmt, {}).get(port)
            if before != after:
                module.fail_json(
                    msg="refusing to change protected port %s in mgmt VLAN %s (%s -> %s)" % (port, mgmt, before, after)
                )

        to_create = sorted((v for v in desired if v not in current), key=int)
        to_delete = sorted((v for v in current if v not in desired), key=int)
        to_edit = sorted((v for v in desired if v in current and desired[v] != current[v]), key=int)
        unmanaged = sorted((v for v in current if v not in vlans and v in desired), key=int)

        result.update(
            created=to_create,
            edited=[v for v in to_create if desired[v]] + to_edit,
            deleted=to_delete,
            unmanaged_vlans=unmanaged,
        )
        if not (to_create or to_delete or to_edit):
            module.exit_json(**result)

        result["changed"] = True
        result["diff"] = dict(before=_render(current), after=_render(desired))
        if module.check_mode:
            module.exit_json(**result)

        def pause():
            if p["delay"]:
                time.sleep(p["delay"])

        def entry(vid):
            members = sorted(desired[vid].items(), key=lambda kv: _port_key(kv[0]))
            return {"key": vid, "val": [{"Port": port, "Tagged": tagged} for port, tagged in members]}

        for vid in to_create:
            client.post("vlan", {"data": [{"key": vid, "val": []}]})
            pause()
        for vid in [v for v in to_create if desired[v]] + to_edit:
            client.put("vlan", {"data": [entry(vid)]})
            pause()
        if to_delete:
            client.delete("vlan", {"idx": to_delete})
            pause()

        readback = _read_table(client)
        if readback != desired:
            result["diff"] = dict(before=_render(desired), after=_render(readback))
            module.fail_json(msg="switch did not apply the VLAN table as written (diff: wanted -> read back)", **result)

        if p["save"]:
            pause()
            client.put("system/save", {})
            result["saved"] = True
    except (QssError, requests.RequestException) as e:
        module.fail_json(msg=str(e), **result)
    finally:
        client.logout()

    module.exit_json(**result)


if __name__ == "__main__":
    main()
