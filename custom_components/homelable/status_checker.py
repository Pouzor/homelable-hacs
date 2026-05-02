"""Per-node status checks: ping, http, https, tcp, ssh, prometheus, health, none.

Hosts targeted by the checker come from canvas data (admin-supplied), but the
canvas can host arbitrary text. To keep the checker from being weaponized as
an SSRF gadget against HA's own host (`localhost`, `127.0.0.1`) or cloud
metadata endpoints (`169.254.169.254`), we resolve every host to an IP and
refuse loopback / link-local / multicast / unspecified addresses **unless**
the IP is explicitly inside one of the configured scan ranges (i.e. the
admin opted in to that subnet).
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import sys
import time
from typing import Any
from urllib.parse import urlparse

import httpx

_LOGGER = logging.getLogger(__name__)

# HTTP-family checks may only fire over these schemes.
_ALLOWED_SCHEMES = {"http", "https"}


def _parse_allowed_networks(
    ranges: list[str] | None,
) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Best-effort parse of CIDR ranges; ignore anything malformed."""
    if not ranges:
        return []
    nets: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for r in ranges:
        try:
            nets.append(ipaddress.ip_network(r.strip(), strict=False))
        except ValueError:
            _LOGGER.debug("Ignoring invalid scan range %r", r)
    return nets


def _extract_host(value: str) -> str:
    """Strip scheme/path/port from a target so we can resolve it."""
    if "://" in value:
        parsed = urlparse(value)
        return parsed.hostname or ""
    # `host:port`
    if value.count(":") == 1 and ":" in value:
        host, _, _ = value.rpartition(":")
        return host
    return value


def _resolve_to_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Return the resolved IP for a host string, or None on failure."""
    if not host:
        return None
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    try:
        info = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError, OSError):
        return None
    if not info:
        return None
    sockaddr = info[0][4]
    try:
        return ipaddress.ip_address(sockaddr[0])
    except (ValueError, IndexError):
        return None


def _is_unsafe_address(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    """Block addresses we never want the checker to hit blindly."""
    return (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
    )


def _host_is_allowed(
    host: str,
    allowed_networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network],
) -> bool:
    """Decide whether the checker may probe this host."""
    ip = _resolve_to_ip(host)
    if ip is None:
        # Couldn't resolve — assume offline rather than reach out anyway.
        return False
    if not _is_unsafe_address(ip):
        return True
    # Unsafe class — only allow if the user explicitly asked for that subnet.
    return any(ip in net for net in allowed_networks)


async def check_node(
    check_method: str,
    target: str | None,
    ip: str | None,
    *,
    allowed_networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] | None = None,
) -> dict[str, Any]:
    """Run the appropriate check and return {status, response_time_ms}.

    status is one of: online, offline, unknown.
    """
    if check_method == "none":
        return {"status": "online", "response_time_ms": None}

    raw_ip = ip.split(",")[0].strip() if ip else None
    host = target or raw_ip
    if not host:
        return {"status": "unknown", "response_time_ms": None}

    nets = allowed_networks or []

    start = time.monotonic()
    try:
        match check_method:
            case "ping":
                if not _host_is_allowed(host, nets):
                    return {"status": "offline", "response_time_ms": None}
                ok = await _ping(host)
            case "http":
                ok = await _http_check(host, "http", nets)
            case "https":
                ok = await _http_check(host, "https", nets)
            case "tcp":
                host_part, _, port_str = host.rpartition(":")
                port = int(port_str) if port_str.isdigit() else 80
                final_host = host_part or host
                if not _host_is_allowed(final_host, nets):
                    return {"status": "offline", "response_time_ms": None}
                ok = await _tcp_connect(final_host, port)
            case "ssh":
                if not _host_is_allowed(host, nets):
                    return {"status": "offline", "response_time_ms": None}
                ok = await _tcp_connect(host, 22)
            case "prometheus":
                ok = await _http_check(host, "http", nets, default_path="/metrics")
            case "health":
                ok = await _http_check(host, "http", nets, default_path="/health")
            case _:
                if not _host_is_allowed(host, nets):
                    return {"status": "offline", "response_time_ms": None}
                ok = await _ping(host)

        elapsed_ms = int((time.monotonic() - start) * 1000)
        return {
            "status": "online" if ok else "offline",
            "response_time_ms": elapsed_ms,
        }

    except Exception as exc:
        _LOGGER.debug("Check failed for %s (%s): %s", host, check_method, exc)
        return {"status": "offline", "response_time_ms": None}


async def _ping(host: str) -> bool:
    if sys.platform == "win32":
        args = ["ping", "-n", "1", "-w", "1000", host]
    else:
        # `--` separates options from the host so a hostname starting with `-`
        # can't be parsed as a flag.
        args = ["ping", "-c", "1", "-W", "1", "--", host]
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    return proc.returncode == 0


async def _http_check(
    host: str,
    default_scheme: str,
    allowed_networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network],
    *,
    default_path: str = "",
) -> bool:
    """Build a safe URL and probe it.

    - Force scheme to http or https; nothing else (no file://, gopher://, …).
    - Refuse to call out to loopback / link-local / multicast / reserved hosts
      unless the resolved IP is explicitly inside a configured scan range.
    """
    if "://" in host:
        parsed = urlparse(host)
        if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
            return False
        url = host
        target_host = parsed.hostname or ""
    else:
        url = f"{default_scheme}://{host}{default_path}"
        target_host = _extract_host(host)

    if not _host_is_allowed(target_host, allowed_networks):
        return False

    return await _http_get(url, verify=default_scheme == "https")


async def _http_get(url: str, verify: bool = False) -> bool:
    # follow_redirects=False — health checks have no business chasing 3xx,
    # and a redirect to an internal endpoint would defeat the host filter
    # we just applied.
    async with httpx.AsyncClient(
        verify=verify, timeout=5, follow_redirects=False
    ) as client:
        resp = await client.get(url)
        # Treat 2xx/3xx/4xx as "the host is up"; 5xx as offline.
        return resp.status_code < 500


async def _tcp_connect(host: str, port: int) -> bool:
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=3
        )
        writer.close()
        await writer.wait_closed()
        return True
    except (TimeoutError, OSError, socket.gaierror):
        return False
