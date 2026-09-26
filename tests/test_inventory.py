"""Device Inventory as the source of truth — coordinator + WS wiring.

Port of homelable #339: the inventory row owns a device's facts, a canvas node
only draws it (``device_id``). Migration hardening from #352 / #354.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.homelable.const import (
    DOMAIN,
    SERVICE_STATUS_SIGNAL,
    STORAGE_KEY_CANVAS,
    STORAGE_KEY_DESIGNS,
    STORAGE_KEY_PENDING,
)
from custom_components.homelable.coordinator import HomelableCoordinator
from custom_components.homelable.websocket import async_register_websocket_commands


def _mock_entry() -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {"scan_ranges": "192.168.1.0/24", "status_interval": 60}
    entry.options = {}
    return entry


@pytest.fixture
def coord(hass: HomeAssistant) -> HomelableCoordinator:
    return HomelableCoordinator(hass, _mock_entry())


@pytest.fixture
async def setup_ws(hass: HomeAssistant) -> HomelableCoordinator:
    coord = HomelableCoordinator(hass, _mock_entry())
    hass.data.setdefault(DOMAIN, {})[coord.entry.entry_id] = coord
    async_register_websocket_commands(hass)
    return coord


def _seed_legacy(hass_storage, designs: dict[str, list[dict]], rows: list[dict] | None = None) -> None:  # noqa: ANN001
    """Stores as a pre-split install left them: facts on every node."""
    hass_storage[STORAGE_KEY_DESIGNS] = {
        "version": 1, "key": STORAGE_KEY_DESIGNS,
        "data": {"designs": [
            {"id": did, "name": did, "design_type": "network", "icon": "network"}
            for did in designs
        ]},
    }
    hass_storage[STORAGE_KEY_CANVAS] = {
        "version": 1, "key": STORAGE_KEY_CANVAS,
        "data": {"canvases": {
            did: {"nodes": nodes, "edges": [], "viewport": {}} for did, nodes in designs.items()
        }},
    }
    hass_storage[STORAGE_KEY_PENDING] = {
        "version": 1, "key": STORAGE_KEY_PENDING, "data": {"devices": rows or []},
    }


async def _add_rows(coord: HomelableCoordinator, *rows: dict) -> None:
    pending = await coord._get_pending()
    pending["devices"].extend({"status": "approved", **r} for r in rows)
    await coord._save_pending()


# ─── Migration on load ───────────────────────────────────────────────────────


async def test_load_moves_node_facts_onto_one_row(hass, hass_storage) -> None:  # noqa: ANN001
    _seed_legacy(hass_storage, {
        "net": [{"id": "a", "type": "nas", "label": "NAS", "ip": "10.0.0.5",
                 "services": [{"port": 5000, "protocol": "tcp", "service_name": "dsm"}],
                 "notes": "attic", "pos_x": 10, "updated_at": "2026-01-01T00:00:00Z"}],
        "lab": [{"id": "b", "type": "nas", "label": "NAS", "ip": "10.0.0.5",
                 "notes": "rack 2", "updated_at": "2026-02-01T00:00:00Z"}],
        "zones": [{"id": "g", "type": "groupRect", "label": "Zone"}],
    })
    coord = HomelableCoordinator(hass, _mock_entry())
    inventory = await coord.list_pending()

    assert len(inventory) == 1
    row = inventory[0]
    assert row["canvas_count"] == 2
    assert row["notes"] == "rack 2"  # most recently edited canvas wins
    assert row["discovery_source"] == "canvas"
    # The stored nodes only link it now; the panel still reads the facts.
    stored = coord._canvases["net"]["nodes"][0]
    assert stored["device_id"] == row["id"]
    assert "ip" not in stored and "services" not in stored
    node = (await coord.get_canvas("net"))["nodes"][0]
    assert node["ip"] == "10.0.0.5"
    assert node["services"][0]["service_name"] == "dsm"
    assert node["pos_x"] == 10
    # Furniture is not a device.
    assert coord._canvases["zones"]["nodes"][0] == {"id": "g", "type": "groupRect", "label": "Zone"}
    # Both stores were rewritten in the new shape.
    assert hass_storage[STORAGE_KEY_PENDING]["data"]["devices"][0]["id"] == row["id"]
    assert "ip" not in hass_storage[STORAGE_KEY_CANVAS]["data"]["canvases"]["lab"]["nodes"][0]


async def test_load_links_a_node_to_its_scanned_row(hass, hass_storage) -> None:  # noqa: ANN001
    _seed_legacy(
        hass_storage,
        {"net": [{"id": "a", "type": "server", "label": "Box", "ip": "10.0.0.5"}]},
        [{"id": "pd-scan", "ip": "10.0.0.5", "status": "approved", "hostname": "box.lan"}],
    )
    coord = HomelableCoordinator(hass, _mock_entry())
    inventory = await coord.list_pending()
    assert [d["id"] for d in inventory] == ["pd-scan"]
    assert inventory[0]["label"] == "Box"
    assert inventory[0]["hostname"] == "box.lan"


async def test_migration_is_a_no_op_on_the_next_start(hass, hass_storage) -> None:  # noqa: ANN001
    _seed_legacy(hass_storage, {"net": [{"id": "a", "type": "server", "ip": "10.0.0.5"}]})
    await HomelableCoordinator(hass, _mock_entry()).list_pending()
    snapshot = (
        hass_storage[STORAGE_KEY_CANVAS]["data"],
        hass_storage[STORAGE_KEY_PENDING]["data"],
    )

    again = HomelableCoordinator(hass, _mock_entry())
    with (
        patch.object(again.canvas_store, "async_save", AsyncMock()) as canvas_write,
        patch.object(again.pending_store, "async_save", AsyncMock()) as rows_write,
    ):
        await again.list_pending()
    assert canvas_write.call_count == 0
    assert rows_write.call_count == 0
    assert (
        hass_storage[STORAGE_KEY_CANVAS]["data"],
        hass_storage[STORAGE_KEY_PENDING]["data"],
    ) == snapshot


# ─── Canvas save write-through ───────────────────────────────────────────────


async def test_an_edit_on_one_canvas_shows_on_every_canvas(coord) -> None:  # noqa: ANN001
    await _add_rows(coord, {"id": "pd-1", "ip": "10.0.0.5", "hostname": "nas"})
    default = (await coord.list_designs())[0]["id"]
    other = (await coord.create_design("Lab"))["id"]
    await coord.approve_pending("pd-1", {"design_id": default})
    await coord.approve_pending("pd-1", {"design_id": other})

    canvas = await coord.get_canvas(default)
    canvas["nodes"][0]["notes"] = "on the shelf"
    canvas["nodes"][0]["changed_facts"] = ["notes"]
    await coord.save_canvas(canvas, default)

    assert (await coord.get_canvas(other))["nodes"][0]["notes"] == "on the shelf"
    assert "changed_facts" not in coord._canvases[default]["nodes"][0]


async def test_moving_a_node_does_not_revert_an_inventory_edit(coord) -> None:  # noqa: ANN001
    await _add_rows(coord, {"id": "pd-1", "ip": "10.0.0.5", "hostname": "nas"})
    await coord.approve_pending("pd-1")
    loaded = await coord.get_canvas()  # the panel's copy: ip .5

    await coord.update_pending("pd-1", {"ip": "10.0.0.6"})

    loaded["nodes"][0]["pos_x"] = 99
    loaded["nodes"][0]["changed_facts"] = []
    await coord.save_canvas(loaded)
    assert (await coord.get_canvas())["nodes"][0]["ip"] == "10.0.0.6"


async def test_a_save_without_the_link_keeps_the_one_on_record(coord) -> None:  # noqa: ANN001
    """A node the panel added locally (no device_id yet) stays on its row."""
    await _add_rows(coord, {"id": "pd-1", "hostname": "no-ip-box"})
    node = await coord.approve_pending("pd-1")
    await coord.save_canvas({"nodes": [{"id": node["id"], "type": "generic", "label": "x"}], "edges": []})
    assert coord._canvases[(await coord.list_designs())[0]["id"]]["nodes"][0]["device_id"] == "pd-1"
    assert len((await coord._get_pending())["devices"]) == 1


async def test_a_new_node_drawn_on_the_canvas_gets_a_row(coord) -> None:  # noqa: ANN001
    await coord.save_canvas(
        {"nodes": [{"id": "n1", "type": "server", "label": "Box", "ip": "10.0.0.9"}], "edges": []}
    )
    inventory = await coord.list_pending()
    assert len(inventory) == 1
    assert inventory[0]["label"] == "Box"
    assert inventory[0]["canvas_count"] == 1


# ─── Approve ─────────────────────────────────────────────────────────────────


async def test_approve_writes_the_device_onto_the_row(coord) -> None:  # noqa: ANN001
    await _add_rows(coord, {"id": "pd-1", "ip": "10.0.0.5", "mac": "aa:bb:cc:dd:ee:ff",
                            "status": "pending", "suggested_type": "server"})
    node = await coord.approve_pending("pd-1", {"label": "Box"})

    row = (await coord._get_pending())["devices"][0]
    assert row["status"] == "approved"
    assert row["label"] == "Box"
    assert row["check_method"] == "ping"
    assert {"key": "MAC", "value": "aa:bb:cc:dd:ee:ff", "icon": None, "visible": False} in row["properties"]
    stored = coord._canvases[(await coord.list_designs())[0]["id"]]["nodes"][0]
    assert stored["device_id"] == "pd-1"
    assert "ip" not in stored
    # The returned node is hydrated, as the panel reads it.
    assert node["ip"] == "10.0.0.5"
    assert node["device_id"] == "pd-1"


async def test_approve_prefers_the_curated_type(coord) -> None:  # noqa: ANN001
    await _add_rows(coord, {"id": "pd-1", "ip": "10.0.0.5", "status": "pending",
                            "suggested_type": "generic", "type": "nas"})
    node = await coord.approve_pending("pd-1")
    assert node["type"] == "nas"


async def test_bulk_approve_skips_a_device_drawn_under_another_address(coord) -> None:  # noqa: ANN001
    """The row was edited after it was placed, so no address matches any more —
    the device link still says it is on this canvas."""
    await _add_rows(coord, {"id": "pd-1", "ip": "10.0.0.5", "status": "pending"})
    first = await coord.approve_pending("pd-1")
    await coord.update_pending("pd-1", {"ip": "10.0.0.8"})
    (await coord._get_pending())["devices"][0]["ip"] = "10.0.0.8"

    res = await coord.approve_batch(["pd-1"])
    assert res["approved"] == 0
    assert res["skipped_devices"][0]["existing_node_id"] == first["id"]


# ─── Status: one check per device ────────────────────────────────────────────


async def test_a_device_on_two_canvases_is_checked_once(coord) -> None:  # noqa: ANN001
    await _add_rows(coord, {"id": "pd-1", "ip": "10.0.0.5", "status": "pending"})
    default = (await coord.list_designs())[0]["id"]
    other = (await coord.create_design("Lab"))["id"]
    a = await coord.approve_pending("pd-1", {"design_id": default})
    b = await coord.approve_pending("pd-1", {"design_id": other, "id": "second"})

    check = AsyncMock(return_value={"status": "online", "response_time_ms": 3})
    with patch("custom_components.homelable.coordinator.status_checker.check_node", check):
        result = await coord._async_update_data()

    assert check.call_count == 1
    assert result[a["id"]]["status"] == "online"
    assert result[b["id"]]["status"] == "online"
    row = (await coord._get_pending())["devices"][0]
    assert row["status_live"] == "online"
    assert row["response_time_ms"] == 3
    assert row["last_seen"] is not None


async def test_undrawn_devices_checked_only_when_asked(coord) -> None:  # noqa: ANN001
    await _add_rows(
        coord,
        {"id": "scanned", "ip": "10.0.0.1", "status": "pending"},
        {"id": "watched", "ip": "10.0.0.2", "check_method": "ping"},
        {"id": "hidden", "ip": "10.0.0.3", "check_method": "ping", "status": "hidden"},
    )
    check = AsyncMock(return_value={"status": "offline", "response_time_ms": None})
    with patch("custom_components.homelable.coordinator.status_checker.check_node", check):
        await coord._async_update_data()
    assert [c.args[2] for c in check.call_args_list] == ["10.0.0.2"]


async def test_a_drawn_device_is_checked_even_when_hidden(coord) -> None:  # noqa: ANN001
    await _add_rows(coord, {"id": "pd-1", "ip": "10.0.0.5", "status": "pending"})
    node = await coord.approve_pending("pd-1")
    await coord.hide_pending("pd-1")
    check = AsyncMock(return_value={"status": "online", "response_time_ms": 1})
    with patch("custom_components.homelable.coordinator.status_checker.check_node", check):
        result = await coord._async_update_data()
    assert result[node["id"]]["status"] == "online"


async def test_service_checks_fan_out_to_every_node(hass, coord) -> None:  # noqa: ANN001
    from homeassistant.helpers.dispatcher import async_dispatcher_connect

    await _add_rows(coord, {"id": "pd-1", "ip": "10.0.0.5", "status": "pending",
                            "services": [{"port": 80, "protocol": "tcp", "service_name": "http"}]})
    default = (await coord.list_designs())[0]["id"]
    other = (await coord.create_design("Lab"))["id"]
    await coord.approve_pending("pd-1", {"design_id": default, "id": "n1"})
    await coord.approve_pending("pd-1", {"design_id": other, "id": "n2"})

    received: list[dict] = []
    unsub = async_dispatcher_connect(hass, SERVICE_STATUS_SIGNAL, received.append)
    check = AsyncMock(return_value=[{"port": 80, "protocol": "tcp", "status": "online"}])
    with patch("custom_components.homelable.coordinator.status_checker.check_services", check):
        await coord._run_service_checks()
        await hass.async_block_till_done()
    unsub()
    assert check.call_count == 1
    assert sorted(m["node_id"] for m in received) == ["n1", "n2"]


# ─── Scan / imports land on the row ──────────────────────────────────────────


async def test_rescan_keeps_curated_services_and_stamps_the_row(coord) -> None:  # noqa: ANN001
    await _add_rows(coord, {
        "id": "pd-1", "ip": "192.168.1.5",
        "services": [{"port": 3000, "protocol": "tcp", "service_name": "Grafana"}],
    })
    scanned = {
        "ip": "192.168.1.5", "mac": None, "hostname": None, "os": None, "open_ports": [],
        "services": [{"port": 3000, "protocol": "tcp", "service_name": "http"}],
        "suggested_type": None, "discovery_source": "arp",
    }
    with patch(
        "custom_components.homelable.coordinator.scanner.run_scan",
        AsyncMock(return_value=[scanned]),
    ):
        await coord.trigger_scan()
        await coord.hass.async_block_till_done(wait_background_tasks=True)

    row = (await coord._get_pending())["devices"][0]
    assert row["services"] == [{"port": 3000, "protocol": "tcp", "service_name": "Grafana"}]
    assert row["last_scan"] is not None


async def test_mesh_reimport_refreshes_the_drawn_row_once(coord) -> None:  # noqa: ANN001
    dev = {"id": "0xR1", "ieee_address": "0xR1", "friendly_name": "Router",
           "type": "zigbee_router", "vendor": "TI", "model": "CC2530", "lqi": 200}
    await coord.import_zigbee_devices([dev])
    row_id = (await coord.list_pending(source="zigbee"))[0]["id"]
    default = (await coord.list_designs())[0]["id"]
    other = (await coord.create_design("Lab"))["id"]
    await coord.approve_pending(row_id, {"design_id": default})
    await coord.approve_pending(row_id, {"design_id": other, "force": True, "id": "second"})

    result = await coord.import_zigbee_devices([{**dev, "lqi": 42}])
    assert result == {"added": 0, "skipped": 0, "refreshed": 1}
    for design in (default, other):
        props = {p["key"]: p["value"] for p in (await coord.get_canvas(design))["nodes"][0]["properties"]}
        assert str(props["LQI"]) == "42"


async def test_proxmox_import_merges_into_the_drawn_row(coord) -> None:  # noqa: ANN001
    await _add_rows(coord, {"id": "pd-1", "ip": "10.0.0.20", "status": "pending"})
    await coord.approve_pending("pd-1")
    pve = [{"ieee_address": "pve-node-a", "ip": "10.0.0.20", "type": "proxmox",
            "label": "pve-a", "hostname": "pve-a"}]
    with patch(
        "custom_components.homelable.coordinator.proxmox.build_proxmox_properties",
        return_value=[{"key": "VMID", "value": "100", "icon": None, "visible": False}],
    ):
        res = await coord.import_proxmox_pending(pve, [], [("pve-node-a", "pve-node-b")])

    assert res["created"] == 0
    rows = (await coord._get_pending())["devices"]
    assert len(rows) == 1
    assert rows[0]["status"] == "approved"
    node = (await coord.get_canvas())["nodes"][0]
    assert node["ieee_address"] == "pve-node-a"
    assert node["left_handles"] == 1 and node["right_handles"] == 1


# ─── Rack inventory ──────────────────────────────────────────────────────────


async def test_rack_prints_the_pinned_nodes_own_device(coord) -> None:  # noqa: ANN001
    """A mount pinned to a node that draws a *different* device prints that
    node's device, not the mount's (fixed in homelable #339)."""
    await _add_rows(
        coord,
        {"id": "pd-mount", "hostname": "mount", "ip": "10.0.0.1", "status": "pending"},
        {"id": "pd-node", "hostname": "real", "ip": "10.0.0.2", "os": "Debian", "status": "pending"},
    )
    network = (await coord.list_designs())[0]["id"]
    await coord.approve_pending("pd-node", {"design_id": network, "id": "n-real"})
    rack = (await coord.create_design("Rack", design_type="rack"))["id"]
    await coord.save_racks(rack, {
        "racks": [{"id": "r1", "name": "R", "u_count": 12, "pos_x": 0, "pos_y": 0}],
        "devices": [{"id": "m1", "rack_id": "r1", "device_id": "pd-mount", "node_id": "n-real",
                     "u_start": 1, "u_height": 1, "col_start": 0, "col_span": 1}],
    })
    item = next(i for i in await coord.rack_inventory(rack) if i["id"] == "pd-mount")
    assert item["node_id"] == "n-real"
    assert item["node_ip"] == "10.0.0.2"
    assert item["node_os"] == "Debian"


# ─── Lifecycle guards ────────────────────────────────────────────────────────


async def test_clear_pending_keeps_a_drawn_row(coord) -> None:  # noqa: ANN001
    await _add_rows(coord, {"id": "pd-1", "ip": "10.0.0.5", "status": "pending"},
                    {"id": "pd-2", "ip": "10.0.0.6", "status": "pending"})
    await coord.approve_pending("pd-1")
    (await coord._get_pending())["devices"][0]["status"] = "pending"
    assert await coord.clear_pending() == 1
    assert [d["id"] for d in (await coord._get_pending())["devices"]] == ["pd-1"]


async def test_restoring_a_drawn_device_brings_it_back_approved(coord) -> None:  # noqa: ANN001
    await _add_rows(coord, {"id": "pd-1", "ip": "10.0.0.5", "status": "pending"})
    await coord.approve_pending("pd-1")
    await coord.hide_pending("pd-1")
    assert await coord.restore_pending("pd-1") is True
    assert (await coord._get_pending())["devices"][0]["status"] == "approved"


async def test_manual_add_fills_the_known_row_and_unhides_it(coord) -> None:  # noqa: ANN001
    await _add_rows(coord, {"id": "pd-1", "ip": "10.0.0.5", "status": "hidden", "hostname": None})
    device = await coord.add_manual_pending(
        hostname="printer", ip="10.0.0.5", fields={"notes": "2nd floor", "type": "printer"}
    )
    assert device["id"] == "pd-1"
    row = (await coord._get_pending())["devices"][0]
    assert row["hostname"] == "printer"
    assert row["notes"] == "2nd floor"
    assert row["status"] == "pending"
    assert "manual" in row["discovery_sources"]


async def test_rack_gear_never_merges_into_a_scanned_row(coord) -> None:  # noqa: ANN001
    await _add_rows(coord, {"id": "pd-1", "ip": "10.0.0.5", "status": "pending"})
    device = await coord.add_manual_pending(
        hostname="Patch panel", ip="10.0.0.5", discovery_source="rack"
    )
    assert device["id"] != "pd-1"


# ─── WS ──────────────────────────────────────────────────────────────────────


async def test_ws_update_pending_applies_only_what_is_sent(hass, hass_ws_client, setup_ws) -> None:  # noqa: ANN001
    await _add_rows(setup_ws, {"id": "pd-1", "ip": "10.0.0.5", "hostname": "nas",
                               "notes": "keep", "suggested_type": "generic"})
    client = await hass_ws_client(hass)
    await client.send_json({
        "id": 1, "type": "homelable/scan/update_pending", "device_id": "pd-1",
        "label": "Synology", "node_type": "nas", "mac": "AA-BB-CC-DD-EE-FF",
        "services": [{"port": 5000, "protocol": "tcp", "service_name": "dsm"}],
        "status": "hidden",  # not editable here — dropped by the schema
    })
    msg = await client.receive_json()
    assert msg["success"] is False  # unknown key refused outright

    await client.send_json({
        "id": 2, "type": "homelable/scan/update_pending", "device_id": "pd-1",
        "label": "Synology", "node_type": "nas", "mac": "AA-BB-CC-DD-EE-FF",
        "services": [{"port": 5000, "protocol": "tcp", "service_name": "dsm"}],
    })
    result = (await client.receive_json())["result"]
    assert result["label"] == "Synology"
    assert result["type"] == "nas"
    assert result["mac"] == "aa:bb:cc:dd:ee:ff"
    assert result["notes"] == "keep"
    assert result["hostname"] == "nas"
    assert result["status"] == "approved"
    assert result["canvas_count"] == 0


async def test_ws_update_pending_unknown_device(hass, hass_ws_client, setup_ws) -> None:  # noqa: ANN001
    client = await hass_ws_client(hass)
    await client.send_json(
        {"id": 1, "type": "homelable/scan/update_pending", "device_id": "nope", "notes": "x"}
    )
    assert (await client.receive_json())["error"]["code"] == "not_found"


async def test_ws_ignore_refuses_a_drawn_device(hass, hass_ws_client, setup_ws) -> None:  # noqa: ANN001
    await _add_rows(setup_ws, {"id": "pd-1", "ip": "10.0.0.5", "status": "pending"})
    await setup_ws.approve_pending("pd-1")
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "homelable/scan/ignore", "device_id": "pd-1"})
    assert (await client.receive_json())["error"]["code"] == "in_use"
    assert len((await setup_ws._get_pending())["devices"]) == 1


async def test_ws_add_pending_carries_curated_fields(hass, hass_ws_client, setup_ws) -> None:  # noqa: ANN001
    client = await hass_ws_client(hass)
    await client.send_json({
        "id": 1, "type": "homelable/scan/add_pending", "hostname": "ups",
        "node_type": "ups", "notes": "basement", "check_method": "none",
    })
    device = (await client.receive_json())["result"]
    assert device["type"] == "ups"
    assert device["notes"] == "basement"
    assert device["check_method"] == "none"
