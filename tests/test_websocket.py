"""Tests for Homelable WebSocket commands."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.homelable.const import DOMAIN
from custom_components.homelable.coordinator import HomelableCoordinator
from custom_components.homelable.websocket import (
    async_register_websocket_commands,
)


def _mock_entry() -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "ws_test_entry"
    entry.data = {"scan_ranges": "192.168.1.0/24", "status_interval": 60}
    entry.options = {}
    return entry


@pytest.fixture
async def setup_ws(hass: HomeAssistant, hass_storage):  # noqa: ANN001
    """Wire a coordinator into hass.data and register WS commands.

    `hass_storage` resets HA's storage layer per-test so Stores stay isolated.
    """
    coord = HomelableCoordinator(hass, _mock_entry())
    hass.data.setdefault(DOMAIN, {})[coord.entry.entry_id] = coord
    async_register_websocket_commands(hass)
    return coord


# ─── Canvas ──────────────────────────────────────────────────────────────────


async def test_get_canvas_returns_default(
    hass: HomeAssistant, hass_ws_client, setup_ws  # noqa: ANN001
) -> None:
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "homelable/get_canvas"})
    msg = await client.receive_json()
    assert msg["success"] is True
    assert msg["result"] == {
        "nodes": [],
        "edges": [],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }


async def test_save_canvas_persists_via_ws(
    hass: HomeAssistant, hass_ws_client, setup_ws  # noqa: ANN001
) -> None:
    client = await hass_ws_client(hass)
    canvas = {"nodes": [{"id": "n1"}], "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}}
    await client.send_json({"id": 1, "type": "homelable/save_canvas", "canvas": canvas})
    msg = await client.receive_json()
    assert msg["success"] is True
    assert msg["result"] == {"ok": True}

    saved = await setup_ws.get_canvas()
    assert saved["nodes"] == [{"id": "n1"}]


# ─── Scan ────────────────────────────────────────────────────────────────────


async def test_scan_start_returns_run_id(
    hass: HomeAssistant, hass_ws_client, setup_ws  # noqa: ANN001
) -> None:
    client = await hass_ws_client(hass)
    with patch.object(
        setup_ws, "trigger_scan", AsyncMock(return_value={"run_id": "abc", "started": True})
    ):
        await client.send_json({"id": 1, "type": "homelable/scan/start"})
        msg = await client.receive_json()
    assert msg["success"] is True
    assert msg["result"]["run_id"] == "abc"


async def test_scan_cancel(
    hass: HomeAssistant, hass_ws_client, setup_ws  # noqa: ANN001
) -> None:
    client = await hass_ws_client(hass)
    with patch.object(setup_ws, "cancel_scan", return_value=True):
        await client.send_json({"id": 1, "type": "homelable/scan/cancel"})
        msg = await client.receive_json()
    assert msg["success"] is True
    assert msg["result"] == {"cancelled": True}


async def test_scan_pending_default_status(
    hass: HomeAssistant, hass_ws_client, setup_ws  # noqa: ANN001
) -> None:
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "homelable/scan/pending"})
    msg = await client.receive_json()
    assert msg["success"] is True
    assert msg["result"] == {"devices": []}


async def test_scan_pending_hidden_status(
    hass: HomeAssistant, hass_ws_client, setup_ws  # noqa: ANN001
) -> None:
    pending = await setup_ws._get_pending()
    pending["devices"].append({"id": "pd-h", "ip": "10.0.0.5", "status": "hidden"})
    await setup_ws._save_pending()

    client = await hass_ws_client(hass)
    await client.send_json(
        {"id": 1, "type": "homelable/scan/pending", "status": "hidden"}
    )
    msg = await client.receive_json()
    assert msg["success"] is True
    assert len(msg["result"]["devices"]) == 1
    assert msg["result"]["devices"][0]["id"] == "pd-h"


async def test_scan_approve_existing(
    hass: HomeAssistant, hass_ws_client, setup_ws  # noqa: ANN001
) -> None:
    pending = await setup_ws._get_pending()
    pending["devices"].append(
        {
            "id": "pd-1",
            "ip": "10.0.0.6",
            "mac": None,
            "hostname": "h",
            "os": None,
            "services": [],
            "suggested_type": "server",
            "status": "pending",
        }
    )
    await setup_ws._save_pending()

    client = await hass_ws_client(hass)
    await client.send_json(
        {
            "id": 1,
            "type": "homelable/scan/approve",
            "device_id": "pd-1",
            "overrides": {"position": {"x": 1, "y": 2}},
        }
    )
    msg = await client.receive_json()
    assert msg["success"] is True
    assert msg["result"]["node"]["data"]["ip"] == "10.0.0.6"


async def test_scan_approve_unknown_returns_error(
    hass: HomeAssistant, hass_ws_client, setup_ws  # noqa: ANN001
) -> None:
    client = await hass_ws_client(hass)
    await client.send_json(
        {"id": 1, "type": "homelable/scan/approve", "device_id": "nope"}
    )
    msg = await client.receive_json()
    assert msg["success"] is False
    assert msg["error"]["code"] == "not_found"


async def test_scan_hide_existing(
    hass: HomeAssistant, hass_ws_client, setup_ws  # noqa: ANN001
) -> None:
    pending = await setup_ws._get_pending()
    pending["devices"].append({"id": "pd-2", "ip": "10.0.0.7", "status": "pending"})
    await setup_ws._save_pending()

    client = await hass_ws_client(hass)
    await client.send_json(
        {"id": 1, "type": "homelable/scan/hide", "device_id": "pd-2"}
    )
    msg = await client.receive_json()
    assert msg["success"] is True
    assert msg["result"] == {"ok": True}


async def test_scan_hide_unknown_returns_error(
    hass: HomeAssistant, hass_ws_client, setup_ws  # noqa: ANN001
) -> None:
    client = await hass_ws_client(hass)
    await client.send_json(
        {"id": 1, "type": "homelable/scan/hide", "device_id": "nope"}
    )
    msg = await client.receive_json()
    assert msg["success"] is False
    assert msg["error"]["code"] == "not_found"


async def test_scan_get_config(
    hass: HomeAssistant, hass_ws_client, setup_ws  # noqa: ANN001
) -> None:
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "homelable/scan/get_config"})
    msg = await client.receive_json()
    assert msg["success"] is True
    assert "ranges" in msg["result"]


async def test_scan_clear(
    hass: HomeAssistant, hass_ws_client, setup_ws  # noqa: ANN001
) -> None:
    pending = await setup_ws._get_pending()
    pending["devices"].extend(
        [
            {"id": "a", "ip": "10.0.0.1", "status": "pending"},
            {"id": "b", "ip": "10.0.0.2", "status": "pending"},
        ]
    )
    await setup_ws._save_pending()

    before = await setup_ws._get_pending()
    expected_removed = sum(
        1 for d in before["devices"] if d.get("status") == "pending"
    )
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "homelable/scan/clear"})
    msg = await client.receive_json()
    assert msg["success"] is True
    assert msg["result"]["removed"] == expected_removed
    after = await setup_ws.list_pending()
    assert after == []


async def test_scan_runs(
    hass: HomeAssistant, hass_ws_client, setup_ws  # noqa: ANN001
) -> None:
    client = await hass_ws_client(hass)
    with patch.object(
        setup_ws, "list_runs", AsyncMock(return_value=[{"run_id": "r1"}])
    ):
        await client.send_json({"id": 1, "type": "homelable/scan/runs"})
        msg = await client.receive_json()
    assert msg["success"] is True
    assert msg["result"] == {"runs": [{"run_id": "r1"}]}


# ─── Status ──────────────────────────────────────────────────────────────────


async def test_status_get_returns_coordinator_data(
    hass: HomeAssistant, hass_ws_client, setup_ws  # noqa: ANN001
) -> None:
    setup_ws.data = {"n1": {"status": "online"}}
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "homelable/status/get"})
    msg = await client.receive_json()
    assert msg["success"] is True
    assert msg["result"] == {"n1": {"status": "online"}}


async def test_status_subscribe_pushes_initial_snapshot(
    hass: HomeAssistant, hass_ws_client, setup_ws  # noqa: ANN001
) -> None:
    setup_ws.data = {"n1": {"status": "online"}}
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "homelable/status/subscribe"})

    # First message: ack of subscription.
    ack = await client.receive_json()
    assert ack["success"] is True

    # Second message: initial snapshot pushed via event_message.
    snapshot = await client.receive_json()
    assert snapshot["type"] == "event"
    assert snapshot["event"] == {"n1": {"status": "online"}}


# ─── Not setup ───────────────────────────────────────────────────────────────


async def test_get_canvas_not_setup_returns_error(
    hass: HomeAssistant, hass_ws_client  # noqa: ANN001
) -> None:
    """When no coordinator is registered, WS commands return not_setup."""
    async_register_websocket_commands(hass)
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "homelable/get_canvas"})
    msg = await client.receive_json()
    assert msg["success"] is False
    assert msg["error"]["code"] == "not_setup"


# ─── Admin gating ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        {"type": "homelable/save_canvas", "canvas": {"nodes": [], "edges": [], "viewport": {}}},
        {"type": "homelable/scan/start"},
        {"type": "homelable/scan/cancel"},
        {"type": "homelable/scan/approve", "device_id": "x"},
        {"type": "homelable/scan/hide", "device_id": "x"},
        {"type": "homelable/scan/clear"},
    ],
)
async def test_mutating_commands_reject_non_admin(
    hass: HomeAssistant, hass_ws_client, hass_read_only_access_token, setup_ws, command  # noqa: ANN001
) -> None:
    """Non-admin users must not be able to scan, save, approve, hide, or clear."""
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json({"id": 1, **command})
    msg = await client.receive_json()
    assert msg["success"] is False
    assert msg["error"]["code"] == "unauthorized"
