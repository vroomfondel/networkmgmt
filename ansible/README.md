# Ansible modules for managed switches

Stand-alone Ansible modules (no collection packaging) for three families of managed switches. They were developed against real hardware and run in production playbooks; the declarative modules (`*_vlan`, `netgear_pro_routing`, `netgear_pro_mgmt`) read the current state, compute a diff, write only what differs, and read the result back.

| Family                               | Models tested                           | Transport                                          | Modules                                                                                                |
|--------------------------------------|-----------------------------------------|----------------------------------------------------|--------------------------------------------------------------------------------------------------------|
| Netgear Smart Managed Plus           | GS305E (GS108E and similar Plus models) | Web UI scraping via the `netgear-tool` SDK         | `netgear_plus_facts`, `netgear_plus_vlan`                                                              |
| Netgear Smart Managed Pro (FASTPATH) | S3300 stack (28X/52X), GS108T v2        | SNMPv3 via `pysnmp`; web UI for static routes only | `netgear_pro_facts`, `netgear_pro_vlan`, `netgear_pro_routing`, `netgear_pro_mgmt`, `netgear_pro_save` |
| QNAP QSW (QSS firmware)              | QSW-M408 series                         | Private REST API `/api/v1` via `requests`          | `qnap_qsw_facts`, `qnap_qsw_vlan`                                                                      |

All modules run on the controller, not on the switch: the switches have no Python and no SSH. Use `connection: local` and set `ansible_python_interpreter: "{{ ansible_playbook_python }}"` so the modules see the pip packages installed next to Ansible.

Example material lives in `examples/` (inventory, playbooks, `host_vars`). All addresses are documentation addresses (RFC 5737) and all names are placeholders.

## Layout

```
ansible/
  ansible.cfg            library/module_utils paths relative to this directory
  library/               the modules
  module_utils/          shared clients (netgear_snmp, netgear_web, qnap_qss)
  examples/
    inventory.yml
    netgear_switches.yml
    qnap_switches.yml
    host_vars/<switch>.yml
```

## Installation

Install the dependencies of the families you use into the Python environment that runs `ansible-playbook`.

| Family       | pip packages            |
|--------------|-------------------------|
| Netgear Plus | `netgear-tool`          |
| Netgear Pro  | `pysnmp>=7`, `requests` |
| QNAP QSW     | `requests`              |

`requests` is needed by the Pro family only for static routes (`netgear_pro_routing`).

```bash
python -m venv .venv && . .venv/bin/activate
pip install ansible netgear-tool 'pysnmp>=7' requests
```

Point Ansible at the modules, either with the `ansible.cfg` in this directory (run Ansible from here)

```ini
[defaults]
library = ./library
module_utils = ./module_utils
```

or with environment variables, which also work from any other directory:

```bash
export ANSIBLE_LIBRARY=/path/to/networkmgmt/ansible/library
export ANSIBLE_MODULE_UTILS=/path/to/networkmgmt/ansible/module_utils
```

Both are required. `module_utils` is not searched next to `library`; without it the modules fail with `Cannot resolve ...` (for example `ansible.module_utils.netgear_snmp`). This bites ad-hoc calls most often:

```bash
export ANSIBLE_LIBRARY=$PWD/library ANSIBLE_MODULE_UTILS=$PWD/module_utils
ansible s3300-stack -i examples/inventory.yml -c local -m netgear_pro_save \
  -a 'host=192.0.2.38 snmp_user=snmpadmin auth_key=... priv_key=...' \
  -e ansible_python_interpreter=$(which python)
```

Run the example playbooks:

```bash
ansible-playbook -i examples/inventory.yml examples/netgear_switches.yml --limit s3300-stack --tags netgear_facts
ansible-playbook -i examples/inventory.yml examples/netgear_switches.yml --check --diff
ansible-playbook -i examples/inventory.yml examples/qnap_switches.yml --tags qnap_vlan
```

## Inventory schema

Keep switches in their own inventory file. A play on `all` must never reach them.

Groups used by the example playbooks:

| Group                   | Purpose                                                 |
|-------------------------|---------------------------------------------------------|
| `netgear_plus_switches` | Plus models; the first play of `netgear_switches.yml`   |
| `netgear_pro_switches`  | Pro models (S3300 stack, GS108T); the second play, SNMP |
| `qnap_switches`         | QNAP QSW; `qnap_switches.yml`                           |

Per-host connection variables:

| Variable                     | Used by   | Meaning                                                        |
|------------------------------|-----------|----------------------------------------------------------------|
| `ansible_host`               | all       | Switch IP or hostname                                          |
| `netgear_password`           | Plus, Pro | Web UI password (Plus: login; Pro: only for static routes)     |
| `netgear_snmp_user`          | Pro       | SNMPv3 user                                                    |
| `netgear_snmp_auth_key`      | Pro       | SNMPv3 authentication key                                      |
| `netgear_snmp_priv`          | Pro       | `true` to use the privacy key (authPriv), otherwise authNoPriv |
| `netgear_snmp_priv_key`      | Pro       | SNMPv3 privacy key                                             |
| `netgear_snmp_auth_protocol` | Pro       | `md5`, `sha`, `sha256`, `sha512`; module default `sha512`      |
| `netgear_snmp_priv_protocol` | Pro       | `des`, `aes128`; module default `aes128`                       |
| `qnap_user`                  | QNAP      | Login user, default `admin`                                    |
| `qnap_password`              | QNAP      | Login password                                                 |

Take secrets from the environment, a lookup plugin or Ansible Vault; the examples use `lookup('env', ...)`.

Desired state, in `host_vars/<switch>.yml`:

| Variable                  | Family | Shape                                                                                           |
|---------------------------|--------|-------------------------------------------------------------------------------------------------|
| `netgear_vlans`           | Plus   | List of VLAN IDs                                                                                |
| `netgear_ports`           | Plus   | `<port number>: {untagged: <vid>, tagged: [<vid>...], pvid: <vid>}`                             |
| `netgear_pro_vlans`       | Pro    | `<vid>: "<name>"`                                                                               |
| `netgear_pro_ports`       | Pro    | `"<ifName>": {pvid: <vid>, untagged: [<vid>...], tagged: [<vid>...]}`, e.g. `"2/g6"`, `"g3"`    |
| `netgear_pro_purge_vlans` | Pro    | `true` deletes VLANs not listed in `netgear_pro_vlans` (see quirks)                             |
| `netgear_pro_mgmt`        | Pro    | `{address: a.b.c.d/len, gateway: a.b.c.d}`                                                      |
| `netgear_pro_routing`     | Pro    | `{enabled, interfaces: {<vid>: {address, routing, mtu}}, static_routes: [...], purge_*, web_*}` |
| `netgear_pro_stack_links` | Pro    | `"<ifName>": "<peer ifName>"`, checked only, never written                                      |
| `qnap_qsw_vlans`          | QNAP   | List of VLAN IDs                                                                                |
| `qnap_qsw_ports`          | QNAP   | `"<port>": {untagged: <vid>, tagged: [<vid>...]}`                                               |
| `qnap_qsw_protect_ports`  | QNAP   | List of ports whose management-VLAN membership must not change                                  |

Plus ports are numbers (`1`..`5`); Pro ports are keyed by `ifName` (`1/g1`, `2/xg27` on a stack, `g1` on a standalone switch); QNAP ports are the strings the switch reports (`"3"`). The `*_facts` modules show the exact names.

## Tags and handlers

The example playbooks use these tags.

| Tag               | Tasks                                                         |
|-------------------|---------------------------------------------------------------|
| `netgear_facts`   | Facts of Plus and Pro switches, plus the stack-link assertion |
| `netgear_stack`   | Stack-link check only                                         |
| `netgear_vlan`    | `netgear_plus_vlan`, `netgear_pro_vlan`                       |
| `netgear_mgmt`    | `netgear_pro_mgmt` (also runs under `netgear_routing`)        |
| `netgear_routing` | `netgear_pro_mgmt` first, then `netgear_pro_routing`          |
| `qnap_facts`      | `qnap_qsw_facts`                                              |
| `qnap_vlan`       | `qnap_qsw_vlan`                                               |

Every changing Pro task `notify`s the handler `Save Netgear Pro config`, so the play saves once at its end and a failed play saves nothing. `-e netgear_save=false` skips the handler; the change then lasts until the next reboot.

## Modules

All modules support `--check` and `--diff`.

### netgear_plus_facts

Reads system info, switch config, port settings and statistics, rate limits, mirror, IGMP, loop detection, broadcast filter, QoS mode, and the 802.1Q state (VLANs with untagged/tagged port lists and PVIDs) into the fact `netgear`. A getter the model lacks lands in `netgear.unavailable` instead of failing.

| Parameter  | Type  | Default  | Meaning                 |
|------------|-------|----------|-------------------------|
| `host`     | str   | required | Switch IP or hostname   |
| `password` | str   | required | Login password          |
| `timeout`  | float | `10.0`   | HTTP timeout in seconds |

```yaml
- netgear_plus_facts:
    host: "{{ ansible_host }}"
    password: "{{ netgear_password }}"
- ansible.builtin.debug:
    var: netgear.vlan.pvids
```

Check mode: read-only, behaves normally.

### netgear_plus_vlan

Reconciles VLAN existence, per-port membership, and PVIDs. A declared port is owned completely; undeclared ports stay untouched. Writes in the order create, widened membership, PVIDs, final membership, delete, because the firmware rejects a PVID outside the port's VLANs.

| Parameter     | Type  | Default  | Meaning                                               |
|---------------|-------|----------|-------------------------------------------------------|
| `host`        | str   | required | Switch IP or hostname                                 |
| `password`    | str   | required | Login password                                        |
| `timeout`     | float | `10.0`   | HTTP timeout in seconds                               |
| `vlans`       | list  | required | VLAN IDs that must exist                              |
| `ports`       | dict  | `{}`     | Port number to `{untagged, tagged, pvid}`             |
| `purge_vlans` | bool  | `false`  | Delete VLANs not in `vlans` (VLAN 1 is never deleted) |

```yaml
- netgear_plus_vlan:
    host: "{{ ansible_host }}"
    password: "{{ netgear_password }}"
    vlans: [1, 101, 102]
    ports:
      1: {untagged: 1, tagged: [101, 102]}
      2: {untagged: 101}
```

Returns `changed` and, with `--diff`, before/after renderings. Check mode computes the diff and writes nothing.

### netgear_pro_facts

Walks IF-MIB, Q-BRIDGE-MIB, LLDP-MIB, ENTITY-MIB and the Netgear routing/stack MIBs into the fact `netgear_pro`: `sysname`, `sysdescr`, `units` (name, model, firmware), `vlans` (id to name), `ports` keyed by ifName (alias, admin/link state, speed, pvid, untagged, tagged, LAG, LLDP neighbours), `routing` (global mode, VLAN routing interfaces, route table) and `stack_ports` (configured/running mode, link, speed, error count; empty on standalone switches). There are no unit serials: firmware 6.6.4.28 reports the management unit's serial for every member.

SNMP connection parameters, shared by all Pro modules:

| Parameter       | Type  | Default  | Meaning                                                                                 |
|-----------------|-------|----------|-----------------------------------------------------------------------------------------|
| `host`          | str   | required | Switch IP or hostname                                                                   |
| `port`          | int   | `161`    | SNMP UDP port                                                                           |
| `timeout`       | float | `5.0`    | Per-request timeout in seconds                                                          |
| `retries`       | int   | `1`      | Retries per request                                                                     |
| `snmp_user`     | str   |          | SNMPv3 user; if unset, v2c with `community`                                             |
| `auth_key`      | str   |          | SNMPv3 authentication key                                                               |
| `auth_protocol` | str   | `sha512` | `md5`, `sha`, `sha256`, `sha512`                                                        |
| `priv_key`      | str   |          | SNMPv3 privacy key; unset means authNoPriv                                              |
| `priv_protocol` | str   | `aes128` | `des`, `aes128`                                                                         |
| `community`     | str   |          | v2c community (read-write needed for the writing modules); `public` for read-only facts |

```yaml
- netgear_pro_facts:
    host: "{{ ansible_host }}"
    snmp_user: "{{ netgear_snmp_user }}"
    auth_key: "{{ netgear_snmp_auth_key }}"
    priv_key: "{{ netgear_snmp_priv_key }}"
- ansible.builtin.debug:
    msg: "{{ netgear_pro.ports | dict2items | selectattr('value.link') | map(attribute='key') | list }}"
```

Check mode: read-only, behaves normally.

### netgear_pro_vlan

Reconciles VLANs, names, per-port membership and PVIDs through Q-BRIDGE-MIB. Also takes the SNMP connection parameters above.

| Parameter     | Type  | Default  | Meaning                                                                     |
|---------------|-------|----------|-----------------------------------------------------------------------------|
| `vlans`       | dict  | required | VLAN ID to name; every listed VLAN must exist with that name                |
| `ports`       | dict  | `{}`     | ifName to `{pvid, untagged, tagged}`; `untagged` and `tagged` are lists     |
| `purge_vlans` | bool  | `false`  | Delete VLANs not in `vlans` (VLAN 1 is never deleted)                       |
| `save`        | bool  | `false`  | Persist after a change; playbooks should use the `netgear_pro_save` handler |
| `settle_wait` | float | `30`     | Seconds to poll until the read-back matches                                 |

```yaml
- netgear_pro_vlan:
    host: "{{ ansible_host }}"
    snmp_user: "{{ netgear_snmp_user }}"
    auth_key: "{{ netgear_snmp_auth_key }}"
    priv_key: "{{ netgear_snmp_priv_key }}"
    vlans: {1: Default, 101: servers}
    ports:
      "1/g22": {pvid: 101, untagged: [101]}
      "2/xg27": {pvid: 1, untagged: [1], tagged: [101]}
  notify: Save Netgear Pro config
```

Every declared port must be a bridge port (stack links are not) in switchport mode `general`. `pvid` is independent of `untagged` and left as is when omitted. Check mode reports the diff without writing.

### netgear_pro_routing

Reconciles global IPv4 routing mode, VLAN routing interfaces (address, routing flag, MTU) through the Netgear routing MIB, and static routes through the web UI. Also takes the SNMP connection parameters.

| Parameter             | Type  | Default | Meaning                                                                                                               |
|-----------------------|-------|---------|-----------------------------------------------------------------------------------------------------------------------|
| `password`            | str   |         | Web UI password; required for static routes                                                                           |
| `enabled`             | bool  | `true`  | Global IPv4 routing mode                                                                                              |
| `interfaces`          | dict  | `{}`    | VLAN ID to `{address: a.b.c.d/len, routing: bool (true), mtu: int (1500)}`                                            |
| `static_routes`       | list  | `[]`    | `{dest: a.b.c.d/len, next_hop: a.b.c.d, preference: 1-255 (1), description: str}`; default route is `dest: 0.0.0.0/0` |
| `purge_interfaces`    | bool  | `false` | Remove routing interfaces not in `interfaces`                                                                         |
| `purge_static_routes` | bool  | `false` | Remove static routes not in `static_routes`                                                                           |
| `settle_wait`         | float | `30`    | Seconds to poll until the read-back matches                                                                           |
| `web_wait`            | int   | `120`   | Seconds to wait for a free web session slot (polls `agentLoginSessionTable` every 10 s)                               |
| `web_soft_timeout`    | int   |         | Web session idle timeout in minutes (1-60), applied over SNMP before the web part                                     |

```yaml
- netgear_pro_routing:
    host: "{{ ansible_host }}"
    snmp_user: "{{ netgear_snmp_user }}"
    auth_key: "{{ netgear_snmp_auth_key }}"
    priv_key: "{{ netgear_snmp_priv_key }}"
    password: "{{ netgear_password }}"
    interfaces:
      101: {address: 192.0.2.38/24}
    static_routes:
      - {dest: 0.0.0.0/0, next_hop: 192.0.2.1, description: upstream}
    web_soft_timeout: 2
  notify: Save Netgear Pro config
```

Returns `static_routes`, `web_soft_timeout` and `web_sessions` (the sessions holding the slots, if it had to wait). Declared VLAN interfaces are owned completely. The interface that carries `host` is protected and never changed. The web UI is not touched unless `static_routes` or `purge_static_routes` is set. Check mode reports the diff without writing.

### netgear_pro_mgmt

Sets the static management IP, mask and gateway (NETGEAR-SWITCHING-MIB `agentNetworkConfigGroup`). The management VLAN stays as it is.

| Parameter     | Type  | Default   | Meaning                                        |
|---------------|-------|-----------|------------------------------------------------|
| `address`     | str   | required  | Management address as `a.b.c.d/len`            |
| `gateway`     | str   | `0.0.0.0` | Management default gateway; `0.0.0.0` for none |
| `settle_wait` | float | `30`      | Seconds to poll until the read-back matches    |

```yaml
- netgear_pro_mgmt:
    host: 192.0.2.38
    snmp_user: "{{ netgear_snmp_user }}"
    auth_key: "{{ netgear_snmp_auth_key }}"
    priv_key: "{{ netgear_snmp_priv_key }}"
    address: 198.51.100.38/24
  notify: Save Netgear Pro config
```

The module refuses to run when `host` is the current or the new management address, because the change would cut its own session; address the switch through a routing interface instead. Check mode reports the diff without writing.

### netgear_pro_save

Sets `agentSaveConfig` and polls `agentSaveConfigStatus` until the save completes. Meant as a handler.

| Parameter      | Type  | Default | Meaning                              |
|----------------|-------|---------|--------------------------------------|
| `save_timeout` | float | `60`    | Seconds to wait for `savingComplete` |

```yaml
handlers:
  - name: Save Netgear Pro config
    netgear_pro_save:
      host: "{{ ansible_host }}"
      snmp_user: "{{ netgear_snmp_user }}"
      auth_key: "{{ netgear_snmp_auth_key }}"
      priv_key: "{{ netgear_snmp_priv_key }}"
    when: netgear_save | default(true) | bool
```

Always reports `changed`, and `saved: true` after a real save. In check mode nothing is written.

### qnap_qsw_facts

Logs in to `/api/v1/users/login`, GETs a list of endpoints and returns each `result` unchanged in the fact `qnap`, keyed by the endpoint path with `/` replaced by `_` (`qnap.ports_status`, `qnap.vlan`). Endpoints the model does not offer are listed in `qnap.unavailable`.

| Parameter        | Type  | Default  | Meaning                                                                                                                                 |
|------------------|-------|----------|-----------------------------------------------------------------------------------------------------------------------------------------|
| `host`           | str   | required | Switch IP or hostname                                                                                                                   |
| `username`       | str   | `admin`  | Login user                                                                                                                              |
| `password`       | str   | required | Login password (sent base64-encoded, as the web UI does)                                                                                |
| `port`           | int   | `443`    | HTTPS port                                                                                                                              |
| `validate_certs` | bool  | `false`  | Verify the TLS certificate (QSS ships a self-signed one)                                                                                |
| `timeout`        | float | `10.0`   | Per-request timeout in seconds                                                                                                          |
| `endpoints`      | list  | built-in | Endpoints relative to `/api/v1`; default covers system, firmware, ports, vlan, lacp, rstp, igmp, mirror, loop protection, ip, qos, snmp |
| `delay`          | float | `0.3`    | Pause between requests in seconds                                                                                                       |

```yaml
- qnap_qsw_facts:
    host: "{{ ansible_host }}"
    password: "{{ qnap_password }}"
    endpoints: [ports/status, vlan]
```

Check mode: read-only, behaves normally.

### qnap_qsw_vlan

Declarative per-port VLAN membership. A declared port is owned completely: memberships it does not list are removed. Undeclared ports stay untouched in every VLAN. One write per changed VLAN, serialised with `delay`, then readback and comparison, then `PUT /system/save`.

| Parameter        | Type  | Default  | Meaning                                                                                 |
|------------------|-------|----------|-----------------------------------------------------------------------------------------|
| `host`           | str   | required | Switch IP or hostname                                                                   |
| `username`       | str   | `admin`  | Login user                                                                              |
| `password`       | str   | required | Login password                                                                          |
| `port`           | int   | `443`    | HTTPS port                                                                              |
| `validate_certs` | bool  | `false`  | Verify the TLS certificate                                                              |
| `timeout`        | float | `10.0`   | Per-request timeout in seconds                                                          |
| `delay`          | float | `0.3`    | Pause between write requests in seconds                                                 |
| `vlans`          | list  | required | VLAN IDs that must exist                                                                |
| `ports`          | dict  | `{}`     | Port name to `{untagged: <vid>, tagged: [<vid>...]}`; `untagged` is one VLAN or omitted |
| `purge_vlans`    | bool  | `false`  | Delete VLANs not in `vlans` (VLAN 1 is never deleted)                                   |
| `mgmt_vlan`      | int   | `1`      | VLAN carrying the management IP                                                         |
| `protect_ports`  | list  | `[]`     | Ports whose `mgmt_vlan` membership must not change                                      |
| `save`           | bool  | `true`   | Persist running to startup config after a change                                        |

```yaml
- qnap_qsw_vlan:
    host: "{{ ansible_host }}"
    password: "{{ qnap_password }}"
    vlans: [1, 101, 188]
    ports:
      "3": {untagged: 1, tagged: [101, 188]}
      "12": {untagged: 188, tagged: [101]}
    protect_ports: ["11"]
```

Returns `changed`, a before/after `diff`, and `saved`. Check mode reports the diff without writing.

## Hardware quirks

### Netgear Smart Managed Plus

`untagged` sets the PVID as well; `pvid` only matters for ports without an `untagged` entry. 802.1Q VLAN mode must be enabled on the switch, otherwise there is nothing to reconcile. The Galaxy collection `jfrancis42.netgear` was dropped because its `vlan` module could add ports to a VLAN but never remove them; `netgear_plus_vlan` owns declared ports completely. The SDK scrapes the web UI, so the switch must be reachable over HTTP from the controller and concurrent logins from a browser can lock a run out.

### Netgear Smart Managed Pro

- **SNMPv3 user.** The S3300 has its own SNMPv3 users. On the GS108T the SNMPv3 user is the web login (`admin`, authentication key = web password), and only MD5/SHA with DES works; set `netgear_snmp_auth_protocol: sha` and `netgear_snmp_priv_protocol: des` on that host, or the module defaults (`sha512`/`aes128`) fail.
- **One varbind per PDU.** The GS108T rejects or silently drops combined sets, so every write is a single-varbind PDU.
- **Writes land late.** Sets and destroys are applied with a delay; the modules poll the read-back until it matches (`settle_wait`) instead of trusting the SNMP response.
- **Factory VLANs.** VLANs of type `Default` on the GS108T are missing from `dot1qVlanStaticTable` and are read from `dot1qVlanCurrentTable`; they can still be written through the static OIDs.
- **Untagged bitmask.** On the GS108T the untagged port bitmap carries the CPU port (bit 13) as a set non-member bit. Clearing it makes every further untagged change on that VLAN fail with `commitFailed` until the raw mask is restored. A cleared bit of a regular port that left the VLAN is harmless (verified on a GS108Tv2, FW 5.4.2.36). The module patches only the bits of ports it moves and never overwrites the whole mask.
- **Stack links.** Stack ports are not bridge ports and are rejected in `netgear_pro_ports`. Do not declare them; `netgear_pro_stack_links` only asserts them (changing stack mode needs a unit reboot).
- **Saving.** The running config is saved by `agentSaveConfig`, OID `...4526.11` (not `4526.10`), and only once per play, through the `Save Netgear Pro config` handler that each changing task notifies. `-e netgear_save=false` leaves the change in the running config only, lost at reboot.
- **Purge traps.** `netgear_pro_purge_vlans: true` deletes every VLAN not listed, including factory VLANs such as 2 and 3 on the GS108T. List them or leave purge off.
- **Static routes.** The firmware refuses `inetCidrRouteTable` writes with `commitFailed`, so static routes go through the web UI (`password`). The switch allows at most 4 web sessions; the module logs out again, waits up to `web_wait` for a free slot and names the holders when it gives up. Set `web_soft_timeout` low so abandoned browser sessions free their slot.
- **Management interface.** The routing interface that carries `host` is never changed or purged. The firmware rejects a VLAN routing interface inside the management subnet, so `netgear_pro_mgmt` moves the management IP to a dedicated network first and runs before `netgear_pro_routing` (also under the `netgear_routing` tag). It refuses when `host` is itself the management address.
- **Debug CLI.** The S3300 exposes a Telnet debug CLI on port 60000 (web UI: Remote Diagnostics, `/telnetSmart.html`) with the full FASTPATH routing CLI. Use it for diagnosis only, not for configuration.

### QNAP QSW (QSS)

- **Request bursts crash the backend.** The backend forks `/bin/ubus` per request; bursts drive it into fork `ENOMEM` and the web service dies. The modules serialise requests with `delay` (0.3 s). On the switch side, `vm.overcommit_memory=1` prevents the crash (needs SSH/debug access to the switch).
- **Avoid `lldp/neighbors/status`.** It is not in the default endpoint list.
- **No PVID.** QSS has no PVID setting: the untagged membership is the native/access VLAN, so a port has at most one `untagged` VLAN.
- **Ownership.** A declared port belongs to the module completely, undeclared ports (for example LAG members) stay untouched.
- **Readback and save.** After writing, the module compares the read-back with the desired state and saves with `PUT /system/save`.
- **Protect ports.** `protect_ports` together with `mgmt_vlan` stops a run from removing the management-VLAN access of the port the controller reaches the switch on.

## Relation to `networkmgmt.switchctrl`

The Python library `networkmgmt.switchctrl` in this project encodes the same hardware findings for scripts and services; these modules expose them to playbooks. Use the library when you need the switches from Python, the modules when you want declarative, idempotent runs with check mode and diff.
