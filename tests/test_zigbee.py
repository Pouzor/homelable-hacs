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
    assert result == {"added": 1, "skipped": 0}
    pending = await coordinator.list_pending(source="zigbee")
    assert len(pending) == 1
    # Flattened on the wire.
    assert pending[0]["ieee_address"] == "0xC"
    assert pending[0]["friendly_name"] == "Coord"
    assert pending[0]["suggested_type"] == "zigbee_coordinator"
    assert pending[0]["source"] == "zigbee"


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
    assert result == {"added": 0, "skipped": 1}


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
    client = await hass_ws_client(hass)
    await client.send_json(
        {
            "id": 1,
            "type": "homelable/zigbee/import",
            "devices": [
                {
                    "id": "0xC",
                    "ieee_address": "0xC",
                    "friendly_name": "Coord",
                    "type": "zigbee_coordinator",
                    "device_type": "Coordinator",
                }
            ],
        }
    )
    msg = await client.receive_json()
    assert msg["success"] is True
    assert msg["result"] == {"added": 1, "skipped": 0}


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
