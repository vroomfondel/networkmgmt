#!/usr/bin/python
# Shebang /usr/bin/python: Ansible only rewrites this form via interpreter discovery.

DOCUMENTATION = r"""
---
module: qnap_qsw_facts
short_description: Reads QNAP QSW (QSS firmware) switch state via the private REST API
description:
  - Logs in to C(/api/v1/users/login), GETs a list of endpoints and returns each C(result) unchanged.
  - Endpoints the model does not offer are reported in C(qnap.unavailable) instead of failing the task.
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
    description: Login password (plaintext, base64-encoded by the module as the web UI does).
    required: true
    type: str
  port:
    description: HTTPS port.
    type: int
    default: 443
  validate_certs:
    description: Verify the TLS certificate (QSS ships a self-signed one).
    type: bool
    default: false
  timeout:
    description: Per-request timeout in seconds.
    type: float
    default: 10.0
  endpoints:
    description: Endpoints relative to C(/api/v1), each returned under its path with C(/) replaced by C(_).
    type: list
    elements: str
  delay:
    description:
      - Pause between endpoint requests in seconds, serialising the polling.
      - The backend forks C(/bin/ubus) per request; bursts drive the switch into fork ENOMEM.
    type: float
    default: 0.3
"""

EXAMPLES = r"""
- name: Gather QSW facts
  qnap_qsw_facts:
    host: "{{ ansible_host }}"
    password: "{{ qnap_password }}"
"""

import time

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.qnap_qss import HAS_REQUESTS, QssClient, QssError

if HAS_REQUESTS:
    import requests

DEFAULT_ENDPOINTS = [
    "system/board",
    "system/info",
    "system/sensor",
    "system/time",
    "firmware/info",
    "firmware/condition",
    "ports/status",
    "ports/statistics",
    "ports/config",
    "ports/transceiver",
    "vlan",
    "lacp/info",
    "lacp/group",
    "rstp",
    "igmp",
    "mirror",
    "loopProtect/state",
    "ip/ipv4/config",
    "qos/mode",
    "snmp/service",
]


def main():
    module = AnsibleModule(
        argument_spec=dict(
            host=dict(type="str", required=True),
            username=dict(type="str", default="admin"),
            password=dict(type="str", required=True, no_log=True),
            port=dict(type="int", default=443),
            validate_certs=dict(type="bool", default=False),
            timeout=dict(type="float", default=10.0),
            endpoints=dict(type="list", elements="str"),
            delay=dict(type="float", default=0.3),
        ),
        supports_check_mode=True,
    )
    if not HAS_REQUESTS:
        module.fail_json(msg="python requests is required on the controller")
    p = module.params

    client = QssClient(p["host"], p["port"], p["validate_certs"], p["timeout"])
    try:
        client.login(p["username"], p["password"])
    except (QssError, requests.RequestException) as e:
        module.fail_json(msg="login to %s failed: %s" % (p["host"], e))

    facts = {}
    unavailable = {}
    try:
        paths = p["endpoints"] or DEFAULT_ENDPOINTS
        for i, path in enumerate(paths):
            path = path.strip("/")
            try:
                facts[path.replace("/", "_")] = client.get(path)
            except (QssError, requests.RequestException) as e:
                unavailable[path] = str(e)
            if p["delay"] and i < len(paths) - 1:
                time.sleep(p["delay"])
    finally:
        client.logout()

    facts["unavailable"] = unavailable
    module.exit_json(changed=False, ansible_facts={"qnap": facts})


if __name__ == "__main__":
    main()
