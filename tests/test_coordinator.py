"""Tests for the HomelableCoordinator."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.homelable.coordinator import HomelableCoordinator


def _mock_entry(scan_ranges: str = "192.168.1.0/24") -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {"scan_ranges": scan_ranges, "status_interval": 60}
    entry.options = {}
    return entry


@pytest.fixture
def coord(hass):  # noqa: ANN001
    """Build a coordinator wired to the test hass instance."""
    return HomelableCoordinator(hass, _mock_entry())


@pytest.mark.asyncio
async def test_get_canvas_returns_default_when_empty(coord) -> None:  # noqa: ANN001
    canvas = await coord.get_canvas()
    assert canvas == {
        "nodes": [],
        "edges": [],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }


@pytest.mark.asyncio
async def test_save_canvas_persists(coord) -> None:  # noqa: ANN001
    new_canvas = {"nodes": [{"id": "n1"}], "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}}
    await coord.save_canvas(new_canvas)
    assert (await coord.get_canvas())["nodes"] == [{"id": "n1"}]


@pytest.mark.asyncio
async def test_trigger_scan_adds_new_device_to_pending(coord) -> None:  # noqa: ANN001
    fake_devices = [
        {
            "ip": "192.168.1.50",
            "mac": "AA:BB:CC:00:00:01",
            "hostname": "device.lan",
            "os": None,
            "open_ports": [{"port": 80, "protocol": "tcp", "banner": "nginx"}],
            "services": [],
            "suggested_type": "generic",
            "discovery_source": "nmap",
        }
    ]
    with patch(
        "custom_components.homelable.coordinator.scanner.run_scan",
        AsyncMock(return_value=fake_devices),
    ):
        result = await coord.trigger_scan()
        assert result["status"] == "running"
        await coord.hass.async_block_till_done()

    pending = await coord.list_pending()
    assert len(pending) == 1
    assert pending[0]["ip"] == "192.168.1.50"
    assert pending[0]["status"] == "pending"
    assert "id" in pending[0]
    assert "discovered_at" in pending[0]


@pytest.mark.asyncio
async def test_trigger_scan_excludes_canvas_and_hidden(coord) -> None:  # noqa: ANN001
    await coord.save_canvas(
        {
            "nodes": [
                {"id": "n1", "type": "router", "position": {"x": 0, "y": 0},
                 "data": {"ip": "192.168.1.1"}}
            ],
            "edges": [],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
        }
    )
    # Pre-populate pending with a hidden device
    pending = await coord._get_pending()
    pending["devices"].append(
        {"id": "pd-hidden", "ip": "192.168.1.99", "status": "hidden"}
    )
    await coord._save_pending()

    captured: dict = {}

    async def _fake(ranges, run_id=None, *, exclude_ips=None):
        captured["exclude"] = exclude_ips
        return []

    with patch(
        "custom_components.homelable.coordinator.scanner.run_scan", _fake
    ):
        await coord.trigger_scan()
        await coord.hass.async_block_till_done()

    assert "192.168.1.1" in captured["exclude"]
    assert "192.168.1.99" in captured["exclude"]


@pytest.mark.asyncio
async def test_hide_pending_marks_status(coord) -> None:  # noqa: ANN001
    pending = await coord._get_pending()
    pending["devices"].append(
        {"id": "pd-1", "ip": "10.0.0.1", "status": "pending"}
    )
    await coord._save_pending()

    assert await coord.hide_pending("pd-1") is True
    hidden = await coord.list_pending(status="hidden")
    assert len(hidden) == 1
    assert hidden[0]["id"] == "pd-1"


@pytest.mark.asyncio
async def test_hide_pending_unknown_id_returns_false(coord) -> None:  # noqa: ANN001
    assert await coord.hide_pending("nope") is False


@pytest.mark.asyncio
async def test_approve_pending_creates_canvas_node(coord) -> None:  # noqa: ANN001
    pending = await coord._get_pending()
    pending["devices"].append(
        {
            "id": "pd-1",
            "ip": "10.0.0.5",
            "mac": None,
            "hostname": "myhost",
            "os": None,
            "services": [],
            "suggested_type": "server",
            "status": "pending",
        }
    )
    await coord._save_pending()

    node = await coord.approve_pending("pd-1", {"position": {"x": 100, "y": 50}})

    assert node is not None
    assert node["type"] == "server"
    assert node["data"]["ip"] == "10.0.0.5"
    assert node["position"] == {"x": 100, "y": 50}

    canvas = await coord.get_canvas()
    assert len(canvas["nodes"]) == 1
    # device removed from pending
    assert await coord.list_pending() == []


@pytest.mark.asyncio
async def test_approve_pending_unknown_returns_none(coord) -> None:  # noqa: ANN001
    assert await coord.approve_pending("nope") is None


@pytest.mark.asyncio
async def test_async_update_data_runs_status_check_per_node(coord) -> None:  # noqa: ANN001
    await coord.save_canvas(
        {
            "nodes": [
                {"id": "n1", "type": "server", "position": {"x": 0, "y": 0},
                 "data": {"ip": "10.0.0.1", "check_method": "ping"}},
                {"id": "n2", "type": "server", "position": {"x": 0, "y": 0},
                 "data": {"ip": "10.0.0.2", "check_method": "ping"}},
            ],
            "edges": [],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
        }
    )

    async def _fake_check(method, target, ip):
        return {"status": "online", "response_time_ms": 5}

    with patch(
        "custom_components.homelable.coordinator.status_checker.check_node",
        _fake_check,
    ):
        result = await coord._async_update_data()

    assert set(result.keys()) == {"n1", "n2"}
    assert result["n1"]["status"] == "online"


@pytest.mark.asyncio
async def test_trigger_scan_updates_existing_pending_in_place(coord) -> None:  # noqa: ANN001
    pending = await coord._get_pending()
    pending["devices"].append(
        {
            "id": "pd-existing",
            "ip": "10.0.0.10",
            "mac": None,
            "hostname": None,
            "status": "pending",
        }
    )
    await coord._save_pending()

    fake = [
        {
            "ip": "10.0.0.10",
            "mac": "AA:BB:CC:11:22:33",
            "hostname": "now-known.lan",
            "os": None,
            "open_ports": [],
            "services": [],
            "suggested_type": "generic",
            "discovery_source": "nmap",
        }
    ]
    with patch(
        "custom_components.homelable.coordinator.scanner.run_scan",
        AsyncMock(return_value=fake),
    ):
        await coord.trigger_scan()
        await coord.hass.async_block_till_done()

    devices = await coord.list_pending()
    assert len(devices) == 1
    assert devices[0]["mac"] == "AA:BB:CC:11:22:33"
    assert devices[0]["hostname"] == "now-known.lan"
