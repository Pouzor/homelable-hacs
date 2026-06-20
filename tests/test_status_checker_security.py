"""SSRF guard tests for status_checker."""
import ipaddress
from unittest.mock import AsyncMock, patch

from custom_components.homelable import status_checker


def _ip(addr: str):
    return ipaddress.ip_address(addr)


def _net(cidr: str):
    return ipaddress.ip_network(cidr, strict=False)


# ─── _is_unsafe_address ──────────────────────────────────────────────────────


def test_loopback_is_unsafe() -> None:
    assert status_checker._is_unsafe_address(_ip("127.0.0.1")) is True


def test_link_local_is_unsafe() -> None:
    # AWS / GCP / Azure metadata.
    assert status_checker._is_unsafe_address(_ip("169.254.169.254")) is True


def test_multicast_is_unsafe() -> None:
    assert status_checker._is_unsafe_address(_ip("224.0.0.1")) is True


def test_unspecified_is_unsafe() -> None:
    assert status_checker._is_unsafe_address(_ip("0.0.0.0")) is True


def test_rfc1918_is_safe() -> None:
    assert status_checker._is_unsafe_address(_ip("10.0.0.1")) is False
    assert status_checker._is_unsafe_address(_ip("192.168.1.1")) is False


def test_public_is_safe() -> None:
    assert status_checker._is_unsafe_address(_ip("8.8.8.8")) is False


# ─── _host_is_allowed ─────────────────────────────────────────────────────────


def test_unsafe_host_blocked_when_not_in_allowed_networks() -> None:
    assert status_checker._host_is_allowed("127.0.0.1", []) is False
    assert status_checker._host_is_allowed("169.254.169.254", []) is False


def test_unsafe_host_allowed_when_explicitly_in_scan_range() -> None:
    nets = [_net("127.0.0.1/32")]
    assert status_checker._host_is_allowed("127.0.0.1", nets) is True


def test_safe_private_host_always_allowed() -> None:
    assert status_checker._host_is_allowed("10.0.0.1", []) is True


def test_unresolvable_host_blocked() -> None:
    """If DNS fails we'd rather mark the node offline than reach out blindly."""
    with patch.object(status_checker, "_resolve_to_ip", return_value=None):
        assert status_checker._host_is_allowed("nonexistent.invalid", []) is False


# ─── _http_check scheme + host filtering ─────────────────────────────────────


async def test_http_check_rejects_non_http_scheme() -> None:
    """A target with file://, gopher://, etc. must be refused."""
    with patch.object(status_checker, "_http_get", AsyncMock(return_value=True)) as mock:
        ok = await status_checker._http_check("file:///etc/passwd", "http", [])
    assert ok is False
    mock.assert_not_awaited()


async def test_http_check_rejects_loopback_without_allowlist() -> None:
    with patch.object(status_checker, "_http_get", AsyncMock(return_value=True)) as mock:
        ok = await status_checker._http_check("127.0.0.1", "http", [])
    assert ok is False
    mock.assert_not_awaited()


async def test_http_check_allows_loopback_when_in_scan_range() -> None:
    with patch.object(status_checker, "_http_get", AsyncMock(return_value=True)) as mock:
        ok = await status_checker._http_check(
            "127.0.0.1", "http", [_net("127.0.0.0/8")]
        )
    assert ok is True
    mock.assert_awaited_once_with("http://127.0.0.1", verify=False)


async def test_http_check_https_passes_verify_true() -> None:
    with patch.object(status_checker, "_http_get", AsyncMock(return_value=True)) as mock:
        await status_checker._http_check("10.0.0.5", "https", [])
    mock.assert_awaited_once_with("https://10.0.0.5", verify=True)


# ─── End-to-end through check_node ───────────────────────────────────────────


async def test_check_node_http_rejects_metadata_endpoint() -> None:
    """169.254.169.254 must never be probed without an explicit opt-in."""
    with patch.object(status_checker, "_http_get", AsyncMock(return_value=True)) as mock:
        result = await status_checker.check_node(
            "http", "169.254.169.254", None, allowed_networks=[]
        )
    assert result["status"] == "offline"
    mock.assert_not_awaited()


async def test_check_node_ping_rejects_loopback_by_default() -> None:
    with patch.object(status_checker, "_ping", AsyncMock(return_value=True)) as mock:
        result = await status_checker.check_node(
            "ping", None, "127.0.0.1", allowed_networks=[]
        )
    assert result["status"] == "offline"
    mock.assert_not_awaited()


async def test_check_node_tcp_rejects_link_local_by_default() -> None:
    with patch.object(status_checker, "_tcp_connect", AsyncMock(return_value=True)) as mock:
        result = await status_checker.check_node(
            "tcp", "169.254.1.1:80", None, allowed_networks=[]
        )
    assert result["status"] == "offline"
    mock.assert_not_awaited()


# ─── Per-service checks honor the host filter ────────────────────────────────


async def test_check_service_blocks_loopback_without_allowlist() -> None:
    """A web service on loopback is reported offline, never probed."""
    svc = {"port": 80, "protocol": "tcp", "service_name": "http"}
    with patch.object(status_checker, "_http_get", AsyncMock(return_value=True)) as mock:
        status = await status_checker.check_service(svc, "127.0.0.1", [])
    assert status == "offline"
    mock.assert_not_awaited()


async def test_check_service_allows_loopback_when_in_scan_range() -> None:
    svc = {"port": 80, "protocol": "tcp", "service_name": "http"}
    with patch.object(status_checker, "_http_get", AsyncMock(return_value=True)) as mock:
        status = await status_checker.check_service(
            svc, "127.0.0.1", [_net("127.0.0.0/8")]
        )
    assert status == "online"
    mock.assert_awaited_once()
