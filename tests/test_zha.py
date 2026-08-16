"""Tests for the ZHA importer (topology builder + fallback + coordinator + WS).

Issue #50: ZHA users have no Zigbee2MQTT broker, so the mesh is read straight
out of HA's own ZHA integration instead of over MQTT.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.homelable import zha
from custom_components.homelable.const import CONF_ZIGBEE_SOURCE, DOMAIN
from custom_components.homelable.coordinator import HomelableCoordinator
from custom_components.homelable.websocket import async_register_websocket_commands

COORD = "00:11:22:33:44:55:66:00"
ROUTER = "00:11:22:33:44:55:66:01"
END = "00:11:22:33:44:55:66:02"


def _info(
    ieee: str,
    device_type: str,
    *,
    name: str | None = None,
    neighbors: list[dict] | None = None,
    **extra,
) -> dict:
    """One `zha_device_info` dict, trimmed to the keys the importer reads."""
    return {
        "ieee": ieee,
        "nwk": 0x1234,
        "name": name or ieee,
        "manufacturer": "IKEA of Sweden",
        "model": "TRADFRI bulb",
        "device_type": device_type,
        "lqi": None,
        "neighbors": neighbors or [],
        **extra,
    }


def _neighbor(ieee: str, relationship: str, lqi: int = 200) -> dict:
    """ZHA stringifies neighbour LQI and depth — mirror that here."""
    return {
        "ieee": ieee,
        "relationship": relationship,
        "device_type": "Router",
        "depth": "1",
        "lqi": str(lqi),
    }


# ─── Topology builder ────────────────────────────────────────────────────────


def test_build_topology_empty() -> None:
    assert zha.build_topology([]) == ([], [])


def test_build_topology_maps_zha_device_types() -> None:
    nodes, _edges = zha.build_topology(
        [
            _info(COORD, "Coordinator"),
            _info(ROUTER, "Router"),
            _info(END, "EndDevice"),
        ]
    )
    by_id = {n["id"]: n for n in nodes}
    assert by_id[COORD]["type"] == "zigbee_coordinator"
    assert by_id[ROUTER]["type"] == "zigbee_router"
    assert by_id[END]["type"] == "zigbee_enddevice"
    # Shape matches zigbee.parse_networkmap so the import path is shared.
    assert by_id[END]["ieee_address"] == END
    assert by_id[END]["vendor"] == "IKEA of Sweden"
    assert by_id[END]["model"] == "TRADFRI bulb"


def test_build_topology_active_coordinator_flag_wins() -> None:
    nodes, _edges = zha.build_topology(
        [_info(COORD, "Router", active_coordinator=True)]
    )
    assert nodes[0]["type"] == "zigbee_coordinator"
    assert nodes[0]["device_type"] == "Coordinator"


def test_build_topology_child_relationship_builds_tree() -> None:
    nodes, edges = zha.build_topology(
        [
            _info(COORD, "Coordinator", neighbors=[_neighbor(ROUTER, "Child", 210)]),
            _info(ROUTER, "Router", neighbors=[_neighbor(END, "Child", 150)]),
            _info(END, "EndDevice"),
        ]
    )
    by_id = {n["id"]: n for n in nodes}
    assert by_id[COORD]["parent_id"] is None
    assert by_id[ROUTER]["parent_id"] == COORD
    assert by_id[END]["parent_id"] == ROUTER
    # LQI comes off the neighbour entry and is coerced from ZHA's string.
    assert by_id[ROUTER]["lqi"] == 210
    assert by_id[END]["lqi"] == 150
    assert {(e["source"], e["target"]) for e in edges} == {
        (COORD, ROUTER),
        (ROUTER, END),
    }


def test_build_topology_parent_relationship_builds_tree() -> None:
    """The end device names its parent rather than the router naming its child."""
    nodes, _edges = zha.build_topology(
        [
            _info(COORD, "Coordinator"),
            _info(ROUTER, "Router", neighbors=[_neighbor(COORD, "Parent")]),
            _info(END, "EndDevice", neighbors=[_neighbor(ROUTER, "Parent", 90)]),
        ]
    )
    by_id = {n["id"]: n for n in nodes}
    assert by_id[ROUTER]["parent_id"] == COORD
    assert by_id[END]["parent_id"] == ROUTER
    assert by_id[END]["lqi"] == 90


def test_build_topology_sibling_carries_no_parenting() -> None:
    nodes, edges = zha.build_topology(
        [
            _info(COORD, "Coordinator"),
            _info(ROUTER, "Router", neighbors=[_neighbor(END, "Sibling")]),
            _info(END, "EndDevice"),
        ]
    )
    by_id = {n["id"]: n for n in nodes}
    # Both fall back to the coordinator rather than being linked to each other.
    assert by_id[ROUTER]["parent_id"] == COORD
    assert by_id[END]["parent_id"] == COORD
    assert (ROUTER, END) not in {(e["source"], e["target"]) for e in edges}


def test_build_topology_orphan_falls_back_to_coordinator() -> None:
    nodes, _edges = zha.build_topology(
        [_info(COORD, "Coordinator"), _info(END, "EndDevice")]
    )
    by_id = {n["id"]: n for n in nodes}
    assert by_id[END]["parent_id"] == COORD


def test_build_topology_without_coordinator_leaves_orphans_unparented() -> None:
    nodes, edges = zha.build_topology([_info(END, "EndDevice")])
    assert nodes[0]["parent_id"] is None
    assert edges == []


def test_build_topology_breaks_parent_cycles() -> None:
    """Stale neighbour tables can have two routers each claim the other."""
    other = "00:11:22:33:44:55:66:03"
    nodes, edges = zha.build_topology(
        [
            _info(COORD, "Coordinator"),
            _info(ROUTER, "Router", neighbors=[_neighbor(other, "Parent")]),
            _info(other, "Router", neighbors=[_neighbor(ROUTER, "Parent")]),
        ]
    )
    by_id = {n["id"]: n for n in nodes}
    # One link survives, the other is re-homed on the coordinator: still a tree.
    parents = {by_id[ROUTER]["parent_id"], by_id[other]["parent_id"]}
    assert COORD in parents
    assert len(edges) == 2
    assert not _has_cycle({e["target"]: e["source"] for e in edges})


def _has_cycle(parent_of: dict[str, str]) -> bool:
    for start in parent_of:
        seen = {start}
        current = start
        while (nxt := parent_of.get(current)) is not None:
            if nxt in seen:
                return True
            seen.add(nxt)
            current = nxt
    return False


def test_build_topology_ignores_unknown_and_self_neighbors() -> None:
    nodes, _edges = zha.build_topology(
        [
            _info(COORD, "Coordinator"),
            _info(
                ROUTER,
                "Router",
                neighbors=[
                    _neighbor(ROUTER, "Parent"),  # itself
                    _neighbor("ff:ff:ff:ff:ff:ff:ff:ff", "Parent"),  # not in the mesh
                    "not-a-dict",  # malformed
                ],
            ),
        ]
    )
    by_id = {n["id"]: n for n in nodes}
    assert by_id[ROUTER]["parent_id"] == COORD


def test_build_topology_skips_entries_without_ieee() -> None:
    nodes, _edges = zha.build_topology(
        [{"name": "no ieee"}, "garbage", _info(COORD, "Coordinator")]
    )
    assert [n["id"] for n in nodes] == [COORD]


def test_build_topology_deduplicates_ieee() -> None:
    nodes, _edges = zha.build_topology(
        [_info(COORD, "Coordinator"), _info(COORD, "Coordinator")]
    )
    assert len(nodes) == 1


def test_coerce_int_handles_zha_strings() -> None:
    assert zha._coerce_int("200") == 200
    assert zha._coerce_int(200) == 200
    assert zha._coerce_int(0) == 0
    assert zha._coerce_int(None) is None
    assert zha._coerce_int("") is None
    assert zha._coerce_int("nope") is None


# ─── fetch_zha_network ───────────────────────────────────────────────────────


async def test_fetch_raises_when_zha_not_set_up(hass: HomeAssistant) -> None:
    with pytest.raises(zha.ZhaNotReadyError):
        await zha.fetch_zha_network(hass)


async def test_fetch_uses_gateway_when_available(hass: HomeAssistant) -> None:
    hass.config.components.add("zha")
    infos = [_info(COORD, "Coordinator", neighbors=[_neighbor(END, "Child")]), _info(END, "EndDevice")]
    with patch.object(zha, "_device_infos", return_value=infos):
        nodes, edges = await zha.fetch_zha_network(hass)
    assert {n["id"] for n in nodes} == {COORD, END}
    assert edges == [{"source": COORD, "target": END}]


async def _add_zha_device(hass: HomeAssistant, ieee: str, name: str) -> None:
    entry = MockConfigEntry(domain="zha")
    entry.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("zha", ieee)},
        name=name,
        manufacturer="IKEA of Sweden",
        model="TRADFRI bulb",
    )


async def test_fetch_falls_back_to_device_registry(hass: HomeAssistant) -> None:
    """ZHA loaded but its private gateway API is gone — devices still import."""
    hass.config.components.add("zha")
    await _add_zha_device(hass, END, "Kitchen bulb")
    with patch.object(
        zha, "_device_infos", side_effect=zha.ZhaNotReadyError("restructured")
    ):
        nodes, edges = await zha.fetch_zha_network(hass)
    assert len(nodes) == 1
    assert nodes[0]["ieee_address"] == END
    assert nodes[0]["friendly_name"] == "Kitchen bulb"
    assert nodes[0]["vendor"] == "IKEA of Sweden"
    # No topology and no LQI is the documented cost of the fallback.
    assert nodes[0]["lqi"] is None
    assert edges == []


async def test_fetch_prefers_user_given_registry_name(hass: HomeAssistant) -> None:
    hass.config.components.add("zha")
    entry = MockConfigEntry(domain="zha")
    entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("zha", END)},
        name="TRADFRI bulb E27",
    )
    registry.async_update_device(device.id, name_by_user="Desk lamp")

    with patch.object(zha, "_device_infos", return_value=[_info(END, "EndDevice")]):
        nodes, _edges = await zha.fetch_zha_network(hass)
    assert nodes[0]["label"] == "Desk lamp"
    assert nodes[0]["friendly_name"] == "Desk lamp"


# ─── Coordinator ─────────────────────────────────────────────────────────────


def _mock_entry() -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "zha_entry"
    entry.data = {"scan_ranges": "192.168.1.0/24", "status_interval": 60}
    entry.options = {}
    return entry


@pytest.fixture
async def coordinator(hass: HomeAssistant) -> HomelableCoordinator:
    return HomelableCoordinator(hass, _mock_entry())


def test_auto_source_prefers_zha_when_available(
    coordinator: HomelableCoordinator, hass: HomeAssistant
) -> None:
    assert coordinator.get_zigbee_source() == "auto"
    assert coordinator.resolve_zigbee_backend() == "z2m"
    hass.config.components.add("zha")
    assert coordinator.resolve_zigbee_backend() == "zha"


def test_configured_source_wins_over_detection(
    coordinator: HomelableCoordinator, hass: HomeAssistant
) -> None:
    """The switch is the authority — a ZHA install can still import from Z2M."""
    hass.config.components.add("zha")
    coordinator.entry.options = {CONF_ZIGBEE_SOURCE: "z2m"}
    assert coordinator.get_zigbee_source() == "z2m"
    assert coordinator.resolve_zigbee_backend() == "z2m"

    coordinator.entry.options = {CONF_ZIGBEE_SOURCE: "zha"}
    assert coordinator.resolve_zigbee_backend() == "zha"


def test_configured_zha_source_is_honoured_without_detection(
    coordinator: HomelableCoordinator,
) -> None:
    """No ZHA component loaded, but the user said ZHA: don't silently use Z2M."""
    coordinator.entry.options = {CONF_ZIGBEE_SOURCE: "zha"}
    assert coordinator.resolve_zigbee_backend() == "zha"


def test_source_falls_back_to_auto_when_garbage(
    coordinator: HomelableCoordinator,
) -> None:
    coordinator.entry.options = {CONF_ZIGBEE_SOURCE: "carrier_pigeon"}
    assert coordinator.get_zigbee_source() == "auto"


def test_source_read_from_entry_data_when_not_in_options(
    coordinator: HomelableCoordinator,
) -> None:
    """Set in the initial config flow, never touched in options."""
    coordinator.entry.data = {**coordinator.entry.data, CONF_ZIGBEE_SOURCE: "zha"}
    assert coordinator.get_zigbee_source() == "zha"


def test_explicit_request_overrides_the_configured_source(
    coordinator: HomelableCoordinator, hass: HomeAssistant
) -> None:
    hass.config.components.add("zha")
    coordinator.entry.options = {CONF_ZIGBEE_SOURCE: "z2m"}
    assert coordinator.resolve_zigbee_backend("zha") == "zha"
    assert coordinator.resolve_zigbee_backend("auto") == "z2m"
    assert coordinator.resolve_zigbee_backend("nonsense") == "z2m"


def test_zigbee_gateway_never_claims_z2m_from_a_loaded_mqtt(
    coordinator: HomelableCoordinator, hass: HomeAssistant
) -> None:
    """MQTT is loaded for plenty of non-Z2M reasons (Tasmota, ESPHome…).

    Reporting Z2M off the `mqtt` component would let the panel advertise a
    gateway that is not there, and an import against it would burn the full
    300s networkmap timeout before failing.
    """
    hass.config.components.add("zha")
    hass.config.components.add("mqtt")
    assert coordinator.zigbee_gateway() == {
        "source": "auto",
        "resolved": "zha",
        "zha_detected": True,
    }


def test_zigbee_gateway_reports_the_configured_source(
    coordinator: HomelableCoordinator, hass: HomeAssistant
) -> None:
    hass.config.components.add("zha")
    coordinator.entry.options = {CONF_ZIGBEE_SOURCE: "z2m"}
    assert coordinator.zigbee_gateway() == {
        "source": "z2m",
        "resolved": "z2m",
        "zha_detected": True,
    }


async def test_fetch_networkmap_routes_to_zha(
    coordinator: HomelableCoordinator, hass: HomeAssistant
) -> None:
    hass.config.components.add("zha")
    with patch.object(
        zha, "fetch_zha_network", new=AsyncMock(return_value=([], []))
    ) as mock_fetch:
        await coordinator.fetch_zigbee_networkmap()
    assert mock_fetch.called


async def test_import_via_zha_tags_discovery_source(
    coordinator: HomelableCoordinator,
) -> None:
    devs = [
        {
            "id": END,
            "ieee_address": END,
            "friendly_name": "Kitchen bulb",
            "type": "zigbee_enddevice",
            "device_type": "EndDevice",
            "model": "TRADFRI bulb",
            "vendor": "IKEA of Sweden",
            "lqi": 150,
            "parent_id": COORD,
        }
    ]
    result = await coordinator.import_zigbee_devices(devs, backend="zha")
    assert result == {"added": 1, "skipped": 0, "refreshed": 0}
    pending = await coordinator.list_pending(source="zigbee")
    assert len(pending) == 1
    assert pending[0]["discovery_source"] == "zha"
    # Same `source` as Z2M, so a mesh device is never listed twice.
    assert pending[0]["source"] == "zigbee"


async def test_zha_and_z2m_imports_dedupe_on_ieee(
    coordinator: HomelableCoordinator,
) -> None:
    dev = {
        "id": END,
        "ieee_address": END,
        "friendly_name": "Kitchen bulb",
        "type": "zigbee_enddevice",
        "device_type": "EndDevice",
    }
    assert (await coordinator.import_zigbee_devices([dev], backend="zha"))["added"] == 1
    second = await coordinator.import_zigbee_devices([dev], backend="z2m")
    assert second == {"added": 0, "skipped": 1, "refreshed": 0}


async def test_trigger_zha_import_records_run(
    coordinator: HomelableCoordinator, hass: HomeAssistant
) -> None:
    hass.config.components.add("zha")
    nodes = [
        {
            "id": COORD,
            "ieee_address": COORD,
            "friendly_name": "Coordinator",
            "type": "zigbee_coordinator",
            "device_type": "Coordinator",
        }
    ]
    with patch.object(
        zha, "fetch_zha_network", new=AsyncMock(return_value=(nodes, []))
    ):
        res = await coordinator.trigger_zigbee_import()
        assert res["status"] == "running"
        assert res["backend"] == "zha"
        await hass.async_block_till_done(wait_background_tasks=True)

    runs = await coordinator.list_runs()
    # Reported under the shared Zigbee kind so Scan History needs no new filter.
    assert runs[0]["kind"] == "zigbee"
    assert runs[0]["status"] == "done"
    assert runs[0]["ranges"] == ["zha"]
    assert runs[0]["devices_found"] == 1


# ─── WebSocket ───────────────────────────────────────────────────────────────


@pytest.fixture
async def setup_ws(hass: HomeAssistant) -> HomelableCoordinator:
    coord = HomelableCoordinator(hass, _mock_entry())
    hass.data.setdefault(DOMAIN, {})[coord.entry.entry_id] = coord
    async_register_websocket_commands(hass)
    return coord


async def test_ws_zigbee_gateway(
    hass: HomeAssistant, hass_ws_client, hass_admin_user, setup_ws  # noqa: ANN001, ARG001
) -> None:
    hass.config.components.add("zha")
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "homelable/zigbee/gateway"})
    msg = await client.receive_json()
    assert msg["success"] is True
    assert msg["result"] == {
        "source": "auto",
        "resolved": "zha",
        "zha_detected": True,
    }


async def test_ws_zigbee_gateway_reflects_the_configured_switch(
    hass: HomeAssistant, hass_ws_client, hass_admin_user, setup_ws  # noqa: ANN001
) -> None:
    hass.config.components.add("zha")
    setup_ws.entry.options = {CONF_ZIGBEE_SOURCE: "z2m"}
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "homelable/zigbee/gateway"})
    msg = await client.receive_json()
    assert msg["result"]["source"] == "z2m"
    assert msg["result"]["resolved"] == "z2m"


async def test_ws_zigbee_import_uses_the_configured_source(
    hass: HomeAssistant, hass_ws_client, hass_admin_user, setup_ws  # noqa: ANN001
) -> None:
    """No `backend` on the wire — the options switch decides."""
    hass.config.components.add("zha")
    setup_ws.entry.options = {CONF_ZIGBEE_SOURCE: "zha"}
    with patch.object(zha, "_device_infos", return_value=[_info(END, "EndDevice")]):
        client = await hass_ws_client(hass)
        await client.send_json({"id": 1, "type": "homelable/zigbee/import"})
        msg = await client.receive_json()
        assert msg["result"]["backend"] == "zha"
        await hass.async_block_till_done(wait_background_tasks=True)
    pending = await setup_ws.list_pending(source="zigbee")
    assert pending[0]["discovery_source"] == "zha"


async def test_ws_zigbee_devices_via_zha(
    hass: HomeAssistant, hass_ws_client, hass_admin_user, setup_ws  # noqa: ANN001, ARG001
) -> None:
    hass.config.components.add("zha")
    infos = [_info(COORD, "Coordinator", neighbors=[_neighbor(END, "Child")]), _info(END, "EndDevice")]
    with patch.object(zha, "_device_infos", return_value=infos):
        client = await hass_ws_client(hass)
        await client.send_json({"id": 1, "type": "homelable/zigbee/devices"})
        msg = await client.receive_json()
    assert msg["success"] is True
    assert msg["result"]["backend"] == "zha"
    assert {n["id"] for n in msg["result"]["nodes"]} == {COORD, END}


async def test_ws_zigbee_devices_explicit_zha_without_zha(
    hass: HomeAssistant, hass_ws_client, hass_admin_user, setup_ws  # noqa: ANN001, ARG001
) -> None:
    client = await hass_ws_client(hass)
    await client.send_json(
        {"id": 1, "type": "homelable/zigbee/devices", "backend": "zha"}
    )
    msg = await client.receive_json()
    assert msg["success"] is False
    assert msg["error"]["code"] == "zha_not_configured"


async def test_ws_zigbee_devices_configured_zha_without_zha_errors_loudly(
    hass: HomeAssistant, hass_ws_client, hass_admin_user, setup_ws  # noqa: ANN001
) -> None:
    """Configured for ZHA but ZHA is gone: say so, never fall back to Z2M and
    burn the 300s networkmap timeout on a gateway the user didn't ask for."""
    setup_ws.entry.options = {CONF_ZIGBEE_SOURCE: "zha"}
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "homelable/zigbee/devices"})
    msg = await client.receive_json()
    assert msg["success"] is False
    assert msg["error"]["code"] == "zha_not_configured"


async def test_ws_zigbee_devices_rejects_unknown_backend(
    hass: HomeAssistant, hass_ws_client, hass_admin_user, setup_ws  # noqa: ANN001, ARG001
) -> None:
    client = await hass_ws_client(hass)
    await client.send_json(
        {"id": 1, "type": "homelable/zigbee/devices", "backend": "carrier_pigeon"}
    )
    msg = await client.receive_json()
    assert msg["success"] is False


async def test_ws_zigbee_import_via_zha_pushes_to_pending(
    hass: HomeAssistant, hass_ws_client, hass_admin_user, setup_ws  # noqa: ANN001, ARG001
) -> None:
    hass.config.components.add("zha")
    with patch.object(zha, "_device_infos", return_value=[_info(END, "EndDevice")]):
        client = await hass_ws_client(hass)
        await client.send_json(
            {"id": 1, "type": "homelable/zigbee/import", "backend": "zha"}
        )
        msg = await client.receive_json()
        assert msg["success"] is True
        assert msg["result"]["backend"] == "zha"
        await hass.async_block_till_done(wait_background_tasks=True)

    pending = await setup_ws.list_pending(source="zigbee")
    assert len(pending) == 1
    assert pending[0]["ieee_address"] == END
    assert pending[0]["discovery_source"] == "zha"
