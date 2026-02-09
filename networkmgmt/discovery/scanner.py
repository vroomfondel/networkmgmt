"""NetworkTopologyScanner — core discovery pipeline (ARP, ping, nmap, DNS, traceroute)."""

from __future__ import annotations

import concurrent.futures
import ipaddress
import os
import re
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from networkmgmt.discovery._util import _run_cmd, _validate_interface_name, _validate_ip
from networkmgmt.discovery.categorize import _categorize_host
from networkmgmt.discovery.lldp import LldpDiscovery
from networkmgmt.discovery.models import (
    DiscoveredHost,
    L2TopologyEntry,
    NetworkInterface,
    NetworkTopology,
    SubnetScan,
    SwitchPortMapping,
    TracerouteHop,
    TraceroutePath,
)
from networkmgmt.discovery.oui import load_oui_db, lookup_vendor
from networkmgmt.discovery.snmp import HAS_PYSNMP, SnmpBridgeDiscovery

# Optional scapy import
try:
    from scapy.all import ARP, Ether, srp  # type: ignore[import-untyped]

    HAS_SCAPY = True
except ImportError:
    HAS_SCAPY = False


class NetworkTopologyScanner:
    def __init__(
        self,
        interfaces: list[str] | None = None,
        use_nmap: bool = False,
        top_ports: int = 100,
        timeout: int = 10,
        max_hops: int = 30,
    ):
        self.interfaces = interfaces or []
        self.interface: str | None = None  # set per-scan in run_discovery
        self.use_nmap = use_nmap
        self.top_ports = top_ports
        self.timeout = timeout
        self.max_hops = max_hops
        self.is_root = os.geteuid() == 0
        self.oui_db: dict[str, str] = {}
        self._trace_cmd: str | None = None  # cached: "traceroute", "tracepath", or ""

    def discover_local_interface(self) -> NetworkInterface:
        """Detect local interface, IP, netmask, MAC from ip route/addr."""
        # Find default routes for all interfaces
        route_output = _run_cmd(["ip", "-4", "route", "show", "default"])
        default_iface = ""
        default_gateways: dict[str, str] = {}

        for line in route_output.splitlines():
            match = re.match(r"default via (\S+) dev (\S+)", line)
            if match:
                default_gateways[match.group(2)] = match.group(1)
                if not default_iface:
                    default_iface = match.group(2)

        iface_name = self.interface or default_iface
        if not iface_name:
            logger.error("No interface found. Specify with -i.")
            sys.exit(1)

        if not _validate_interface_name(iface_name):
            logger.error(f"Invalid interface name: {iface_name}")
            sys.exit(1)

        # Gateway: check default routes first, then per-device routes
        gateway_ip = default_gateways.get(iface_name, "")
        if not gateway_ip:
            dev_routes = _run_cmd(["ip", "-4", "route", "show", "dev", iface_name])
            for line in dev_routes.splitlines():
                m = re.search(r"via (\S+)", line)
                if m:
                    gateway_ip = m.group(1)
                    break

        # Get interface details
        addr_output = _run_cmd(["ip", "-4", "addr", "show", "dev", iface_name])
        ip_addr = ""
        netmask = ""
        mac = ""

        for line in addr_output.splitlines():
            line = line.strip()
            m = re.match(r"inet (\S+)/(\d+)", line)
            if m:
                ip_addr = m.group(1)
                prefix_len = int(m.group(2))
                net = ipaddress.IPv4Network(f"0.0.0.0/{prefix_len}", strict=False)
                netmask = str(net.netmask)

        # Get MAC
        link_output = _run_cmd(["ip", "link", "show", "dev", iface_name])
        for line in link_output.splitlines():
            m = re.search(r"link/ether ([0-9a-f:]{17})", line)
            if m:
                mac = m.group(1)

        self._gateway_ip = gateway_ip
        self._iface_name = iface_name
        self._local_ip = ip_addr
        self._prefix_len = (
            int(re.search(r"/(\d+)", addr_output).group(1))  # type: ignore[union-attr]
            if re.search(r"/(\d+)", addr_output)
            else 24
        )

        return NetworkInterface(
            name=iface_name,
            ip=ip_addr,
            netmask=netmask,
            mac=mac,
            is_default=(iface_name == default_iface),
        )

    def scan_local_subnet(self) -> list[tuple[str, str]]:
        """ARP scan the local subnet. Returns list of (ip, mac) tuples."""
        network = ipaddress.IPv4Network(f"{self._local_ip}/{self._prefix_len}", strict=False)

        if self.is_root and HAS_SCAPY:
            return self._scapy_arp_scan(str(network))
        else:
            if not self.is_root:
                logger.info("Not running as root, using ARP table + ping sweep fallback")
            elif not HAS_SCAPY:
                logger.info("scapy not available, using ARP table + ping sweep fallback")
            return self._fallback_scan(network)

    def _scapy_arp_scan(self, network: str) -> list[tuple[str, str]]:
        """ARP scan using scapy (requires root)."""
        logger.info(f"ARP scanning {network} via scapy...")
        results: list[tuple[str, str]] = []

        arp = ARP(pdst=network)
        ether = Ether(dst="ff:ff:ff:ff:ff:ff")
        packet = ether / arp

        answered, _ = srp(packet, timeout=self.timeout, verbose=False, iface=self._iface_name)

        for _, received in answered:
            results.append((received.psrc, received.hwsrc))

        logger.info(f"Found {len(results)} hosts via ARP scan")
        return results

    def _read_arp_table(self) -> list[tuple[str, str]]:
        """Read current ARP table for the interface (IPv4 only)."""
        arp_output = _run_cmd(["ip", "-4", "neigh", "show", "dev", self._iface_name])
        results: list[tuple[str, str]] = []

        for line in arp_output.splitlines():
            m = re.match(r"(\S+)\s+lladdr\s+([0-9a-f:]{17})\s+\S+", line)
            if m and _validate_ip(m.group(1)):
                results.append((m.group(1), m.group(2)))

        return results

    def _fallback_scan(self, network: ipaddress.IPv4Network) -> list[tuple[str, str]]:
        """Fallback: read ARP table first, ping sweep only if needed."""
        # Check existing ARP table first
        results = self._read_arp_table()
        if results:
            logger.info(f"Found {len(results)} hosts in existing ARP table, " "skipping ping sweep")
            return results

        # ARP table empty — try fping (fast), then concurrent pings
        logger.info(f"ARP table empty, ping sweeping {network}...")

        fping_output = _run_cmd(
            ["fping", "-a", "-q", "-g", str(network), "-r", "1", "-t", "200"],
            timeout=self.timeout + 30,
        )

        if not fping_output:
            # Concurrent pings via subprocess (up to 50 at a time)
            hosts = list(network.hosts())[:256]
            logger.info(f"fping not available, pinging {len(hosts)} hosts concurrently...")

            def ping_one(ip: str) -> None:
                subprocess.run(
                    ["ping", "-c", "1", "-W", "1", ip],
                    capture_output=True,
                    timeout=3,
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=50) as pool:
                pool.map(ping_one, [str(h) for h in hosts])

        results = self._read_arp_table()
        logger.info(f"Found {len(results)} hosts via ARP table after ping sweep")
        return results

    def identify_hosts(
        self,
        raw_hosts: list[tuple[str, str]],
        gateway_ip: str = "",
    ) -> list[DiscoveredHost]:
        """Enrich raw (ip, mac) with hostname, vendor, optional services.

        Args:
            raw_hosts: List of (ip, mac) tuples from ARP scan.
            gateway_ip: Gateway IP for this subnet. If empty, falls back to
                        self._gateway_ip for backwards compatibility.
        """
        gw_ip = gateway_ip or self._gateway_ip
        self.oui_db = load_oui_db()
        hosts: list[DiscoveredHost] = []

        total = sum(1 for ip, _ in raw_hosts if ip != self._local_ip)

        for idx, (ip, mac) in enumerate(raw_hosts, start=1):
            if ip == self._local_ip:
                continue

            hostname = self._reverse_dns(ip)
            vendor = lookup_vendor(mac, self.oui_db)
            is_gw = ip == gw_ip
            services: list[str] = []

            if self.use_nmap and _validate_ip(ip):
                display = hostname or ip
                logger.info(f"[{idx}/{total}] nmap scanning {display} ({ip})...")
                services = self._nmap_scan(ip)

            hosts.append(
                DiscoveredHost(
                    ip=ip,
                    mac=mac,
                    hostname=hostname,
                    vendor=vendor,
                    services=services,
                    is_gateway=is_gw,
                )
            )

        # If gateway wasn't found in ARP results, add it
        if gw_ip and not any(h.ip == gw_ip for h in hosts):
            hostname = self._reverse_dns(gw_ip)
            hosts.insert(
                0,
                DiscoveredHost(
                    ip=gw_ip,
                    hostname=hostname,
                    is_gateway=True,
                ),
            )

        # Sort: gateway first, then by IP
        hosts.sort(key=lambda h: (not h.is_gateway, ipaddress.IPv4Address(h.ip)))

        # Categorize hosts
        for host in hosts:
            host.category = _categorize_host(host).value

        return hosts

    def _reverse_dns(self, ip: str) -> str:
        """Reverse DNS lookup, returns empty string on failure."""
        try:
            hostname, _, _ = socket.gethostbyaddr(ip)
            return hostname
        except socket.herror, socket.gaierror, OSError:
            return ""

    def _nmap_scan(self, ip: str) -> list[str]:
        """nmap service scan with live output streaming via Popen."""
        cmd = [
            "nmap",
            "-sV",
            "--top-ports",
            str(self.top_ports),
            "-T4",
            "--open",
            "--stats-every",
            "5s",
            ip,
        ]

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except FileNotFoundError:
            logger.warning("nmap not found in PATH")
            return []

        services: list[str] = []
        all_lines: list[str] = []

        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                line = line.rstrip("\n")
                all_lines.append(line)

                # Print nmap progress/status lines in real time
                if "Stats:" in line or "Timing:" in line or "% done" in line:
                    logger.info(f"  nmap: {line.strip()}")
                elif line.startswith("Nmap scan report") or line.startswith("Nmap done"):
                    logger.info(f"  nmap: {line.strip()}")

                # Parse open port lines: 22/tcp open ssh OpenSSH 8.9p1
                m = re.match(r"\s*(\d+/\w+)\s+open\s+(\S+)\s*(.*)", line)
                if m:
                    port = m.group(1)
                    service = m.group(2)
                    version = m.group(3).strip()
                    entry = f"{port} {service}"
                    if version:
                        entry += f" ({version})"
                    services.append(entry)
                    logger.info(f"  nmap: found {entry}")

            proc.wait(timeout=self.timeout + 60)
        except subprocess.TimeoutExpired:
            logger.warning(f"nmap timed out scanning {ip}")
            proc.kill()
            proc.wait()

        return services

    def traceroute_targets(self, targets: list[str]) -> list[TraceroutePath]:
        """Run traceroute to each target and parse the output."""
        paths: list[TraceroutePath] = []

        for target in targets:
            if not _validate_ip(target):
                # Try resolving hostname
                try:
                    ipaddress.ip_address(target)
                except ValueError:
                    try:
                        socket.gethostbyname(target)
                    except socket.gaierror:
                        logger.warning(f"Cannot resolve target: {target}")
                        continue

            path = self._run_traceroute(target)
            paths.append(path)

        return paths

    def _detect_trace_cmd(self) -> str:
        """Detect available trace tool once, cache the result."""
        if self._trace_cmd is not None:
            return self._trace_cmd

        import shutil

        if shutil.which("tracepath"):
            self._trace_cmd = "tracepath"
            logger.info("Using tracepath for route tracing")
        elif shutil.which("traceroute"):
            self._trace_cmd = "traceroute"
            logger.info("Using traceroute for route tracing")
        else:
            self._trace_cmd = ""
            logger.warning("Neither tracepath nor traceroute found in PATH")

        return self._trace_cmd

    def _run_traceroute(
        self,
        target: str,
        local_max_hops: int | None = None,
        timeout_secs: int | None = None,
    ) -> TraceroutePath:
        """Run tracepath (preferred) or traceroute and parse output."""
        cmd = self._detect_trace_cmd()
        if not cmd:
            return TraceroutePath(target=target)

        max_hops = local_max_hops or self.max_hops
        timeout = timeout_secs or (max_hops * 3 + 10)
        logger.debug(f"Running {cmd} to {target} (max_hops={max_hops}, timeout={timeout}s)...")
        path: TraceroutePath | None = None

        if cmd == "tracepath":
            output = _run_cmd(
                ["tracepath", "-n", "-m", str(max_hops), target],
                timeout=timeout,
            )
            if output:
                path = self._parse_tracepath(target, output)
        else:
            output = _run_cmd(
                ["traceroute", "-n", "-m", str(max_hops), "-w", "2", target],
                timeout=timeout,
            )
            if output:
                path = self._parse_traceroute(target, output)

        if path is None:
            return TraceroutePath(target=target)

        return self._cleanup_trace(path)

    @staticmethod
    def _cleanup_trace(path: TraceroutePath) -> TraceroutePath:
        """Collapse incomplete traces: keep real hops, replace trailing timeouts with one UNREACHABLE."""
        if path.completed:
            return path

        real_hops = [h for h in path.hops if not h.is_timeout and h.ip]

        if not real_hops:
            # All timeouts — single unreachable marker
            path.hops = [
                TracerouteHop(
                    hop_number=1,
                    ip=path.target,
                    hostname="UNREACHABLE",
                    is_timeout=True,
                )
            ]
        else:
            # Keep real hops, add one trailing unreachable if there were timeouts after
            last_real = real_hops[-1].hop_number
            had_trailing_timeouts = any(h.is_timeout and h.hop_number > last_real for h in path.hops)
            if had_trailing_timeouts:
                real_hops.append(
                    TracerouteHop(
                        hop_number=last_real + 1,
                        ip=path.target,
                        hostname="UNREACHABLE",
                        is_timeout=True,
                    )
                )
            path.hops = real_hops

        return path

    def _parse_traceroute(self, target: str, output: str) -> TraceroutePath:
        """Parse standard traceroute -n output."""
        hops: list[TracerouteHop] = []
        completed = False

        for line in output.splitlines():
            # Match: " 1  10.0.0.1  1.234 ms  1.456 ms  1.789 ms"
            m = re.match(r"\s*(\d+)\s+(.+)", line)
            if not m:
                continue

            hop_num = int(m.group(1))
            rest = m.group(2).strip()

            if rest.startswith("* * *"):
                hops.append(TracerouteHop(hop_number=hop_num, is_timeout=True))
                continue

            # Parse IP and RTT
            ip_match = re.match(r"(\S+)\s+(.+)", rest)
            if ip_match:
                hop_ip = ip_match.group(1)
                rtt_rest = ip_match.group(2)

                rtt_match = re.search(r"([\d.]+)\s*ms", rtt_rest)
                rtt = float(rtt_match.group(1)) if rtt_match else 0.0

                hostname = self._reverse_dns(hop_ip) if _validate_ip(hop_ip) else ""

                hops.append(
                    TracerouteHop(
                        hop_number=hop_num,
                        ip=hop_ip,
                        hostname=hostname,
                        rtt_ms=rtt,
                    )
                )

                try:
                    target_ip = socket.gethostbyname(target)
                    if hop_ip == target_ip:
                        completed = True
                except socket.gaierror:
                    if hop_ip == target:
                        completed = True

        return TraceroutePath(target=target, hops=hops, completed=completed)

    def _parse_tracepath(self, target: str, output: str) -> TraceroutePath:
        """Parse tracepath -n output.

        Format: " 1:  192.168.1.1    0.710ms"
        or      " 1?: [LOCALHOST]     pmtu 1500"
        """
        hops: list[TracerouteHop] = []
        completed = False
        seen_hops: set[int] = set()

        for line in output.splitlines():
            # Match: " 1:  IP  RTTms" or " 1?: ..."
            m = re.match(r"\s*(\d+)[?]?:\s+(.+)", line)
            if not m:
                continue

            hop_num = int(m.group(1))
            rest = m.group(2).strip()

            # Skip LOCALHOST/pmtu/Resume lines
            if "[LOCALHOST]" in rest or rest.startswith("Resume:") or "Too many hops" in rest:
                continue

            # Skip duplicate hop numbers (tracepath shows multiple probes)
            if hop_num in seen_hops:
                continue
            seen_hops.add(hop_num)

            if rest.startswith("no reply"):
                hops.append(TracerouteHop(hop_number=hop_num, is_timeout=True))
                continue

            # Parse: "IP  RTTms" or "IP  RTTms asymm N"
            ip_match = re.match(r"(\S+)\s+([\d.]+)ms", rest)
            if ip_match:
                hop_ip = ip_match.group(1)
                rtt = float(ip_match.group(2))

                hostname = self._reverse_dns(hop_ip) if _validate_ip(hop_ip) else ""

                hops.append(
                    TracerouteHop(
                        hop_number=hop_num,
                        ip=hop_ip,
                        hostname=hostname,
                        rtt_ms=rtt,
                    )
                )

                try:
                    target_ip = socket.gethostbyname(target)
                    if hop_ip == target_ip:
                        completed = True
                except socket.gaierror:
                    if hop_ip == target:
                        completed = True

        return TraceroutePath(target=target, hops=hops, completed=completed)

    def trace_local_hosts(self, hosts: list[DiscoveredHost]) -> list[TraceroutePath]:
        """Run tracepath/traceroute to all discovered LAN hosts in parallel.

        Uses aggressive timeouts (5s) since local hosts should respond in <1s
        if reachable through intermediate routers.
        """
        targets = [h.ip for h in hosts if not h.is_gateway and h.ip != self._local_ip]
        if not targets:
            return []

        total = len(targets)
        logger.info(f"Running tracepath to {total} local hosts (20 parallel, 5s timeout)...")
        paths: list[TraceroutePath] = []
        done = 0

        def trace_one(ip: str) -> TraceroutePath:
            return self._run_traceroute(ip, local_max_hops=5, timeout_secs=5)

        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
            futures = {pool.submit(trace_one, ip): ip for ip in targets}
            for future in concurrent.futures.as_completed(futures):
                ip = futures[future]
                done += 1
                try:
                    path = future.result()
                    paths.append(path)
                    hops = len([h for h in path.hops if not h.is_timeout and h.ip])
                    logger.info(f"  [{done}/{total}] {ip}: {hops} hop(s)")
                except Exception as e:
                    logger.debug(f"Tracepath to {ip} failed: {e}")

        logger.info(f"Completed tracepath to {len(paths)}/{total} hosts")
        return paths

    def build_topology_tree(
        self,
        hosts: list[DiscoveredHost],
        trace_paths: list[TraceroutePath],
        gateway_ip: str,
    ) -> dict[str, str]:
        """Build a parent mapping from tracepath results.

        Returns dict {host_ip: parent_ip} where parent is either the gateway
        (for 1-hop / direct hosts) or the penultimate hop (for multi-hop hosts).
        Also marks hosts appearing as intermediate hops with is_infrastructure=True.
        """
        host_ips = {h.ip for h in hosts}
        tree: dict[str, str] = {}
        infrastructure_ips: set[str] = set()

        for path in trace_paths:
            # Filter out timeout hops
            real_hops = [h for h in path.hops if not h.is_timeout and h.ip]
            if not real_hops:
                # No usable hops — default to gateway
                tree[path.target] = gateway_ip
                continue

            if len(real_hops) == 1:
                # 1 hop (direct) — parent is gateway
                tree[path.target] = gateway_ip
            else:
                # 2+ hops — penultimate hop is direct parent
                penultimate = real_hops[-2]
                tree[path.target] = penultimate.ip
                # All intermediate hops (not first, not last) are infrastructure
                for hop in real_hops[:-1]:
                    if hop.ip in host_ips and hop.ip != gateway_ip:
                        infrastructure_ips.add(hop.ip)

        # Mark infrastructure hosts
        for host in hosts:
            if host.ip in infrastructure_ips:
                host.is_infrastructure = True

        return tree

    def run_discovery(
        self,
        traceroute_targets: list[str] | None = None,
        trace_local: bool = False,
        switches: list[tuple[str, str]] | None = None,
        lldp_collect_dir: Path | None = None,
        lldp_dir: Path | None = None,
        manual_topology: list[tuple[str, str, str]] | None = None,
    ) -> NetworkTopology:
        """Orchestrate full discovery pipeline.

        Source priority (later overrides earlier per host_ip):
        1. SNMP (--switches)
        2. LLDP (--lldp-collect / --lldp-dir)
        3. Manual (--topology)
        4. Traceroute (--trace-local) — only if no other sources provide data
        """
        logger.info("Starting network topology discovery...")

        interfaces_to_scan: list[str | None] = list(self.interfaces) if self.interfaces else [None]
        subnets: list[SubnetScan] = []
        all_hosts: list[DiscoveredHost] = []
        primary_iface: NetworkInterface | None = None
        primary_gateway: DiscoveredHost | None = None
        merged_tree: dict[str, str] = {}
        merged_l2: list[L2TopologyEntry] = []

        # SNMP bridge discovery (shared across subnets)
        mac_table: dict[str, list[SwitchPortMapping]] | None = None
        switch_ips: set[str] = set()
        if switches and HAS_PYSNMP:
            switch_ips = {ip for ip, _ in switches}
            snmp_discovery = SnmpBridgeDiscovery(switches)
            mac_table = snmp_discovery.discover()
            logger.info(f"SNMP: {len(mac_table)} unique MACs from {len(switches)} switch(es)")

        for iface_override in interfaces_to_scan:
            self.interface = iface_override
            iface = self.discover_local_interface()
            logger.info(f"Interface: {iface.name} ({iface.ip}/{iface.netmask})")

            if not iface.ip:
                logger.warning(f"Skipping {iface.name}: no IPv4 address assigned")
                continue

            if primary_iface is None:
                primary_iface = iface

            # Save gateway IP before it gets overwritten by next interface scan
            subnet_gateway_ip = self._gateway_ip

            raw_hosts = self.scan_local_subnet()
            hosts = self.identify_hosts(raw_hosts, gateway_ip=subnet_gateway_ip)
            logger.info(f"Identified {len(hosts)} hosts on {iface.name}")

            gateway = next((h for h in hosts if h.is_gateway), None)
            if primary_gateway is None and gateway:
                primary_gateway = gateway

            topology_tree: dict[str, str] = {}
            l2_entries: list[L2TopologyEntry] = []
            # Track which hosts have L2 data from any source
            has_l2_data = False

            # --- Source 1: SNMP ---
            if mac_table is not None:
                l2_entries, topology_tree = SnmpBridgeDiscovery.build_l2_topology(
                    hosts,
                    mac_table,
                    switch_ips,
                )
                # Mark switch hosts as infrastructure
                for host in hosts:
                    if host.ip in switch_ips:
                        host.is_infrastructure = True
                if l2_entries:
                    has_l2_data = True
                logger.info(
                    f"L2 topology SNMP ({iface.name}): {len(l2_entries)} entries, "
                    f"{sum(1 for h in hosts if h.is_infrastructure)} infrastructure nodes"
                )

            # --- Source 2: LLDP ---
            effective_lldp_dir: Path | None
            if lldp_collect_dir is not None:
                lldp_collect_dir.mkdir(parents=True, exist_ok=True)
                lldp_discovery = LldpDiscovery(hosts)
                lldp_discovery.collect(lldp_collect_dir)
                effective_lldp_dir = lldp_dir or lldp_collect_dir
            else:
                effective_lldp_dir = lldp_dir

            if effective_lldp_dir is not None:
                lldp_entries = LldpDiscovery.load_and_parse(effective_lldp_dir, hosts)
                if lldp_entries:
                    lldp_l2, lldp_tree = LldpDiscovery.build_l2_from_lldp(lldp_entries)
                    # Merge: LLDP overrides SNMP per host_ip
                    l2_by_host = {e.host_ip: e for e in l2_entries}
                    for entry in lldp_l2:
                        l2_by_host[entry.host_ip] = entry
                    l2_entries = list(l2_by_host.values())
                    topology_tree.update(lldp_tree)
                    # Mark LLDP-discovered switches as infrastructure
                    for entry in lldp_entries:
                        if entry.switch.switch_ip:
                            for host in hosts:
                                if host.ip == entry.switch.switch_ip:
                                    host.is_infrastructure = True
                    has_l2_data = True
                    logger.info(f"L2 topology LLDP ({iface.name}): {len(lldp_entries)} entries merged")

            # --- Source 3: Manual topology ---
            if manual_topology:
                manual_l2: list[L2TopologyEntry] = []
                manual_tree: dict[str, str] = {}
                host_by_ip = {h.ip: h for h in hosts}
                for host_ip, sw_ip, port_name in manual_topology:
                    if host_ip not in host_by_ip:
                        continue  # Skip entries not on this subnet
                    host = host_by_ip[host_ip]
                    manual_l2.append(
                        L2TopologyEntry(
                            host_ip=host_ip,
                            host_mac=host.mac,
                            switch=SwitchPortMapping(
                                switch_ip=sw_ip,
                                switch_name="",
                                port_index=0,
                                port_name=port_name,
                            ),
                            source="manual",
                        )
                    )
                    manual_tree[host_ip] = sw_ip
                    # Mark manual switch as infrastructure
                    if sw_ip in host_by_ip:
                        host_by_ip[sw_ip].is_infrastructure = True

                if manual_l2:
                    # Merge: manual overrides everything per host_ip
                    l2_by_host = {e.host_ip: e for e in l2_entries}
                    for entry in manual_l2:
                        l2_by_host[entry.host_ip] = entry
                    l2_entries = list(l2_by_host.values())
                    topology_tree.update(manual_tree)
                    has_l2_data = True
                    logger.info(f"L2 topology manual ({iface.name}): {len(manual_l2)} entries merged")

            # --- Hosts not mapped to any switch -> parent is gateway ---
            if has_l2_data and gateway:
                for host in hosts:
                    if not host.is_gateway and host.ip not in topology_tree and host.ip != iface.ip:
                        topology_tree[host.ip] = gateway.ip

            # --- Source 4: Traceroute fallback ---
            if not has_l2_data and trace_local and gateway:
                local_traces = self.trace_local_hosts(hosts)
                topology_tree = self.build_topology_tree(hosts, local_traces, gateway.ip)
                logger.info(
                    f"Topology tree ({iface.name}): {len(topology_tree)} entries, "
                    f"{sum(1 for h in hosts if h.is_infrastructure)} infrastructure nodes"
                )

            subnets.append(
                SubnetScan(
                    interface=iface,
                    gateway=gateway,
                    hosts=hosts,
                    topology_tree=topology_tree,
                    l2_topology=l2_entries,
                )
            )
            all_hosts.extend(hosts)
            merged_tree.update(topology_tree)
            merged_l2.extend(l2_entries)

        trace_paths: list[TraceroutePath] = []
        if traceroute_targets:
            trace_paths = self.traceroute_targets(traceroute_targets)

        assert primary_iface is not None
        return NetworkTopology(
            local_interface=primary_iface,
            subnets=subnets,
            gateway=primary_gateway,
            local_hosts=all_hosts,
            traceroute_paths=trace_paths,
            topology_tree=merged_tree,
            l2_topology=merged_l2,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
