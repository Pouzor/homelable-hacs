"""Tests for the status_checker module."""
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.homelable import status_checker


@pytest.fixture(autouse=True)
def _allow_all_hosts():
    """Bypass the SSRF host filter so unit tests can use synthetic hostnames.

    The filter resolves DNS, which is blocked under pytest-socket. Tests that
    care about the filter live in `test_status_checker_security`.
    """
    with patch.object(status_checker, "_host_is_allowed", return_value=True):
        yield


@pytest.mark.asyncio
async def test_check_none_returns_online() -> None:
    """check_method='none' always reports online without probing."""
    result = await status_checker.check_node("none", None, None)
    assert result == {"status": "online", "response_time_ms": None}


@pytest.mark.asyncio
async def test_check_no_host_returns_unknown() -> None:
    """No target and no IP → status unknown."""
    result = await status_checker.check_node("ping", None, None)
    assert result == {"status": "unknown", "response_time_ms": None}


@pytest.mark.asyncio
async def test_check_ping_online() -> None:
    """Successful ping → online with response_time_ms set."""
    with patch.object(status_checker, "_ping", AsyncMock(return_value=True)):
        result = await status_checker.check_node("ping", None, "10.0.0.1")
    assert result["status"] == "online"
    assert isinstance(result["response_time_ms"], int)


@pytest.mark.asyncio
async def test_check_ping_offline() -> None:
    """Failed ping → offline."""
    with patch.object(status_checker, "_ping", AsyncMock(return_value=False)):
        result = await status_checker.check_node("ping", None, "10.0.0.1")
    assert result["status"] == "offline"


@pytest.mark.asyncio
async def test_check_uses_target_over_ip() -> None:
    """target wins over ip when both provided."""
    with patch.object(status_checker, "_ping", AsyncMock(return_value=True)) as mock:
        await status_checker.check_node("ping", "host.local", "10.0.0.1")
    mock.assert_awaited_once_with("host.local")


@pytest.mark.asyncio
async def test_check_ip_first_value_when_comma_separated() -> None:
    """Multi-IP field uses only the first address."""
    with patch.object(status_checker, "_ping", AsyncMock(return_value=True)) as mock:
        await status_checker.check_node("ping", None, "10.0.0.1, 10.0.0.2")
    mock.assert_awaited_once_with("10.0.0.1")


@pytest.mark.asyncio
async def test_check_ping_strips_url_target() -> None:
    """A URL typed into a ping target is reduced to its host before pinging.

    Regression for #24: the `http://...` placeholder leads users to enter a
    URL even for ping checks; the scheme used to break resolution and the
    device was wrongly reported offline.
    """
    with patch.object(status_checker, "_ping", AsyncMock(return_value=True)) as mock:
        await status_checker.check_node("ping", "http://192.168.1.10:8080", None)
    mock.assert_awaited_once_with("192.168.1.10")


@pytest.mark.asyncio
async def test_check_ping_strips_host_port_target() -> None:
    """A `host:port` ping target is reduced to the bare host."""
    with patch.object(status_checker, "_ping", AsyncMock(return_value=True)) as mock:
        await status_checker.check_node("ping", "host.local:8080", None)
    mock.assert_awaited_once_with("host.local")


@pytest.mark.asyncio
async def test_check_ssh_strips_url_target() -> None:
    """SSH target with a scheme is reduced to the host before connecting."""
    with patch.object(status_checker, "_tcp_connect", AsyncMock(return_value=True)) as mock:
        await status_checker.check_node("ssh", "ssh://10.0.0.1", None)
    mock.assert_awaited_once_with("10.0.0.1", 22)


@pytest.mark.asyncio
async def test_check_tcp_parses_url_port() -> None:
    """tcp check pulls the port from a full URL target."""
    with patch.object(status_checker, "_tcp_connect", AsyncMock(return_value=True)) as mock:
        await status_checker.check_node("tcp", "http://host:8080/path", None)
    mock.assert_awaited_once_with("host", 8080)


@pytest.mark.asyncio
async def test_check_http_prepends_scheme() -> None:
    """http check adds http:// when target lacks scheme."""
    with patch.object(status_checker, "_http_get", AsyncMock(return_value=True)) as mock:
        await status_checker.check_node("http", "example.com", None)
    mock.assert_awaited_once_with("http://example.com", verify=False)


@pytest.mark.asyncio
async def test_check_tcp_parses_port() -> None:
    """tcp check parses host:port."""
    with patch.object(status_checker, "_tcp_connect", AsyncMock(return_value=True)) as mock:
        await status_checker.check_node("tcp", "host:8080", None)
    mock.assert_awaited_once_with("host", 8080)


@pytest.mark.asyncio
async def test_check_ssh_uses_port_22() -> None:
    with patch.object(status_checker, "_tcp_connect", AsyncMock(return_value=True)) as mock:
        await status_checker.check_node("ssh", None, "10.0.0.1")
    mock.assert_awaited_once_with("10.0.0.1", 22)


@pytest.mark.asyncio
async def test_check_unknown_method_falls_back_to_ping() -> None:
    """Unknown check_method silently uses ping."""
    with patch.object(status_checker, "_ping", AsyncMock(return_value=True)) as mock:
        await status_checker.check_node("bogus", None, "10.0.0.1")
    mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_exception_returns_offline() -> None:
    """Any unexpected exception during probing → offline."""
    with patch.object(
        status_checker, "_ping", AsyncMock(side_effect=RuntimeError("boom"))
    ):
        result = await status_checker.check_node("ping", None, "10.0.0.1")
    assert result == {"status": "offline", "response_time_ms": None}


# --- IPv6 detection (#196 B2) ---

def test_is_ipv6_detects_literals() -> None:
    assert status_checker._is_ipv6("fe80::1")
    assert status_checker._is_ipv6("[2001:db8::1]")
    assert not status_checker._is_ipv6("192.168.1.1")
    assert not status_checker._is_ipv6("example.com")


# --- Per-service status checks (#196 follow-up) ---

@pytest.mark.asyncio
async def test_check_service_web_online() -> None:
    """A web port that answers HTTP → online."""
    svc = {"port": 8080, "protocol": "tcp", "service_name": "http"}
    with patch.object(
        status_checker, "_http_get", AsyncMock(return_value=True)
    ) as mock:
        status = await status_checker.check_service(svc, "10.0.0.5")
    assert status == "online"
    assert mock.call_args.args[0] == "http://10.0.0.5:8080"


@pytest.mark.asyncio
async def test_check_service_web_offline() -> None:
    svc = {"port": 8080, "protocol": "tcp", "service_name": "http"}
    with patch.object(status_checker, "_http_get", AsyncMock(return_value=False)):
        status = await status_checker.check_service(svc, "10.0.0.5")
    assert status == "offline"


@pytest.mark.asyncio
async def test_check_service_https_port_uses_https_scheme() -> None:
    svc = {"port": 443, "protocol": "tcp", "service_name": "web"}
    with patch.object(
        status_checker, "_http_get", AsyncMock(return_value=True)
    ) as mock:
        await status_checker.check_service(svc, "10.0.0.5")
    assert mock.call_args.args[0] == "https://10.0.0.5:443"


@pytest.mark.asyncio
async def test_check_service_non_http_port_is_unknown() -> None:
    """SSH and other non-web ports are never probed — stay grey."""
    svc = {"port": 22, "protocol": "tcp", "service_name": "ssh"}
    with patch.object(status_checker, "_http_get", AsyncMock()) as mock:
        status = await status_checker.check_service(svc, "10.0.0.5")
    assert status == "unknown"
    mock.assert_not_called()


@pytest.mark.asyncio
async def test_check_service_udp_is_unknown() -> None:
    svc = {"port": 53, "protocol": "udp", "service_name": "dns"}
    status = await status_checker.check_service(svc, "10.0.0.5")
    assert status == "unknown"


@pytest.mark.asyncio
async def test_check_service_portless_non_web_is_unknown() -> None:
    svc = {"protocol": "tcp", "service_name": "smtp"}
    status = await status_checker.check_service(svc, "10.0.0.5")
    assert status == "unknown"


@pytest.mark.asyncio
async def test_check_service_no_host_is_unknown() -> None:
    svc = {"port": 80, "protocol": "tcp", "service_name": "http"}
    assert await status_checker.check_service(svc, None) == "unknown"


@pytest.mark.asyncio
async def test_check_services_returns_one_row_per_service() -> None:
    services = [
        {"port": 80, "protocol": "tcp", "service_name": "http"},
        {"port": 22, "protocol": "tcp", "service_name": "ssh"},
    ]
    with patch.object(status_checker, "_http_get", AsyncMock(return_value=True)):
        rows = await status_checker.check_services("10.0.0.5", services)
    assert rows == [
        {"port": 80, "protocol": "tcp", "status": "online"},
        {"port": 22, "protocol": "tcp", "status": "unknown"},
    ]


# --- HTTPS verify: SSL context built off the event loop (blocking-call fix) ---

@pytest.fixture(autouse=True)
def _reset_ssl_context():
    """Clear the cached SSL context so each test observes a fresh build."""
    status_checker._ssl_context = None
    yield
    status_checker._ssl_context = None


@pytest.mark.asyncio
async def test_verifying_ssl_context_built_in_thread_and_cached() -> None:
    """The verifying context is created via a thread (never load CA on the loop)
    and reused on subsequent calls."""
    import ssl

    with patch.object(
        status_checker.asyncio, "to_thread", wraps=status_checker.asyncio.to_thread
    ) as to_thread:
        ctx1 = await status_checker._verifying_ssl_context()
        ctx2 = await status_checker._verifying_ssl_context()

    assert isinstance(ctx1, ssl.SSLContext)
    assert ctx1 is ctx2  # cached, not rebuilt
    to_thread.assert_awaited_once()  # only the first call builds it


@pytest.mark.asyncio
async def test_http_get_verify_passes_ssl_context_not_true() -> None:
    """_http_get(verify=True) must hand httpx a pre-built SSLContext, so httpx
    never loads the CA bundle (blocking I/O) on the event loop."""
    import ssl

    captured: dict = {}

    class _FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            return type("R", (), {"status_code": 200})()

    with patch.object(status_checker.httpx, "AsyncClient", _FakeClient):
        assert await status_checker._http_get("https://example.com", verify=True)

    assert isinstance(captured["verify"], ssl.SSLContext)
    assert captured["verify"] is not True


@pytest.mark.asyncio
async def test_http_get_no_verify_stays_false() -> None:
    """verify=False keeps verify False — no SSL context, no CA load at all."""
    captured: dict = {}

    class _FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            return type("R", (), {"status_code": 204})()

    with patch.object(status_checker.httpx, "AsyncClient", _FakeClient):
        assert await status_checker._http_get("http://example.com", verify=False)

    assert captured["verify"] is False


@pytest.mark.asyncio
async def test_check_services_empty() -> None:
    assert await status_checker.check_services("10.0.0.5", []) == []
