"""Tests for the Z-Wave JS UI importer (parser + coordinator import + WS)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.homelable import zwave
from custom_components.homelable.const import DOMAIN
from custom_components.homelable.coordinator import HomelableCoordinator
from custom_components.homelable.websocket import async_register_websocket_commands

# ─── Parser ──────────────────────────────────────────────────────────────────


def _controller(node_id: int = 1, home_id: str = "0xdeadbeef") -> dict:
    return {
        "id": node_id,
        "homeId": home_id,
        "isControllerNode": True,
        "name": "Controller",
        "manufacturer": "Aeotec",
        "productLabel": "Z-Stick",
        "neighbors": [],
    }


def _router(node_id: int, home_id: str = "0xdeadbeef", neighbors=None) -> dict:
    return {
        "id": node_id,
        "homeId": home_id,
        "isRouting": True,
        "name": f"Router {node_id}",
        "manufacturer": "Fibaro",
        "productLabel": "Wall Plug",
        "neighbors": neighbors or [],
    }


def _end(node_id: int, home_id: str = "0xdeadbeef", neighbors=None) -> dict:
    return {
        "id": node_id,
        "homeId": home_id,
        "name": f"Sensor {node_id}",
        "manufacturer": "Aeotec",
        "productLabel": "MultiSensor",
        "neighbors": neighbors or [],
    }


def test_parse_zwave_nodes_empty() -> None:
    nodes, edges = zwave.parse_zwave_nodes({"success": True, "result": []})
    assert nodes == []
    assert edges == []


def test_parse_zwave_nodes_type_mapping() -> None:
    payload = {
        "success": True,
        "result": [_controller(1), _router(2), _end(3)],
    }
    nodes, _ = zwave.parse_zwave_nodes(payload)
    by_type = {n["type"] for n in nodes}
    assert by_type == {"zwave_coordinator", "zwave_router", "zwave_enddevice"}
    controller = next(n for n in nodes if n["type"] == "zwave_coordinator")
    assert controller["device_type"] == "Controller"
    assert controller["vendor"] == "Aeotec"
    assert controller["model"] == "Z-Stick"
    # Synthetic identity: zwave-<homeId>-<nodeId>.
    assert controller["ieee_address"] == "zwave-0xdeadbeef-1"
    # Z-Wave has no LQI.
    assert controller["lqi"] is None


def test_parse_zwave_nodes_builds_tree_from_neighbors() -> None:
    payload = {
        "success": True,
        "result": [
            _controller(1),
            _router(2, neighbors=[1, 3]),
            _end(3, neighbors=[2]),
        ],
    }
    nodes, edges = zwave.parse_zwave_nodes(payload)
    by_id = {n["id"]: n for n in nodes}
    router = by_id["zwave-0xdeadbeef-2"]
    end = by_id["zwave-0xdeadbeef-3"]
    # Router hangs off the controller; end device hangs off the router.
    assert router["parent_id"] == "zwave-0xdeadbeef-1"
    assert end["parent_id"] == "zwave-0xdeadbeef-2"
    assert sorted((e["source"], e["target"]) for e in edges) == [
        ("zwave-0xdeadbeef-1", "zwave-0xdeadbeef-2"),
        ("zwave-0xdeadbeef-2", "zwave-0xdeadbeef-3"),
    ]
    # Transient helper keys are stripped.
    assert "neighbors" not in router
    assert "node_id" not in router


def test_parse_zwave_nodes_end_device_falls_back_to_controller() -> None:
    # End device with no routing neighbor → parent_id = controller.
    payload = {"success": True, "result": [_controller(1), _end(5, neighbors=[1])]}
    nodes, edges = zwave.parse_zwave_nodes(payload)
    by_id = {n["id"]: n for n in nodes}
    assert by_id["zwave-0xdeadbeef-5"]["parent_id"] == "zwave-0xdeadbeef-1"
    assert edges == [
        {"source": "zwave-0xdeadbeef-1", "target": "zwave-0xdeadbeef-5"}
    ]


def test_parse_zwave_nodes_skips_invalid_entries() -> None:
    payload = {
        "success": True,
        "result": [
            _controller(1),
            {"name": "no id"},  # missing id → skipped
            "not a dict",
            _controller(1),  # duplicate identity → skipped
        ],
    }
    nodes, _ = zwave.parse_zwave_nodes(payload)
    assert len(nodes) == 1


def test_parse_zwave_nodes_rejects_failure() -> None:
    with pytest.raises(ValueError, match="reported failure"):
        zwave.parse_zwave_nodes({"success": False})


def test_parse_zwave_nodes_rejects_non_list_result() -> None:
    with pytest.raises(ValueError, match="'result' is not a list"):
        zwave.parse_zwave_nodes({"success": True, "result": "oops"})


def test_resolve_home_id_prefers_controller() -> None:
    raw = [_end(2, home_id="0xother"), _controller(1, home_id="0xctrl")]
    assert zwave._resolve_home_id(raw) == "0xctrl"


# ─── fetch_zwave_network (HA MQTT path) ──────────────────────────────────────


async def test_fetch_zwave_network_raises_when_mqtt_missing(hass: HomeAssistant) -> None:
    with pytest.raises(zwave.ZwaveMqttNotReadyError):
        await zwave.fetch_zwave_network(hass, "zwave", "zwavejs2mqtt", timeout=0.1)


# ─── Property builders ───────────────────────────────────────────────────────


def test_build_zwave_properties_includes_only_non_empty() -> None:
    props = zwave.build_zwave_properties("zwave-0x1-2", "Fibaro", None)
    keys = [p["key"] for p in props]
    assert keys == ["Z-Wave ID", "Vendor"]  # Model omitted (None)
    assert all(p["visible"] is False for p in props)
    assert all(p["icon"] is None for p in props)


def test_build_zwave_properties_has_no_lqi() -> None:
    props = zwave.build_zwave_properties("zwave-0x1-2", "Fibaro", "Wall Plug")
    assert all(p["key"] != "LQI" for p in props)


def test_merge_zwave_properties_preserves_user_visibility() -> None:
    existing = [
        {"key": "Z-Wave ID", "value": "zwave-0x1-2", "icon": None, "visible": True},
        {"key": "Custom", "value": "mine", "icon": "star", "visible": True},
    ]
    new = zwave.build_zwave_properties("zwave-0x1-2", "Fibaro", "Wall Plug")
    merged = zwave.merge_zwave_properties(existing, new)
    by_key = {p["key"]: p for p in merged}
    assert by_key["Z-Wave ID"]["visible"] is True
    assert by_key["Vendor"]["visible"] is False
    assert by_key["Model"]["value"] == "Wall Plug"
    assert by_key["Custom"] == existing[1]


# ─── Coordinator import path ─────────────────────────────────────────────────


def _mock_entry() -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "zw_entry"
    entry.data = {"scan_ranges": "192.168.1.0/24", "status_interval": 60}
    entry.options = {}
    return entry


@pytest.fixture
async def coordinator(hass: HomeAssistant) -> HomelableCoordinator:
    return HomelableCoordinator(hass, _mock_entry())


def _dev(node_id: int = 1, ntype: str = "zwave_coordinator") -> dict:
    return {
        "id": f"zwave-0x1-{node_id}",
        "ieee_address": f"zwave-0x1-{node_id}",
        "friendly_name": f"Node {node_id}",
        "type": ntype,
        "device_type": "Controller",
        "model": "Z-Stick",
        "vendor": "Aeotec",
        "lqi": None,
        "parent_id": None,
    }


async def test_import_zwave_devices_adds_to_pending(
    coordinator: HomelableCoordinator,
) -> None:
    result = await coordinator.import_zwave_devices([_dev()])
    assert result == {"added": 1, "skipped": 0, "refreshed": 0}
    pending = await coordinator.list_pending(source="zwave")
    assert len(pending) == 1
    assert pending[0]["ieee_address"] == "zwave-0x1-1"
    assert pending[0]["suggested_type"] == "zwave_coordinator"
    assert pending[0]["source"] == "zwave"
    assert pending[0]["discovery_source"] == "zwavejs2mqtt"


async def test_import_zwave_devices_skips_duplicates(
    coordinator: HomelableCoordinator,
) -> None:
    dev = _dev(2, "zwave_router")
    await coordinator.import_zwave_devices([dev])
    result = await coordinator.import_zwave_devices([dev])
    assert result == {"added": 0, "skipped": 1, "refreshed": 0}


async def test_zwave_import_and_zigbee_import_are_isolated(
    coordinator: HomelableCoordinator,
) -> None:
    """A same-id Zigbee row must not shadow a Z-Wave dedup and vice versa."""
    await coordinator.import_zwave_devices([_dev(1, "zwave_coordinator")])
    await coordinator.import_zigbee_devices(
        [
            {
                "id": "0xZ",
                "ieee_address": "0xZ",
                "friendly_name": "Zig",
                "type": "zigbee_router",
                "device_type": "Router",
            }
        ]
    )
    assert len(await coordinator.list_pending(source="zwave")) == 1
    assert len(await coordinator.list_pending(source="zigbee")) == 1


async def test_approve_zwave_lands_online_no_check_no_lqi(
    coordinator: HomelableCoordinator,
) -> None:
    """Approving a Z-Wave device: online, check_method none, Z-Wave props (no LQI)."""
    await coordinator.import_zwave_devices([_dev(3, "zwave_router")])
    pending = await coordinator.list_pending(source="zwave")
    node = await coordinator.approve_pending(pending[0]["id"])
    assert node is not None
    assert node["status"] == "online"
    assert node["check_method"] == "none"
    keys = [p["key"] for p in node["properties"]]
    assert "Z-Wave ID" in keys
    assert "LQI" not in keys


async def test_trigger_zwave_import_records_running_then_done(
    coordinator: HomelableCoordinator,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    nodes = [_dev(1, "zwave_coordinator"), _dev(2, "zwave_router")]

    async def fake_fetch() -> tuple[list[dict], list[dict]]:
        return nodes, []

    monkeypatch.setattr(coordinator, "fetch_zwave_network", fake_fetch)

    res = await coordinator.trigger_zwave_import()
    assert res["status"] == "running"
    run_id = res["run_id"]

    await hass.async_block_till_done(wait_background_tasks=True)

    runs = await coordinator.list_runs()
    assert len(runs) == 1
    run = runs[0]
    assert run["id"] == run_id
    assert run["kind"] == "zwave"
    assert run["status"] == "done"
    assert run["devices_found"] == 2
    assert len(await coordinator.list_pending(source="zwave")) == 2


async def test_trigger_zwave_import_records_error_run(
    coordinator: HomelableCoordinator,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom() -> tuple[list[dict], list[dict]]:
        raise RuntimeError("mqtt not ready")

    monkeypatch.setattr(coordinator, "fetch_zwave_network", boom)
    res = await coordinator.trigger_zwave_import()
    await hass.async_block_till_done(wait_background_tasks=True)

    runs = await coordinator.list_runs()
    assert runs[0]["id"] == res["run_id"]
    assert runs[0]["kind"] == "zwave"
    assert runs[0]["status"] == "error"
    assert runs[0]["error"] == "mqtt not ready"


# ─── WS commands ─────────────────────────────────────────────────────────────


@pytest.fixture
async def setup_ws(hass: HomeAssistant) -> HomelableCoordinator:
    coord = HomelableCoordinator(hass, _mock_entry())
    hass.data.setdefault(DOMAIN, {})[coord.entry.entry_id] = coord
    async_register_websocket_commands(hass)
    return coord


async def test_ws_zwave_devices_returns_mqtt_not_configured(
    hass: HomeAssistant, hass_ws_client, hass_admin_user, setup_ws  # noqa: ANN001, ARG001
) -> None:
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "homelable/zwave/devices"})
    msg = await client.receive_json()
    assert msg["success"] is False
    assert msg["error"]["code"] == "mqtt_not_configured"


async def test_ws_zwave_import_pushes_to_pending(
    hass: HomeAssistant, hass_ws_client, hass_admin_user, setup_ws  # noqa: ANN001, ARG001
) -> None:
    nodes = [_dev(1, "zwave_coordinator")]
    with patch.object(
        setup_ws, "fetch_zwave_network", new=AsyncMock(return_value=(nodes, []))
    ):
        client = await hass_ws_client(hass)
        await client.send_json({"id": 1, "type": "homelable/zwave/import"})
        msg = await client.receive_json()
        assert msg["success"] is True
        assert msg["result"]["status"] == "running"
        assert "run_id" in msg["result"]

        await hass.async_block_till_done(wait_background_tasks=True)

    pending = await setup_ws.list_pending(source="zwave")
    assert len(pending) == 1
    assert pending[0]["ieee_address"] == "zwave-0x1-1"
    runs = await setup_ws.list_runs()
    assert runs[0]["kind"] == "zwave"
    assert runs[0]["status"] == "done"


async def test_ws_zwave_devices_happy_path_with_mocked_mqtt(
    hass: HomeAssistant, hass_ws_client, hass_admin_user, setup_ws  # noqa: ANN001, ARG001
) -> None:
    hass.config.components.add("mqtt")
    payload = {
        "success": True,
        "result": [_controller(1), _router(2, neighbors=[1])],
    }

    async def fake_fetch(hass_, prefix, gateway, timeout=300.0):  # noqa: ARG001
        return zwave.parse_zwave_nodes(payload)

    with patch.object(zwave, "fetch_zwave_network", new=AsyncMock(side_effect=fake_fetch)):
        client = await hass_ws_client(hass)
        await client.send_json({"id": 1, "type": "homelable/zwave/devices"})
        msg = await client.receive_json()
    assert msg["success"] is True
    assert {n["type"] for n in msg["result"]["nodes"]} == {
        "zwave_coordinator",
        "zwave_router",
    }
    assert msg["result"]["prefix"] == "zwave"
    assert msg["result"]["gateway"] == "zwavejs2mqtt"
