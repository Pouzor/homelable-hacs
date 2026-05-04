"""Network scanner: ping sweep + ARP cache + TCP service detection + mDNS discovery.

HA-adapted: pure scan logic, returns device dicts. No DB, no persistence.
The caller (coordinator) is responsible for filtering, dedup, and storage.

Phase 2 service detection runs through a pure-Python TCP connect scan. We
previously had an nmap path too, but it proved unreliable on HA OS (nmap
binary present but python-nmap parsed empty output) so it was removed —
the TCP path is deterministic and works on every install without root or
external binaries.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
import subprocess
import threading
from collections.abc import Awaitable, Callable
from typing import Any

from .fingerprint import fingerprint_ports, suggest_node_type
from .tcp_scanner import tcp_connect_scan

# Coroutine the caller can pass to receive incremental scan events. Schema:
#   {"event": "scan_phase", "phase": "discovery"|"enrichment", "total": int}
#   {"event": "device_discovered", "device": {ip, mac?, hostname?, discovery_source}}
#   {"event": "device_enriched",   "device": {ip, ...full enriched dict}}
# Errors raised inside the callback are caught and logged — they never abort
# the scan.
ScanEventCallback = Callable[[dict[str, Any]], Awaitable[None]]


async def _safe_emit(
    on_event: ScanEventCallback | None, payload: dict[str, Any]
) -> None:
    """Invoke `on_event` swallowing any error so subscribers can't kill a scan."""
    if on_event is None:
        return
    try:
        await on_event(payload)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("scan event subscriber raised %s: %s", type(exc).__name__, exc)

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

_PORT_LIST: tuple[int, ...] = tuple(int(p) for p in _EXTRA_PORTS.split(","))

_MDNS_SERVICE_TYPES = [
    "_http._tcp.local.",
    "_shelly._tcp.local.",
    "_esphomelib._tcp.local.",
    "_hap._tcp.local.",
    "_mqtt._tcp.local.",
    "_device-info._tcp.local.",
]

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


async def _ping_sweep(
    target: str,
    *,
    on_host: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Phase 1: Concurrent ICMP ping sweep + ARP cache.

    `on_host` (if provided) is awaited per alive host with its bare host_dict
    once ping + hostname + ARP have settled. Used to stream `device_discovered`
    events before phase 2 starts.
    """
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
        host_dict = {
            "ip": ip,
            "mac": mac,
            "hostname": hostname,
            "os": None,
            "open_ports": [],
        }
        alive[ip] = host_dict
        if on_host is not None:
            try:
                await on_host(host_dict)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("[Phase 1] on_host raised: %s", exc)

    for ip, host in arp_cache.items():
        if ip not in alive:
            alive[ip] = host
            if on_host is not None:
                try:
                    await on_host(host)
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.warning("[Phase 1] on_host raised: %s", exc)

    return alive


async def _phase2_port_scan(
    alive: dict[str, dict[str, Any]],
    *,
    on_host_done: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> list[dict[str, Any]]:
    """Phase 2: per-IP TCP connect scan with bounded concurrency.

    `on_host_done` (if provided) is awaited per host as soon as that host's
    scan completes — the caller uses this to stream `device_enriched` events.
    """
    if not alive:
        return []

    semaphore = asyncio.Semaphore(10)

    async def _scan_and_notify(host_dict: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            result = await tcp_connect_scan(host_dict, _PORT_LIST)
        if on_host_done is not None:
            try:
                await on_host_done(result)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("[Phase 2] on_host_done raised: %s", exc)
        return result

    raw = await asyncio.gather(
        *[_scan_and_notify(h) for h in alive.values()],
        return_exceptions=True,
    )
    results: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, BaseException):
            _LOGGER.warning("[Phase 2] gather error: %s", item)
        else:
            results.append(item)
    return results


async def _scan_target(
    target: str,
    *,
    on_phase1_host: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    on_phase2_host: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> list[dict[str, Any]]:
    """Two-phase scan for a CIDR range (ping → TCP port scan).

    If ping + ARP yield zero hosts (foreign subnet, unprivileged container with
    no CAP_NET_RAW, ICMP-blocked LAN), fall back to a full TCP sweep across
    every IP in the CIDR. Slower but catches cases where ping is the blocker.
    """
    alive = await _ping_sweep(target, on_host=on_phase1_host)
    if alive:
        return await _phase2_port_scan(alive, on_host_done=on_phase2_host)

    _LOGGER.info(
        "[Phase 1] no hosts via ping/ARP for %s — full TCP sweep fallback", target
    )
    net = ipaddress.ip_network(target, strict=False)
    full_alive: dict[str, dict[str, Any]] = {
        str(ip): {
            "ip": str(ip),
            "mac": None,
            "hostname": None,
            "os": None,
            "open_ports": [],
        }
        for ip in net.hosts()
    }

    async def _filtered_phase2(host: dict[str, Any]) -> None:
        if not host.get("open_ports"):
            return
        if host.get("hostname") is None:
            host["hostname"] = await asyncio.to_thread(_resolve_hostname, host["ip"])
        if on_phase1_host is not None:
            await on_phase1_host(host)
        if on_phase2_host is not None:
            await on_phase2_host(host)

    results = await _phase2_port_scan(full_alive, on_host_done=_filtered_phase2)
    return [h for h in results if h.get("open_ports")]


async def _mdns_discover(
    hass: Any | None = None, timeout: float = 4.0
) -> list[dict[str, Any]]:
    """Passive mDNS sweep for IoT device types.

    Uses Home Assistant's shared Zeroconf instance when `hass` is provided —
    HA logs a warning otherwise, and creating a second Zeroconf binds another
    UDP listener on 5353 which can interfere with HA's own discovery.
    """
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

    # Acquire a Zeroconf instance: prefer HA's shared one, fall back to a
    # fresh one when called outside HA (tests / standalone smoke).
    azc: Any = None
    owned = False
    try:
        if hass is not None:
            try:
                from homeassistant.components import zeroconf as ha_zeroconf

                azc = await ha_zeroconf.async_get_async_instance(hass)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("Shared zeroconf unavailable, creating own: %s", exc)
        if azc is None:
            azc = AsyncZeroconf()
            owned = True

        browser = AsyncServiceBrowser(
            azc.zeroconf, _MDNS_SERVICE_TYPES, handlers=[_on_change]
        )
        try:
            await asyncio.sleep(timeout)
        finally:
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
    finally:
        if owned and azc is not None:
            try:
                await azc.async_close()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("AsyncZeroconf close failed: %s", exc)

    return list(discovered.values())


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
    on_event: ScanEventCallback | None = None,
    hass: Any | None = None,
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
        on_event: optional async callback invoked with progressive scan events
            (`device_discovered`, `device_enriched`, `scan_phase`). Subscribers
            crashing won't abort the scan.

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

    async def _emit_phase1(host: dict[str, Any]) -> None:
        ip = host.get("ip")
        if not ip or ip in exclude_ips or ip in seen_ips:
            return
        seen_ips.add(ip)
        await _safe_emit(
            on_event,
            {
                "event": "device_discovered",
                "device": {
                    "ip": ip,
                    "mac": host.get("mac"),
                    "hostname": host.get("hostname"),
                    "discovery_source": "tcp",
                },
            },
        )

    async def _emit_phase2(host: dict[str, Any]) -> None:
        ip = host.get("ip")
        if not ip:
            return
        # Honor exclude_ips even on the enrichment side (defensive — phase 1
        # already gated, but a host could enter via the ARP-only path).
        if ip in exclude_ips:
            return
        enriched = _enrich(host, discovery_source="tcp")
        await _safe_emit(
            on_event,
            {"event": "device_enriched", "device": enriched},
        )

    mdns_task: asyncio.Task[list[dict[str, Any]]] | None = None
    try:
        mdns_task = asyncio.create_task(_mdns_discover(hass))

        await _safe_emit(on_event, {"event": "scan_phase", "phase": "discovery"})

        for cidr in ranges:
            if _is_cancelled(run_id):
                break
            hosts = await _scan_target(
                cidr,
                on_phase1_host=_emit_phase1,
                on_phase2_host=_emit_phase2,
            )
            for host in hosts:
                if _is_cancelled(run_id):
                    break
                ip = host["ip"]
                if ip in exclude_ips:
                    continue
                # seen_ips was populated by _emit_phase1 above; for hosts that
                # only came through the ARP fallback (no ping), add now.
                seen_ips.add(ip)
                devices.append(_enrich(host, discovery_source="tcp"))

        if not _is_cancelled(run_id):
            mdns_hosts = await mdns_task
            for host in mdns_hosts:
                if _is_cancelled(run_id):
                    break
                ip = host["ip"]
                if ip in exclude_ips or ip in seen_ips:
                    continue
                seen_ips.add(ip)
                enriched = _enrich(host, discovery_source="mdns")
                devices.append(enriched)
                # mDNS is single-shot: emit discovery + enrichment together so
                # the UI gets a fully populated card in one frame.
                await _safe_emit(
                    on_event,
                    {
                        "event": "device_discovered",
                        "device": {
                            "ip": ip,
                            "mac": host.get("mac"),
                            "hostname": host.get("hostname"),
                            "discovery_source": "mdns",
                        },
                    },
                )
                await _safe_emit(
                    on_event, {"event": "device_enriched", "device": enriched}
                )
        elif mdns_task and not mdns_task.done():
            mdns_task.cancel()

        await _safe_emit(
            on_event,
            {
                "event": "scan_finished",
                "devices_found": len(devices),
                "cancelled": _is_cancelled(run_id),
            },
        )

        return devices
    finally:
        if run_id is not None:
            with _cancelled_lock:
                _cancelled_runs.discard(run_id)
        if mdns_task is not None and not mdns_task.done():
            mdns_task.cancel()
