"""Tests for the HomelableCoordinator."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.homelable.coordinator import (
    HomelableCoordinator,
    build_mac_property,
    merge_mac_property,
)


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
            "discovery_source": "tcp",
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

    async def _fake(ranges, run_id=None, *, exclude_ips=None, on_event=None, **_kw):
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
async def test_streaming_events_create_then_enrich_pending(coord) -> None:  # noqa: ANN001
    """device_discovered → 'discovering' entry; device_enriched → 'pending' with services."""
    captured_events: list[dict] = []

    from homeassistant.helpers.dispatcher import async_dispatcher_connect

    from custom_components.homelable.const import SCAN_SIGNAL

    unsub = async_dispatcher_connect(
        coord.hass, SCAN_SIGNAL, lambda p: captured_events.append(p)
    )

    async def _fake(ranges, run_id=None, *, exclude_ips=None, on_event=None, **_kw):
        await on_event(
            {
                "event": "device_discovered",
                "device": {"ip": "10.0.0.7", "mac": None, "hostname": None},
            }
        )
        # Mid-scan: pending must already reflect the discovery.
        mid = await coord.list_pending(status="discovering")
        assert any(d["ip"] == "10.0.0.7" for d in mid)
        await on_event(
            {
                "event": "device_enriched",
                "device": {
                    "ip": "10.0.0.7",
                    "mac": "AA:BB:CC:DD:EE:FF",
                    "hostname": "host.lan",
                    "os": None,
                    "open_ports": [{"port": 22, "protocol": "tcp", "banner": "OpenSSH"}],
                    "services": [{"port": 22, "name": "ssh"}],
                    "suggested_type": "generic",
                    "discovery_source": "tcp",
                },
            }
        )
        return [
            {
                "ip": "10.0.0.7",
                "mac": "AA:BB:CC:DD:EE:FF",
                "hostname": "host.lan",
                "os": None,
                "open_ports": [{"port": 22, "protocol": "tcp", "banner": "OpenSSH"}],
                "services": [{"port": 22, "name": "ssh"}],
                "suggested_type": "generic",
                "discovery_source": "tcp",
            }
        ]

    with patch(
        "custom_components.homelable.coordinator.scanner.run_scan", _fake
    ):
        await coord.trigger_scan()
        await coord.hass.async_block_till_done()

    unsub()

    pending = await coord.list_pending()
    assert len(pending) == 1
    assert pending[0]["ip"] == "10.0.0.7"
    assert pending[0]["status"] == "pending"
    assert pending[0]["mac"] == "AA:BB:CC:DD:EE:FF"
    assert pending[0]["services"] == [{"port": 22, "name": "ssh"}]

    discovered = [e for e in captured_events if e.get("event") == "device_discovered"]
    enriched = [e for e in captured_events if e.get("event") == "device_enriched"]
    finished = [e for e in captured_events if e.get("event") == "scan_finished"]
    assert len(discovered) == 1
    assert len(enriched) == 1
    assert len(finished) == 1
    assert enriched[0]["device"]["ip"] == "10.0.0.7"
    # Coordinator echoes the stored device id back so the frontend can reconcile.
    assert enriched[0]["device"]["id"] == pending[0]["id"]


@pytest.mark.asyncio
async def test_streaming_discovering_entry_dropped_when_never_enriched(coord) -> None:  # noqa: ANN001
    """If a host was discovered but never enriched (e.g. cancelled), drop it at scan end."""
    async def _fake(ranges, run_id=None, *, exclude_ips=None, on_event=None, **_kw):
        await on_event(
            {
                "event": "device_discovered",
                "device": {"ip": "10.0.0.99"},
            }
        )
        return []  # No enrichment

    with patch(
        "custom_components.homelable.coordinator.scanner.run_scan", _fake
    ):
        await coord.trigger_scan()
        await coord.hass.async_block_till_done()

    pending = await coord.list_pending(status="discovering")
    assert pending == []
    pending_done = await coord.list_pending()
    assert pending_done == []


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
    # Nodes are stored FLAT (top-level fields), matching what the frontend
    # serializes on Save and reads back via deserializeApiNode. A nested
    # {position, data:{...}} node would deserialize to empty (no ip/services)
    # on the next reload. See approve_pending.
    assert "data" not in node
    assert node["ip"] == "10.0.0.5"
    assert node["hostname"] == "myhost"
    assert node["services"] == []
    assert node["label"] == "myhost"
    assert node["status"] == "unknown"
    assert node["pos_x"] == 100
    assert node["pos_y"] == 50

    canvas = await coord.get_canvas()
    assert len(canvas["nodes"]) == 1
    # the persisted node carries ip/services flat, so a frontend reload sees them
    stored = canvas["nodes"][0]
    assert stored["ip"] == "10.0.0.5"
    assert stored["services"] == []
    assert stored["pos_x"] == 100
    # device removed from pending
    assert await coord.list_pending() == []


@pytest.mark.asyncio
async def test_approve_pending_node_has_services_flat(coord) -> None:  # noqa: ANN001
    """Regression: approved scan devices must keep ip + services on the node.

    Previously approve_pending emitted a nested {position, data:{...}} node;
    the frontend deserializer reads flat top-level keys, so on the next reload
    ip/services were undefined and the node rendered empty (issue homelable#164).
    """
    pending = await coord._get_pending()
    pending["devices"].append(
        {
            "id": "pd-svc",
            "ip": "192.168.1.20",
            "mac": "aa:bb:cc:dd:ee:ff",
            "hostname": "nas",
            "os": "linux",
            "services": [{"name": "http", "port": 80}],
            "suggested_type": "nas",
            "status": "pending",
        }
    )
    await coord._save_pending()

    node = await coord.approve_pending("pd-svc")

    assert node["ip"] == "192.168.1.20"
    assert node["mac"] == "aa:bb:cc:dd:ee:ff"
    assert node["os"] == "linux"
    assert node["services"] == [{"name": "http", "port": 80}]
    assert node["check_method"] == "ping"


@pytest.mark.asyncio
async def test_approve_zigbee_child_links_to_flat_parent(coord) -> None:  # noqa: ANN001
    """Approving a zigbee child should link to an already-approved (flat) parent.

    Both parent and child are stored flat (ieee_address / parent_id top-level),
    so _create_zigbee_parent_edge must match on the flat shape.
    """
    # Parent already approved → flat node on the canvas.
    pending = await coord._get_pending()
    pending["devices"].append(
        {
            "id": "pd-parent",
            "ip": None,
            "mac": None,
            "hostname": "Router",
            "os": None,
            "services": [],
            "suggested_type": "zigbee_router",
            "source": "zigbee",
            "status": "pending",
            "data_extras": {"ieee_address": "0xR1", "parent_id": None},
        }
    )
    await coord._save_pending()
    parent_node = await coord.approve_pending("pd-parent")
    assert parent_node["ieee_address"] == "0xR1"
    assert "data" not in parent_node

    # Child references the parent by ieee_address.
    pending = await coord._get_pending()
    pending["devices"].append(
        {
            "id": "pd-child",
            "ip": None,
            "mac": None,
            "hostname": "Bulb",
            "os": None,
            "services": [],
            "suggested_type": "zigbee_router",
            "source": "zigbee",
            "status": "pending",
            "data_extras": {"ieee_address": "0xC1", "parent_id": "0xR1"},
        }
    )
    await coord._save_pending()
    child_node = await coord.approve_pending("pd-child")

    edge = await coord._create_zigbee_parent_edge(child_node)
    assert edge is not None
    assert edge["source"] == parent_node["id"]
    assert edge["target"] == child_node["id"]


@pytest.mark.asyncio
async def test_approve_zigbee_attaches_hidden_properties_and_online(coord) -> None:  # noqa: ANN001
    """Approving a zigbee device builds hidden IEEE/Vendor/Model/LQI props,
    lands the node online, and sets check_method to none."""
    pending = await coord._get_pending()
    pending["devices"].append(
        {
            "id": "pd-zb",
            "ip": None,
            "mac": None,
            "hostname": "Sensor",
            "os": None,
            "services": [],
            "suggested_type": "zigbee_enddevice",
            "source": "zigbee",
            "status": "pending",
            "data_extras": {
                "ieee_address": "0xS1",
                "vendor": "Aqara",
                "model": "WSDCGQ11LM",
                "lqi": 180,
                "parent_id": None,
            },
        }
    )
    await coord._save_pending()
    node = await coord.approve_pending("pd-zb")

    assert node["status"] == "online"
    assert node["check_method"] == "none"
    by_key = {p["key"]: p for p in node["properties"]}
    assert set(by_key) == {"IEEE", "Vendor", "Model", "LQI"}
    assert all(p["visible"] is False for p in node["properties"])
    assert by_key["LQI"]["value"] == "180"


@pytest.mark.asyncio
async def test_approve_non_zigbee_has_empty_properties(coord) -> None:  # noqa: ANN001
    pending = await coord._get_pending()
    pending["devices"].append(
        {
            "id": "pd-ip",
            "ip": "192.168.1.5",
            "mac": None,
            "hostname": "nas",
            "os": None,
            "services": [],
            "suggested_type": "generic",
            "status": "pending",
        }
    )
    await coord._save_pending()
    node = await coord.approve_pending("pd-ip")
    assert node["properties"] == []
    assert node["status"] == "unknown"
    assert node["check_method"] == "ping"


# --- MAC address propagation on approve (issue #168) ---

def test_build_mac_property_returns_hidden_row() -> None:
    assert build_mac_property("aa:bb:cc:dd:ee:ff") == [
        {"key": "MAC", "value": "aa:bb:cc:dd:ee:ff", "icon": None, "visible": False}
    ]


def test_build_mac_property_empty_when_no_mac() -> None:
    assert build_mac_property(None) == []
    assert build_mac_property("") == []


def test_merge_mac_property_appends_when_absent() -> None:
    existing = [{"key": "Note", "value": "x", "icon": None, "visible": True}]
    merged = merge_mac_property(existing, "aa:bb:cc:dd:ee:ff")
    assert merged == [
        {"key": "Note", "value": "x", "icon": None, "visible": True},
        {"key": "MAC", "value": "aa:bb:cc:dd:ee:ff", "icon": None, "visible": False},
    ]
    # Source list left untouched.
    assert existing == [{"key": "Note", "value": "x", "icon": None, "visible": True}]


def test_merge_mac_property_keeps_existing_mac_row() -> None:
    existing = [{"key": "MAC", "value": "old", "icon": None, "visible": True}]
    assert merge_mac_property(existing, "aa:bb:cc:dd:ee:ff") == existing


@pytest.mark.asyncio
async def test_approve_non_zigbee_carries_scanned_mac(coord) -> None:  # noqa: ANN001
    """A non-zigbee device with a scanned MAC lands a hidden MAC property row."""
    pending = await coord._get_pending()
    pending["devices"].append(
        {
            "id": "pd-mac",
            "ip": "192.168.1.6",
            "mac": "aa:bb:cc:dd:ee:ff",
            "hostname": "printer",
            "os": None,
            "services": [],
            "suggested_type": "generic",
            "status": "pending",
        }
    )
    await coord._save_pending()
    node = await coord.approve_pending("pd-mac")
    assert node["mac"] == "aa:bb:cc:dd:ee:ff"
    assert node["properties"] == [
        {"key": "MAC", "value": "aa:bb:cc:dd:ee:ff", "icon": None, "visible": False}
    ]


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

    async def _fake_check(method, target, ip, **_kw):
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
            "discovery_source": "tcp",
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
