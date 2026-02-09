# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Development Commands

- **Install/setup venv:** `make install` (uses Python 3.14)
- **Run tests:** `make tests` (runs pytest)
- **Run a single test:** `pytest tests/test_switchctrl_cisco_parsing.py::TestParseShowVlan::test_parse_show_vlan_basic`
- **Format code:** `make lint` (black, line length 120)
- **Sort imports:** `make isort` (isort with black profile, line length 120)
- **Type check:** `make tcheck` (mypy, strict mode, excludes tests/)
- **Secret scan:** `make gitleaks`
- **All pre-commit hooks:** `make commit-checks`
- **Full validation:** `make prepare` (tests + commit-checks)
- **Build package:** `make pypibuild` (hatch)
- **Build Docker image:** `make docker`

## Code Style

- Python 3.14+ required
- Line length: 120 characters
- Formatter: black, import sorter: isort (black profile)
- Strict mypy: `disallow_untyped_defs`, `disallow_incomplete_defs`, `no_implicit_optional` (tests excluded)
- Logging: loguru (not stdlib logging)

## Architecture

Three independent subsystems under `networkmgmt/`:

### switchctrl — Multi-Vendor Switch Management

Uses a **factory + registry pattern** for vendor extensibility:

- `factory.py` holds `_VENDOR_REGISTRY` dict and `@register_vendor(name)` decorator
- `vendors/__init__.py` imports all vendor modules, triggering auto-registration
- New vendors: subclass `BaseSwitchClient` (ABC in `base/client.py`), implement four manager properties (`monitoring`, `vlan`, `port`, `lacp`), decorate with `@register_vendor`
- Managers are **lazy-initialized** on first property access

**Transport layer:** `BaseTransport` ABC in `base/transport.py`. Vendors may use dual transports (REST for monitoring, SSH for config). QNAP reuses `CiscoCLITransport` and Cisco manager implementations since QNAP QSW uses Cisco-style CLI.

**Exception hierarchy:** `SwitchError` base with `AuthenticationError`, `APIError`, `SSHError`, `VLANError`, `PortError`, `LACPError` in `exceptions.py`.

**Models:** Plain dataclasses in `models/` (PortConfig, VLAN, SystemInfo, etc.).

### discovery — Network Topology Discovery

Pipeline-based scanner (`NetworkTopologyScanner` in `scanner.py`) that chains: ARP scan → DNS → OUI lookup → optional nmap/SNMP/LLDP/traceroute → categorization.

**Data models:** Pydantic v2 in `models.py` (`NetworkTopology`, `DiscoveredHost`, `SubnetScan`, etc.).

**Optional dependencies** with graceful degradation: `scapy` (ARP scan), `pysnmp` (SNMP bridge tables) — checked via `HAS_SCAPY`/`HAS_PYSNMP` flags.

**Output:** Mermaid diagrams (flat/categorized/hierarchical/auto styles) via `mermaid.py`, or JSON.

### snmp_vlan_dump — SNMP VLAN Port Mapping

Collects VLAN-port assignments from Netgear switches via SNMPv2c. Models use Pydantic v2. Output via terminal/markdown formatters or Mermaid diagrams.

## Testing

- All tests in `tests/`, organized by module (e.g., `test_switchctrl_cisco_parsing.py`)
- Shared fixtures in `conftest.py`: mock transports (`mock_cisco_transport`, `mock_routeros_transport`, etc.) and factory fixtures (`sample_discovered_host`, `sample_vlan_dump_data`)
- Tests use class-based grouping and mock-heavy isolation
- pytest.ini config: `python_files=tests/*.py`

## CI Pipeline

Push/PR to main triggers: black check → mypy → pytest. On main after tests pass: multi-arch Docker build (amd64/arm64) pushed to Docker Hub (`xomoxcc/networkmgmt`).
