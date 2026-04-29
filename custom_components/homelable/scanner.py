"""Network scanner: ping sweep + ARP cache + nmap service detection + mDNS discovery.

HA-adapted: pure scan logic, returns device dicts. No DB, no persistence.
The caller (coordinator) is responsible for filtering, dedup, and storage.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import socket
import subprocess
import threading
from typing import Any

from .fingerprint import fingerprint_ports, suggest_node_type

_LOGGER = logging.getLogger(__name__)

# Run IDs requested to cancel (thread-safe)
_cancelled_runs: set[str] = set()
_cancelled_lock = threading.Lock()

_EXTRA_PORTS = (
    "80,443,22,21,23,25,53,110,143,161,162,179,389,445,548,"
    "554,636,873,1883,1880,1935,2020,2375,2376,3000,3001,3306,"
    "3389,4711,4915,5000,5001,5432,5601,5683,5684,5900,5984,"
    "6052,6379,6432,6443,6767,6789,6800,7878,8000,8006,8080,"
    "8081,8086,8088,8090,8096,8112,8123,8200,8291,8428,8443,"
    "8554,8686,8789,8843,8880,8883,8971,8989,9000,9001,9090,"
    "9091,9092,9093,9100,9117,9200,9300,9411,9443,9696,10051,"
    "16686,34567,37777,51413,64738"
)

_MDNS_SERVICE_TYPES = [
    "_http._tcp.local.",
    "_shelly._tcp.local.",
    "_esphomelib._tcp.local.",
    "_hap._tcp.local.",
    "_mqtt._tcp.local.",
    "_device-info._tcp.local.",
]

try:
    import nmap

    _NMAP_AVAILABLE = True
except ImportError:
    _NMAP_AVAILABLE = False
    _LOGGER.warning("python-nmap not available — scanner will run in mock mode")

try:
    from zeroconf import ServiceStateChange
    from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

    _ZEROCONF_AVAILABLE = True
except ImportError:
    _ZEROCONF_AVAILABLE = False
    _LOGGER.warning("zeroconf not available — mDNS discovery disabled")


def request_cancel(run_id: str) -> None:
    """Signal a running scan to stop early."""
    with _cancelled_lock:
        _cancelled_runs.add(run_id)


def _is_cancelled(run_id: str | None) -> bool:
    if run_id is None:
        return False
    with _cancelled_lock:
        return run_id in _cancelled_runs


def _resolve_hostname(ip: str) -> str | None:
    try:
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return None


def _extract_os(nm: object, host: str) -> str | None:
    try:
        osmatch = nm[host].get("osmatch", [])  # type: ignore[index]
        if osmatch:
            return str(osmatch[0]["name"])
    except Exception:
        pass
    return None


def _arp_table_hosts(network: str) -> dict[str, dict[str, Any]]:
    """Read OS ARP cache for hosts in the target network. Linux + macOS."""
    try:
        net = ipaddress.ip_network(network, strict=False)
        found: dict[str, dict[str, Any]] = {}

        proc_arp = "/proc/net/arp"
        try:
            with open(proc_arp) as f:
                for line in f.readlines()[1:]:
                    parts = line.split()
                    if len(parts) >= 4:
                        ip, mac = parts[0], parts[3]
                        if mac == "00:00:00:00:00:00":
                            continue
                        try:
                            if ipaddress.ip_address(ip) in net:
                                found[ip] = {
                                    "ip": ip,
                                    "mac": mac,
                                    "hostname": _resolve_hostname(ip),
                                    "os": None,
                                    "open_ports": [],
                                }
                        except ValueError:
                            pass
            return found
        except FileNotFoundError:
            pass

        # macOS fallback
        result = subprocess.run(
            ["arp", "-a"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            m = re.search(r"\((\d+\.\d+\.\d+\.\d+)\)\s+at\s+([0-9a-f:]+)", line)
            if not m:
                continue
            ip, mac = m.group(1), m.group(2)
            if mac in ("(incomplete)", "ff:ff:ff:ff:ff:ff"):
                continue
            try:
                if ipaddress.ip_address(ip) in net:
                    found[ip] = {
                        "ip": ip,
                        "mac": mac,
                        "hostname": _resolve_hostname(ip),
                        "os": None,
                        "open_ports": [],
                    }
            except ValueError:
                pass
        return found
    except Exception as exc:
        _LOGGER.warning("[Phase 1] ARP cache lookup failed: %s", exc)
        return {}


async def _ping_sweep(target: str) -> dict[str, dict[str, Any]]:
    """Phase 1: Concurrent ICMP ping sweep + ARP cache."""
    net = ipaddress.ip_network(target, strict=False)
    all_ips = [str(ip) for ip in net.hosts()]
    _LOGGER.info("[Phase 1] Pinging %d hosts in %s", len(all_ips), target)

    sem = asyncio.Semaphore(50)

    async def _ping(ip: str) -> str | None:
        async with sem:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "ping", "-c", "1", "-W", "1", ip,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
                return ip if proc.returncode == 0 else None
            except Exception:
                return None

    ping_results = await asyncio.gather(*[_ping(ip) for ip in all_ips])
    alive_ips: set[str] = {ip for ip in ping_results if ip is not None}
    _LOGGER.info("[Phase 1] %d/%d hosts responded", len(alive_ips), len(all_ips))

    arp_cache = await asyncio.to_thread(_arp_table_hosts, target)

    alive: dict[str, dict[str, Any]] = {}
    for ip in alive_ips:
        mac = arp_cache.get(ip, {}).get("mac")
        hostname = await asyncio.to_thread(_resolve_hostname, ip)
        alive[ip] = {
            "ip": ip,
            "mac": mac,
            "hostname": hostname,
            "os": None,
            "open_ports": [],
        }

    for ip, host in arp_cache.items():
        if ip not in alive:
            alive[ip] = host

    return alive


def _nmap_scan_single(host_dict: dict[str, Any]) -> dict[str, Any]:
    """Phase 2: per-IP service detection. Blocking — call via to_thread."""
    ip = host_dict["ip"]
    if not _NMAP_AVAILABLE:
        return host_dict

    is_root = os.geteuid() == 0 if hasattr(os, "geteuid") else False
    base = "-sS" if is_root else "-sT"
    scan_args = f"{base} -sV --open -T4 -Pn --host-timeout 60s -p {_EXTRA_PORTS}"

    nm = nmap.PortScanner()
    try:
        nm.scan(hosts=ip, arguments=scan_args)
    except Exception as exc:
        _LOGGER.warning("[Phase 2] nmap failed for %s (%s)", ip, exc)
        return host_dict

    if ip not in nm.all_hosts():
        return host_dict

    open_ports = []
    for proto in nm[ip].all_protocols():
        for port, info in nm[ip][proto].items():
            if info["state"] == "open":
                banner = (
                    info.get("product", "") + " " + info.get("version", "")
                ).strip()
                open_ports.append(
                    {"port": port, "protocol": proto, "banner": banner}
                )

    host_dict["open_ports"] = open_ports
    if not host_dict.get("mac"):
        host_dict["mac"] = nm[ip].get("addresses", {}).get("mac")
    host_dict["os"] = _extract_os(nm, ip)
    return host_dict


async def _nmap_port_scan(
    alive: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Phase 2: per-IP port scan with bounded concurrency (10 hosts at a time)."""
    if not alive:
        return []

    semaphore = asyncio.Semaphore(10)

    async def _scan_with_sem(host_dict: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            return await asyncio.to_thread(_nmap_scan_single, host_dict)

    raw = await asyncio.gather(
        *[_scan_with_sem(h) for h in alive.values()],
        return_exceptions=True,
    )
    results: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, BaseException):
            _LOGGER.warning("[Phase 2] gather error: %s", item)
        else:
            results.append(item)
    return results


async def _nmap_scan(target: str) -> list[dict[str, Any]]:
    """Two-phase scan for a CIDR range (ping → port scan)."""
    if not _NMAP_AVAILABLE:
        return _mock_scan(target)
    alive = await _ping_sweep(target)
    return await _nmap_port_scan(alive)


async def _mdns_discover(timeout: float = 4.0) -> list[dict[str, Any]]:
    """Passive mDNS sweep for IoT device types."""
    if not _ZEROCONF_AVAILABLE:
        return []

    found_services: list[tuple[str, str]] = []

    def _on_change(
        zeroconf: Any,
        service_type: str,
        name: str,
        state_change: Any,
    ) -> None:
        if state_change == ServiceStateChange.Added:
            found_services.append((service_type, name))

    discovered: dict[str, dict[str, Any]] = {}

    try:
        async with AsyncZeroconf() as azc:
            browser = AsyncServiceBrowser(
                azc.zeroconf, _MDNS_SERVICE_TYPES, handlers=[_on_change]
            )
            await asyncio.sleep(timeout)
            await browser.async_cancel()

            for service_type, name in found_services:
                try:
                    info = AsyncServiceInfo(service_type, name)
                    await info.async_request(azc.zeroconf, 3000)
                    if not info.addresses:
                        continue
                    ip = str(ipaddress.IPv4Address(info.addresses[0]))
                    if ip in discovered:
                        continue
                    discovered[ip] = {
                        "ip": ip,
                        "hostname": info.server,
                        "mac": None,
                        "os": None,
                        "open_ports": (
                            [{"port": info.port, "protocol": "tcp", "banner": ""}]
                            if info.port
                            else []
                        ),
                    }
                except Exception as exc:
                    _LOGGER.debug("mDNS resolve failed for %s: %s", name, exc)
    except Exception as exc:
        _LOGGER.warning("mDNS discovery error: %s", exc)

    return list(discovered.values())


def _mock_scan(target: str) -> list[dict[str, Any]]:
    """Fake results for dev/test without nmap."""
    return [
        {
            "ip": "192.168.1.99",
            "hostname": "unknown-device.lan",
            "mac": "AA:BB:CC:DD:EE:FF",
            "os": None,
            "open_ports": [
                {"port": 80, "protocol": "tcp", "banner": "nginx"},
                {"port": 22, "protocol": "tcp", "banner": "OpenSSH 9.0"},
            ],
        }
    ]


def _enrich(host: dict[str, Any], discovery_source: str) -> dict[str, Any]:
    """Add fingerprint + suggested_type + discovery_source to a host dict."""
    services = fingerprint_ports(host.get("open_ports", []))
    suggested_type = suggest_node_type(
        host.get("open_ports", []), host.get("mac")
    )
    return {
        **host,
        "services": services,
        "suggested_type": suggested_type,
        "discovery_source": discovery_source,
    }


async def run_scan(
    ranges: list[str],
    run_id: str | None = None,
    *,
    exclude_ips: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Execute a scan for the given CIDR ranges.

    Returns enriched device dicts:
        {ip, mac, hostname, os, open_ports, services, suggested_type, discovery_source}

    Caller is responsible for persisting/filtering further (e.g., dedup against
    pending store, broadcasting updates).

    Args:
        ranges: list of CIDR strings (e.g., ["192.168.1.0/24"]).
        run_id: optional cancellation token; pair with `request_cancel(run_id)`.
        exclude_ips: IPs to skip (e.g., already on canvas or hidden by user).

    Raises:
        ValueError: if any range is not a valid CIDR.
    """
    exclude_ips = exclude_ips or set()
    for r in ranges:
        try:
            ipaddress.ip_network(r, strict=False)
        except ValueError as err:
            raise ValueError(f"Invalid CIDR range: {r!r}") from err

    devices: list[dict[str, Any]] = []
    seen_ips: set[str] = set()

    mdns_task: asyncio.Task[list[dict[str, Any]]] | None = None
    try:
        mdns_task = asyncio.create_task(_mdns_discover())

        for cidr in ranges:
            if _is_cancelled(run_id):
                break
            hosts = await _nmap_scan(cidr)
            for host in hosts:
                if _is_cancelled(run_id):
                    break
                ip = host["ip"]
                if ip in exclude_ips or ip in seen_ips:
                    continue
                seen_ips.add(ip)
                devices.append(_enrich(host, discovery_source="nmap"))

        if not _is_cancelled(run_id):
            mdns_hosts = await mdns_task
            for host in mdns_hosts:
                if _is_cancelled(run_id):
                    break
                ip = host["ip"]
                if ip in exclude_ips or ip in seen_ips:
                    continue
                seen_ips.add(ip)
                devices.append(_enrich(host, discovery_source="mdns"))
        elif mdns_task and not mdns_task.done():
            mdns_task.cancel()

        return devices
    finally:
        if run_id is not None:
            with _cancelled_lock:
                _cancelled_runs.discard(run_id)
        if mdns_task is not None and not mdns_task.done():
            mdns_task.cancel()
