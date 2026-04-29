"""Tests for the status_checker module."""
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.homelable import status_checker


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
async def test_check_http_prepends_scheme() -> None:
    """http check adds http:// when target lacks scheme."""
    with patch.object(status_checker, "_http_get", AsyncMock(return_value=True)) as mock:
        await status_checker.check_node("http", "example.com", None)
    mock.assert_awaited_once_with("http://example.com")


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
