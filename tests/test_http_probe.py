"""Tests for the HTTP probe used by deep-scan service identification."""
from unittest.mock import AsyncMock, patch

import httpx

from custom_components.homelable.http_probe import (
    _extract_title,
    probe_open_ports,
    probe_port,
)


def _response(text: str = "", headers: dict | None = None, status: int = 200) -> httpx.Response:
    return httpx.Response(status_code=status, text=text, headers=headers or {})


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
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_response("<title>Jellyfin</title>"))):
        result = await probe_port("10.0.0.5", 8096)
    assert result == {"title": "Jellyfin", "headers": {}}


async def test_probe_port_reads_headers() -> None:
    resp = _response("", headers={"Server": "nginx", "X-Powered-By": "Express"})
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=resp)):
        result = await probe_port("10.0.0.5", 3000)
    assert result["headers"] == {"Server": "nginx", "X-Powered-By": "Express"}


async def test_probe_port_falls_back_to_http() -> None:
    # https raises, http succeeds
    calls = {"n": 0}

    async def fake_get(self, url, **kw):  # noqa: ANN001, ANN003
        calls["n"] += 1
        if url.startswith("https"):
            raise httpx.ConnectError("tls fail")
        return _response("<title>HTTP App</title>")

    with patch("httpx.AsyncClient.get", new=fake_get):
        result = await probe_port("10.0.0.5", 8080)
    assert result["title"] == "HTTP App"
    assert calls["n"] == 2  # tried https then http


async def test_probe_port_no_signal_returns_none() -> None:
    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=_response(""))):
        result = await probe_port("10.0.0.5", 8080)
    assert result is None


async def test_probe_port_timeout_returns_none() -> None:
    with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=httpx.TimeoutException("slow"))):
        result = await probe_port("10.0.0.5", 8080)
    assert result is None


async def test_probe_port_skips_non_http_ports() -> None:
    # SSH should never trigger an HTTP request
    get = AsyncMock()
    with patch("httpx.AsyncClient.get", new=get):
        result = await probe_port("10.0.0.5", 22)
    assert result is None
    get.assert_not_called()


async def test_probe_port_verify_tls_flag_passed() -> None:
    # verify_tls=True must hand httpx a pre-built SSLContext (built off the event
    # loop), never verify=True — which would load the CA bundle on the loop.
    import ssl

    with patch("custom_components.homelable.http_probe.httpx.AsyncClient") as client_cls:
        instance = client_cls.return_value.__aenter__.return_value
        instance.get = AsyncMock(return_value=_response("<title>X</title>"))
        await probe_port("10.0.0.5", 8443, verify_tls=True)
    assert isinstance(client_cls.call_args.kwargs["verify"], ssl.SSLContext)


async def test_probe_port_no_verify_stays_false() -> None:
    with patch("custom_components.homelable.http_probe.httpx.AsyncClient") as client_cls:
        instance = client_cls.return_value.__aenter__.return_value
        instance.get = AsyncMock(return_value=_response("<title>X</title>"))
        await probe_port("10.0.0.5", 8080, verify_tls=False)
    assert client_cls.call_args.kwargs["verify"] is False


# ── probe_open_ports ─────────────────────────────────────────────────────────

async def test_probe_open_ports_enriches_each_port() -> None:
    async def fake_get(self, url, **kw):  # noqa: ANN001, ANN003
        if ":8096" in url:
            return _response("<title>Jellyfin</title>")
        return _response("")

    ports = [{"port": 8096, "protocol": "tcp"}, {"port": 9999, "protocol": "tcp"}]
    with patch("httpx.AsyncClient.get", new=fake_get):
        result = await probe_open_ports("10.0.0.5", ports)

    by_port = {p["port"]: p for p in result}
    assert by_port[8096]["http_signals"]["title"] == "Jellyfin"
    assert by_port[9999]["http_signals"] is None
