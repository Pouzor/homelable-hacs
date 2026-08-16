"""Tests for the Zigbee2MQTT importer (parser + coordinator import + WS)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.homelable import zigbee
from custom_components.homelable.const import DOMAIN
from custom_components.homelable.coordinator import HomelableCoordinator
from custom_components.homelable.websocket import async_register_websocket_commands

# ─── Parser ──────────────────────────────────────────────────────────────────


def _coord_node(ieee: str = "0x00124b001234abcd") -> dict:
    return {
        "ieeeAddr": ieee,
        "type": "Coordinator",
        "friendlyName": "Coordinator",
        "definition": {"model": "CC2652P", "vendor": "Texas Instruments"},
    }


def _router(ieee: str, friendly: str = "router") -> dict:
    return {
        "ieeeAddr": ieee,
        "type": "Router",
        "friendlyName": friendly,
        "definition": {"model": "E11-N1EA", "vendor": "Sengled"},
    }


def _end(ieee: str, friendly: str = "sensor") -> dict:
    return {
        "ieeeAddr": ieee,
        "type": "EndDevice",
        "friendlyName": friendly,
        "definition": {"model": "WSDCGQ11LM", "vendor": "Aqara"},
    }


def test_parse_networkmap_empty_payload() -> None:
    nodes, edges = zigbee.parse_networkmap({"data": {"value": {"nodes": [], "links": []}}})
    assert nodes == []
    assert edges == []


def test_parse_networkmap_modern_shape() -> None:
    payload = {
        "data": {
            "value": {
                "nodes": [_coord_node("0xC"), _router("0xR1"), _end("0xE1")],
                "links": [
                    {"source": {"ieeeAddr": "0xC"}, "target": {"ieeeAddr": "0xR1"}, "lqi": 200},
                    {"source": {"ieeeAddr": "0xR1"}, "target": {"ieeeAddr": "0xE1"}, "lqi": 180},
                ],
            }
        }
    }
    nodes, edges = zigbee.parse_networkmap(payload)
    assert {n["id"] for n in nodes} == {"0xC", "0xR1", "0xE1"}
    by_id = {n["id"]: n for n in nodes}
    assert by_id["0xR1"]["parent_id"] == "0xC"
    assert by_id["0xE1"]["parent_id"] == "0xR1"
    assert by_id["0xR1"]["lqi"] == 200
    # Strict tree → 2 edges (coord→router, router→end), not the 2 mesh links
    assert sorted((e["source"], e["target"]) for e in edges) == [
        ("0xC", "0xR1"),
        ("0xR1", "0xE1"),
    ]


def test_parse_networkmap_legacy_flat_shape() -> None:
    payload = {"data": {"nodes": [_coord_node("0xC")], "links": []}}
    nodes, edges = zigbee.parse_networkmap(payload)
    assert nodes[0]["device_type"] == "Coordinator"
    assert nodes[0]["type"] == "zigbee_coordinator"
    # Coordinator alone gets no parent edge.
    assert edges == []


def test_parse_networkmap_end_device_falls_back_to_coordinator() -> None:
    # End device with no router link → parent_id = coordinator.
    payload = {
        "data": {
            "value": {
                "nodes": [_coord_node("0xC"), _end("0xE1")],
                "links": [{"source": {"ieeeAddr": "0xC"}, "target": {"ieeeAddr": "0xE1"}}],
            }
        }
    }
    nodes, edges = zigbee.parse_networkmap(payload)
    by_id = {n["id"]: n for n in nodes}
    assert by_id["0xE1"]["parent_id"] == "0xC"
    assert edges == [{"source": "0xC", "target": "0xE1"}]


def test_parse_networkmap_skips_invalid_entries() -> None:
    payload = {
        "data": {
            "value": {
                "nodes": [
                    _coord_node("0xC"),
                    {"type": "EndDevice"},  # missing ieeeAddr → skipped
                    "not a dict",  # type: ignore[list-item]
                    _coord_node("0xC"),  # duplicate ieee → skipped
                ],
                "links": [],
            }
        }
    }
    nodes, _ = zigbee.parse_networkmap(payload)
    assert len(nodes) == 1


def test_parse_networkmap_rejects_non_list_nodes() -> None:
    with pytest.raises(ValueError, match="'nodes' is not a list"):
        zigbee.parse_networkmap({"data": {"value": {"nodes": "oops", "links": []}}})


# ─── fetch_networkmap (HA MQTT path) ─────────────────────────────────────────


async def test_fetch_networkmap_raises_when_mqtt_missing(hass: HomeAssistant) -> None:
    # mqtt not in hass.config.components → should raise.
    with pytest.raises(zigbee.ZigbeeMqttNotReadyError):
        await zigbee.fetch_networkmap(hass, "zigbee2mqtt", timeout=0.1)


# ─── Property builders ───────────────────────────────────────────────────────


def test_build_zigbee_properties_includes_only_non_empty() -> None:
    props = zigbee.build_zigbee_properties("0xABCD", "Aqara", None, 200)
    keys = [p["key"] for p in props]
    assert keys == ["IEEE", "Vendor", "LQI"]  # Model omitted (None)
    assert all(p["visible"] is False for p in props)
    assert all(p["icon"] is None for p in props)
    lqi = next(p for p in props if p["key"] == "LQI")
    assert lqi["value"] == "200"  # stringified


def test_build_zigbee_properties_keeps_lqi_zero() -> None:
    # lqi=0 is a real value (None is the only "missing" sentinel).
    props = zigbee.build_zigbee_properties(None, None, None, 0)
    assert props == [{"key": "LQI", "value": "0", "icon": None, "visible": False}]


def test_merge_zigbee_properties_preserves_user_visibility() -> None:
    existing = [
        {"key": "IEEE", "value": "0xABCD", "icon": None, "visible": True},
        {"key": "Custom", "value": "mine", "icon": "star", "visible": True},
    ]
    new = zigbee.build_zigbee_properties("0xABCD", "Aqara", "WSDCGQ11LM", 180)
    merged = zigbee.merge_zigbee_properties(existing, new)
    by_key = {p["key"]: p for p in merged}
    # Existing IEEE keeps user's visible=True, value unchanged.
    assert by_key["IEEE"]["visible"] is True
    # New keys appended hidden.
    assert by_key["Vendor"]["visible"] is False
    assert by_key["Model"]["value"] == "WSDCGQ11LM"
    # Non-zigbee custom prop untouched.
    assert by_key["Custom"] == existing[1]


def test_merge_zigbee_properties_updates_value() -> None:
    existing = [{"key": "LQI", "value": "100", "icon": None, "visible": True}]
    new = zigbee.build_zigbee_properties(None, None, None, 250)
    merged = zigbee.merge_zigbee_properties(existing, new)
    assert merged[0]["value"] == "250"
    assert merged[0]["visible"] is True  # visibility preserved


def test_merge_zigbee_properties_handles_none_existing() -> None:
    new = zigbee.build_zigbee_properties("0xABCD", None, None, None)
    assert zigbee.merge_zigbee_properties(None, new) == new


# ─── Coordinator import path ─────────────────────────────────────────────────


def _mock_entry() -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "zb_entry"
    entry.data = {"scan_ranges": "192.168.1.0/24", "status_interval": 60}
    entry.options = {}
    return entry


@pytest.fixture
async def coordinator(hass: HomeAssistant) -> HomelableCoordinator:
    return HomelableCoordinator(hass, _mock_entry())


async def test_import_zigbee_devices_adds_to_pending(coordinator: HomelableCoordinator) -> None:
    devs = [
        {
            "id": "0xC",
            "ieee_address": "0xC",
            "friendly_name": "Coord",
            "type": "zigbee_coordinator",
            "device_type": "Coordinator",
            "model": "CC2652P",
            "vendor": "TI",
            "lqi": None,
            "parent_id": None,
        }
    ]
    result = await coordinator.import_zigbee_devices(devs)
    assert result == {"added": 1, "skipped": 0, "refreshed": 0}
    pending = await coordinator.list_pending(source="zigbee")
    assert len(pending) == 1
    # Flattened on the wire.
    assert pending[0]["ieee_address"] == "0xC"
    assert pending[0]["friendly_name"] == "Coord"
    assert pending[0]["suggested_type"] == "zigbee_coordinator"
    assert pending[0]["source"] == "zigbee"


async def test_trigger_zigbee_import_records_running_then_done(
    coordinator: HomelableCoordinator,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nodes = [
        {"id": "0xC", "ieee_address": "0xC", "friendly_name": "Coord", "type": "zigbee_coordinator"},
        {"id": "0xR", "ieee_address": "0xR", "friendly_name": "Router", "type": "zigbee_router"},
    ]

    async def fake_fetch(backend: str | None = None) -> tuple[list[dict], list[dict]]:  # noqa: ARG001
        return nodes, []

    monkeypatch.setattr(coordinator, "fetch_zigbee_networkmap", fake_fetch)

    # Returns immediately with a running run for the UI to poll.
    res = await coordinator.trigger_zigbee_import()
    assert res["status"] == "running"
    assert res["devices_found"] == 0
    run_id = res["run_id"]

    await hass.async_block_till_done(wait_background_tasks=True)

    runs = await coordinator.list_runs()
    assert len(runs) == 1
    run = runs[0]
    assert run["id"] == run_id
    assert run["kind"] == "zigbee"
    assert run["status"] == "done"
    assert run["devices_found"] == 2
    assert run["finished_at"]
    assert run["error"] is None
    # Discovered devices actually landed in pending.
    assert len(await coordinator.list_pending(source="zigbee")) == 2


async def test_trigger_zigbee_import_records_error_run(
    coordinator: HomelableCoordinator,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(backend: str | None = None) -> tuple[list[dict], list[dict]]:  # noqa: ARG001
        raise RuntimeError("mqtt not ready")

    monkeypatch.setattr(coordinator, "fetch_zigbee_networkmap", boom)
    res = await coordinator.trigger_zigbee_import()
    await hass.async_block_till_done(wait_background_tasks=True)

    runs = await coordinator.list_runs()
    assert len(runs) == 1
    assert runs[0]["id"] == res["run_id"]
    assert runs[0]["kind"] == "zigbee"
    assert runs[0]["status"] == "error"
    assert runs[0]["error"] == "mqtt not ready"
    assert runs[0]["devices_found"] == 0


async def test_import_zigbee_devices_skips_duplicates(
    coordinator: HomelableCoordinator,
) -> None:
    dev = {
        "id": "0xR1",
        "ieee_address": "0xR1",
        "friendly_name": "Router",
        "type": "zigbee_router",
        "device_type": "Router",
    }
    await coordinator.import_zigbee_devices([dev])
    result = await coordinator.import_zigbee_devices([dev])
    assert result == {"added": 0, "skipped": 1, "refreshed": 0}


async def test_import_zigbee_devices_refreshes_approved_canvas_node(
    coordinator: HomelableCoordinator,
) -> None:
    """Re-import of an approved device refreshes its props, preserving the
    user's visibility choice, and does not re-create a pending row."""
    # Approve a zigbee device onto the canvas first.
    await coordinator.import_zigbee_devices(
        [
            {
                "id": "0xR1",
                "ieee_address": "0xR1",
                "friendly_name": "Router",
                "type": "zigbee_router",
                "device_type": "Router",
                "model": "E11-N1EA",
                "vendor": "Sengled",
                "lqi": 100,
            }
        ]
    )
    pending = await coordinator.list_pending(source="zigbee")
    node = await coordinator.approve_pending(pending[0]["id"])
    assert node is not None
    # User opts to show LQI on the canvas card.
    canvas = await coordinator.get_canvas()
    saved = next(n for n in canvas["nodes"] if n["ieee_address"] == "0xR1")
    for p in saved["properties"]:
        if p["key"] == "LQI":
            p["visible"] = True
    await coordinator.save_canvas(canvas)

    # Re-import with a new LQI value.
    result = await coordinator.import_zigbee_devices(
        [
            {
                "id": "0xR1",
                "ieee_address": "0xR1",
                "friendly_name": "Router",
                "type": "zigbee_router",
                "device_type": "Router",
                "model": "E11-N1EA",
                "vendor": "Sengled",
                "lqi": 250,
            }
        ]
    )
    assert result == {"added": 0, "skipped": 0, "refreshed": 1}
    # No new/duplicate pending row — the device stays as a single "approved"
    # inventory row (Device Inventory keeps approved devices listed and badged).
    inv = await coordinator.list_pending(source="zigbee")
    assert len(inv) == 1
    assert inv[0]["status"] == "approved"
    assert inv[0]["canvas_count"] == 1
    # Props refreshed, visibility preserved.
    canvas = await coordinator.get_canvas()
    refreshed = next(n for n in canvas["nodes"] if n["ieee_address"] == "0xR1")
    lqi = next(p for p in refreshed["properties"] if p["key"] == "LQI")
    assert lqi["value"] == "250"
    assert lqi["visible"] is True


async def test_import_zigbee_devices_refreshes_all_canvases(
    coordinator: HomelableCoordinator,
) -> None:
    """A device placed on more than one canvas has its props refreshed on
    every canvas on re-import — not just one (one Node per design is valid)."""
    await coordinator.import_zigbee_devices(
        [
            {
                "id": "0xR1",
                "ieee_address": "0xR1",
                "friendly_name": "Router",
                "type": "zigbee_router",
                "device_type": "Router",
                "model": "E11-N1EA",
                "vendor": "Sengled",
                "lqi": 100,
            }
        ]
    )
    pending = await coordinator.list_pending(source="zigbee")
    device_id = pending[0]["id"]

    # Approve the same device onto two separate designs.
    default = (await coordinator.list_designs())[0]["id"]
    second = (await coordinator.create_design("Second"))["id"]
    node_a = await coordinator.approve_pending(device_id, {"design_id": default})
    node_b = await coordinator.approve_pending(device_id, {"design_id": second})
    assert node_a is not None and node_b is not None

    # Re-import with a fresh LQI: both canvases must be updated.
    result = await coordinator.import_zigbee_devices(
        [
            {
                "id": "0xR1",
                "ieee_address": "0xR1",
                "friendly_name": "Router",
                "type": "zigbee_router",
                "device_type": "Router",
                "model": "E11-N1EA",
                "vendor": "Sengled",
                "lqi": 250,
            }
        ]
    )
    # One device refreshed (counted per device, not per canvas), no new pending.
    assert result == {"added": 0, "skipped": 0, "refreshed": 1}
    for design_id in (default, second):
        canvas = await coordinator.get_canvas(design_id)
        node = next(n for n in canvas["nodes"] if n["ieee_address"] == "0xR1")
        lqi = next(p for p in node["properties"] if p["key"] == "LQI")
        assert lqi["value"] == "250"


async def test_import_zigbee_devices_stays_listed_after_node_deleted(
    coordinator: HomelableCoordinator,
) -> None:
    """Regression for homelable#167: an approved device is never swallowed.

    With Device Inventory, ``approve_pending`` keeps the row (flipped to
    ``"approved"``) instead of deleting it. So the device stays visible in the
    inventory through approval, canvas-node deletion (canvas_count drops to 0),
    and re-import (skipped as already present) — it can never vanish.
    """
    dev = {
        "id": "0xR1",
        "ieee_address": "0xR1",
        "friendly_name": "Router",
        "type": "zigbee_router",
        "device_type": "Router",
        "model": "CC2530",
        "vendor": "TI",
        "lqi": 220,
    }
    # Import → pending.
    await coordinator.import_zigbee_devices([dev])
    pending = await coordinator.list_pending(source="zigbee")
    # Approve → node on canvas, row kept as "approved" and badged.
    node = await coordinator.approve_pending(pending[0]["id"])
    assert node is not None
    approved = await coordinator.list_pending(source="zigbee")
    assert len(approved) == 1
    assert approved[0]["status"] == "approved"
    assert approved[0]["canvas_count"] == 1
    # User deletes the canvas node (frontend saves the canvas without it).
    canvas = await coordinator.get_canvas()
    canvas["nodes"] = [n for n in canvas["nodes"] if n.get("ieee_address") != "0xR1"]
    await coordinator.save_canvas(canvas)
    # Re-import → device is already present (approved), so it's skipped, not
    # duplicated. It stays in the inventory with canvas_count now 0.
    result = await coordinator.import_zigbee_devices([dev])
    assert result == {"added": 0, "skipped": 1, "refreshed": 0}
    relisted = await coordinator.list_pending(source="zigbee")
    assert {p["ieee_address"] for p in relisted} == {"0xR1"}
    assert relisted[0]["status"] == "approved"
    assert relisted[0]["canvas_count"] == 0


async def test_import_zigbee_devices_keeps_hidden_hidden_on_reimport(
    coordinator: HomelableCoordinator,
) -> None:
    """A user-hidden zigbee device stays hidden on re-import (not revived like #167)."""
    dev = {
        "id": "0xR1",
        "ieee_address": "0xR1",
        "friendly_name": "Router",
        "type": "zigbee_router",
        "device_type": "Router",
    }
    await coordinator.import_zigbee_devices([dev])
    pending = await coordinator.list_pending(source="zigbee")
    assert await coordinator.hide_pending(pending[0]["id"]) is True
    # Re-import must not flip the hidden device back to pending.
    result = await coordinator.import_zigbee_devices([dev])
    assert result == {"added": 0, "skipped": 1, "refreshed": 0}
    assert await coordinator.list_pending(source="zigbee") == []
    hidden = await coordinator.list_pending(status="hidden", source="zigbee")
    assert {p["ieee_address"] for p in hidden} == {"0xR1"}


# ─── WS commands ─────────────────────────────────────────────────────────────


@pytest.fixture
async def setup_ws(hass: HomeAssistant) -> HomelableCoordinator:
    coord = HomelableCoordinator(hass, _mock_entry())
    hass.data.setdefault(DOMAIN, {})[coord.entry.entry_id] = coord
    async_register_websocket_commands(hass)
    return coord


async def test_ws_zigbee_devices_returns_mqtt_not_configured(
    hass: HomeAssistant, hass_ws_client, hass_admin_user, setup_ws  # noqa: ANN001, ARG001
) -> None:
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "homelable/zigbee/devices"})
    msg = await client.receive_json()
    assert msg["success"] is False
    assert msg["error"]["code"] == "mqtt_not_configured"


async def test_ws_zigbee_import_pushes_to_pending(
    hass: HomeAssistant, hass_ws_client, hass_admin_user, setup_ws  # noqa: ANN001, ARG001
) -> None:
    nodes = [
        {
            "id": "0xC",
            "ieee_address": "0xC",
            "friendly_name": "Coord",
            "type": "zigbee_coordinator",
            "device_type": "Coordinator",
        }
    ]
    with patch.object(
        setup_ws, "fetch_zigbee_networkmap", new=AsyncMock(return_value=(nodes, []))
    ):
        client = await hass_ws_client(hass)
        await client.send_json({"id": 1, "type": "homelable/zigbee/import"})
        msg = await client.receive_json()
        assert msg["success"] is True
        # Import runs in the background; WS returns a running run for Scan History.
        assert msg["result"]["status"] == "running"
        assert "run_id" in msg["result"]

        await hass.async_block_till_done(wait_background_tasks=True)

    pending = await setup_ws.list_pending(source="zigbee")
    assert len(pending) == 1
    assert pending[0]["ieee_address"] == "0xC"
    runs = await setup_ws.list_runs()
    assert runs[0]["kind"] == "zigbee"
    assert runs[0]["status"] == "done"
    assert runs[0]["devices_found"] == 1


async def test_ws_zigbee_devices_happy_path_with_mocked_mqtt(
    hass: HomeAssistant, hass_ws_client, hass_admin_user, setup_ws  # noqa: ANN001, ARG001
) -> None:
    # Pretend HA's MQTT integration is loaded.
    hass.config.components.add("mqtt")
    payload = {
        "data": {
            "value": {
                "nodes": [_coord_node("0xC"), _router("0xR1")],
                "links": [
                    {"source": {"ieeeAddr": "0xC"}, "target": {"ieeeAddr": "0xR1"}}
                ],
            }
        }
    }

    async def fake_fetch(hass_, base_topic, timeout=300.0):  # noqa: ARG001
        return zigbee.parse_networkmap(payload)

    with patch.object(zigbee, "fetch_networkmap", new=AsyncMock(side_effect=fake_fetch)):
        client = await hass_ws_client(hass)
        await client.send_json({"id": 1, "type": "homelable/zigbee/devices"})
        msg = await client.receive_json()
    assert msg["success"] is True
    assert {n["id"] for n in msg["result"]["nodes"]} == {"0xC", "0xR1"}
    assert msg["result"]["base_topic"] == "zigbee2mqtt"


async def test_ws_scan_ignore_removes_pending(
    hass: HomeAssistant, hass_ws_client, hass_admin_user, setup_ws  # noqa: ANN001, ARG001
) -> None:
    coord = setup_ws
    await coord.import_zigbee_devices(
        [
            {
                "id": "0xR1",
                "ieee_address": "0xR1",
                "friendly_name": "R",
                "type": "zigbee_router",
                "device_type": "Router",
            }
        ]
    )
    pending = await coord.list_pending(source="zigbee")
    device_id = pending[0]["id"]

    client = await hass_ws_client(hass)
    await client.send_json(
        {"id": 1, "type": "homelable/scan/ignore", "device_id": device_id}
    )
    msg = await client.receive_json()
    assert msg["success"] is True
    assert await coord.list_pending(source="zigbee") == []


async def test_ws_scan_restore_unhides(
    hass: HomeAssistant, hass_ws_client, hass_admin_user, setup_ws  # noqa: ANN001, ARG001
) -> None:
    coord = setup_ws
    await coord.import_zigbee_devices(
        [{"id": "0xR1", "ieee_address": "0xR1", "friendly_name": "R", "type": "zigbee_router", "device_type": "Router"}]
    )
    pending = await coord.list_pending(source="zigbee")
    device_id = pending[0]["id"]
    await coord.hide_pending(device_id)

    client = await hass_ws_client(hass)
    await client.send_json(
        {"id": 1, "type": "homelable/scan/restore", "device_id": device_id}
    )
    msg = await client.receive_json()
    assert msg["success"] is True
    assert len(await coord.list_pending(status="pending", source="zigbee")) == 1
