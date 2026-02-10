[![black-lint](https://github.com/vroomfondel/networkmgmt/actions/workflows/checkblack.yml/badge.svg)](https://github.com/vroomfondel/networkmgmt/actions/workflows/checkblack.yml)
[![mypy and pytests](https://github.com/vroomfondel/networkmgmt/actions/workflows/mypynpytests.yml/badge.svg)](https://github.com/vroomfondel/networkmgmt/actions/workflows/mypynpytests.yml)
[![BuildAndPushMultiarch](https://github.com/vroomfondel/networkmgmt/actions/workflows/buildmultiarchandpush.yml/badge.svg)](https://github.com/vroomfondel/networkmgmt/actions/workflows/buildmultiarchandpush.yml)
![Cumulative Clones](https://img.shields.io/endpoint?logo=github&url=https://gist.githubusercontent.com/vroomfondel/906f5c4deb7e6e6bcd6ee0f7c96f586c/raw/networkmgmt_clone_count.json)
[![Docker Pulls](https://img.shields.io/docker/pulls/xomoxcc/networkmgmt?logo=docker)](https://hub.docker.com/r/xomoxcc/networkmgmt/tags)
[![PyPI](https://img.shields.io/pypi/v/networkmgmt?logo=python&logoColor=white)](https://pypi.org/project/networkmgmt/)

[![Gemini_Generated_Image_8bu6gi8bu6gi8bu6_250x250.png](https://raw.githubusercontent.com/vroomfondel/networkmgmt/main/Gemini_Generated_Image_8bu6gi8bu6gi8bu6_250x250.png)](https://hub.docker.com/r/xomoxcc/networkmgmt/tags)

# WIP !!!

# networkmgmt — Network Management Toolkit

CLI tool and Python library for multi-vendor switch management and network topology discovery.

Quick links:
- [Docker Hub](https://hub.docker.com/r/xomoxcc/networkmgmt/tags) — multi-arch image (amd64/arm64)
- [PyPI](https://pypi.org/project/networkmgmt/) — `pip install networkmgmt`
- [GitHub](https://github.com/vroomfondel/networkmgmt) — source, issues, CI

## Modules

| Module | Description |
|--------|-------------|
| **networkmgmt.switchctrl** | Multi-vendor switch management (Cisco, MikroTik, QNAP, Netgear) |
| **networkmgmt.discovery** | Network topology discovery (ARP, DNS, SNMP, LLDP, nmap, traceroute) |
| **networkmgmt.snmp_vlan_dump** | SNMP VLAN-port dump for Netgear switches |

## Supported Switch Vendors

| Vendor | Model(s) | Transport | Notes |
|--------|----------|-----------|-------|
| **cisco** | Catalyst 1200 (C1200-8T-D) | SSH CLI only | 8 GE ports (`gi1`–`gi8`), no REST API, enable password required |
| **mikrotik** | RouterOS v7+ switches | REST API + SSH CLI | REST for monitoring, SSH for configuration |
| **qnap** | QSW-M408 series | REST API + SSH CLI | Cisco-style CLI, enable password auto-generated from serial |
| **netgear** | Managed switches | — | Stub (not yet implemented) |

## CLI Structure

`networkmgmt` is an orchestrator that dispatches to three sub-CLIs. Each sub-CLI can also be invoked directly:

| Orchestrator command | Direct script | Description |
|---|---|---|
| `networkmgmt switchctrl ...` | `networkmgmt-switchctrl` | Multi-vendor switch management |
| `networkmgmt discover ...` | `networkmgmt-discover` | Network topology discovery |
| `networkmgmt vlan-dump ...` | `networkmgmt-vlan-dump` | SNMP VLAN-port dump |

## Installation

```bash
pip install .
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

# SNMP L2 topology (query switch MAC tables)
networkmgmt-discover -i eth0 --switches 192.168.1.38:public,192.168.1.19

# LLDP-based topology
networkmgmt-discover -i eth0 --lldp-collect /tmp/lldp

# Manual topology + diagram style
networkmgmt-discover -i eth0 --topology 192.168.1.50:192.168.1.19:g3 -d hierarchical

# Traceroute to external targets
networkmgmt-discover -i eth0 -t 8.8.8.8,1.1.1.1
sudo networkmgmt-discover -i eth0 -t 192.168.1.0/24
```

### Discovery Options

| Option | Description |
|--------|-------------|
| `-i` / `--interface` | Network interface(s), comma-separated (default: auto-detect) |
| `-t` / `--targets` | Traceroute targets: IPs, CIDR (`192.168.1.0/24`), or range (`192.168.1.1-254`) |
| `--nmap` | Enable nmap service detection |
| `--top-ports` | nmap top ports to scan (default: 100) |
| `-o` / `--output` | Output file (default: stdout) |
| `--format` | `mermaid` (default) or `json` |
| `--timeout` | Scan timeout in seconds (default: 10) |
| `--max-hops` | Max traceroute hops (default: 30) |
| `--trace-local` | Tracepath to all LAN hosts to detect switch hierarchy |
| `--switches` | SNMP L2 discovery: `IP[:community],...` (default community: `public`) |
| `--lldp-collect` | SSH into hosts, collect lldpctl JSON to directory |
| `--lldp-dir` | Read existing LLDP JSON files from directory |
| `--topology` | Manual L2 topology: `HOST:SWITCH:PORT,...` |
| `-d` / `--diagram-style` | `auto` (default), `flat`, `categorized`, `hierarchical` |
| `--direction` | Mermaid flowchart direction: `LR` or `TD` (default: auto) |
| `--elk` | Use ELK layout engine for large diagrams |
| `-v` / `--verbose` | Verbose logging |

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

## Switch Management CLI

```bash
networkmgmt-switchctrl --vendor <VENDOR> --host <IP> --password <PW> [options] <command>
# or: networkmgmt switchctrl --vendor <VENDOR> --host <IP> --password <PW> [options] <command>
```

### Global Options

| Option | Description |
|--------|-------------|
| `--vendor` | Switch vendor (`cisco`, `mikrotik`, `netgear`, `qnap`) |
| `--host` | Switch IP address or hostname |
| `--password` | Admin password |
| `--username` | Username (default: `admin`) |
| `--enable-password` | Enable password (Cisco: required; QNAP: auto-generated if omitted) |
| `--ssh-port` | SSH port (default: 22) |
| `--rest-port` | REST API port (default: 443) |
| `--ssh-username` | SSH username (vendor-specific default if omitted) |
| `--ssh-password` | SSH password (vendor-specific default if omitted) |
| `--verify-ssl` | Verify SSL certificates for REST API |
| `-v` / `--verbose` | Enable debug logging |

### Commands

#### `monitor` — Show system info, sensors, ports, and LACP status

```bash
# Cisco Catalyst 1200
networkmgmt-switchctrl --vendor cisco --host 192.168.1.254 \
    --username admin --password <PW> --enable-password <EN> monitor

# QNAP QSW
networkmgmt-switchctrl --vendor qnap --host 192.168.1.1 --password <PW> monitor
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

- **Dual transport** — REST API for monitoring, SSH (Cisco-style CLI) for configuration
- **Enable password** can be auto-generated from the switch serial number
- **Default SSH credentials**: `guest` / `guest123`

### MikroTik

- **Dual transport** — REST API (RouterOS v7+) for monitoring, SSH for configuration
- **No enable mode** — RouterOS uses direct admin access

## Architecture

```
networkmgmt/
├── __init__.py              # Public API: create_switch(), list_vendors()
├── __main__.py              # Orchestrator CLI — dispatches to sub-CLIs
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
│       │   └── utils.py     #     generate_enable_password()
│       └── netgear/         #   Netgear (stub)
│           └── client.py    #     NetgearSwitch (NotImplementedError)
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