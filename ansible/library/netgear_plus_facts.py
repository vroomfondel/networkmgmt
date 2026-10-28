#!/usr/bin/python

DOCUMENTATION = r"""
module: netgear_plus_facts
short_description: Read system, port and VLAN state from Netgear Smart Managed Plus switches
description:
  - Returns C(netgear) facts via the C(netgear_tool) SDK. A getter the model lacks lands in C(netgear.unavailable).
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
"""

EXAMPLES = r"""
- name: Gather switch facts
  netgear_plus_facts:
    host: "{{ ansible_host }}"
    password: "{{ netgear_password }}"
"""

import dataclasses
import enum
import warnings

from ansible.module_utils.basic import AnsibleModule

try:
    from netgear_tool import make_switch

    HAS_SDK = True
except ImportError:
    HAS_SDK = False

GETTERS = [
    ("system", "get_system_info"),
    ("config", "get_switch_config"),
    ("ports", "get_port_settings"),
    ("port_stats", "get_port_stats"),
    ("rate_limits", "get_rate_limits"),
    ("mirror", "get_mirror_config"),
    ("igmp", "get_igmp_config"),
    ("loop_detection", "get_loop_detection"),
    ("broadcast_filter", "get_broadcast_filter"),
    ("qos_mode", "get_qos_mode"),
]


def _plain(value):
    if dataclasses.is_dataclass(value):
        return {f.name: _plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, enum.Enum):
        return str(value)
    if isinstance(value, (list, tuple, set)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    return value


def main():
    module = AnsibleModule(
        argument_spec=dict(
            host=dict(type="str", required=True),
            password=dict(type="str", required=True, no_log=True),
            timeout=dict(type="float", default=10.0),
        ),
        supports_check_mode=True,
    )
    if not HAS_SDK:
        module.fail_json(msg="netgear-tool is required on the controller")
    p = module.params

    facts = {}
    unavailable = {}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            sw = make_switch(p["host"], password=p["password"], timeout=p["timeout"])
        with sw:
            for key, getter in GETTERS:
                try:
                    facts[key] = _plain(getattr(sw, getter)())
                except Exception as e:
                    unavailable[key] = str(e)
            vlan = {"dot1q_enabled": sw.get_dot1q_enabled(), "vlans": [], "pvids": {}}
            if vlan["dot1q_enabled"]:
                for vid in sw.get_vlan_ids():
                    mem = sw.get_vlan_membership(vid)
                    vlan["vlans"].append(
                        {
                            "vid": vid,
                            "membership": mem,
                            "untagged_ports": [i + 1 for i, c in enumerate(mem) if c == "1"],
                            "tagged_ports": [i + 1 for i, c in enumerate(mem) if c == "2"],
                        }
                    )
                vlan["pvids"] = {str(k): v for k, v in sw.get_port_pvids().items()}
            facts["vlan"] = vlan
    except Exception as e:
        module.fail_json(msg="switch operation failed: %s" % e)

    facts["unavailable"] = unavailable
    module.exit_json(changed=False, ansible_facts={"netgear": facts})


if __name__ == "__main__":
    main()
