"""Pure-Python TCP connect scan + banner grab.

Used as fallback when the `nmap` binary is unavailable (e.g. HA OS, where
the core container does not ship nmap). Produces the same
{port, protocol, banner} shape as the nmap path, so fingerprint.py is
mode-agnostic.

No OS detection, no SYN scan. Open-port detection only, with best-effort
banner grab. Stdlib only (asyncio, ssl).
"""
from __future__ import annotations

import asyncio
import logging
import ssl
from typing import Any

_LOGGER = logging.getLogger(__name__)

# Per-host port concurrency. Outer host concurrency is capped by the caller.
_PORT_CONCURRENCY = 50

# Default per-port timeouts.
_CONNECT_TIMEOUT = 1.5
_BANNER_TIMEOUT = 1.0

# Ports we expect to speak HTTP. We probe these with HEAD and parse Server:.
# Raw-print ports (9100 JetDirect/AppSocket, 515 LPD) must never be here: a
# printer treats anything written to the socket as a print job and ejects a
# page. They stay in the scan list and are still reported open, connect-only.
_HTTP_PORTS: frozenset[int] = frozenset({
    80, 3000, 3001, 5000, 5001, 5601, 6789, 6800, 6767, 7878, 8000, 8080,
    8081, 8086, 8088, 8090, 8096, 8112, 8123, 8200, 8291, 8428, 8686, 8789,
    8880, 8971, 8989, 9000, 9001, 9090, 9091, 9092, 9093, 9117, 9200,
    9300, 9411, 9696, 16686, 34567, 1880,
})

# Ports that almost always speak TLS.
_TLS_PORTS: frozenset[int] = frozenset({
    443, 465, 636, 993, 995, 5601, 6443, 8006, 8443, 8843, 8883, 9443,
})

# Ports that send a banner immediately on connect (no probe needed).
_PASSIVE_BANNER_PORTS: frozenset[int] = frozenset({
    21, 22, 23, 25, 110, 143, 3306, 5432, 6379,
})

_BANNER_READ_BYTES = 1024


def _build_ssl_context() -> ssl.SSLContext:
    """Permissive context — homelab gear often has self-signed certs."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


_SSL_CONTEXT = _build_ssl_context()


def _parse_http_server(payload: bytes) -> str:
    """Pull the `Server:` header out of an HTTP response. Empty if missing."""
    try:
        text = payload.decode("latin-1", errors="replace")
    except Exception:
        return ""
    for line in text.split("\r\n"):
        if line.lower().startswith("server:"):
            return line.split(":", 1)[1].strip()
    return ""


async def _grab_http_banner(
    ip: str, port: int, *, use_tls: bool, timeout: float
) -> str:
    """Send HEAD /, return Server header or empty."""
    try:
        coro = asyncio.open_connection(
            ip, port, ssl=_SSL_CONTEXT if use_tls else None
        )
        reader, writer = await asyncio.wait_for(coro, timeout=timeout)
    except (TimeoutError, OSError, ssl.SSLError):
        return ""

    try:
        writer.write(
            f"HEAD / HTTP/1.0\r\nHost: {ip}\r\nUser-Agent: homelable\r\n\r\n".encode()
        )
        await asyncio.wait_for(writer.drain(), timeout=timeout)
        data = await asyncio.wait_for(
            reader.read(_BANNER_READ_BYTES), timeout=timeout
        )
        return _parse_http_server(data)
    except (TimeoutError, OSError, ssl.SSLError):
        return ""
    finally:
        try:
            writer.close()
            await asyncio.wait_for(writer.wait_closed(), timeout=0.5)
        except (TimeoutError, OSError, ssl.SSLError):
            pass


async def _grab_passive_banner(
    reader: asyncio.StreamReader, *, timeout: float
) -> str:
    """Read whatever the server sends on connect (SSH/FTP/SMTP/etc)."""
    try:
        data = await asyncio.wait_for(
            reader.read(_BANNER_READ_BYTES), timeout=timeout
        )
    except (TimeoutError, OSError):
        return ""
    return data.decode("latin-1", errors="replace").strip()


async def _scan_port(
    ip: str,
    port: int,
    *,
    connect_timeout: float,
    banner_timeout: float,
) -> dict[str, Any] | None:
    """Probe a single port. Return {port, protocol, banner} if open, else None."""
    try:
        coro = asyncio.open_connection(ip, port)
        reader, writer = await asyncio.wait_for(coro, timeout=connect_timeout)
    except (TimeoutError, OSError):
        return None

    banner = ""
    try:
        if port in _PASSIVE_BANNER_PORTS:
            banner = await _grab_passive_banner(reader, timeout=banner_timeout)
    finally:
        try:
            writer.close()
            await asyncio.wait_for(writer.wait_closed(), timeout=0.5)
        except (TimeoutError, OSError):
            pass

    # HTTP / TLS probe is a second connection: keep the open-detection path
    # cheap, only spend on banner where it pays off.
    if not banner and (port in _HTTP_PORTS or port in _TLS_PORTS):
        banner = await _grab_http_banner(
            ip, port, use_tls=port in _TLS_PORTS, timeout=banner_timeout
        )

    return {"port": port, "protocol": "tcp", "banner": banner}


async def tcp_connect_scan(
    host: dict[str, Any],
    ports: tuple[int, ...] | list[int],
    *,
    connect_timeout: float = _CONNECT_TIMEOUT,
    banner_timeout: float = _BANNER_TIMEOUT,
    port_concurrency: int = _PORT_CONCURRENCY,
    socket_sem: asyncio.Semaphore | None = None,
) -> dict[str, Any]:
    """Scan all `ports` against host['ip']. Mutates and returns the host dict.

    Mirrors the shape produced by scanner._nmap_scan_single so downstream
    fingerprinting works unchanged. Sets host['open_ports']; leaves 'os' alone.

    `socket_sem`, when given, is a semaphore shared by every host in the scan
    and replaces the per-host `port_concurrency` cap. Without it, N hosts
    scanned in parallel each get their own budget and the open-socket count is
    the product of the two — the fan-out behind issue #73.
    """
    ip = host["ip"]
    sem = socket_sem if socket_sem is not None else asyncio.Semaphore(port_concurrency)

    async def _bound(port: int) -> dict[str, Any] | None:
        async with sem:
            try:
                return await _scan_port(
                    ip, port,
                    connect_timeout=connect_timeout,
                    banner_timeout=banner_timeout,
                )
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("tcp scan %s:%s failed: %s", ip, port, exc)
                return None

    results = await asyncio.gather(*[_bound(p) for p in ports])
    host["open_ports"] = [r for r in results if r is not None]
    return host
