"""Tests for multi-design canvas support (PR #177 port)."""
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.homelable.const import DOMAIN
from custom_components.homelable.coordinator import HomelableCoordinator
from custom_components.homelable.websocket import async_register_websocket_commands


def _mock_entry() -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "designs_test_entry"
    entry.data = {"scan_ranges": "192.168.1.0/24", "status_interval": 60}
    entry.options = {}
    return entry


@pytest.fixture
def coord(hass):  # noqa: ANN001
    return HomelableCoordinator(hass, _mock_entry())


@pytest.fixture
async def setup_ws(hass: HomeAssistant, hass_storage):  # noqa: ANN001
    coord = HomelableCoordinator(hass, _mock_entry())
    hass.data.setdefault(DOMAIN, {})[coord.entry.entry_id] = coord
    async_register_websocket_commands(hass)
    return coord


# ─── Coordinator: designs lifecycle ──────────────────────────────────────────


async def test_default_design_seeded_on_first_use(coord) -> None:  # noqa: ANN001
    designs = await coord.list_designs()
    assert len(designs) == 1
    assert designs[0]["name"] == "Network Topology"
    assert designs[0]["design_type"] == "network"
    assert designs[0]["icon"] == "network"
    assert "id" in designs[0]


async def test_legacy_single_canvas_migrated_into_default_design(coord) -> None:  # noqa: ANN001
    # Simulate a pre-multi-design install: a bare {nodes,edges,viewport} blob
    # stored under the canvas key, with no designs store yet.
    legacy = {
        "nodes": [{"id": "old-node", "ip": "192.168.1.5"}],
        "edges": [],
        "viewport": {"x": 0, "y": 0, "zoom": 1},
    }
    await coord.canvas_store.async_save(legacy)

    designs = await coord.list_designs()
    assert len(designs) == 1
    canvas = await coord.get_canvas(designs[0]["id"])
    assert canvas["nodes"] == [{"id": "old-node", "ip": "192.168.1.5"}]


async def test_create_design_starts_with_empty_canvas(coord) -> None:  # noqa: ANN001
    design = await coord.create_design("Rack Power", icon="zap")
    assert design["name"] == "Rack Power"
    assert design["icon"] == "zap"
    designs = await coord.list_designs()
    assert len(designs) == 2
    assert (await coord.get_canvas(design["id"]))["nodes"] == []


async def test_canvases_are_isolated_per_design(coord) -> None:  # noqa: ANN001
    default = (await coord.list_designs())[0]
    second = await coord.create_design("Second")

    await coord.save_canvas(
        {"nodes": [{"id": "a"}], "edges": [], "viewport": {}}, default["id"]
    )
    await coord.save_canvas(
        {"nodes": [{"id": "b"}], "edges": [], "viewport": {}}, second["id"]
    )

    assert (await coord.get_canvas(default["id"]))["nodes"] == [{"id": "a"}]
    assert (await coord.get_canvas(second["id"]))["nodes"] == [{"id": "b"}]


async def test_get_canvas_without_id_uses_default_design(coord) -> None:  # noqa: ANN001
    default = (await coord.list_designs())[0]
    await coord.save_canvas(
        {"nodes": [{"id": "z"}], "edges": [], "viewport": {}}, default["id"]
    )
    assert (await coord.get_canvas())["nodes"] == [{"id": "z"}]


async def test_update_design_renames_and_reicons(coord) -> None:  # noqa: ANN001
    design = (await coord.list_designs())[0]
    updated = await coord.update_design(design["id"], name="Renamed", icon="home")
    assert updated["name"] == "Renamed"
    assert updated["icon"] == "home"
    assert (await coord.list_designs())[0]["name"] == "Renamed"


async def test_update_unknown_design_returns_none(coord) -> None:  # noqa: ANN001
    assert await coord.update_design("nope", name="x") is None


async def test_delete_design_removes_its_canvas(coord) -> None:  # noqa: ANN001
    default = (await coord.list_designs())[0]
    second = await coord.create_design("Second")
    await coord.save_canvas(
        {"nodes": [{"id": "b"}], "edges": [], "viewport": {}}, second["id"]
    )

    assert await coord.delete_design(second["id"]) == "ok"
    designs = await coord.list_designs()
    assert [d["id"] for d in designs] == [default["id"]]
    assert second["id"] not in coord._canvases


async def test_cannot_delete_only_design(coord) -> None:  # noqa: ANN001
    only = (await coord.list_designs())[0]
    assert await coord.delete_design(only["id"]) == "last"
    assert len(await coord.list_designs()) == 1


async def test_delete_unknown_design(coord) -> None:  # noqa: ANN001
    assert await coord.delete_design("missing") == "not_found"


async def test_approve_targets_requested_design(coord) -> None:  # noqa: ANN001
    second = await coord.create_design("Second")
    # Seed a pending device.
    pending = await coord._get_pending()
    pending["devices"].append(
        {
            "id": "pd-1",
            "ip": "192.168.1.77",
            "mac": None,
            "hostname": "host.lan",
            "status": "pending",
        }
    )
    await coord._save_pending()

    node = await coord.approve_pending("pd-1", {"design_id": second["id"]})
    assert node is not None
    # The node lands on the requested design, not the default one.
    default = (await coord.list_designs())[0]
    assert node["id"] in [n["id"] for n in (await coord.get_canvas(second["id"]))["nodes"]]
    assert (await coord.get_canvas(default["id"]))["nodes"] == []
    # And design_id never leaks onto the node payload.
    assert "design_id" not in node


# ─── WebSocket: designs commands ─────────────────────────────────────────────


async def test_ws_designs_list(
    hass: HomeAssistant, hass_ws_client, setup_ws  # noqa: ANN001
) -> None:
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "homelable/designs/list"})
    msg = await client.receive_json()
    assert msg["success"] is True
    assert len(msg["result"]["designs"]) == 1


async def test_ws_designs_create_update_delete(
    hass: HomeAssistant, hass_ws_client, setup_ws  # noqa: ANN001
) -> None:
    client = await hass_ws_client(hass)

    await client.send_json(
        {"id": 1, "type": "homelable/designs/create", "name": "Power", "icon": "zap"}
    )
    created = (await client.receive_json())["result"]
    assert created["name"] == "Power"
    design_id = created["id"]

    await client.send_json(
        {"id": 2, "type": "homelable/designs/update", "design_id": design_id, "name": "Power 2"}
    )
    updated = (await client.receive_json())["result"]
    assert updated["name"] == "Power 2"

    await client.send_json(
        {"id": 3, "type": "homelable/designs/delete", "design_id": design_id}
    )
    deleted = await client.receive_json()
    assert deleted["success"] is True
    assert len(await setup_ws.list_designs()) == 1


async def test_ws_delete_only_design_errors(
    hass: HomeAssistant, hass_ws_client, setup_ws  # noqa: ANN001
) -> None:
    only = (await setup_ws.list_designs())[0]
    client = await hass_ws_client(hass)
    await client.send_json(
        {"id": 1, "type": "homelable/designs/delete", "design_id": only["id"]}
    )
    msg = await client.receive_json()
    assert msg["success"] is False
    assert msg["error"]["code"] == "last_design"


async def test_ws_save_and_get_canvas_with_design_id(
    hass: HomeAssistant, hass_ws_client, setup_ws  # noqa: ANN001
) -> None:
    design = await setup_ws.create_design("Second")
    client = await hass_ws_client(hass)

    canvas = {"nodes": [{"id": "n9"}], "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}}
    await client.send_json(
        {"id": 1, "type": "homelable/save_canvas", "canvas": canvas, "design_id": design["id"]}
    )
    assert (await client.receive_json())["success"] is True

    await client.send_json(
        {"id": 2, "type": "homelable/get_canvas", "design_id": design["id"]}
    )
    msg = await client.receive_json()
    assert msg["result"]["nodes"] == [{"id": "n9"}]
