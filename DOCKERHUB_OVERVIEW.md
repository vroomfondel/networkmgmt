[![black-lint](https://github.com/vroomfondel/networkmgmt/actions/workflows/checkblack.yml/badge.svg)](https://github.com/vroomfondel/networkmgmt/actions/workflows/checkblack.yml)
[![mypy and pytests](https://github.com/vroomfondel/networkmgmt/actions/workflows/mypynpytests.yml/badge.svg)](https://github.com/vroomfondel/networkmgmt/actions/workflows/mypynpytests.yml)
[![BuildAndPushMultiarch](https://github.com/vroomfondel/networkmgmt/actions/workflows/buildmultiarchandpush.yml/badge.svg)](https://github.com/vroomfondel/networkmgmt/actions/workflows/buildmultiarchandpush.yml)
![Cumulative Clones](https://img.shields.io/endpoint?logo=github&url=https://gist.githubusercontent.com/vroomfondel/906f5c4deb7e6e6bcd6ee0f7c96f586c/raw/networkmgmt_clone_count.json)
[![PyPI](https://img.shields.io/pypi/v/networkmgmt?logo=python&logoColor=white)](https://pypi.org/project/networkmgmt/)
[![Gemini_Generated_Image_8bu6gi8bu6gi8bu6_250x250.png](https://raw.githubusercontent.com/vroomfondel/networkmgmt/main/Gemini_Generated_Image_8bu6gi8bu6gi8bu6_250x250.png)](https://github.com/vroomfondel/networkmgmt)

# WIP !!!

# networkmgmt — Network Management Toolkit

CLI tool and Python library for multi-vendor switch management and network topology discovery.

Quick links:
- [Docker Hub](https://hub.docker.com/r/xomoxcc/networkmgmt/tags) — multi-arch image (amd64/arm64)
- [PyPI](https://pypi.org/project/networkmgmt/) — `pip install networkmgmt`
- [GitHub](https://github.com/vroomfondel/networkmgmt) — source, issues, CI

## Modules

| Module                         | Description                                                               |
|--------------------------------|---------------------------------------------------------------------------|
| **networkmgmt.switchctrl**     | Multi-vendor switch management (Cisco, MikroTik, QNAP, Netgear Pro/Plus)  |
| **networkmgmt.snmp**           | Shared SNMPv2c/v3 core (optional `pysnmp`) used by the Netgear Pro vendor |
| **networkmgmt.discovery**      | Network topology discovery (ARP, DNS, SNMP, LLDP, nmap, traceroute)       |
| **networkmgmt.snmp_vlan_dump** | SNMP VLAN-port dump for Netgear switches                                  |

## Supported Switch Vendors

| Vendor           | Model(s)                                              | Transport                                            | Notes                                                                                                                               |
|------------------|-------------------------------------------------------|------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------|
| **cisco**        | Catalyst 1200 (C1200-8T-D)                            | SSH CLI only                                         | 8 GE ports (`gi1`–`gi8`), no REST API, enable password required                                                                     |
| **mikrotik**     | RouterOS v7+ switches                                 | REST API + SSH CLI                                   | REST for monitoring, SSH for configuration                                                                                          |
| **qnap**         | QSW-M408 series                                       | REST API + SSH CLI                                   | Monitoring and VLAN via REST (declarative `apply_port_membership`, 0.3 s request delay), port/LACP via Cisco-style SSH CLI; no PVID |
| **netgear**      | Smart Managed Pro: S3300 stack, GS108Tv2, M4300 class | SNMPv3 (v2c fallback), web UI for static routes only | VLAN incl. PVID, port admin, LAG read-only, routing interfaces, management IP, static routes, `save_config()`                       |
| **netgear_plus** | Smart Managed Plus: GS105E, GS108E, GS305E            | Web UI via optional `netgear-tool`                   | Declarative 802.1Q incl. removal, PVID, port admin; no LACP, no sensors                                                             |

## CLI Structure

`networkmgmt` is an orchestrator that dispatches to three sub-CLIs. Each sub-CLI can also be invoked directly:

| Orchestrator command         | Direct script            | Description                    |
|------------------------------|--------------------------|--------------------------------|
| `networkmgmt switchctrl ...` | `networkmgmt-switchctrl` | Multi-vendor switch management |
| `networkmgmt discover ...`   | `networkmgmt-discover`   | Network topology discovery     |
| `networkmgmt vlan-dump ...`  | `networkmgmt-vlan-dump`  | SNMP VLAN-port dump            |

## Installation

```bash
pip install .
```

Optional extras:

```bash
pip install networkmgmt[snmp]          # pysnmp: netgear (Smart Managed Pro) vendor
pip install networkmgmt[netgear-plus]  # netgear-tool: netgear_plus (Smart Managed Plus) vendor
pip install networkmgmt[discovery]     # scapy: ARP scan in networkmgmt-discover
pip install networkmgmt[all]           # everything above
```

## Network Topology Discovery

Discover local network topology via ARP scanning, reverse DNS, MAC vendor lookup, SNMP bridge tables, LLDP, optional nmap service detection, and traceroute. Outputs Mermaid flowcharts or JSON.

### CLI

```bash
# Basic discovery (auto-detect interface)
networkmgmt-discover -i eth0
# or: networkmgmt discover -i eth0

# With nmap service detection (requires root for ARP scan)
sudo networkmgmt-discover -i eth0 --nmap -o topology.md

# JSON output
networkmgmt-discover -i eth0 --format json -o topology.json

# Multi-subnet scan
networkmgmt-discover -i eth0,vlan100 -o multi.md

# SNMP L2 topology (query switch MAC tables), SNMPv2c per switch
networkmgmt-discover -i eth0 --switches 192.0.2.38:public,192.0.2.19

# SNMPv3 for every switch without :community (v2c entries can be mixed in)
networkmgmt-discover -i eth0 --switches 192.0.2.38,192.0.2.150,192.0.2.19:public \
    --snmp-user admin --snmp-auth-key 'AUTHPASS' --snmp-auth-protocol sha512

# LLDP-based topology
networkmgmt-discover -i eth0 --lldp-collect /tmp/lldp

# Manual topology + diagram style
networkmgmt-discover -i eth0 --topology 192.0.2.50:192.0.2.19:g3 -d hierarchical

# Traceroute to external targets
networkmgmt-discover -i eth0 -t 8.8.8.8,1.1.1.1
sudo networkmgmt-discover -i eth0 -t 192.0.2.0/24
```

### Discovery Options

| Option                   | Description                                                                    |
|--------------------------|--------------------------------------------------------------------------------|
| `-i` / `--interface`     | Network interface(s), comma-separated (default: auto-detect)                   |
| `-t` / `--targets`       | Traceroute targets: IPs, CIDR (`192.168.1.0/24`), or range (`192.168.1.1-254`) |
| `--nmap`                 | Enable nmap service detection                                                  |
| `--top-ports`            | nmap top ports to scan (default: 100)                                          |
| `-o` / `--output`        | Output file (default: stdout)                                                  |
| `--format`               | `mermaid` (default) or `json`                                                  |
| `--timeout`              | Scan timeout in seconds (default: 10)                                          |
| `--max-hops`             | Max traceroute hops (default: 30)                                              |
| `--trace-local`          | Tracepath to all LAN hosts to detect switch hierarchy                          |
| `--switches`             | SNMP L2 discovery: `IP[:community],...` (default community: `public`)          |
| `--snmp-user`            | SNMPv3 user for `--switches` entries without `:community`                      |
| `--snmp-auth-key`        | SNMPv3 auth passphrase (or `$SNMP_AUTH_KEY`)                                   |
| `--snmp-auth-protocol`   | `md5`, `sha`, `sha256`, `sha512` (default)                                     |
| `--snmp-priv-key`        | SNMPv3 privacy passphrase (or `$SNMP_PRIV_KEY`); unset = authNoPriv            |
| `--snmp-priv-protocol`   | `des`, `aes128` (default)                                                      |
| `--snmp-timeout`         | SNMP timeout per request in seconds (default: 5)                               |
| `--snmp-retries`         | SNMP retries per request (default: 1)                                          |
| `--lldp-collect`         | SSH into hosts, collect lldpctl JSON to directory                              |
| `--lldp-dir`             | Read existing LLDP JSON files from directory                                   |
| `--topology`             | Manual L2 topology: `HOST:SWITCH:PORT,...`                                     |
| `-d` / `--diagram-style` | `auto` (default), `flat`, `categorized`, `hierarchical`                        |
| `--direction`            | Mermaid flowchart direction: `LR` or `TD` (default: auto)                      |
| `--elk`                  | Use ELK layout engine for large diagrams                                       |
| `-v` / `--verbose`       | Verbose logging                                                                |

### Python Library Usage (Discovery)

```python
from networkmgmt.discovery import NetworkTopologyScanner, MermaidGenerator

scanner = NetworkTopologyScanner(interfaces=["eth0"], use_nmap=True)
topology = scanner.run_discovery()

# Generate Mermaid diagram
gen = MermaidGenerator(topology, diagram_style="hierarchical")
print(gen.generate())

# Or export as JSON
print(topology.model_dump_json(indent=2))
```

## SNMP VLAN Dump

Dumps VLAN membership (tagged/untagged), PVIDs and link state per port from Q-BRIDGE-MIB capable switches (tested on Netgear S3300 stacks and GS108Tv2) as a terminal table, Markdown or Mermaid diagram.

```bash
# SNMPv2c
networkmgmt vlan-dump 192.0.2.38 public
networkmgmt vlan-dump 192.0.2.38 public -m -o s3300.md -d trunks

# SNMPv3 (defaults sha512/aes128; authNoPriv when --snmp-priv-key is missing)
networkmgmt vlan-dump 192.0.2.38 --snmp-user admin --snmp-auth-key 'AUTHPASS'

# GS108Tv2: the web login is the SNMPv3 user, md5/sha + des only
networkmgmt vlan-dump 192.0.2.150 --snmp-user admin --snmp-auth-key 'WEBPASS' \
    --snmp-auth-protocol sha --snmp-priv-key 'WEBPASS' --snmp-priv-protocol des

SNMP_AUTH_KEY='AUTHPASS' networkmgmt vlan-dump 192.0.2.38 --snmp-user admin --snmp-timeout 10 --snmp-retries 2
```

- Factory VLANs (1, 2 Voice, 3 Auto-Video on the GS108Tv2) are missing from `dot1qVlanStaticTable`; the dump merges them in from `dot1qVlanCurrentTable`, static entries win.
- Stack links (S3300) show up in the VLAN 1 bitmap but are not bridge ports; they are listed in a note line and left out of tables and diagrams.
- A wrong user or key is reported as `SNMP authentication failed`. With SNMPv3 this often surfaces only as a timeout, so the message names user, auth and privacy settings as the likely cause.

## Switch Management CLI

```bash
networkmgmt-switchctrl --vendor <VENDOR> --host <IP> --password <PW> [options] <command>
# or: networkmgmt switchctrl --vendor <VENDOR> --host <IP> --password <PW> [options] <command>
```

### Global Options

| Option              | Description                                                                        |
|---------------------|------------------------------------------------------------------------------------|
| `--vendor`          | Switch vendor (`cisco`, `mikrotik`, `netgear`, `netgear_plus`, `qnap`)             |
| `--host`            | Switch IP address or hostname                                                      |
| `--password`        | Admin password (required except for `netgear` with `--community` or `--snmp-user`) |
| `--username`        | Username (default: `admin`; `netgear`: alias for `--snmp-user`)                    |
| `--enable-password` | Enable password (Cisco: required; QNAP: auto-generated if omitted)                 |
| `--ssh-port`        | SSH port (default: 22)                                                             |
| `--rest-port`       | REST API port (default: 443)                                                       |
| `--ssh-username`    | SSH username (vendor-specific default if omitted)                                  |
| `--ssh-password`    | SSH password (vendor-specific default if omitted)                                  |
| `--verify-ssl`      | Verify SSL certificates for REST API                                               |
| `-v` / `--verbose`  | Enable debug logging                                                               |

### Commands

### Vendor Options

All options are command-line flags; each is rejected for vendors it does not belong to.

| Option                                    | Vendor       | Description                                                                  |
|-------------------------------------------|--------------|------------------------------------------------------------------------------|
| `--community`                             | netgear      | SNMPv2c community (fallback when no `--snmp-user` is given)                  |
| `--snmp-user`                             | netgear      | SNMPv3 user                                                                  |
| `--auth-key`                              | netgear      | SNMPv3 authentication passphrase (default: `--password`)                     |
| `--auth-protocol {md5,sha,sha256,sha512}` | netgear      | SNMPv3 authentication protocol (default: `sha512`; GS108Tv2: `md5` or `sha`) |
| `--priv-key`                              | netgear      | SNMPv3 privacy passphrase (omit for authNoPriv)                              |
| `--priv-protocol {des,aes128}`            | netgear      | SNMPv3 privacy protocol (default: `aes128`; GS108Tv2: `des`)                 |
| `--web-password`                          | netgear      | Web UI password for static routes (default: `--password`)                    |
| `--password`                              | netgear_plus | Web UI password (the only option this vendor needs)                          |
| `--vlan-backend {rest,ssh}`               | qnap         | VLAN backend (default: `rest`; `ssh` if SSH options are given)               |
| `--rest-delay`                            | qnap         | Seconds between REST requests (default: 0.3)                                 |

#### `monitor` — Show system info, sensors, ports, and LACP status

Sections a vendor does not implement (Netgear sensors, LACP on `netgear_plus`) are reported as not supported and skipped; the rest is still printed.

```bash
# Cisco Catalyst 1200
networkmgmt-switchctrl --vendor cisco --host 192.168.1.254 \
    --username admin --password <PW> --enable-password <EN> monitor

# QNAP QSW
networkmgmt-switchctrl --vendor qnap --host 192.168.1.1 --password <PW> monitor

# Netgear Smart Managed Pro, SNMPv3 (S3300: sha512 + aes128 are the defaults)
networkmgmt-switchctrl --vendor netgear --host 192.168.1.38 --snmp-user monitor \
    --auth-key <AUTH> --priv-key <PRIV> monitor

# Netgear GS108Tv2: web login is the SNMPv3 user, md5/sha + des only
networkmgmt-switchctrl --vendor netgear --host 192.168.1.150 --snmp-user admin \
    --auth-key <WEBPW> --auth-protocol sha --priv-key <WEBPW> --priv-protocol des monitor

# Netgear, SNMPv2c
networkmgmt-switchctrl --vendor netgear --host 192.168.1.38 --community public vlan list

# Netgear Smart Managed Plus
networkmgmt-switchctrl --vendor netgear_plus --host 192.168.1.5 --password <PW> vlan list
```

#### `vlan` — VLAN management

```bash
# Create VLAN
networkmgmt-switchctrl --vendor cisco --host 192.168.1.254 \
    --username admin --password <PW> --enable-password <EN> \
    vlan create 100 --name servers

# List VLANs
networkmgmt-switchctrl --vendor cisco ... vlan list

# Delete VLAN
networkmgmt-switchctrl --vendor cisco ... vlan delete 100
```

#### `port` — Port configuration

```bash
# Configure port (Cisco uses gi1-gi8, QNAP uses GigabitEthernet1/0/1)
networkmgmt-switchctrl --vendor cisco --host 192.168.1.254 \
    --username admin --password <PW> --enable-password <EN> \
    port config gi1 --speed 1000 --duplex full

# Disable a port
networkmgmt-switchctrl --vendor cisco ... port config gi3 --shutdown
```

#### `example` — Quick connectivity test

```bash
networkmgmt-switchctrl --vendor cisco --host 192.168.1.254 \
    --username admin --password <PW> --enable-password <EN> example
```

## Python Library Usage (Switch Management)

```python
from networkmgmt import create_switch

# Cisco Catalyst 1200
with create_switch(
        "cisco",
        host="192.168.1.254",
        username="admin",
        password="mypass",
        enable_password="myenable",
) as switch:
    switch.enable()

    # Monitoring
    info = switch.monitoring.get_system_info()
    print(f"{info.model} — FW {info.firmware_version}")

    sensors = switch.monitoring.get_sensor_data()
    print(f"Temperature: {sensors.temperature}°C")

    ports = switch.monitoring.get_port_status()
    for p in ports:
        print(f"  {p.port}: {'UP' if p.link_up else 'DOWN'}")

    # VLAN management (uses 'vlan database' mode on C1200)
    switch.vlan.create_vlan(100, "servers")
    switch.vlan.assign_port_to_vlan("gi3", 100)

    # LACP (uses 'mode auto' on C1200)
    switch.lacp.create_port_channel(1, ["gi5", "gi6"])
```

```python
# QNAP QSW
with create_switch("qnap", host="192.168.1.1", password="admin123") as switch:
    switch.enable()  # auto-generates enable password from serial
    info = switch.monitoring.get_system_info()
    print(info.model)

# QNAP QSW: declarative VLAN reconcile over REST
from networkmgmt.switchctrl.vendors.qnap.vlan_rest import PortMembership

with create_switch("qnap", host="192.168.1.1", password="admin123", rest_delay=0.3) as switch:
    result = switch.rest_vlan.apply_port_membership(
        [1, 100],
        {"3": PortMembership(untagged=100), "4": PortMembership(tagged=[100])},
        protect_ports=["11"],
    )
    print(result)
```

```python
# Netgear Smart Managed Pro (S3300 stack) over SNMPv3 — needs networkmgmt[snmp]
with create_switch(
        "netgear",
        host="192.168.1.38",
        snmp_user="monitor",
        auth_key="authpass",
        priv_key="privpass",
        web_password="webpass",  # only for static routes
) as switch:
    print(switch.monitoring.get_system_info().model)

    switch.vlan.create_vlan(20, "iot")
    switch.vlan.assign_port_to_vlan("1/g5", 20)  # untagged; also sets the PVID
    switch.port.disable_port("1/g6")

    routing = switch.routing
    routing.set_interface(20, "192.168.20.1/24")
    routing.add_static_route("10.9.0.0/16", "192.168.20.254")

    switch.save_config()  # running -> startup, otherwise lost on reboot
```

```python
# Netgear Smart Managed Plus (GS105E/GS108E/GS305E) — needs networkmgmt[netgear-plus]
from networkmgmt.switchctrl.vendors.netgear_plus.managers import PortVlanSpec

with create_switch("netgear_plus", host="192.168.1.5", password="secret") as switch:
    plan = switch.vlan.reconcile(
        [1, 20, 101],
        {
            1: PortVlanSpec(untagged=1, tagged=[20, 101]),
            2: PortVlanSpec(untagged=20),  # untagged also sets the PVID
        },
        purge_vlans=True,
    )
    print(plan.render(), plan.changed)
```

## Vendor-Specific Notes

### Cisco Catalyst 1200 (C1200-8T-D)

- **SSH only** — no REST API available
- **Port names**: `gi1` through `gi8` (not `GigabitEthernet1/0/1`)
- **Enable password required** — no auto-generation, C1200 enforces password change on first login
- **VLAN creation** uses `vlan database` mode (not `configure terminal` > `vlan {id}`)
- **LACP** uses `channel-group {id} mode auto` (not `mode active`)
- **Fanless** — PoE-powered, `get_sensor_data()` always returns `fan_speed=0`

### QNAP QSW

- **Dual transport** — REST API for monitoring and VLANs, SSH (Cisco-style CLI) for port/LACP configuration
- **VLAN backend** — REST by default (`vlan_backend="rest"`); SSH if `ssh_username`, `ssh_password` or `enable_password` is given (or `vlan_backend="ssh"`)
- **Serialised REST** — `rest_delay` (default 0.3 s) between requests
- **Enable password** can be auto-generated from the switch serial number
- **Default SSH credentials**: `guest` / `guest123`
- **No PVID** — the untagged membership is the native/access VLAN, so at most one per port

### Netgear Smart Managed Pro (`netgear`)

- **SNMP** — SNMPv3 (`snmp_user`, `auth_key`, `priv_key`) preferred, SNMPv2c (`community`) as fallback; needs `pysnmp` (`networkmgmt[snmp]`)
- **Models** — S3300 stack, GS108Tv2, M4300 class; port names are `<unit>/g<n>`
- **Managers** — VLAN incl. PVID, port admin state, LAG read-only, stack units/ports; sensors and LAG changes raise `NotImplementedError`
- **`switch.routing`** — VLAN routing interfaces, management IP/gateway and static routes; static routes need `web_password` (web UI)
- **`switch.save_config()`** — writes are running-config only until saved
- **Writes are verified** — every write is polled until the read-back shows it

### Netgear Smart Managed Plus (`netgear_plus`)

- **Web UI** via the optional `netgear-tool` package (`networkmgmt[netgear-plus]`); single web session, use the client as a context manager
- **Models** — GS105E, GS108E, GS305E; ports are 1-based numbers
- **VLAN** — declarative `switch.vlan.reconcile(...)` (create, edit, PVID, delete, read-back check), `ensure_vlan`, `ensure_vlan_absent`, `ensure_port_vlans`
- **802.1Q mode** must be enabled on the switch; no LACP, no sensors (`NotImplementedError`)

### MikroTik

- **Dual transport** — REST API (RouterOS v7+) for monitoring, SSH for configuration
- **No enable mode** — RouterOS uses direct admin access

## Hardware quirks

- **GS108T SNMPv3** — the user is the web login (`admin`, auth key = web password) and only `md5`/`sha` with `des` work.
- **Delayed writes** — SNMP and REST writes take effect with delay, so every write is verified by polling the read-back.
- **One varbind per PDU** — VLAN writes on Netgear Pro are sent as single-varbind SETs; bundled varbinds are rejected with `commitFailed`.
- **Factory VLANs** — Netgear Pro lists default VLANs only in the Q-BRIDGE current table, not in the static table.
- **Untagged mask** — patch the Netgear Pro untagged port list bit by bit; clearing the CPU-port bit (13 on the GS108T) blocks every later untagged change of that VLAN, cleared bits of regular ports are harmless.
- **Stack links** — Netgear Pro stack ports are not bridge ports and are rejected as VLAN members.
- **Save** — Netgear Pro persists the config via `agentSaveConfig` under `4526.11` (not `4526.10`).
- **Static routes** — Netgear Pro accepts them only through the web UI (SNMP writes fail with `commitFailed`), which allows at most 4 sessions.
- **Plus VLANs** — `untagged` also sets the PVID, and 802.1Q mode must be on.
- **QNAP backend** — bursts of REST requests crash the switch backend (fork ENOMEM), so requests are serialised; avoid `lldp/neighbors/status`; there is no PVID.

## Ansible modules

The `ansible/` directory contains stand-alone Ansible modules (no collection packaging) for Netgear Smart Managed Plus and Pro switches and QNAP QSW switches. They encode the same hardware findings as `networkmgmt.switchctrl`, but target playbooks: declarative, idempotent, with check mode and diff. See [ansible/README.md](ansible/README.md).

## Architecture

```
networkmgmt/
├── __init__.py              # Public API: create_switch(), list_vendors()
├── __main__.py              # Orchestrator CLI — dispatches to sub-CLIs
├── snmp/                    # Shared SNMP core (optional pysnmp): credentials, session, polling, portlists
├── switchctrl/              # Multi-vendor switch management
│   ├── __init__.py          #   Re-exports: create_switch, list_vendors, SwitchError
│   ├── cli.py               #   Switch management CLI (networkmgmt-switchctrl)
│   ├── factory.py           #   Vendor registry and factory
│   ├── exceptions.py        #   Exception hierarchy
│   ├── base/                #   Abstract base classes
│   │   ├── client.py        #     BaseSwitchClient
│   │   ├── managers.py      #     BaseMonitoringManager, BaseVLANManager, ...
│   │   └── transport.py     #     BaseTransport
│   ├── models/              #   Dataclasses (vendor-agnostic)
│   │   ├── port.py          #     PortConfig, PortStatus, PortSpeed, ...
│   │   ├── vlan.py          #     VLAN, TrunkConfig
│   │   ├── stats.py         #     PortStatistics
│   │   └── system.py        #     SystemInfo, SensorData, LACPInfo
│   └── vendors/
│       ├── common/          #   Shared Cisco-style CLI transport & managers
│       │   ├── cisco_cli.py #     CiscoCLITransport (SSH + paramiko)
│       │   └── cisco_managers.py  CiscoVLANManager, CiscoPortManager, CiscoLACPManager
│       ├── cisco/           #   Cisco Catalyst 1200
│       │   ├── client.py    #     CiscoSwitch — SSH only
│       │   └── managers.py  #     CiscoCLIMonitoringManager, CiscoCatalyst*Manager
│       ├── mikrotik/        #   MikroTik RouterOS
│       │   ├── client.py    #     MikroTikSwitch
│       │   ├── managers.py  #     MikroTik*Manager
│       │   ├── rest.py      #     MikroTikRESTTransport
│       │   └── ssh.py       #     RouterOSTransport
│       ├── qnap/            #   QNAP QSW
│       │   ├── client.py    #     QNAPSwitch
│       │   ├── rest.py      #     QNAPRESTTransport, QNAPMonitoringManager
│       │   ├── vlan_rest.py #     QNAPRESTVLANManager (apply_port_membership)
│       │   └── utils.py     #     generate_enable_password()
│       ├── netgear/         #   Netgear Smart Managed Pro (SNMP)
│       │   ├── client.py    #     NetgearSwitch
│       │   ├── backend.py   #     SNMP backend (read-back polling, save)
│       │   ├── snmp_managers.py  Netgear*Manager
│       │   ├── routing.py   #     NetgearRoutingManager
│       │   └── web.py       #     NetgearWeb (static routes)
│       └── netgear_plus/    #   Netgear Smart Managed Plus (web UI)
│           ├── client.py    #     NetgearPlusSwitch
│           ├── managers.py  #     NetgearPlus*Manager (reconcile)
│           └── sdk.py       #     netgear-tool wrapper
├── discovery/               # Network topology discovery
│   ├── __init__.py          #   Public API: NetworkTopologyScanner, MermaidGenerator, ...
│   ├── cli.py               #   Discovery CLI (networkmgmt-discover)
│   ├── models.py            #   Pydantic models: NetworkTopology, DiscoveredHost, ...
│   ├── _util.py             #   Shared helpers: _run_cmd, _validate_*, _strip_hostname
│   ├── oui.py               #   IEEE OUI database loading and vendor lookup
│   ├── categorize.py        #   Device category classification rules
│   ├── scanner.py           #   NetworkTopologyScanner (ARP, ping, nmap, DNS, traceroute)
│   ├── snmp.py              #   SnmpBridgeDiscovery (SNMP MAC table queries)
│   ├── lldp.py              #   LldpDiscovery (SSH + lldpctl)
│   └── mermaid.py           #   MermaidGenerator (flowchart diagram output)
└── snmp_vlan_dump/          # SNMP VLAN-port dump
    ├── __init__.py
    ├── cli.py               #   VLAN dump CLI (networkmgmt-vlan-dump)
    ├── collector.py         #   VlanDataCollector
    ├── formatters.py        #   TerminalFormatter, MarkdownFormatter
    ├── mermaid.py           #   VlanMermaidGenerator
    └── snmp.py              #   SNMP operations
```

## Adding a New Vendor

1. Create `networkmgmt/switchctrl/vendors/<name>/` with `__init__.py`, `client.py`, and optionally `managers.py`
2. Decorate your client class with `@register_vendor("<name>")`
3. Add `import networkmgmt.switchctrl.vendors.<name>` to `networkmgmt/switchctrl/vendors/__init__.py`
4. The CLI picks it up automatically via `list_vendors()`


## License
This project is licensed under the LGPL where applicable/possible — see [LICENSE.md](LICENSE.md). Some files/parts may use other licenses: [MIT](LICENSEMIT.md) | [GPL](LICENSEGPL.md) | [LGPL](LICENSELGPL.md). Always check per‑file headers/comments.


## Authors
- Repo owner (primary author)
- Additional attributions are noted inline in code comments


## Acknowledgments
- Inspirations and snippets are referenced in code comments where appropriate.


## ⚠️ Note

This is a development/experimental project. For production use, review security settings, customize configurations, and test thoroughly in your environment. Provided "as is" without warranty of any kind, express or implied, including but not limited to the warranties of merchantability, fitness for a particular purpose and noninfringement. In no event shall the authors or copyright holders be liable for any claim, damages or other liability, whether in an action of contract, tort or otherwise, arising from, out of or in connection with the software or the use or other dealings in the software. Use at your own risk.