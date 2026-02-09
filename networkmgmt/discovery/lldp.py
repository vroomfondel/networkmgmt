"""LLDP-based L2 topology discovery."""

from __future__ import annotations

import concurrent.futures
import json
import subprocess
from pathlib import Path

from loguru import logger

from networkmgmt.discovery._util import _strip_hostname_suffix
from networkmgmt.discovery.models import (
    DiscoveredHost,
    L2TopologyEntry,
    SwitchPortMapping,
)


class LldpDiscovery:
    """Discover L2 topology by SSH-ing into hosts and querying lldpctl."""

    def __init__(self, hosts: list[DiscoveredHost]):
        self.hosts = hosts

    def collect(self, output_dir: Path) -> None:
        """SSH into each host in parallel, write raw lldpctl JSON to files."""
        targets = [h for h in self.hosts if h.ip and not h.is_gateway]
        if not targets:
            return

        logger.info(f"LLDP collect: querying {len(targets)} hosts via SSH...")
        done = 0
        total = len(targets)
        written = 0

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
            futures = {pool.submit(self._query_host_raw, h): h for h in targets}
            for future in concurrent.futures.as_completed(futures):
                host = futures[future]
                done += 1
                try:
                    raw_json = future.result()
                    if raw_json is not None:
                        out_file = output_dir / f"{host.ip}.json"
                        out_file.write_text(raw_json)
                        written += 1
                        logger.info(f"  [{done}/{total}] {host.ip}: written to {out_file}")
                    else:
                        logger.debug(f"  [{done}/{total}] {host.ip}: no LLDP data")
                except Exception as e:
                    logger.debug(f"  [{done}/{total}] {host.ip}: failed: {e}")

        logger.info(f"LLDP collect: {written}/{total} hosts written to {output_dir}")

    @staticmethod
    def _query_host_raw(host: DiscoveredHost) -> str | None:
        """SSH into a single host, run lldpctl -f json, return raw stdout."""
        try:
            result = subprocess.run(
                [
                    "ssh",
                    "-o",
                    "StrictHostKeyChecking=no",
                    "-o",
                    "ConnectTimeout=5",
                    "-o",
                    "BatchMode=yes",
                    f"root@{host.ip}",
                    "lldpctl",
                    "-f",
                    "json",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
        except subprocess.TimeoutExpired, FileNotFoundError:
            return None

        if result.returncode != 0 or not result.stdout.strip():
            return None

        # Validate JSON before returning
        try:
            json.loads(result.stdout)
        except json.JSONDecodeError:
            return None

        return result.stdout

    @staticmethod
    def load_and_parse(
        lldp_dir: Path,
        hosts: list[DiscoveredHost],
    ) -> list[L2TopologyEntry]:
        """Read LLDP JSON files from a directory and parse into L2 entries."""
        # Build lookup tables for matching LLDP chassis to known hosts
        mac_to_host: dict[str, DiscoveredHost] = {}
        name_to_host: dict[str, DiscoveredHost] = {}
        ip_to_host: dict[str, DiscoveredHost] = {}
        for h in hosts:
            if h.mac:
                mac_to_host[h.mac.lower()] = h
            if h.hostname:
                name_to_host[_strip_hostname_suffix(h.hostname).lower()] = h
            if h.ip:
                ip_to_host[h.ip] = h

        entries: list[L2TopologyEntry] = []
        json_files = sorted(lldp_dir.glob("*.json"))
        if not json_files:
            logger.warning(f"LLDP: no JSON files found in {lldp_dir}")
            return entries

        for json_file in json_files:
            host_ip = json_file.stem  # e.g. "192.168.101.50"
            host = ip_to_host.get(host_ip)
            if host is None:
                logger.debug(f"LLDP: {json_file.name}: host {host_ip} not in discovered hosts, skipping")
                continue

            try:
                data = json.loads(json_file.read_text())
            except (json.JSONDecodeError, OSError) as e:
                logger.debug(f"LLDP: {json_file.name}: failed to read: {e}")
                continue

            parsed = LldpDiscovery._parse_lldp_json(host, data, mac_to_host, name_to_host)
            entries.extend(parsed)

        logger.info(f"LLDP: parsed {len(entries)} entries from {len(json_files)} files in {lldp_dir}")
        return entries

    @staticmethod
    def _parse_lldp_json(
        host: DiscoveredHost,
        data: dict,
        mac_to_host: dict[str, DiscoveredHost],
        name_to_host: dict[str, DiscoveredHost],
    ) -> list[L2TopologyEntry]:
        """Parse lldpctl JSON output into L2TopologyEntry list."""
        entries: list[L2TopologyEntry] = []
        lldp = data.get("lldp", {})
        interfaces = lldp.get("interface", {})

        # lldpctl can return interface as a dict or a list of dicts
        if isinstance(interfaces, list):
            iface_items: list[tuple[str, dict]] = []
            for item in interfaces:
                if isinstance(item, dict):
                    for k, v in item.items():
                        iface_items.append((k, v))
        elif isinstance(interfaces, dict):
            iface_items = list(interfaces.items())
        else:
            return entries

        for _iface_name, iface_data in iface_items:
            if not isinstance(iface_data, dict):
                continue

            chassis = iface_data.get("chassis", {})
            port = iface_data.get("port", {})

            # Extract chassis name and MAC
            chassis_name = ""
            chassis_mac = ""

            # chassis can have a nested key (the name) or direct fields
            # Format varies: {"chassis": {"switch-name": {"id": {...}, ...}}}
            # or: {"chassis": {"id": {...}, "name": "switch-name"}}
            if "id" in chassis:
                # Direct format
                chassis_id = chassis.get("id", {})
                if isinstance(chassis_id, dict):
                    if chassis_id.get("type") == "mac":
                        chassis_mac = chassis_id.get("value", "").lower()
                chassis_name = chassis.get("name", "")
            else:
                # Nested format: first value is the actual chassis data
                for _key, cdata in chassis.items():
                    if isinstance(cdata, dict):
                        chassis_id = cdata.get("id", {})
                        if isinstance(chassis_id, dict):
                            if chassis_id.get("type") == "mac":
                                chassis_mac = chassis_id.get("value", "").lower()
                        chassis_name = cdata.get("name", _key)
                        break

            # Extract port info
            port_id = port.get("id", {})
            port_name = ""
            if isinstance(port_id, dict):
                port_name = port_id.get("value", "")
            port_descr = port.get("descr", "")
            if port_descr and not port_name:
                port_name = port_descr

            # Match switch to known host by MAC or name
            switch_ip = ""
            switch_name = chassis_name

            if chassis_mac and chassis_mac in mac_to_host:
                switch_ip = mac_to_host[chassis_mac].ip
            elif chassis_name:
                norm_name = _strip_hostname_suffix(chassis_name).lower()
                if norm_name in name_to_host:
                    switch_ip = name_to_host[norm_name].ip

            entries.append(
                L2TopologyEntry(
                    host_ip=host.ip,
                    host_mac=host.mac,
                    switch=SwitchPortMapping(
                        switch_ip=switch_ip,
                        switch_name=switch_name,
                        port_index=0,
                        port_name=port_name,
                    ),
                    source="lldp",
                )
            )

        return entries

    @staticmethod
    def build_l2_from_lldp(
        lldp_entries: list[L2TopologyEntry],
    ) -> tuple[list[L2TopologyEntry], dict[str, str]]:
        """Build topology tree from LLDP entries.

        Returns (l2_entries, topology_tree) with same signature as
        SnmpBridgeDiscovery.build_l2_topology().
        """
        topology_tree: dict[str, str] = {}
        for entry in lldp_entries:
            if entry.switch.switch_ip:
                topology_tree[entry.host_ip] = entry.switch.switch_ip
        return lldp_entries, topology_tree
