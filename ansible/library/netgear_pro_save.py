#!/usr/bin/python

DOCUMENTATION = r"""
module: netgear_pro_save
short_description: Persist the running config of a Netgear Smart Managed Pro switch via SNMP
description:
  - Sets NETGEAR-SWITCHING-MIB C(agentSaveConfig) and polls C(agentSaveConfigStatus) until the save completes.
  - Meant as a handler notified by the config tasks, so a play saves once at its end and a failed play saves nothing.
  - Always reports changed; in check mode nothing is written.
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
  save_timeout:
    description: Seconds to wait for agentSaveConfigStatus to report savingComplete.
    type: float
    default: 60
"""

EXAMPLES = r"""
- name: Save Netgear Pro config
  netgear_pro_save:
    host: "{{ ansible_host }}"
    snmp_user: "{{ netgear_snmp_user }}"
    auth_key: "{{ netgear_snmp_auth_key }}"
    priv_key: "{{ netgear_snmp_priv_key }}"
"""

import asyncio

from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.netgear_snmp import (
    CONNECTION_ARGS,
    CONNECTION_CONSTRAINTS,
    HAS_PYSNMP,
    SnmpError,
    SnmpSession,
    save_config,
)


async def _run(params):
    s = SnmpSession(params)
    try:
        await s.open()
        await save_config(s, params["save_timeout"])
    finally:
        s.close()


def main():
    module = AnsibleModule(
        argument_spec=dict(CONNECTION_ARGS, save_timeout=dict(type="float", default=60)),
        supports_check_mode=True,
        **CONNECTION_CONSTRAINTS,
    )
    if not HAS_PYSNMP:
        module.fail_json(msg="pysnmp >= 7 is required on the controller")
    if module.check_mode:
        module.exit_json(changed=True)
    try:
        asyncio.run(_run(module.params))
    except SnmpError as e:
        module.fail_json(msg=str(e))
    module.exit_json(changed=True, saved=True)


if __name__ == "__main__":
    main()
