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
    assert [n["id"] for n in saved["nodes"]] == ["n1"]


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


async def test_scan_start_forwards_deep_scan_options(
    hass: HomeAssistant, hass_ws_client, setup_ws  # noqa: ANN001
) -> None:
    """Deep-scan params on scan/start are forwarded to trigger_scan."""
    client = await hass_ws_client(hass)
    trigger = AsyncMock(return_value={"run_id": "abc"})
    with patch.object(setup_ws, "trigger_scan", trigger):
        await client.send_json(
            {
                "id": 1,
                "type": "homelable/scan/start",
                "http_ranges": ["8000-8100"],
                "http_probe_enabled": True,
                "verify_tls": True,
            }
        )
        msg = await client.receive_json()
    assert msg["success"] is True
    trigger.assert_awaited_once_with(
        http_ranges=["8000-8100"], http_probe_enabled=True, verify_tls=True
    )


async def test_scan_start_rejects_invalid_port_range(
    hass: HomeAssistant, hass_ws_client, setup_ws  # noqa: ANN001
) -> None:
    """A malformed deep-scan port range is rejected before a scan starts."""
    client = await hass_ws_client(hass)
    trigger = AsyncMock(return_value={"run_id": "abc"})
    with patch.object(setup_ws, "trigger_scan", trigger):
        await client.send_json(
            {
                "id": 1,
                "type": "homelable/scan/start",
                "http_ranges": ["not-a-range"],
            }
        )
        msg = await client.receive_json()
    assert msg["success"] is False
    assert msg["error"]["code"] == "invalid_port_range"
    trigger.assert_not_awaited()


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
    # Node is returned (and stored) flat — top-level ip, not nested under data.
    assert msg["result"]["node"]["ip"] == "10.0.0.6"
    assert msg["result"]["node"]["pos_x"] == 1


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


async def test_scan_subscribe_forwards_dispatcher_events(
    hass: HomeAssistant, hass_ws_client, setup_ws  # noqa: ANN001
) -> None:
    """Events fired on SCAN_SIGNAL reach the subscribed WS client."""
    from homeassistant.helpers.dispatcher import async_dispatcher_send

    from custom_components.homelable.const import SCAN_SIGNAL

    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "homelable/scan/subscribe"})
    ack = await client.receive_json()
    assert ack["success"] is True

    payload = {
        "event": "device_discovered",
        "run_id": "r-1",
        "device": {"ip": "10.0.0.5", "mac": None, "hostname": None},
    }
    async_dispatcher_send(hass, SCAN_SIGNAL, payload)
    msg = await client.receive_json()
    assert msg["type"] == "event"
    assert msg["event"] == payload


async def test_service_status_subscribe_pushes_events(
    hass: HomeAssistant, hass_ws_client, setup_ws  # noqa: ANN001
) -> None:
    from homeassistant.helpers.dispatcher import async_dispatcher_send

    from custom_components.homelable.const import SERVICE_STATUS_SIGNAL

    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "homelable/service_status/subscribe"})
    ack = await client.receive_json()
    assert ack["success"] is True

    payload = {
        "node_id": "n1",
        "services": [{"port": 80, "protocol": "tcp", "status": "offline"}],
        "checked_at": "2024-01-01T00:00:00+00:00",
    }
    async_dispatcher_send(hass, SERVICE_STATUS_SIGNAL, payload)
    msg = await client.receive_json()
    assert msg["type"] == "event"
    assert msg["event"] == payload


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
        {"type": "homelable/scan/rescan", "device_id": "x"},
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


# ─── Per-device deep scan (homelable #363) ───────────────────────────────────


def _device_scan_result(ports: list[int], *, scanned: int = 8, cancelled: bool = False) -> dict:
    open_ports = [{"port": p, "protocol": "tcp", "banner": ""} for p in ports]
    return {
        "device": {
            "ip": "10.0.0.5",
            "mac": None,
            "hostname": None,
            "os": None,
            "open_ports": open_ports,
            "services": [
                {"port": p, "protocol": "tcp", "service_name": f"svc-{p}", "icon": "guess", "category": None}
                for p in ports
            ],
            "suggested_type": "server",
            "discovery_source": "tcp",
        },
        "scanned": scanned,
        "total": 8,
        "cancelled": cancelled,
    }


async def _seed_device(coord, **fields) -> dict:  # noqa: ANN001
    pending = await coord._get_pending()
    row = {"id": "pd-1", "ip": "10.0.0.5", "status": "approved", "services": [], **fields}
    pending["devices"].append(row)
    return row


async def test_rescan_deep_scans_one_device_and_unions_its_services(
    hass: HomeAssistant, hass_ws_client, setup_ws  # noqa: ANN001
) -> None:
    row = await _seed_device(
        setup_ws,
        discovery_source="proxmox",
        open_ports=[{"port": 8006, "protocol": "tcp"}],
        services=[
            {"port": 22, "protocol": "tcp", "service_name": "SSH", "icon": "terminal"},
            {"port": 8006, "protocol": "tcp", "service_name": "Proxmox", "icon": "proxmox"},
        ],
    )
    fake = AsyncMock(return_value=_device_scan_result([22, 9000]))
    client = await hass_ws_client(hass)
    with patch("custom_components.homelable.scanner.run_device_scan", fake):
        await client.send_json({"id": 1, "type": "homelable/scan/rescan", "device_id": "pd-1"})
        msg = await client.receive_json()
        await hass.async_block_till_done()

    assert msg["success"] is True
    assert msg["result"]["status"] == "running"
    run_id = msg["result"]["run_id"]

    args, kwargs = fake.call_args
    assert args == ("10.0.0.5",)
    assert kwargs["ports"] == "1-65535"
    assert kwargs["discovery_source"] == "proxmox"

    # A hand-picked icon survives, a hand-added service stays, a new one lands.
    by_port = {s["port"]: s for s in row["services"]}
    assert by_port[22]["icon"] == "terminal"
    assert by_port[22]["service_name"] == "SSH"
    assert by_port[8006]["service_name"] == "Proxmox"
    assert by_port[9000]["service_name"] == "svc-9000"
    assert [p["port"] for p in row["open_ports"]] == [22, 8006, 9000]
    assert row["discovery_source"] == "proxmox"
    assert row["last_scan"]
    assert setup_ws._scan_run_id is None

    await client.send_json({"id": 2, "type": "homelable/scan/run", "run_id": run_id})
    run = (await client.receive_json())["result"]["run"]
    assert run["kind"] == "device"
    assert run["ranges"] == ["10.0.0.5/32"]
    assert run["status"] == "done"
    assert run["error"] is None


async def test_rescan_skips_a_stored_open_port_without_a_number(
    hass: HomeAssistant, hass_ws_client, setup_ws  # noqa: ANN001
) -> None:
    """A malformed stored entry must not crash the merge and strand the run."""
    row = await _seed_device(setup_ws, open_ports=[{"protocol": "tcp"}, {"port": 443, "protocol": "tcp"}])
    fake = AsyncMock(return_value=_device_scan_result([22]))
    client = await hass_ws_client(hass)
    with patch("custom_components.homelable.scanner.run_device_scan", fake):
        await client.send_json({"id": 1, "type": "homelable/scan/rescan", "device_id": "pd-1"})
        run_id = (await client.receive_json())["result"]["run_id"]
        await hass.async_block_till_done()

    assert [p["port"] for p in row["open_ports"]] == [22, 443]
    assert (await setup_ws.get_run(run_id))["status"] == "done"


async def test_rescan_passes_the_requested_port_range(
    hass: HomeAssistant, hass_ws_client, setup_ws  # noqa: ANN001
) -> None:
    await _seed_device(setup_ws, ip="fe80::1, 10.0.0.5")
    fake = AsyncMock(return_value=_device_scan_result([]))
    client = await hass_ws_client(hass)
    with patch("custom_components.homelable.scanner.run_device_scan", fake):
        await client.send_json(
            {"id": 1, "type": "homelable/scan/rescan", "device_id": "pd-1", "ports": " 80,443 "}
        )
        assert (await client.receive_json())["success"] is True
        await hass.async_block_till_done()

    assert fake.call_args.kwargs["ports"] == "80,443"
    assert fake.call_args.args == ("fe80::1",)


async def test_rescan_reports_a_partial_sweep(
    hass: HomeAssistant, hass_ws_client, setup_ws  # noqa: ANN001
) -> None:
    await _seed_device(setup_ws)
    fake = AsyncMock(return_value=_device_scan_result([22], scanned=3))
    client = await hass_ws_client(hass)
    with patch("custom_components.homelable.scanner.run_device_scan", fake):
        await client.send_json({"id": 1, "type": "homelable/scan/rescan", "device_id": "pd-1"})
        run_id = (await client.receive_json())["result"]["run_id"]
        await hass.async_block_till_done()

    run = await setup_ws.get_run(run_id)
    assert run["status"] == "done"
    assert run["error"].startswith("Scanned 3/8 port ranges (1 open)")


async def test_rescan_cancelled_leaves_the_row_alone(
    hass: HomeAssistant, hass_ws_client, setup_ws  # noqa: ANN001
) -> None:
    row = await _seed_device(setup_ws)
    fake = AsyncMock(return_value=_device_scan_result([22], scanned=1, cancelled=True))
    client = await hass_ws_client(hass)
    with patch("custom_components.homelable.scanner.run_device_scan", fake):
        await client.send_json({"id": 1, "type": "homelable/scan/rescan", "device_id": "pd-1"})
        run_id = (await client.receive_json())["result"]["run_id"]
        await hass.async_block_till_done()

    assert row["services"] == []
    run = await setup_ws.get_run(run_id)
    assert (run["status"], run["error"]) == ("cancelled", None)


async def test_rescan_records_a_failure_as_error(
    hass: HomeAssistant, hass_ws_client, setup_ws  # noqa: ANN001
) -> None:
    await _seed_device(setup_ws)
    fake = AsyncMock(side_effect=OSError("boom"))
    client = await hass_ws_client(hass)
    with patch("custom_components.homelable.scanner.run_device_scan", fake):
        await client.send_json({"id": 1, "type": "homelable/scan/rescan", "device_id": "pd-1"})
        run_id = (await client.receive_json())["result"]["run_id"]
        await hass.async_block_till_done()

    run = await setup_ws.get_run(run_id)
    assert (run["status"], run["error"]) == ("error", "boom")
    assert setup_ws._scan_run_id is None


async def test_rescan_shares_the_scan_slot(
    hass: HomeAssistant, hass_ws_client, setup_ws  # noqa: ANN001
) -> None:
    await _seed_device(setup_ws)
    setup_ws._scan_run_id = "network-run"
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "homelable/scan/rescan", "device_id": "pd-1"})
    msg = await client.receive_json()
    assert msg["result"] == {"run_id": "network-run", "status": "already_running"}


@pytest.mark.parametrize(
    ("fields", "payload", "code"),
    [
        ({}, {"device_id": "nope"}, "not_found"),
        ({"ip": None}, {"device_id": "pd-1"}, "no_ip"),
        ({"status": "hidden"}, {"device_id": "pd-1"}, "hidden"),
        ({}, {"device_id": "pd-1", "ports": "1-99999"}, "invalid_port_range"),
    ],
)
async def test_rescan_refuses_what_it_cannot_scan(
    hass: HomeAssistant, hass_ws_client, setup_ws, fields, payload, code  # noqa: ANN001
) -> None:
    await _seed_device(setup_ws, **fields)
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "homelable/scan/rescan", **payload})
    msg = await client.receive_json()
    assert msg["success"] is False
    assert msg["error"]["code"] == code
    assert await setup_ws.list_runs() == []


async def test_scan_run_unknown_id_is_not_found(
    hass: HomeAssistant, hass_ws_client, setup_ws  # noqa: ANN001
) -> None:
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "homelable/scan/run", "run_id": "nope"})
    msg = await client.receive_json()
    assert msg["error"]["code"] == "not_found"
