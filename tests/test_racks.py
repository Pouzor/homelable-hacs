"""Tests for the rack canvas (port of homelable #315, #323, #405, #407)."""
import copy
import re
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.homelable import racks
from custom_components.homelable.const import DOMAIN
from custom_components.homelable.coordinator import HomelableCoordinator
from custom_components.homelable.websocket import async_register_websocket_commands


def _mock_entry() -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "racks_test_entry"
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


def _rack(rack_id: str = "r1", u_height: int = 12, **kw: Any) -> dict[str, Any]:
    return {"id": rack_id, "name": "Rack", "u_height": u_height, **kw}


def _mount(
    mount_id: str,
    rack_id: str = "r1",
    u_start: int = 1,
    u_height: int = 1,
    **kw: Any,
) -> dict[str, Any]:
    return {
        "id": mount_id,
        "rack_id": rack_id,
        "label": mount_id,
        "u_start": u_start,
        "u_height": u_height,
        "col_start": 0,
        "col_span": 12,
        "faceplate_id": "switch-24",
        "ports": [{"id": f"{mount_id}-p1", "type": "rj45"}, {"id": f"{mount_id}-p2", "type": "rj45"}],
        **kw,
    }


def _cable(cable_id: str, a: str, b: str, **kw: Any) -> dict[str, Any]:
    return {
        "id": cable_id,
        "from_device_id": a,
        "from_port_id": f"{a}-p1",
        "to_device_id": b,
        "to_port_id": f"{b}-p1",
        **kw,
    }


async def _rack_design(coord: HomelableCoordinator, name: str = "Lab rack") -> str:
    return (await coord.create_design(name, icon="server", design_type="rack"))["id"]


async def _add_inventory(coord: HomelableCoordinator, *devices: dict[str, Any]) -> None:
    pending = await coord._get_pending()
    for d in devices:
        pending["devices"].append({"status": "pending", "services": [], **d})
    await coord._save_pending()


# ─── Save / load ─────────────────────────────────────────────────────────────


async def test_save_load_round_trip(coord) -> None:  # noqa: ANN001
    design_id = await _rack_design(coord)
    payload = {
        "racks": [_rack(location="Garage", style={"frame": "#111"}, pos_x=10, pos_y=20)],
        "devices": [_mount("m1"), _mount("m2", u_start=3, port_visibility="always")],
        "cables": [_cable("c1", "m1", "m2", type="fiber", label="uplink", label_visible=True)],
        "viewport": {"x": 5, "y": 6, "zoom": 1.5},
    }
    assert await coord.save_racks(design_id, payload)

    state = await coord.get_racks(design_id)
    assert [r["id"] for r in state["racks"]] == ["r1"]
    assert state["racks"][0]["location"] == "Garage"
    assert state["racks"][0]["pos_x"] == 10
    assert {d["id"] for d in state["devices"]} == {"m1", "m2"}
    m2 = next(d for d in state["devices"] if d["id"] == "m2")
    assert m2["port_visibility"] == "always"
    assert state["cables"][0]["type"] == "fiber"
    assert state["cables"][0]["label"] == "uplink"
    assert state["viewport"] == {"x": 5, "y": 6, "zoom": 1.5}


async def test_unknown_port_visibility_reads_back_as_auto(coord) -> None:  # noqa: ANN001
    design_id = await _rack_design(coord)
    await coord.save_racks(
        design_id, {"racks": [_rack()], "devices": [_mount("m1", port_visibility="sometimes")]}
    )
    state = await coord.get_racks(design_id)
    assert state["devices"][0]["port_visibility"] == "auto"


async def test_save_replaces_the_whole_state(coord) -> None:  # noqa: ANN001
    design_id = await _rack_design(coord)
    await coord.save_racks(
        design_id,
        {
            "racks": [_rack("r1"), _rack("r2")],
            "devices": [_mount("m1"), _mount("m2", u_start=2)],
            "cables": [_cable("c1", "m1", "m2")],
        },
    )
    await coord.save_racks(design_id, {"racks": [_rack("r2")], "devices": [], "cables": []})

    state = await coord.get_racks(design_id)
    assert [r["id"] for r in state["racks"]] == ["r2"]
    assert state["devices"] == []
    assert state["cables"] == []


async def test_rack_state_is_isolated_per_design(coord) -> None:  # noqa: ANN001
    a = await _rack_design(coord, "A")
    b = await _rack_design(coord, "B")
    await coord.save_racks(a, {"racks": [_rack("ra")]})
    await coord.save_racks(b, {"racks": [_rack("rb")]})
    assert [r["id"] for r in (await coord.get_racks(a))["racks"]] == ["ra"]
    assert [r["id"] for r in (await coord.get_racks(b))["racks"]] == ["rb"]


async def test_new_rack_design_loads_empty(coord) -> None:  # noqa: ANN001
    design_id = await _rack_design(coord)
    state = await coord.get_racks(design_id)
    assert state == racks.empty_state()


async def test_unknown_design_is_refused(coord) -> None:  # noqa: ANN001
    assert await coord.get_racks("nope") is None
    assert await coord.save_racks("nope", {"racks": []}) is False
    assert await coord.rack_inventory("nope") is None


async def test_rack_state_survives_a_reload(hass, coord) -> None:  # noqa: ANN001
    design_id = await _rack_design(coord)
    await coord.save_racks(design_id, {"racks": [_rack()], "devices": [_mount("m1")]})

    fresh = HomelableCoordinator(hass, _mock_entry())
    state = await fresh.get_racks(design_id)
    assert [d["id"] for d in state["devices"]] == ["m1"]


# ─── Payload validation ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        ({"racks": [_rack(u_height=0)]}, "u_height"),
        ({"racks": [_rack(u_height=101)]}, "u_height"),
        ({"racks": [_rack(width_standard="23")]}, "width_standard"),
        ({"racks": [_rack(numbering="sideways")]}, "numbering"),
        ({"racks": [_rack()], "devices": [_mount("m1", rack_id="ghost")]}, "unknown rack"),
        ({"racks": [_rack(u_height=4)], "devices": [_mount("m1", u_start=4, u_height=2)]}, "does not fit"),
        ({"racks": [_rack()], "devices": [_mount("m1", u_start=0)]}, "1-based"),
        ({"racks": [_rack()], "devices": [_mount("m1", col_start=12)]}, "col_start"),
        ({"racks": [_rack()], "devices": [_mount("m1", col_span=13)]}, "col_span"),
        ({"racks": [_rack()], "devices": [_mount("m1", col_start=11, col_span=2)]}, "column grid"),
        ({"racks": [_rack()], "devices": [_mount("m1", u_height=2), _mount("m2", u_start=2)]}, "overlap"),
        ({"racks": [_rack()], "devices": [_mount("m1")], "cables": [_cable("c1", "m1", "ghost")]}, "outside"),
        (
            {"racks": [_rack()], "devices": [_mount("m1"), _mount("m2", u_start=2)], "cables": [_cable("c1", "m1", "m2", type="copper")]},
            "type",
        ),
        ({"racks": [_rack(), _rack()]}, "Duplicate rack"),
    ],
)
def test_clean_save_refuses_invalid_payloads(payload: dict[str, Any], reason: str) -> None:
    with pytest.raises(ValueError, match=reason):
        racks.clean_save(payload)


def test_side_by_side_mounts_share_a_unit() -> None:
    state = racks.clean_save(
        {
            "racks": [_rack()],
            "devices": [
                _mount("left", col_start=0, col_span=6),
                _mount("right", col_start=6, col_span=6),
            ],
        }
    )
    assert len(state["devices"]) == 2


def test_cable_properties_without_a_key_are_dropped() -> None:
    state = racks.clean_save(
        {
            "racks": [_rack()],
            "devices": [_mount("m1"), _mount("m2", u_start=2)],
            "cables": [_cable("c1", "m1", "m2", properties=[{"key": "len", "value": "2m"}, {"key": " "}])],
        }
    )
    assert state["cables"][0]["properties"] == [{"key": "len", "value": "2m"}]


# ─── Inventory-owned front panel ─────────────────────────────────────────────


async def test_save_writes_the_front_panel_through_to_the_inventory(coord) -> None:  # noqa: ANN001
    await _add_inventory(coord, {"id": "pd-1", "hostname": "sw1"})
    design_id = await _rack_design(coord)
    await coord.save_racks(
        design_id,
        {"racks": [_rack()], "devices": [_mount("m1", device_id="pd-1", u_height=2, color="#abcdef")]},
    )
    entry = (await coord._get_pending())["devices"][0]
    assert entry["rack_faceplate_id"] == "switch-24"
    assert entry["rack_u_height"] == 2
    assert entry["rack_col_span"] == 12
    assert entry["rack_color"] == "#abcdef"
    assert [p["id"] for p in entry["rack_ports"]] == ["m1-p1", "m1-p2"]


async def test_another_rack_picks_up_the_inventory_plate(coord) -> None:  # noqa: ANN001
    await _add_inventory(coord, {"id": "pd-1", "hostname": "sw1"})
    a = await _rack_design(coord, "A")
    b = await _rack_design(coord, "B")
    await coord.save_racks(b, {"racks": [_rack()], "devices": [_mount("mb", device_id="pd-1")]})
    await coord.save_racks(
        a,
        {"racks": [_rack()], "devices": [_mount("ma", device_id="pd-1", faceplate_id="server-2u", u_height=2)]},
    )

    mb = (await coord.get_racks(b))["devices"][0]
    assert mb["faceplate_id"] == "server-2u"
    assert mb["u_height"] == 2
    assert [p["id"] for p in mb["ports"]] == ["ma-p1", "ma-p2"]


async def test_overlaid_size_never_lands_on_a_neighbour(coord) -> None:  # noqa: ANN001
    await _add_inventory(coord, {"id": "pd-1", "hostname": "sw1"})
    a = await _rack_design(coord, "A")
    b = await _rack_design(coord, "B")
    # In B the device sits under another mount at U2, so a 2U model cannot fit.
    await coord.save_racks(
        b,
        {"racks": [_rack()], "devices": [_mount("mb", device_id="pd-1"), _mount("above", u_start=2)]},
    )
    await coord.save_racks(a, {"racks": [_rack()], "devices": [_mount("ma", device_id="pd-1", u_height=2)]})

    mb = next(d for d in (await coord.get_racks(b))["devices"] if d["id"] == "mb")
    assert mb["u_height"] == 1


async def test_overlaid_size_never_passes_the_top_rail(coord) -> None:  # noqa: ANN001
    await _add_inventory(coord, {"id": "pd-1", "hostname": "sw1"})
    a = await _rack_design(coord, "A")
    b = await _rack_design(coord, "B")
    await coord.save_racks(b, {"racks": [_rack(u_height=4)], "devices": [_mount("mb", device_id="pd-1", u_start=4)]})
    await coord.save_racks(a, {"racks": [_rack()], "devices": [_mount("ma", device_id="pd-1", u_height=3)]})

    mb = (await coord.get_racks(b))["devices"][0]
    assert mb["u_height"] == 1


async def test_an_echoed_clamped_size_does_not_shrink_the_device(coord) -> None:  # noqa: ANN001
    await _add_inventory(coord, {"id": "pd-1", "hostname": "sw1"})
    a = await _rack_design(coord, "A")
    b = await _rack_design(coord, "B")
    await coord.save_racks(b, {"racks": [_rack(u_height=4)], "devices": [_mount("mb", device_id="pd-1", u_start=4)]})
    await coord.save_racks(a, {"racks": [_rack()], "devices": [_mount("ma", device_id="pd-1", u_height=3)]})

    # B loads with the size clamped to 1U, and saves that back untouched.
    state_b = await coord.get_racks(b)
    await coord.save_racks(b, state_b)

    entry = (await coord._get_pending())["devices"][0]
    assert entry["rack_u_height"] == 3


async def test_accessories_have_no_inventory_to_write(coord) -> None:  # noqa: ANN001
    await _add_inventory(coord, {"id": "pd-1", "hostname": "sw1"})
    design_id = await _rack_design(coord)
    await coord.save_racks(design_id, {"racks": [_rack()], "devices": [_mount("blank")]})
    entry = (await coord._get_pending())["devices"][0]
    assert "rack_faceplate_id" not in entry


# ─── Inventory offered to the tray ───────────────────────────────────────────


async def test_inventory_excludes_unrackable_and_hidden_entries(coord) -> None:  # noqa: ANN001
    await _add_inventory(
        coord,
        {"id": "pd-srv", "hostname": "srv", "suggested_type": "server"},
        {"id": "pd-bare", "ip": "192.168.1.9"},
        {"id": "pd-vm", "hostname": "vm", "suggested_type": "vm"},
        {"id": "pd-zb", "hostname": "bulb", "suggested_type": "zigbee_enddevice"},
        {"id": "pd-hid", "hostname": "gone", "status": "hidden"},
    )
    design_id = await _rack_design(coord)
    items = await coord.rack_inventory(design_id)
    assert {i["id"] for i in items} == {"pd-srv", "pd-bare"}


async def test_inventory_flags_what_is_racked_in_this_design(coord) -> None:  # noqa: ANN001
    await _add_inventory(coord, {"id": "pd-1", "hostname": "a"}, {"id": "pd-2", "hostname": "b"})
    a = await _rack_design(coord, "A")
    b = await _rack_design(coord, "B")
    await coord.save_racks(a, {"racks": [_rack()], "devices": [_mount("m1", device_id="pd-1")]})

    racked_a = {i["id"]: i["racked"] for i in await coord.rack_inventory(a)}
    racked_b = {i["id"]: i["racked"] for i in await coord.rack_inventory(b)}
    assert racked_a == {"pd-1": True, "pd-2": False}
    assert racked_b == {"pd-1": False, "pd-2": False}


async def test_inventory_resolves_the_canvas_node_and_its_status(coord) -> None:  # noqa: ANN001
    await _add_inventory(
        coord,
        {
            "id": "pd-1",
            "ip": "192.168.1.10",
            "hostname": "nas",
            "services": [{"port": 32400, "service_name": "plex", "category": "media"}],
        },
    )
    designs = await coord.list_designs()
    network_id = designs[0]["id"]
    await coord.save_canvas(
        {
            "nodes": [
                {
                    "id": "n1",
                    "type": "nas",
                    "label": "Synology",
                    "ip": "192.168.1.10, fe80::1",
                    "hostname": "nas.lan",
                    "os": "DSM",
                    "check_method": "ping",
                    "status": "unknown",
                }
            ],
            "edges": [],
        },
        network_id,
    )
    coord.data = {"n1": {"status": "online"}}
    design_id = await _rack_design(coord)

    item = (await coord.rack_inventory(design_id))[0]
    # One device, one set of facts: the node's hostname edit landed on the row.
    assert item["label"] == "nas.lan"
    assert item["node_id"] == "n1"
    assert item["node_status"] == "online"
    assert item["node_label"] == "Synology"
    assert item["node_type"] == "nas"
    assert item["node_os"] == "DSM"
    assert item["node_check_method"] == "ping"
    assert item["node_design_id"] == network_id
    assert item["node_design_name"] == "Network Topology"
    assert item["services"] == [{"port": 32400, "name": "plex"}]


async def test_a_node_pinned_on_the_mount_beats_the_ip_guess(coord) -> None:  # noqa: ANN001
    await _add_inventory(coord, {"id": "pd-1", "ip": "192.168.1.10", "hostname": "x"})
    network_id = (await coord.list_designs())[0]["id"]
    await coord.save_canvas(
        {
            "nodes": [
                {"id": "by-ip", "type": "server", "label": "Guess", "ip": "192.168.1.10"},
                {"id": "pinned", "type": "server", "label": "Chosen", "ip": "10.0.0.2"},
            ],
            "edges": [],
        },
        network_id,
    )
    design_id = await _rack_design(coord)
    await coord.save_racks(
        design_id, {"racks": [_rack()], "devices": [_mount("m1", device_id="pd-1", node_id="pinned")]}
    )
    item = (await coord.rack_inventory(design_id))[0]
    assert item["node_id"] == "pinned"
    assert item["node_label"] == "Chosen"


async def test_inventory_carries_the_rack_model(coord) -> None:  # noqa: ANN001
    await _add_inventory(coord, {"id": "pd-1", "hostname": "sw"})
    design_id = await _rack_design(coord)
    await coord.save_racks(design_id, {"racks": [_rack()], "devices": [_mount("m1", device_id="pd-1")]})
    item = (await coord.rack_inventory(design_id))[0]
    assert item["rack_faceplate_id"] == "switch-24"
    assert item["rack_u_height"] == 1
    assert len(item["rack_ports"]) == 2


# ─── Manual inventory entries + approve gating ───────────────────────────────


async def test_add_manual_pending_lands_in_the_inventory(coord) -> None:  # noqa: ANN001
    device = await coord.add_manual_pending(
        hostname="Patch panel", mac="AA-BB-CC-11-22-33", suggested_type="patch_panel", discovery_source="rack"
    )
    assert device["status"] == "pending"
    assert device["mac"] == "aa:bb:cc:11:22:33"
    assert device["discovery_sources"] == ["rack"]
    listed = await coord.list_pending()
    assert [d["id"] for d in listed] == [device["id"]]


async def test_rack_devices_cannot_be_approved(coord) -> None:  # noqa: ANN001
    device = await coord.add_manual_pending(hostname="Shelf", discovery_source="rack")
    assert await coord.approve_pending(device["id"]) == {"rack_only": True}
    canvas = await coord.get_canvas()
    assert canvas["nodes"] == []


async def test_bulk_approve_skips_rack_devices(coord) -> None:  # noqa: ANN001
    rack_dev = await coord.add_manual_pending(hostname="PDU", discovery_source="rack")
    host = await coord.add_manual_pending(hostname="box", ip="192.168.1.50")
    result = await coord.approve_batch([rack_dev["id"], host["id"]])
    assert result["approved"] == 1
    assert result["skipped"] == [rack_dev["id"]]
    assert result["skipped_devices"][0]["match"] == "rack"


# ─── Design lifecycle ────────────────────────────────────────────────────────


async def test_deleting_a_design_drops_its_racks(coord) -> None:  # noqa: ANN001
    design_id = await _rack_design(coord)
    await coord.save_racks(design_id, {"racks": [_rack()]})
    assert await coord.delete_design(design_id) == "ok"
    assert design_id not in await coord._get_racks()


async def test_copying_a_design_duplicates_its_racks_under_new_ids(coord) -> None:  # noqa: ANN001
    source = await _rack_design(coord)
    await coord.save_racks(
        source,
        {
            "racks": [_rack()],
            "devices": [_mount("m1", device_id="pd-x", node_id="n1"), _mount("m2", u_start=2)],
            "cables": [_cable("c1", "m1", "m2")],
        },
    )
    copy_design = await coord.copy_design(source, "Copy")
    assert copy_design["design_type"] == "rack"

    copied = await coord.get_racks(copy_design["id"])
    original = await coord.get_racks(source)
    assert copied["racks"][0]["id"] != original["racks"][0]["id"]
    assert {d["rack_id"] for d in copied["devices"]} == {copied["racks"][0]["id"]}
    copied_ids = {d["id"] for d in copied["devices"]}
    assert copied_ids.isdisjoint({"m1", "m2"})
    cable = copied["cables"][0]
    assert {cable["from_device_id"], cable["to_device_id"]} == copied_ids
    m1 = next(d for d in copied["devices"] if d["device_id"] == "pd-x")
    assert m1["node_id"] == "n1"


def test_copy_state_leaves_the_source_untouched() -> None:
    state = racks.clean_save({"racks": [_rack()], "devices": [_mount("m1")]})
    before = copy.deepcopy(state)
    racks.copy_state(state)
    assert state == before


# ─── WebSocket ───────────────────────────────────────────────────────────────


async def test_ws_rack_round_trip(hass, hass_ws_client, setup_ws) -> None:  # noqa: ANN001
    client = await hass_ws_client(hass)
    await client.send_json(
        {"id": 1, "type": "homelable/designs/create", "name": "Rack", "design_type": "rack"}
    )
    design_id = (await client.receive_json())["result"]["id"]

    await client.send_json(
        {
            "id": 2,
            "type": "homelable/racks/save",
            "design_id": design_id,
            "racks": [_rack()],
            "devices": [_mount("m1")],
            "cables": [],
            "viewport": {"x": 1, "y": 2, "zoom": 1},
        }
    )
    assert (await client.receive_json())["result"] == {"saved": True}

    await client.send_json({"id": 3, "type": "homelable/racks/get", "design_id": design_id})
    result = (await client.receive_json())["result"]
    assert [d["id"] for d in result["devices"]] == ["m1"]
    assert result["viewport"] == {"x": 1, "y": 2, "zoom": 1}


async def test_ws_rack_save_reports_an_invalid_payload(hass, hass_ws_client, setup_ws) -> None:  # noqa: ANN001
    design_id = await _rack_design(setup_ws)
    client = await hass_ws_client(hass)
    await client.send_json(
        {
            "id": 1,
            "type": "homelable/racks/save",
            "design_id": design_id,
            "racks": [_rack(u_height=2)],
            "devices": [_mount("m1", u_start=2, u_height=2)],
        }
    )
    msg = await client.receive_json()
    assert not msg["success"]
    assert msg["error"]["code"] == "invalid_format"


async def test_ws_racks_unknown_design(hass, hass_ws_client, setup_ws) -> None:  # noqa: ANN001
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "homelable/racks/get", "design_id": "nope"})
    assert (await client.receive_json())["error"]["code"] == "not_found"
    await client.send_json({"id": 2, "type": "homelable/racks/inventory", "design_id": "nope"})
    assert (await client.receive_json())["error"]["code"] == "not_found"


async def test_ws_racks_inventory(hass, hass_ws_client, setup_ws) -> None:  # noqa: ANN001
    await _add_inventory(setup_ws, {"id": "pd-1", "hostname": "sw"})
    design_id = await _rack_design(setup_ws)
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "homelable/racks/inventory", "design_id": design_id})
    items = (await client.receive_json())["result"]["items"]
    assert [i["id"] for i in items] == ["pd-1"]


async def test_ws_add_pending_and_rack_approve_gate(hass, hass_ws_client, setup_ws) -> None:  # noqa: ANN001
    client = await hass_ws_client(hass)
    await client.send_json(
        {
            "id": 1,
            "type": "homelable/scan/add_pending",
            "hostname": "Patch panel",
            "suggested_type": "patch_panel",
            "discovery_source": "rack",
        }
    )
    device = (await client.receive_json())["result"]
    assert device["discovery_source"] == "rack"

    await client.send_json({"id": 2, "type": "homelable/scan/approve", "device_id": device["id"]})
    msg = await client.receive_json()
    assert not msg["success"]
    assert msg["error"]["code"] == "rack_device"


async def test_ws_add_pending_refuses_an_unknown_source(hass, hass_ws_client, setup_ws) -> None:  # noqa: ANN001
    client = await hass_ws_client(hass)
    await client.send_json(
        {"id": 1, "type": "homelable/scan/add_pending", "hostname": "x", "discovery_source": "arp"}
    )
    assert not (await client.receive_json())["success"]


async def test_ws_design_create_refuses_an_unknown_type(hass, hass_ws_client, setup_ws) -> None:  # noqa: ANN001
    client = await hass_ws_client(hass)
    await client.send_json(
        {"id": 1, "type": "homelable/designs/create", "name": "X", "design_type": "floorplan"}
    )
    assert not (await client.receive_json())["success"]


# ─── Frontend sync ───────────────────────────────────────────────────────────


def test_unrackable_types_match_the_frontend() -> None:
    """`UNRACKABLE_TYPES` is mirrored in the panel's rackable.ts; keep them equal."""
    source = (
        Path(__file__).resolve().parents[1] / "frontend-src" / "src" / "utils" / "rackable.ts"
    ).read_text()
    block = re.search(r"UNRACKABLE_TYPES = new Set\(\[(.*?)\]\)", source, re.S)
    assert block is not None
    frontend = set(re.findall(r"'([^']+)'", block.group(1)))
    assert frontend == set(racks.UNRACKABLE_TYPES)
