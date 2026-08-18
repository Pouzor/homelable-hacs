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
import ssl
import sys
import time
from typing import Any
from urllib.parse import urlparse

import httpx

_LOGGER = logging.getLogger(__name__)

# A verifying SSL context loads the system CA bundle from disk
# (`load_verify_locations`), which is blocking I/O. Building it on the event
# loop trips Home Assistant's blocking-call detector, so build it once in a
# thread and reuse the cached context for every https check.
_ssl_context: ssl.SSLContext | None = None
_ssl_context_lock = asyncio.Lock()


async def _verifying_ssl_context() -> ssl.SSLContext:
    global _ssl_context
    if _ssl_context is None:
        async with _ssl_context_lock:
            if _ssl_context is None:
                _ssl_context = await asyncio.to_thread(ssl.create_default_context)
    return _ssl_context

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


def _extract_port(value: str, default: int) -> int:
    """Pull a port out of a `host:port` or URL target; fall back to default."""
    if "://" in value:
        parsed = urlparse(value)
        return parsed.port or default
    if value.count(":") == 1 and ":" in value:
        _, _, port_str = value.rpartition(":")
        if port_str.isdigit():
            return int(port_str)
    return default


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
                # The target may be a bare host, host:port, or a URL (the node
                # editor's placeholder suggests `http://...`). Strip everything
                # but the host so resolution + ping don't choke on the scheme.
                final_host = _extract_host(host) or host
                if not _host_is_allowed(final_host, nets):
                    return {"status": "offline", "response_time_ms": None}
                ok = await _ping(final_host)
            case "http":
                ok = await _http_check(host, "http", nets)
            case "https":
                ok = await _http_check(host, "https", nets)
            case "tcp":
                final_host = _extract_host(host) or host
                port = _extract_port(host, 80)
                if not _host_is_allowed(final_host, nets):
                    return {"status": "offline", "response_time_ms": None}
                ok = await _tcp_connect(final_host, port)
            case "ssh":
                final_host = _extract_host(host) or host
                if not _host_is_allowed(final_host, nets):
                    return {"status": "offline", "response_time_ms": None}
                ok = await _tcp_connect(final_host, 22)
            case "prometheus":
                ok = await _http_check(host, "http", nets, default_path="/metrics")
            case "health":
                ok = await _http_check(host, "http", nets, default_path="/health")
            case _:
                final_host = _extract_host(host) or host
                if not _host_is_allowed(final_host, nets):
                    return {"status": "offline", "response_time_ms": None}
                ok = await _ping(final_host)

        elapsed_ms = int((time.monotonic() - start) * 1000)
        return {
            "status": "online" if ok else "offline",
            "response_time_ms": elapsed_ms,
        }

    except Exception as exc:
        _LOGGER.debug("Check failed for %s (%s): %s", host, check_method, exc)
        return {"status": "offline", "response_time_ms": None}


def _is_ipv6(host: str) -> bool:
    """True if host is a literal IPv6 address (bracketed or bare)."""
    try:
        socket.inet_pton(socket.AF_INET6, host.strip("[]"))
        return True
    except OSError:
        return False


async def _ping(host: str) -> bool:
    # Send 2 probes with a ~2s timeout so a single dropped packet or a slow
    # device (ESPHome, IoT) doesn't flap a node offline. Success = any reply.
    #
    # -W flag units differ by OS:
    #   Linux:   seconds        (-W 2    = 2s)
    #   macOS:   milliseconds   (-W 2000 = 2s)
    #   Windows: -w in ms       (-w 2000 = 2s)
    #
    # IPv6-only hosts (e.g. Alexa) never answer IPv4 ping, so target the right
    # stack: macOS ships a separate ping6; Linux/Windows take a family flag.
    # `--` separates options from the host so a hostname starting with `-` can't
    # be parsed as a flag (IPv6 literals can't start with `-`, so skip it there).
    ipv6 = _is_ipv6(host)
    if sys.platform == "win32":
        family = ["-6"] if ipv6 else ["-4"]
        args = ["ping", *family, "-n", "2", "-w", "2000", host]
    elif sys.platform == "darwin":
        args = (
            ["ping6", "-c", "2", host]
            if ipv6
            else ["ping", "-c", "2", "-W", "2000", "--", host]
        )
    else:
        family = ["-6"] if ipv6 else []
        args = ["ping", *family, "-c", "2", "-W", "2", "--", host]
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
    # we just applied. When verifying, pass a pre-built SSL context so httpx
    # doesn't load the CA bundle (blocking I/O) on the event loop.
    verify_arg: bool | ssl.SSLContext = verify
    if verify:
        verify_arg = await _verifying_ssl_context()
    async with httpx.AsyncClient(
        verify=verify_arg, timeout=5, follow_redirects=False
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


# --- Per-service status checks ---

# Ports that are not HTTP/web. These get NO status check — a service here stays
# grey (unknown) rather than going red. An open TCP socket doesn't prove the
# service is healthy, and a closed one flaps red misleadingly (e.g. SSH on a
# box that simply firewalls 22). Only HTTP(S)-reachable services are checked.
_NON_HTTP_PORTS = frozenset({
    22, 21, 23, 25, 465, 587, 53, 110, 143, 993, 995, 389, 636, 445, 514,
    1433, 3306, 5432, 5672, 6379, 9092, 11211, 27017, 27018,
})
_HTTPS_PORTS = frozenset({443, 8443})


def _service_host(host: str) -> str:
    """Bracket bare IPv6 literals for use in a URL."""
    return f"[{host}]" if _is_ipv6(host) else host


def _parse_override(raw: str) -> tuple[str, str | None, int | None]:
    """Split a service `host` override into (hostname, scheme, port).

    Mirrors the frontend `parseHostParts`: a node can serve several domains, so
    a service may carry its own host, optionally with a scheme and a port
    (`blog.example.com`, `blog.example.com:8443`, `https://blog.example.com`).
    """
    # Like a node ip, an override may list several hosts — the first one wins,
    # same as the frontend's `splitFirstHost`.
    rest = raw.split(",")[0].strip()
    scheme: str | None = None
    for prefix in ("https://", "http://"):
        if rest.lower().startswith(prefix):
            scheme = prefix[:-3]
            rest = rest[len(prefix):]
            break
    rest = rest.split("/", 1)[0]

    if rest.startswith("["):
        closing = rest.find("]")
        if closing == -1:
            return rest, scheme, None
        hostname = rest[1:closing]
        remainder = rest[closing + 1:]
        port = remainder[1:] if remainder.startswith(":") else ""
        return hostname, scheme, int(port) if port.isdigit() else None

    if rest.count(":") == 1:
        hostname, _, port = rest.partition(":")
        if hostname and port.isdigit():
            return hostname, scheme, int(port)

    return rest, scheme, None


async def check_service(
    svc: dict[str, Any],
    host: str | None,
    allowed_networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] | None = None,
) -> str:
    """Check a single service. Returns 'online' | 'offline' | 'unknown'.

    Only HTTP(S)-reachable services get a real check (an HTTP GET). Everything
    else — SSH, databases, mail, DNS, raw TCP, UDP, port-less — stays 'unknown'
    so it keeps its category colour instead of flashing red. An open TCP socket
    doesn't prove a non-web service is healthy, so we don't pretend it does.

    The same SSRF guard as node checks applies: a host that resolves to an
    unsafe address outside the configured scan ranges is treated as offline.
    """
    override = str(svc.get("host") or "").strip()
    override_scheme: str | None = None
    override_port: int | None = None
    if override:
        host, override_scheme, override_port = _parse_override(override)

    if not host or host.startswith("-"):
        return "unknown"
    if str(svc.get("protocol", "")).lower() == "udp":
        return "unknown"

    port = svc.get("port")
    port = int(port) if isinstance(port, int) or (
        isinstance(port, str) and port.isdigit()
    ) else None
    if port is None:
        port = override_port

    # Non-HTTP ports (SSH 22, DB, mail, …) are never checked — keep them grey.
    if port is not None and port in _NON_HTTP_PORTS:
        return "unknown"

    name = str(svc.get("service_name", "")).lower()
    is_web = port is not None or "http" in name or override_scheme is not None
    if not is_web:
        return "unknown"

    if not _host_is_allowed(_extract_host(host) or host, allowed_networks or []):
        return "offline"

    try:
        scheme = override_scheme or ("https" if (
            (port is not None and port in _HTTPS_PORTS)
            or "https" in name
            or "ssl" in name
            or "tls" in name
        ) else "http")
        url_host = _service_host(host)
        url = f"{scheme}://{url_host}" + (f":{port}" if port is not None else "")
        return "online" if await _http_get(url, verify=False) else "offline"
    except Exception as exc:
        _LOGGER.debug("Service check failed for %s:%s (%s)", host, port, exc)
        return "offline"


async def check_services(
    host: str | None,
    services: list[dict[str, Any]],
    allowed_networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] | None = None,
    concurrency: int = 10,
) -> list[dict[str, Any]]:
    """Check every service against host concurrently (bounded).

    Returns a list of {port, protocol, host, status} dicts, one per input service.
    """
    sem = asyncio.Semaphore(concurrency)

    async def _one(svc: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            status = await check_service(svc, host, allowed_networks)
        # `host` rides along: several vhosts can share one port on one node, so
        # port+protocol alone no longer identifies a service on the client side.
        return {
            "port": svc.get("port"),
            "protocol": svc.get("protocol"),
            "host": svc.get("host"),
            "status": status,
        }

    return await asyncio.gather(*[_one(s) for s in services]) if services else []
