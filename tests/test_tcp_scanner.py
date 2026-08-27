"""Tests for the pure-Python TCP connect scan fallback.

Real loopback sockets are blocked by pytest-socket inside this test suite,
so we mock asyncio.open_connection. The interesting logic lives in
banner-mode dispatch and result shape, both of which mock cleanly.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from custom_components.homelable import tcp_scanner


class _FakeReader:
    def __init__(self, payload: bytes = b"") -> None:
        self._payload = payload
        self._consumed = False

    async def read(self, n: int) -> bytes:
        if self._consumed:
            return b""
        self._consumed = True
        return self._payload[:n]


class _FakeWriter:
    def __init__(self) -> None:
        self.written: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


def _connect_factory(open_ports: dict[int, bytes], *, tls_payloads: dict[int, bytes] | None = None):
    """Build a fake asyncio.open_connection. open_ports keyed by port → payload."""
    tls_payloads = tls_payloads or {}

    async def fake_open(host: str, port: int, *, ssl: Any = None, **_: Any):
        if ssl is not None:
            if port in tls_payloads:
                return _FakeReader(tls_payloads[port]), _FakeWriter()
            raise OSError("connection refused")
        if port in open_ports:
            return _FakeReader(open_ports[port]), _FakeWriter()
        raise OSError("connection refused")

    return fake_open


@pytest.mark.asyncio
async def test_closed_port_not_returned() -> None:
    """Connection refused → port omitted from results."""
    fake = _connect_factory(open_ports={})
    with patch.object(tcp_scanner.asyncio, "open_connection", fake):
        result = await tcp_scanner.tcp_connect_scan(
            {"ip": "10.0.0.1"}, [9999], connect_timeout=0.5, banner_timeout=0.2
        )
    assert result["open_ports"] == []


@pytest.mark.asyncio
async def test_passive_banner_captured_for_ssh_port() -> None:
    """Port 22 is in the passive set; the scanner reads the SSH banner on connect."""
    fake = _connect_factory(open_ports={22: b"SSH-2.0-OpenSSH_9.6\r\n"})
    with patch.object(tcp_scanner.asyncio, "open_connection", fake):
        result = await tcp_scanner.tcp_connect_scan(
            {"ip": "10.0.0.1"}, [22], connect_timeout=0.5, banner_timeout=0.2
        )
    assert len(result["open_ports"]) == 1
    entry = result["open_ports"][0]
    assert entry["port"] == 22
    assert entry["protocol"] == "tcp"
    assert "SSH-2.0-OpenSSH_9.6" in entry["banner"]


@pytest.mark.asyncio
async def test_http_banner_via_head_request() -> None:
    """Port 80 triggers a HEAD probe; Server header surfaces in the banner field."""
    response = b"HTTP/1.0 200 OK\r\nServer: nginx/1.99\r\nContent-Length: 0\r\n\r\n"
    fake = _connect_factory(open_ports={80: response})
    with patch.object(tcp_scanner.asyncio, "open_connection", fake):
        result = await tcp_scanner.tcp_connect_scan(
            {"ip": "10.0.0.1"}, [80], connect_timeout=0.5, banner_timeout=0.5
        )
    assert len(result["open_ports"]) == 1
    assert result["open_ports"][0]["banner"] == "nginx/1.99"


@pytest.mark.asyncio
async def test_tls_port_uses_ssl_context() -> None:
    """Port 443 is in the TLS set; the HEAD probe is sent over an SSL connection."""
    response = b"HTTP/1.0 200 OK\r\nServer: Caddy\r\n\r\n"
    fake = _connect_factory(open_ports={443: b""}, tls_payloads={443: response})
    with patch.object(tcp_scanner.asyncio, "open_connection", fake):
        result = await tcp_scanner.tcp_connect_scan(
            {"ip": "10.0.0.1"}, [443], connect_timeout=0.5, banner_timeout=0.5
        )
    assert len(result["open_ports"]) == 1
    assert result["open_ports"][0]["banner"] == "Caddy"


@pytest.mark.asyncio
async def test_silent_open_port_reports_empty_banner() -> None:
    """A port that's open but neither HTTP nor passive returns an empty banner."""
    fake = _connect_factory(open_ports={64738: b""})  # Mumble port, not in any probe set
    with patch.object(tcp_scanner.asyncio, "open_connection", fake):
        result = await tcp_scanner.tcp_connect_scan(
            {"ip": "10.0.0.1"}, [64738], connect_timeout=0.5, banner_timeout=0.2
        )
    assert len(result["open_ports"]) == 1
    assert result["open_ports"][0]["banner"] == ""


@pytest.mark.asyncio
async def test_mixed_ports_only_open_returned() -> None:
    """Out of (open, closed, open), only the listening pair survives."""
    fake = _connect_factory(open_ports={22: b"SSH-2.0-test", 80: b"HTTP/1.0 200 OK\r\nServer: x\r\n\r\n"})
    with patch.object(tcp_scanner.asyncio, "open_connection", fake):
        result = await tcp_scanner.tcp_connect_scan(
            {"ip": "10.0.0.1"}, [22, 9999, 80], connect_timeout=0.5, banner_timeout=0.2
        )
    open_set = {p["port"] for p in result["open_ports"]}
    assert open_set == {22, 80}


@pytest.mark.asyncio
async def test_connect_timeout_treated_as_closed() -> None:
    """asyncio.TimeoutError on open_connection is swallowed; port omitted."""
    async def slow_open(*args: Any, **kwargs: Any):
        await asyncio.sleep(10)

    with patch.object(tcp_scanner.asyncio, "open_connection", slow_open):
        result = await tcp_scanner.tcp_connect_scan(
            {"ip": "10.0.0.1"}, [22], connect_timeout=0.05, banner_timeout=0.05
        )
    assert result["open_ports"] == []


@pytest.mark.asyncio
async def test_scan_preserves_existing_host_fields() -> None:
    """Scan mutates open_ports but leaves mac/hostname/os intact."""
    fake = _connect_factory(open_ports={})
    host = {"ip": "10.0.0.1", "mac": "AA:BB:CC:DD:EE:FF", "hostname": "x.lan", "os": None}
    with patch.object(tcp_scanner.asyncio, "open_connection", fake):
        result = await tcp_scanner.tcp_connect_scan(
            host, [22], connect_timeout=0.05, banner_timeout=0.05
        )
    assert result["mac"] == "AA:BB:CC:DD:EE:FF"
    assert result["hostname"] == "x.lan"
    assert result["os"] is None
    assert result["open_ports"] == []


def test_parse_http_server_header() -> None:
    payload = b"HTTP/1.1 200 OK\r\nServer: Caddy\r\nContent-Type: text/html\r\n\r\n"
    assert tcp_scanner._parse_http_server(payload) == "Caddy"


def test_parse_http_server_missing_header() -> None:
    payload = b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n\r\n"
    assert tcp_scanner._parse_http_server(payload) == ""


def test_parse_http_server_undecodable_bytes() -> None:
    payload = b"\x00\x01\x02non-http-garbage"
    assert tcp_scanner._parse_http_server(payload) == ""


@pytest.mark.asyncio
async def test_raw_print_port_is_never_written_to() -> None:
    """9100 is raw print: opening proves it's up, writing prints a page (#87)."""
    writers: list[_FakeWriter] = []

    async def fake_open(host: str, port: int, *, ssl: Any = None, **_: Any):
        writer = _FakeWriter()
        writers.append(writer)
        return _FakeReader(b""), writer

    with patch.object(tcp_scanner.asyncio, "open_connection", fake_open):
        result = await tcp_scanner.tcp_connect_scan(
            {"ip": "10.0.0.1"}, [9100], connect_timeout=0.5, banner_timeout=0.5
        )

    assert len(result["open_ports"]) == 1
    assert result["open_ports"][0]["port"] == 9100
    assert result["open_ports"][0]["banner"] == ""
    assert all(w.written == [] for w in writers)


def test_printing_ports_absent_from_the_http_probe_set() -> None:
    """No printing port may ever reach _grab_http_banner."""
    for port in (515, 631, 9100, 9101, 9102):
        assert port not in tcp_scanner._HTTP_PORTS
        assert port not in tcp_scanner._TLS_PORTS
