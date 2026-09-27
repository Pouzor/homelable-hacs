"""Tests for the HTTP probe used by deep-scan service identification."""
import ssl
from unittest.mock import patch

import httpx

from custom_components.homelable.http_probe import (
    _MAX_BODY_BYTES,
    _extract_title,
    probe_open_ports,
    probe_port,
)


def _response(text: str = "", headers: dict | None = None, status: int = 200) -> httpx.Response:
    return httpx.Response(status_code=status, text=text, headers=headers or {})


class _TransportClient:
    """
    Patch target for httpx.AsyncClient that routes through a MockTransport.

    Real client, fake network: the probe exercises the genuine httpx request
    path (including .stream() and aiter_bytes()) instead of a mock that would
    happily accept any call shape. `requests` records what was actually sent.
    """

    def __init__(self, handler):  # noqa: ANN001
        self._handler = handler
        # Bound before patching, so building the real client here does not
        # recurse back into this stand-in.
        self._real = httpx.AsyncClient
        self.kwargs: list[dict] = []
        self.requests: list[httpx.Request] = []

    def _record(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)

    def __call__(self, **kwargs):  # noqa: ANN003
        self.kwargs.append(dict(kwargs))
        kwargs.pop("verify", None)
        return self._real(transport=httpx.MockTransport(self._record), **kwargs)


def _patch_client(handler) -> tuple:  # noqa: ANN001
    """Return (context manager, factory) patching http_probe's AsyncClient."""
    factory = _TransportClient(handler)
    return patch("custom_components.homelable.http_probe.httpx.AsyncClient", factory), factory


# ── _extract_title ──────────────────────────────────────────────────────────

def test_extract_title_basic() -> None:
    assert _extract_title("<html><title>Jellyfin</title></html>") == "Jellyfin"


def test_extract_title_collapses_whitespace() -> None:
    assert _extract_title("<title>\n  My  App\n</title>") == "My App"


def test_extract_title_missing() -> None:
    assert _extract_title("<html><body>no title</body></html>") is None


def test_extract_title_case_insensitive() -> None:
    assert _extract_title("<TITLE>Portainer</TITLE>") == "Portainer"


# ── probe_port ──────────────────────────────────────────────────────────────

async def test_probe_port_reads_title() -> None:
    ctx, _ = _patch_client(lambda req: _response("<title>Jellyfin</title>"))
    with ctx:
        result = await probe_port("10.0.0.5", 8096)
    assert result == {"title": "Jellyfin", "headers": {}}


async def test_probe_port_reads_headers() -> None:
    ctx, _ = _patch_client(
        lambda req: _response("", headers={"Server": "nginx", "X-Powered-By": "Express"})
    )
    with ctx:
        result = await probe_port("10.0.0.5", 3000)
    assert result["headers"] == {"Server": "nginx", "X-Powered-By": "Express"}


async def test_probe_port_falls_back_to_http() -> None:
    # https raises, http succeeds
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith("https"):
            raise httpx.ConnectError("tls fail")
        return _response("<title>HTTP App</title>")

    ctx, factory = _patch_client(handler)
    with ctx:
        result = await probe_port("10.0.0.5", 8080)
    assert result["title"] == "HTTP App"
    assert len(factory.requests) == 2  # tried https then http


async def test_probe_port_no_signal_returns_none() -> None:
    ctx, _ = _patch_client(lambda req: _response(""))
    with ctx:
        result = await probe_port("10.0.0.5", 8080)
    assert result is None


async def test_probe_port_timeout_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow")

    ctx, _ = _patch_client(handler)
    with ctx:
        result = await probe_port("10.0.0.5", 8080)
    assert result is None


async def test_probe_port_skips_non_http_ports() -> None:
    # SSH should never trigger an HTTP request
    ctx, factory = _patch_client(lambda req: _response("<title>nope</title>"))
    with ctx:
        result = await probe_port("10.0.0.5", 22)
    assert result is None
    assert factory.requests == []


async def test_probe_port_verify_tls_flag_passed() -> None:
    # verify_tls=True must hand httpx a pre-built SSLContext (built off the event
    # loop), never verify=True — which would load the CA bundle on the loop.
    ctx, factory = _patch_client(lambda req: _response("<title>X</title>"))
    with ctx:
        await probe_port("10.0.0.5", 8443, verify_tls=True)
    assert isinstance(factory.kwargs[0]["verify"], ssl.SSLContext)


async def test_probe_port_no_verify_stays_false() -> None:
    ctx, factory = _patch_client(lambda req: _response("<title>X</title>"))
    with ctx:
        await probe_port("10.0.0.5", 8080, verify_tls=False)
    assert factory.kwargs[0]["verify"] is False


# ── endless bodies (standalone #375) ────────────────────────────────────────

_CHUNK_SIZE = 16 * 1024


def _endless_body(counter: dict, head: bytes = b""):  # noqa: ANN202
    """
    A body that never ends and declares no Content-Length. Bounded at 512
    chunks so a regression fails the test instead of hanging the suite.
    """

    async def gen():  # noqa: ANN202
        if head:
            counter["bytes"] += len(head)
            yield head
        for _ in range(512):
            counter["bytes"] += _CHUNK_SIZE
            yield b"\0" * _CHUNK_SIZE

    return gen()


async def test_probe_caps_the_download_of_an_endless_body() -> None:
    # _MAX_BODY_BYTES caps the download, not just the <title> scan: the body is
    # streamed and the connection dropped once we hold enough. Allow one chunk
    # of overshoot — the read stops on a chunk boundary.
    counter = {"bytes": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        # Plain HTTP only, so the counter covers one probe.
        if str(request.url).startswith("https"):
            raise httpx.ConnectError("no tls")
        return httpx.Response(
            200,
            headers={"Content-Type": "application/octet-stream"},
            content=_endless_body(counter),
        )

    ctx, _ = _patch_client(handler)
    with ctx:
        result = await probe_port("10.0.0.5", 8095)
    assert result is None  # null bytes carry no title and no signal header
    assert counter["bytes"] <= _MAX_BODY_BYTES + _CHUNK_SIZE


async def test_probe_still_reads_a_title_from_an_endless_body() -> None:
    # Stopping early must not cost us the signal: a <title> in the first chunk
    # is still found even though the rest of the stream is abandoned.
    counter = {"bytes": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith("https"):
            raise httpx.ConnectError("no tls")
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html"},
            content=_endless_body(counter, head=b"<html><title>Jellyfin</title>"),
        )

    ctx, _ = _patch_client(handler)
    with ctx:
        result = await probe_port("10.0.0.5", 8096)
    assert result["title"] == "Jellyfin"
    assert counter["bytes"] <= _MAX_BODY_BYTES + _CHUNK_SIZE


# ── probe_open_ports ─────────────────────────────────────────────────────────

async def test_probe_open_ports_enriches_each_port() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if ":8096" in str(request.url):
            return _response("<title>Jellyfin</title>")
        return _response("")

    ports = [{"port": 8096, "protocol": "tcp"}, {"port": 9999, "protocol": "tcp"}]
    ctx, _ = _patch_client(handler)
    with ctx:
        result = await probe_open_ports("10.0.0.5", ports)

    by_port = {p["port"]: p for p in result}
    assert by_port[8096]["http_signals"]["title"] == "Jellyfin"
    assert by_port[9999]["http_signals"] is None


async def test_probe_port_skips_raw_print_ports() -> None:
    """A GET to 9100 is printed verbatim by a JetDirect printer (#87)."""
    ctx, factory = _patch_client(lambda req: _response("<title>nope</title>"))
    with ctx:
        for port in (515, 631, 9100, 9101, 9102):
            assert await probe_port("10.0.0.5", port) is None
    assert factory.requests == []
