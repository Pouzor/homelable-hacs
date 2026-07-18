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
    saved = (await coord.get_canvas())["nodes"]
    assert len(saved) == 1
    assert saved[0]["id"] == "n1"
    # A newly saved node is stamped with lifecycle timestamps.
    assert saved[0]["created_at"] == saved[0]["updated_at"]
    assert saved[0]["last_scan"] is None
    assert saved[0]["last_seen"] is None


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
async def test_trigger_scan_excludes_hidden_but_keeps_canvas(coord) -> None:  # noqa: ANN001
    """Device Inventory: on-canvas devices are NOT excluded (they stay listed and
    badged); only user-hidden devices are suppressed."""
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

    # Canvas IP stays scannable; only the hidden IP is excluded.
    assert "192.168.1.1" not in captured["exclude"]
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
    # MAC is canonicalized (lowercase, ':'-separated) on write so cross-source
    # dedup can match an ARP scan against a Proxmox import by a plain ==.
    assert pending[0]["mac"] == "aa:bb:cc:dd:ee:ff"
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
    # Device Inventory: the device is NOT removed — it stays listed as "approved"
    # and is badged with the number of canvases it appears on.
    inventory = await coord.list_pending()
    assert len(inventory) == 1
    assert inventory[0]["id"] == "pd-1"
    assert inventory[0]["status"] == "approved"
    assert inventory[0]["canvas_count"] == 1


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
    so _create_wireless_parent_edge must match on the flat shape.
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

    edge = await coord._create_wireless_parent_edge(child_node)
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
async def test_async_update_data_runs_checks_concurrently(coord) -> None:  # noqa: ANN001
    """Regression for #51: node checks must run in parallel, not serially.

    Each fake check blocks until every check has started. A sequential loop
    would deadlock (later checks never start), tripping the timeout below.
    """
    import asyncio

    nodes = [
        {"id": f"n{i}", "type": "server", "position": {"x": 0, "y": 0},
         "data": {"ip": f"10.0.0.{i}", "check_method": "ping"}}
        for i in range(5)
    ]
    await coord.save_canvas(
        {"nodes": nodes, "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}}
    )

    started = 0
    all_started = asyncio.Event()

    async def _fake_check(method, target, ip, **_kw):
        nonlocal started
        started += 1
        if started == len(nodes):
            all_started.set()
        await all_started.wait()
        return {"status": "online", "response_time_ms": 5}

    with patch(
        "custom_components.homelable.coordinator.status_checker.check_node",
        _fake_check,
    ):
        result = await asyncio.wait_for(coord._async_update_data(), timeout=5)

    assert len(result) == len(nodes)
    assert all(r["status"] == "online" for r in result.values())


@pytest.mark.asyncio
async def test_async_update_data_isolates_check_exceptions(coord) -> None:  # noqa: ANN001
    """A single failing check must not sink the rest of the batch (#51)."""
    await coord.save_canvas(
        {
            "nodes": [
                {"id": "ok", "type": "server", "position": {"x": 0, "y": 0},
                 "data": {"ip": "10.0.0.1", "check_method": "ping"}},
                {"id": "boom", "type": "server", "position": {"x": 0, "y": 0},
                 "data": {"ip": "10.0.0.2", "check_method": "ping"}},
            ],
            "edges": [],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
        }
    )

    async def _fake_check(method, target, ip, **_kw):
        if ip == "10.0.0.2":
            raise RuntimeError("ping blew up")
        return {"status": "online", "response_time_ms": 5}

    with patch(
        "custom_components.homelable.coordinator.status_checker.check_node",
        _fake_check,
    ):
        result = await coord._async_update_data()

    assert result["ok"]["status"] == "online"
    assert result["boom"]["status"] == "unknown"


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
    assert devices[0]["mac"] == "aa:bb:cc:11:22:33"  # canonicalized on write
    assert devices[0]["hostname"] == "now-known.lan"


# --- Per-service status checks (#196 follow-up) ---


@pytest.mark.asyncio
async def test_service_check_getters_default_disabled(coord) -> None:  # noqa: ANN001
    assert coord.get_service_check_enabled() is False
    assert coord.get_service_check_interval() == 300


@pytest.mark.asyncio
async def test_service_check_interval_floored_at_minimum(hass) -> None:  # noqa: ANN001
    entry = _mock_entry()
    entry.options = {"service_check_enabled": True, "service_check_interval": 5}
    c = HomelableCoordinator(hass, entry)
    assert c.get_service_check_enabled() is True
    assert c.get_service_check_interval() == 30  # MIN_SERVICE_CHECK_INTERVAL


@pytest.mark.asyncio
async def test_start_service_checks_noop_when_disabled(coord) -> None:  # noqa: ANN001
    coord.async_start_service_checks()
    assert coord._service_check_unsub is None


@pytest.mark.asyncio
async def test_start_stop_service_checks_when_enabled(hass) -> None:  # noqa: ANN001
    entry = _mock_entry()
    entry.options = {"service_check_enabled": True}
    c = HomelableCoordinator(hass, entry)
    c.async_start_service_checks()
    assert c._service_check_unsub is not None
    c.async_stop_service_checks()
    assert c._service_check_unsub is None


@pytest.mark.asyncio
async def test_run_service_checks_dispatches_per_node(hass) -> None:  # noqa: ANN001
    from homeassistant.helpers.dispatcher import async_dispatcher_connect

    from custom_components.homelable.const import SERVICE_STATUS_SIGNAL

    entry = _mock_entry()
    entry.options = {"service_check_enabled": True}
    c = HomelableCoordinator(hass, entry)
    await c.save_canvas(
        {
            "nodes": [
                {
                    "id": "n1",
                    "ip": "192.168.1.5",
                    "services": [{"port": 80, "protocol": "tcp", "service_name": "http"}],
                },
                {"id": "n2", "ip": "192.168.1.6", "services": []},  # skipped: no services
            ],
            "edges": [],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
        }
    )

    received: list[dict] = []
    unsub = async_dispatcher_connect(
        hass, SERVICE_STATUS_SIGNAL, lambda p: received.append(p)
    )
    with patch(
        "custom_components.homelable.coordinator.status_checker.check_services",
        AsyncMock(return_value=[{"port": 80, "protocol": "tcp", "status": "offline"}]),
    ) as mock:
        await c._run_service_checks()
        await hass.async_block_till_done()
    unsub()

    assert mock.called
    # Only the node with services is checked / dispatched.
    assert len(received) == 1
    assert received[0]["node_id"] == "n1"
    assert received[0]["services"] == [
        {"port": 80, "protocol": "tcp", "status": "offline"}
    ]
    # host derived from the node's first IP.
    assert mock.call_args.args[0] == "192.168.1.5"


# ── Device Inventory: canvas_count + approved retention + deep scan ────────────

@pytest.mark.asyncio
async def test_list_pending_inventory_includes_approved_with_canvas_count(coord) -> None:  # noqa: ANN001
    """The inventory view returns approved devices badged with their canvas count."""
    await coord.save_canvas(
        {
            "nodes": [
                {"id": "n1", "type": "server", "position": {"x": 0, "y": 0},
                 "ip": "10.0.0.5"}
            ],
            "edges": [],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
        }
    )
    pending = await coord._get_pending()
    pending["devices"].append(
        {"id": "pd-1", "ip": "10.0.0.5", "status": "approved", "services": []}
    )
    pending["devices"].append(
        {"id": "pd-2", "ip": "10.0.0.9", "status": "pending", "services": []}
    )
    await coord._save_pending()

    inventory = await coord.list_pending()
    by_id = {d["id"]: d for d in inventory}
    assert set(by_id) == {"pd-1", "pd-2"}
    assert by_id["pd-1"]["canvas_count"] == 1  # on the canvas
    assert by_id["pd-2"]["canvas_count"] == 0  # not on any canvas


@pytest.mark.asyncio
async def test_list_pending_canvas_count_matches_by_ieee(coord) -> None:  # noqa: ANN001
    """Zigbee devices correlate by ieee_address, not IP."""
    await coord.save_canvas(
        {
            "nodes": [
                {"id": "zb1", "type": "zigbee_router", "position": {"x": 0, "y": 0},
                 "ieee_address": "0x00124b0012345678"}
            ],
            "edges": [],
            "viewport": {"x": 0, "y": 0, "zoom": 1},
        }
    )
    pending = await coord._get_pending()
    pending["devices"].append(
        {
            "id": "pd-zb",
            "ip": None,
            "status": "approved",
            "services": [],
            "data_extras": {"ieee_address": "0x00124b0012345678"},
        }
    )
    await coord._save_pending()

    inventory = await coord.list_pending()
    assert inventory[0]["canvas_count"] == 1


@pytest.mark.asyncio
async def test_list_pending_does_not_persist_canvas_count(coord) -> None:  # noqa: ANN001
    """canvas_count is transient — it never leaks back into the store."""
    pending = await coord._get_pending()
    pending["devices"].append(
        {"id": "pd-1", "ip": "10.0.0.9", "status": "pending", "services": []}
    )
    await coord._save_pending()

    await coord.list_pending()
    store = await coord._get_pending()
    assert "canvas_count" not in store["devices"][0]


@pytest.mark.asyncio
async def test_trigger_scan_forwards_deep_scan_options(coord) -> None:  # noqa: ANN001
    """Per-scan deep-scan options reach scanner.run_scan as a DeepScanOptions."""
    captured: dict = {}

    async def _fake(ranges, run_id=None, *, exclude_ips=None, on_event=None,
                    hass=None, deep_scan=None, **_kw):
        captured["deep_scan"] = deep_scan
        return []

    with patch(
        "custom_components.homelable.coordinator.scanner.run_scan", _fake
    ):
        await coord.trigger_scan(
            http_ranges=["8000-8100"], http_probe_enabled=True, verify_tls=True
        )
        await coord.hass.async_block_till_done()

    deep = captured["deep_scan"]
    assert deep is not None
    assert deep.http_ranges == ["8000-8100"]
    assert deep.http_probe_enabled is True
    assert deep.verify_tls is True


# ─── Inventory timestamps (port of homelable#233) ────────────────────────────

def _scan_device(ip: str, mac: str | None = None) -> dict:
    return {
        "ip": ip,
        "mac": mac,
        "hostname": None,
        "os": None,
        "open_ports": [],
        "services": [],
        "suggested_type": None,
        "discovery_source": "arp",
    }


@pytest.mark.asyncio
async def test_approve_stamps_created_and_updated(coord) -> None:  # noqa: ANN001
    pending = await coord._get_pending()
    pending["devices"].append({"id": "pd-1", "ip": "10.0.0.5", "status": "pending"})
    await coord._save_pending()

    node = await coord.approve_pending("pd-1")

    assert node["created_at"] == node["updated_at"]
    assert node["last_scan"] is None
    assert node["last_seen"] is None


@pytest.mark.asyncio
async def test_save_canvas_preserves_created_bumps_updated_on_change(coord) -> None:  # noqa: ANN001
    await coord.save_canvas(
        {"nodes": [{"id": "n1", "label": "A"}], "edges": [], "viewport": {}}
    )
    first = (await coord.get_canvas())["nodes"][0]
    created, updated = first["created_at"], first["updated_at"]

    # Re-save identical content: created_at kept, updated_at must not move.
    await coord.save_canvas(
        {"nodes": [{"id": "n1", "label": "A"}], "edges": [], "viewport": {}}
    )
    same = (await coord.get_canvas())["nodes"][0]
    assert same["created_at"] == created
    assert same["updated_at"] == updated

    # Re-save changed content: created_at preserved, updated_at bumps.
    await coord.save_canvas(
        {"nodes": [{"id": "n1", "label": "B"}], "edges": [], "viewport": {}}
    )
    changed = (await coord.get_canvas())["nodes"][0]
    assert changed["created_at"] == created
    assert changed["updated_at"] != updated


@pytest.mark.asyncio
async def test_save_canvas_ignores_frontend_supplied_timestamps(coord) -> None:  # noqa: ANN001
    """The Store stays authoritative — a frontend round-trip can't clobber the
    server-managed timestamps."""
    await coord.save_canvas(
        {"nodes": [{"id": "n1", "label": "A"}], "edges": [], "viewport": {}}
    )
    created = (await coord.get_canvas())["nodes"][0]["created_at"]

    await coord.save_canvas(
        {
            "nodes": [
                {
                    "id": "n1",
                    "label": "A",
                    "created_at": "1999-01-01T00:00:00Z",
                    "last_scan": "1999-01-01T00:00:00Z",
                    "last_seen": "1999-01-01T00:00:00Z",
                }
            ],
            "edges": [],
            "viewport": {},
        }
    )
    node = (await coord.get_canvas())["nodes"][0]
    assert node["created_at"] == created
    assert node["last_scan"] is None
    assert node["last_seen"] is None


@pytest.mark.asyncio
async def test_scan_stamps_last_scan_by_ip(coord) -> None:  # noqa: ANN001
    await coord.save_canvas(
        {"nodes": [{"id": "n1", "ip": "192.168.1.5"}], "edges": [], "viewport": {}}
    )
    with patch(
        "custom_components.homelable.coordinator.scanner.run_scan",
        AsyncMock(return_value=[_scan_device("192.168.1.5")]),
    ):
        await coord.trigger_scan()
        await coord.hass.async_block_till_done()

    assert (await coord.get_canvas())["nodes"][0]["last_scan"] is not None


@pytest.mark.asyncio
async def test_scan_stamps_last_scan_by_mac(coord) -> None:  # noqa: ANN001
    # Node has no ip but a matching mac.
    await coord.save_canvas(
        {"nodes": [{"id": "n1", "mac": "AA:BB:CC:DD:EE:FF"}], "edges": [], "viewport": {}}
    )
    with patch(
        "custom_components.homelable.coordinator.scanner.run_scan",
        AsyncMock(return_value=[_scan_device("192.168.1.9", mac="AA:BB:CC:DD:EE:FF")]),
    ):
        await coord.trigger_scan()
        await coord.hass.async_block_till_done()

    assert (await coord.get_canvas())["nodes"][0]["last_scan"] is not None


@pytest.mark.asyncio
async def test_scan_leaves_last_scan_untouched_on_unmatched_node(coord) -> None:  # noqa: ANN001
    await coord.save_canvas(
        {"nodes": [{"id": "n1", "ip": "10.0.0.99"}], "edges": [], "viewport": {}}
    )
    with patch(
        "custom_components.homelable.coordinator.scanner.run_scan",
        AsyncMock(return_value=[_scan_device("192.168.1.5")]),
    ):
        await coord.trigger_scan()
        await coord.hass.async_block_till_done()

    assert (await coord.get_canvas())["nodes"][0]["last_scan"] is None


@pytest.mark.asyncio
async def test_status_refresh_stamps_last_seen_when_online(coord) -> None:  # noqa: ANN001
    await coord.save_canvas(
        {
            "nodes": [{"id": "n1", "ip": "10.0.0.5", "check_method": "ping"}],
            "edges": [],
            "viewport": {},
        }
    )
    with patch(
        "custom_components.homelable.coordinator.status_checker.check_node",
        AsyncMock(return_value={"status": "online", "response_time_ms": 5}),
    ):
        await coord._async_update_data()
    assert (await coord.get_canvas())["nodes"][0]["last_seen"] is not None


@pytest.mark.asyncio
async def test_status_refresh_leaves_last_seen_when_offline(coord) -> None:  # noqa: ANN001
    await coord.save_canvas(
        {
            "nodes": [{"id": "n1", "ip": "10.0.0.5", "check_method": "ping"}],
            "edges": [],
            "viewport": {},
        }
    )
    with patch(
        "custom_components.homelable.coordinator.status_checker.check_node",
        AsyncMock(return_value={"status": "offline", "response_time_ms": None}),
    ):
        await coord._async_update_data()
    assert (await coord.get_canvas())["nodes"][0]["last_seen"] is None


@pytest.mark.asyncio
async def test_list_pending_node_timestamps_null_without_node(coord) -> None:  # noqa: ANN001
    pending = await coord._get_pending()
    pending["devices"].append({"id": "pd-1", "ip": "192.168.1.100", "status": "pending"})
    await coord._save_pending()

    d = (await coord.list_pending())[0]
    assert d["node_created_at"] is None
    assert d["node_last_scan"] is None
    assert d["node_last_modified"] is None
    assert d["node_last_seen"] is None


@pytest.mark.asyncio
async def test_list_pending_aggregates_node_timestamps_across_matches(coord) -> None:  # noqa: ANN001
    """Two canvas nodes share the device ip: created = oldest, last_scan = newest."""
    pending = await coord._get_pending()
    pending["devices"].append({"id": "pd-1", "ip": "192.168.1.100", "status": "approved"})
    await coord._save_pending()

    await coord._ensure_loaded()
    default = coord._designs[0]["id"]
    d2 = await coord.create_design("Lab")
    # Seed nodes directly to control the timestamps precisely.
    coord._canvases[default]["nodes"] = [
        {
            "id": "a", "ip": "192.168.1.100",
            "created_at": "2026-01-01T00:00:00Z", "last_scan": "2026-03-01T00:00:00Z",
            "updated_at": "2026-03-01T00:00:00Z", "last_seen": None,
        }
    ]
    coord._canvases[d2["id"]]["nodes"] = [
        {
            "id": "b", "ip": "192.168.1.100",
            "created_at": "2026-05-01T00:00:00Z", "last_scan": "2026-06-01T00:00:00Z",
            "updated_at": "2026-06-01T00:00:00Z", "last_seen": None,
        }
    ]

    d = (await coord.list_pending())[0]
    assert d["canvas_count"] == 2
    assert d["node_created_at"].startswith("2026-01-01")  # oldest
    assert d["node_last_scan"].startswith("2026-06-01")   # newest
    assert d["node_last_modified"].startswith("2026-06-01")  # newest


@pytest.mark.asyncio
async def test_approve_batch_places_already_approved_on_another_design(coord) -> None:  # noqa: ANN001
    pending = await coord._get_pending()
    pending["devices"] += [
        {"id": "pd-1", "ip": "192.168.1.10", "status": "pending"},
        {"id": "pd-2", "ip": "192.168.1.11", "status": "pending"},
    ]
    await coord._save_pending()
    default = (await coord.list_designs())[0]
    d2 = await coord.create_design("B")

    r1 = await coord.approve_batch(["pd-1", "pd-2"], {"design_id": default["id"]})
    assert r1["approved"] == 2

    # Re-approve the same (now approved) devices onto design B — must place.
    r2 = await coord.approve_batch(["pd-1", "pd-2"], {"design_id": d2["id"]})
    assert r2["approved"] == 2
    assert r2["skipped"] == []
    assert len((await coord.get_canvas(d2["id"]))["nodes"]) == 2


@pytest.mark.asyncio
async def test_approve_batch_skips_device_already_on_target(coord) -> None:  # noqa: ANN001
    pending = await coord._get_pending()
    pending["devices"] += [
        {"id": "pd-1", "ip": "192.168.1.10", "status": "pending"},
        {"id": "pd-2", "ip": "192.168.1.11", "status": "pending"},
    ]
    await coord._save_pending()
    default = (await coord.list_designs())[0]
    await coord.approve_pending("pd-1", {"design_id": default["id"]})

    r = await coord.approve_batch(["pd-1", "pd-2"], {"design_id": default["id"]})
    assert r["approved"] == 1
    assert r["skipped"] == ["pd-1"]
    ips = sorted(n["ip"] for n in (await coord.get_canvas(default["id"]))["nodes"])
    assert ips == ["192.168.1.10", "192.168.1.11"]


# ─── Duplicate prompt on approve (homelable #260 / #261) ─────────────────────

@pytest.mark.asyncio
async def test_approve_pending_conflicts_on_existing_ip(coord) -> None:  # noqa: ANN001
    """A host whose ip already sits on the target design is NOT silently
    duplicated: approve returns the conflict + existing node so the UI can ask."""
    pending = await coord._get_pending()
    pending["devices"] += [
        {"id": "pd-1", "ip": "192.168.1.100", "status": "pending", "suggested_type": "server"},
        {"id": "pd-2", "ip": "192.168.1.100", "status": "pending", "suggested_type": "server"},
    ]
    await coord._save_pending()
    first = await coord.approve_pending("pd-1")

    res = await coord.approve_pending("pd-2")
    assert "duplicate" in res
    conflict = res["duplicate"]
    assert conflict["match"] == "ip"
    assert conflict["value"] == "192.168.1.100"
    assert conflict["existing_node_id"] == first["id"]
    # No second node created; device stays pending until the user decides.
    assert len((await coord.get_canvas())["nodes"]) == 1


@pytest.mark.asyncio
async def test_approve_pending_conflicts_on_existing_mac(coord) -> None:  # noqa: ANN001
    """MAC match (device re-IP'd via DHCP) also triggers the duplicate guard."""
    pending = await coord._get_pending()
    pending["devices"] += [
        {"id": "pd-1", "ip": "10.0.0.9", "mac": "aa:bb:cc:dd:ee:ff", "status": "pending"},
        {"id": "pd-2", "ip": "192.168.1.55", "mac": "aa:bb:cc:dd:ee:ff", "status": "pending"},
    ]
    await coord._save_pending()
    first = await coord.approve_pending("pd-1")

    res = await coord.approve_pending("pd-2")
    assert res["duplicate"]["match"] == "mac"
    assert res["duplicate"]["existing_node_id"] == first["id"]


@pytest.mark.asyncio
async def test_approve_pending_conflicts_on_existing_ieee(coord) -> None:  # noqa: ANN001
    """IEEE (Zigbee/Z-Wave) uses the same prompt as ip/mac, not an auto-merge."""
    pending = await coord._get_pending()
    for did in ("pd-1", "pd-2"):
        pending["devices"].append({
            "id": did, "ip": None, "mac": None, "status": "pending",
            "suggested_type": "zigbee_router", "source": "zigbee",
            "data_extras": {"ieee_address": "0xZZZ", "parent_id": None},
        })
    await coord._save_pending()
    first = await coord.approve_pending("pd-1")

    res = await coord.approve_pending("pd-2")
    assert res["duplicate"]["match"] == "ieee"
    assert res["duplicate"]["value"] == "0xZZZ"
    assert res["duplicate"]["existing_node_id"] == first["id"]
    assert len((await coord.get_canvas())["nodes"]) == 1


@pytest.mark.asyncio
async def test_approve_pending_force_creates_duplicate(coord) -> None:  # noqa: ANN001
    """force=True lets the user place a second card for the same host."""
    pending = await coord._get_pending()
    pending["devices"] += [
        {"id": "pd-1", "ip": "192.168.1.100", "status": "pending"},
        {"id": "pd-2", "ip": "192.168.1.100", "status": "pending"},
    ]
    await coord._save_pending()
    await coord.approve_pending("pd-1")

    node = await coord.approve_pending("pd-2", {"force": True})
    assert "duplicate" not in node
    assert len((await coord.get_canvas())["nodes"]) == 2


@pytest.mark.asyncio
async def test_approve_pending_same_device_other_design_places(coord) -> None:  # noqa: ANN001
    """Canvas membership is per-design: a device already on one canvas can be
    approved onto another (one node per design)."""
    pending = await coord._get_pending()
    pending["devices"].append({"id": "pd-1", "ip": "192.168.1.100", "status": "pending"})
    await coord._save_pending()
    default = (await coord.list_designs())[0]
    other = await coord.create_design("Other")

    await coord.approve_pending("pd-1", {"design_id": default["id"]})
    node = await coord.approve_pending("pd-1", {"design_id": other["id"]})
    assert "duplicate" not in node
    assert len((await coord.get_canvas(other["id"]))["nodes"]) == 1


@pytest.mark.asyncio
async def test_approve_pending_conflicts_on_ip_in_comma_list(coord) -> None:  # noqa: ANN001
    """The existing node's ip holds an IPv6 before the IPv4 the device scanned
    as. Per-token matching still catches the duplicate (issue #258)."""
    pending = await coord._get_pending()
    pending["devices"] += [
        {"id": "pd-1", "ip": "fe80::1, 192.168.1.100", "status": "pending"},
        {"id": "pd-2", "ip": "192.168.1.100", "status": "pending"},
    ]
    await coord._save_pending()
    await coord.approve_pending("pd-1")

    res = await coord.approve_pending("pd-2")
    assert res["duplicate"]["match"] == "ip"
    assert res["duplicate"]["value"] == "192.168.1.100"


@pytest.mark.asyncio
async def test_approve_pending_no_conflict_on_ip_substring(coord) -> None:  # noqa: ANN001
    """The ip guard matches whole addresses, not substrings: 10.0.0.40 is not a
    duplicate of 10.0.0.4 (issue #258)."""
    pending = await coord._get_pending()
    pending["devices"] += [
        {"id": "pd-1", "ip": "10.0.0.40", "status": "pending"},
        {"id": "pd-2", "ip": "10.0.0.4", "status": "pending"},
    ]
    await coord._save_pending()
    await coord.approve_pending("pd-1")

    node = await coord.approve_pending("pd-2")
    assert "duplicate" not in node
    assert len((await coord.get_canvas())["nodes"]) == 2


@pytest.mark.asyncio
async def test_list_pending_canvas_count_matches_ip_in_comma_list(coord) -> None:  # noqa: ANN001
    """A node's ip holds several comma-separated addresses (IPv6 added first);
    the device scanned as the plain IPv4 must still correlate (issue #258)."""
    pending = await coord._get_pending()
    pending["devices"].append({"id": "pd-1", "ip": "192.168.1.100", "status": "approved"})
    await coord._save_pending()
    await coord._ensure_loaded()
    default = coord._designs[0]["id"]
    coord._canvases[default]["nodes"] = [{"id": "a", "ip": "fe80::1, 192.168.1.100"}]

    d = (await coord.list_pending())[0]
    assert d["canvas_count"] == 1


@pytest.mark.asyncio
async def test_list_pending_canvas_count_correlates_by_mac(coord) -> None:  # noqa: ANN001
    """Node's ip differs entirely (user edited it) but the MAC still matches:
    the device is recognised as on the canvas (issue #258)."""
    pending = await coord._get_pending()
    pending["devices"].append(
        {"id": "pd-1", "ip": "192.168.1.55", "mac": "aa:bb:cc:dd:ee:ff", "status": "approved"}
    )
    await coord._save_pending()
    await coord._ensure_loaded()
    default = coord._designs[0]["id"]
    coord._canvases[default]["nodes"] = [{"id": "a", "ip": "10.9.9.9", "mac": "aa:bb:cc:dd:ee:ff"}]

    d = (await coord.list_pending())[0]
    assert d["canvas_count"] == 1


@pytest.mark.asyncio
async def test_approve_batch_reports_skipped_devices(coord) -> None:  # noqa: ANN001
    """Bulk can't prompt per-device, so it reports each duplicate it skipped
    (with the existing node id) instead of silently dropping it."""
    pending = await coord._get_pending()
    pending["devices"] += [
        {"id": "pd-1", "ip": "192.168.1.10", "hostname": "host-a", "status": "pending"},
        {"id": "pd-2", "ip": "192.168.1.11", "status": "pending"},
    ]
    await coord._save_pending()
    default = (await coord.list_designs())[0]
    first = await coord.approve_pending("pd-1", {"design_id": default["id"]})

    r = await coord.approve_batch(["pd-1", "pd-2"], {"design_id": default["id"]})
    assert r["approved"] == 1
    assert len(r["skipped_devices"]) == 1
    entry = r["skipped_devices"][0]
    assert entry["device_id"] == "pd-1"
    assert entry["match"] == "ip"
    assert entry["value"] == "192.168.1.10"
    assert entry["label"] == "host-a"
    assert entry["existing_node_id"] == first["id"]


@pytest.mark.asyncio
async def test_approve_batch_skips_device_matching_ip_in_comma_list(coord) -> None:  # noqa: ANN001
    """The on-canvas node's ip holds an IPv6 before the IPv4; the device scanned
    as the plain IPv4 is still recognised as already placed (issue #258)."""
    pending = await coord._get_pending()
    pending["devices"] += [
        {"id": "pd-1", "ip": "192.168.1.10", "status": "pending"},
        {"id": "pd-2", "ip": "192.168.1.11", "status": "pending"},
    ]
    await coord._save_pending()
    await coord._ensure_loaded()
    default = coord._designs[0]["id"]
    coord._canvases[default]["nodes"] = [{"id": "a", "ip": "fe80::1, 192.168.1.10"}]

    r = await coord.approve_batch(["pd-1", "pd-2"], {"design_id": default})
    assert r["approved"] == 1
    assert r["skipped"] == ["pd-1"]
    assert r["skipped_devices"][0]["value"] == "192.168.1.10"
