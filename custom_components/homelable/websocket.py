"""WebSocket API commands for Homelable."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from . import proxmox, scanner
from .const import DOMAIN, SCAN_SIGNAL, SERVICE_STATUS_SIGNAL
from .zigbee import ZigbeeMqttNotReadyError
from .zwave import ZwaveMqttNotReadyError


@callback
def async_register_websocket_commands(hass: HomeAssistant) -> None:
    """Register all Homelable WS commands."""
    websocket_api.async_register_command(hass, ws_get_canvas)
    websocket_api.async_register_command(hass, ws_save_canvas)
    websocket_api.async_register_command(hass, ws_designs_list)
    websocket_api.async_register_command(hass, ws_designs_create)
    websocket_api.async_register_command(hass, ws_designs_copy)
    websocket_api.async_register_command(hass, ws_designs_update)
    websocket_api.async_register_command(hass, ws_designs_delete)
    websocket_api.async_register_command(hass, ws_scan_start)
    websocket_api.async_register_command(hass, ws_scan_cancel)
    websocket_api.async_register_command(hass, ws_scan_pending)
    websocket_api.async_register_command(hass, ws_scan_approve)
    websocket_api.async_register_command(hass, ws_scan_hide)
    websocket_api.async_register_command(hass, ws_scan_runs)
    websocket_api.async_register_command(hass, ws_scan_get_config)
    websocket_api.async_register_command(hass, ws_scan_clear)
    websocket_api.async_register_command(hass, ws_status_get)
    websocket_api.async_register_command(hass, ws_status_subscribe)
    websocket_api.async_register_command(hass, ws_service_status_subscribe)
    websocket_api.async_register_command(hass, ws_scan_subscribe)
    websocket_api.async_register_command(hass, ws_scan_approve_batch)
    websocket_api.async_register_command(hass, ws_scan_hide_batch)
    websocket_api.async_register_command(hass, ws_scan_ignore)
    websocket_api.async_register_command(hass, ws_scan_restore)
    websocket_api.async_register_command(hass, ws_scan_restore_batch)
    websocket_api.async_register_command(hass, ws_zigbee_devices)
    websocket_api.async_register_command(hass, ws_zigbee_import)
    websocket_api.async_register_command(hass, ws_zwave_devices)
    websocket_api.async_register_command(hass, ws_zwave_import)
    websocket_api.async_register_command(hass, ws_proxmox_get_config)
    websocket_api.async_register_command(hass, ws_proxmox_test_connection)
    websocket_api.async_register_command(hass, ws_proxmox_import)
    websocket_api.async_register_command(hass, ws_proxmox_import_pending)


def _coordinator(hass: HomeAssistant):
    """Return the first (and only) coordinator instance."""
    entries = hass.data.get(DOMAIN, {})
    if not entries:
        return None
    return next(iter(entries.values()))


def _send_not_setup(connection, msg_id: int) -> None:
    connection.send_error(msg_id, "not_setup", "Homelable not configured")


def _overrides_with_design(msg: dict[str, Any]) -> dict[str, Any]:
    """Merge a top-level `design_id` into the approve `overrides` dict so the
    coordinator approves the node onto the active design. Returns a copy."""
    overrides = dict(msg.get("overrides") or {})
    if msg.get("design_id") and "design_id" not in overrides:
        overrides["design_id"] = msg["design_id"]
    return overrides


# ─── Canvas ──────────────────────────────────────────────────────────────────

@websocket_api.websocket_command(
    {
        vol.Required("type"): "homelable/get_canvas",
        vol.Optional("design_id"): vol.Any(str, None),
    }
)
@websocket_api.async_response
async def ws_get_canvas(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return
    canvas = await coord.get_canvas(msg.get("design_id"))
    connection.send_result(msg["id"], canvas)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homelable/save_canvas",
        vol.Required("canvas"): dict,
        vol.Optional("design_id"): vol.Any(str, None),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_save_canvas(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return
    await coord.save_canvas(msg["canvas"], msg.get("design_id"))
    connection.send_result(msg["id"], {"ok": True})


# ─── Designs (multiple canvases) ──────────────────────────────────────────────

@websocket_api.websocket_command({vol.Required("type"): "homelable/designs/list"})
@websocket_api.async_response
async def ws_designs_list(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return
    connection.send_result(msg["id"], {"designs": await coord.list_designs()})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homelable/designs/create",
        vol.Required("name"): str,
        vol.Optional("icon", default="dashboard"): str,
        vol.Optional("design_type", default="network"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_designs_create(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return
    design = await coord.create_design(
        msg["name"], msg["icon"], msg["design_type"]
    )
    connection.send_result(msg["id"], design)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homelable/designs/copy",
        vol.Required("source_id"): str,
        vol.Required("name"): str,
        vol.Optional("icon", default="dashboard"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_designs_copy(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return
    design = await coord.copy_design(msg["source_id"], msg["name"], msg["icon"])
    if design is None:
        connection.send_error(msg["id"], "not_found", "Source design not found")
        return
    connection.send_result(msg["id"], design)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homelable/designs/update",
        vol.Required("design_id"): str,
        vol.Optional("name"): str,
        vol.Optional("icon"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_designs_update(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return
    design = await coord.update_design(
        msg["design_id"], name=msg.get("name"), icon=msg.get("icon")
    )
    if design is None:
        connection.send_error(msg["id"], "not_found", "Design not found")
        return
    connection.send_result(msg["id"], design)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homelable/designs/delete",
        vol.Required("design_id"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_designs_delete(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return
    result = await coord.delete_design(msg["design_id"])
    if result == "not_found":
        connection.send_error(msg["id"], "not_found", "Design not found")
        return
    if result == "last":
        connection.send_error(
            msg["id"], "last_design", "Cannot delete the only canvas"
        )
        return
    connection.send_result(msg["id"], {"ok": True})


# ─── Scan ────────────────────────────────────────────────────────────────────

@websocket_api.websocket_command(
    {
        vol.Required("type"): "homelable/scan/start",
        # Deep-scan overrides (per-scan only; not persisted).
        vol.Optional("http_ranges"): [str],
        vol.Optional("http_probe_enabled"): bool,
        vol.Optional("verify_tls"): bool,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_scan_start(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return
    http_ranges = msg.get("http_ranges")
    if http_ranges:
        invalid = [r for r in http_ranges if not scanner._valid_port_range(r.strip())]
        if invalid:
            connection.send_error(
                msg["id"], "invalid_port_range", f"Invalid port range(s): {invalid}"
            )
            return
    result = await coord.trigger_scan(
        http_ranges=http_ranges,
        http_probe_enabled=bool(msg.get("http_probe_enabled", False)),
        verify_tls=bool(msg.get("verify_tls", False)),
    )
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command({vol.Required("type"): "homelable/scan/cancel"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_scan_cancel(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return
    cancelled = coord.cancel_scan()
    connection.send_result(msg["id"], {"cancelled": cancelled})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homelable/scan/pending",
        vol.Optional("status", default="pending"): vol.In(["pending", "hidden"]),
        vol.Optional("source"): vol.In(["scan", "zigbee", "zwave"]),
    }
)
@websocket_api.async_response
async def ws_scan_pending(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return
    devices = await coord.list_pending(status=msg["status"], source=msg.get("source"))
    connection.send_result(msg["id"], {"devices": devices})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homelable/scan/approve",
        vol.Required("device_id"): str,
        vol.Optional("overrides", default={}): dict,
        vol.Optional("design_id"): vol.Any(str, None),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_scan_approve(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return
    overrides = _overrides_with_design(msg)
    node = await coord.approve_pending(msg["device_id"], overrides)
    if node is None:
        connection.send_error(msg["id"], "not_found", "Device not found")
        return
    auto_edge = await coord._create_wireless_parent_edge(
        node, overrides.get("design_id")
    )
    edges = [auto_edge] if auto_edge else []
    edges.extend(await coord._create_proxmox_edges(node, overrides.get("design_id")))
    connection.send_result(
        msg["id"],
        {
            "node": node,
            "node_id": node["id"],
            "edges": edges,
            "edges_created": len(edges),
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homelable/scan/hide",
        vol.Required("device_id"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_scan_hide(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return
    ok = await coord.hide_pending(msg["device_id"])
    if not ok:
        connection.send_error(msg["id"], "not_found", "Device not found")
        return
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.websocket_command({vol.Required("type"): "homelable/scan/get_config"})
@websocket_api.async_response
async def ws_scan_get_config(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return
    connection.send_result(msg["id"], {"ranges": coord.get_scan_ranges()})


@websocket_api.websocket_command({vol.Required("type"): "homelable/scan/clear"})
@websocket_api.require_admin
@websocket_api.async_response
async def ws_scan_clear(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return
    removed = await coord.clear_pending()
    connection.send_result(msg["id"], {"removed": removed})


@websocket_api.websocket_command({vol.Required("type"): "homelable/scan/runs"})
@websocket_api.async_response
async def ws_scan_runs(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return
    runs = await coord.list_runs()
    connection.send_result(msg["id"], {"runs": runs})


# ─── Status ──────────────────────────────────────────────────────────────────

@websocket_api.websocket_command({vol.Required("type"): "homelable/status/get"})
@websocket_api.async_response
async def ws_status_get(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return latest status map (keyed by node id) from the coordinator."""
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return
    connection.send_result(msg["id"], coord.data or {})


@websocket_api.websocket_command(
    {vol.Required("type"): "homelable/status/subscribe"}
)
@callback
def ws_status_subscribe(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Push the coordinator's status map on every refresh until unsubscribed."""
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return

    @callback
    def _push() -> None:
        connection.send_message(
            websocket_api.event_message(msg["id"], coord.data or {})
        )

    unsub = coord.async_add_listener(_push)
    connection.subscriptions[msg["id"]] = unsub
    connection.send_result(msg["id"])
    # Send the current snapshot so the client doesn't have to wait for the
    # next coordinator tick to populate.
    _push()


@websocket_api.websocket_command(
    {vol.Required("type"): "homelable/scan/subscribe"}
)
@callback
def ws_scan_subscribe(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Stream live scan events (device_discovered / device_enriched / phase / finished)."""
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return

    @callback
    def _forward(payload: dict[str, Any]) -> None:
        connection.send_message(websocket_api.event_message(msg["id"], payload))

    unsub = async_dispatcher_connect(hass, SCAN_SIGNAL, _forward)
    connection.subscriptions[msg["id"]] = unsub
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {vol.Required("type"): "homelable/service_status/subscribe"}
)
@callback
def ws_service_status_subscribe(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Push per-service status results as the coordinator computes them.

    Each event is {node_id, services: [{port, protocol, status}], checked_at}.
    Independent of the node-status subscription; only fires when per-service
    checks are enabled in the options flow.
    """
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return

    @callback
    def _forward(payload: dict[str, Any]) -> None:
        connection.send_message(websocket_api.event_message(msg["id"], payload))

    unsub = async_dispatcher_connect(hass, SERVICE_STATUS_SIGNAL, _forward)
    connection.subscriptions[msg["id"]] = unsub
    connection.send_result(msg["id"])


# ─── Pending devices: batch ops ──────────────────────────────────────────────

@websocket_api.websocket_command(
    {
        vol.Required("type"): "homelable/scan/approve_batch",
        vol.Required("device_ids"): [str],
        vol.Optional("overrides", default={}): dict,
        vol.Optional("design_id"): vol.Any(str, None),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_scan_approve_batch(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return
    overrides = _overrides_with_design(msg)
    result = await coord.approve_batch(msg["device_ids"], overrides)
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homelable/scan/hide_batch",
        vol.Required("device_ids"): [str],
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_scan_hide_batch(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return
    hidden = 0
    for device_id in msg["device_ids"]:
        if await coord.hide_pending(device_id):
            hidden += 1
    connection.send_result(msg["id"], {"hidden": hidden})


# ─── Pending devices: single ignore + restore ────────────────────────────────

@websocket_api.websocket_command(
    {
        vol.Required("type"): "homelable/scan/ignore",
        vol.Required("device_id"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_scan_ignore(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Remove a pending or hidden device from the store (permanently drop)."""
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return
    ok = await coord.remove_pending(msg["device_id"])
    if not ok:
        connection.send_error(msg["id"], "not_found", "Device not found")
        return
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homelable/scan/restore",
        vol.Required("device_id"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_scan_restore(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Flip a hidden device back to pending."""
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return
    ok = await coord.restore_pending(msg["device_id"])
    if not ok:
        connection.send_error(msg["id"], "not_found", "Device not found or not hidden")
        return
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homelable/scan/restore_batch",
        vol.Required("device_ids"): [str],
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_scan_restore_batch(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return
    restored = 0
    for device_id in msg["device_ids"]:
        if await coord.restore_pending(device_id):
            restored += 1
    connection.send_result(msg["id"], {"restored": restored})


# ─── Zigbee2MQTT ─────────────────────────────────────────────────────────────

@websocket_api.websocket_command(
    {vol.Required("type"): "homelable/zigbee/devices"}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_zigbee_devices(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Fetch the Z2M networkmap and return parsed nodes + edges."""
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return
    try:
        nodes, edges = await coord.fetch_zigbee_networkmap()
    except ZigbeeMqttNotReadyError as exc:
        connection.send_error(msg["id"], "mqtt_not_configured", str(exc))
        return
    except TimeoutError as exc:
        connection.send_error(msg["id"], "timeout", str(exc))
        return
    except ValueError as exc:
        connection.send_error(msg["id"], "bad_response", str(exc))
        return
    connection.send_result(
        msg["id"],
        {"nodes": nodes, "edges": edges, "base_topic": coord.get_zigbee_base_topic()},
    )


@websocket_api.websocket_command(
    {vol.Required("type"): "homelable/zigbee/import"}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_zigbee_import(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Kick off a background Zigbee import (fetch + import); surfaces under
    Scan History with a running → done transition."""
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return
    result = await coord.trigger_zigbee_import()
    connection.send_result(msg["id"], result)


# ─── Z-Wave JS UI ─────────────────────────────────────────────────────────────

@websocket_api.websocket_command(
    {vol.Required("type"): "homelable/zwave/devices"}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_zwave_devices(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Fetch the Z-Wave node list and return parsed nodes + edges."""
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return
    try:
        nodes, edges = await coord.fetch_zwave_network()
    except ZwaveMqttNotReadyError as exc:
        connection.send_error(msg["id"], "mqtt_not_configured", str(exc))
        return
    except TimeoutError as exc:
        connection.send_error(msg["id"], "timeout", str(exc))
        return
    except ValueError as exc:
        connection.send_error(msg["id"], "bad_response", str(exc))
        return
    prefix, gateway = coord.get_zwave_config()
    connection.send_result(
        msg["id"],
        {"nodes": nodes, "edges": edges, "prefix": prefix, "gateway": gateway},
    )


@websocket_api.websocket_command(
    {vol.Required("type"): "homelable/zwave/import"}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_zwave_import(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Kick off a background Z-Wave import (fetch + import); surfaces under
    Scan History with a running → done transition."""
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return
    result = await coord.trigger_zwave_import()
    connection.send_result(msg["id"], result)


# ─── Proxmox VE ───────────────────────────────────────────────────────────────

# Connection params are all optional on the wire: a blank value falls back to
# the integration-stored config (host/port/tls) and credential (token), so the
# panel never has to hold the token to run an import.
_PROXMOX_CONN_SCHEMA = {
    vol.Optional("host"): vol.Any(str, None),
    vol.Optional("port"): vol.Any(int, None),
    vol.Optional("token_id"): vol.Any(str, None),
    vol.Optional("token_secret"): vol.Any(str, None),
    vol.Optional("verify_tls"): vol.Any(bool, None),
}


def _proxmox_request(coord, msg: dict[str, Any]) -> dict[str, Any]:
    """Resolve request connection params over the stored config."""
    return coord.resolve_proxmox_request(
        host=msg.get("host"),
        port=msg.get("port"),
        token_id=msg.get("token_id"),
        token_secret=msg.get("token_secret"),
        verify_tls=msg.get("verify_tls"),
    )


@websocket_api.websocket_command(
    {vol.Required("type"): "homelable/proxmox/get_config"}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_proxmox_get_config(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return non-secret Proxmox config (host/port/tls/sync + token_configured)."""
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return
    connection.send_result(msg["id"], coord.get_proxmox_config())


@websocket_api.websocket_command(
    {vol.Required("type"): "homelable/proxmox/test_connection", **_PROXMOX_CONN_SCHEMA}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_proxmox_test_connection(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Validate host reachability + token before importing."""
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return
    try:
        req = _proxmox_request(coord, msg)
    except ValueError as exc:
        connection.send_error(msg["id"], "not_configured", str(exc))
        return
    connected, message = await proxmox.test_proxmox_connection(
        hass,
        req["host"],
        req["port"],
        req["token_id"],
        req["token_secret"],
        req["verify_tls"],
    )
    connection.send_result(
        msg["id"], {"connected": connected, "message": message}
    )


@websocket_api.websocket_command(
    {vol.Required("type"): "homelable/proxmox/import", **_PROXMOX_CONN_SCHEMA}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_proxmox_import(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Fetch the inventory and return nodes + edges + cluster pairs for a direct
    canvas drop (the panel injects them client-side)."""
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return
    try:
        req = _proxmox_request(coord, msg)
    except ValueError as exc:
        connection.send_error(msg["id"], "not_configured", str(exc))
        return
    try:
        nodes, edges = await proxmox.fetch_proxmox_inventory(
            hass,
            req["host"],
            req["port"],
            req["token_id"],
            req["token_secret"],
            req["verify_tls"],
        )
    except proxmox.ProxmoxError as exc:
        connection.send_error(msg["id"], "proxmox_error", str(exc))
        return
    except ValueError as exc:
        connection.send_error(msg["id"], "bad_response", str(exc))
        return
    cluster_pairs = proxmox.build_proxmox_cluster_links(nodes)
    connection.send_result(
        msg["id"],
        {
            "nodes": nodes,
            "edges": edges,
            "cluster_pairs": [list(p) for p in cluster_pairs],
            "device_count": len(nodes),
            "advisory": proxmox.guest_visibility_advisory(nodes),
        },
    )


@websocket_api.websocket_command(
    {vol.Required("type"): "homelable/proxmox/import_pending", **_PROXMOX_CONN_SCHEMA}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_proxmox_import_pending(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Kick off a background Proxmox import into pending (kind="proxmox");
    surfaces under Scan History with a running → done transition."""
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return
    try:
        result = await coord.trigger_proxmox_import(
            host=msg.get("host"),
            port=msg.get("port"),
            token_id=msg.get("token_id"),
            token_secret=msg.get("token_secret"),
            verify_tls=msg.get("verify_tls"),
        )
    except ValueError as exc:
        connection.send_error(msg["id"], "not_configured", str(exc))
        return
    connection.send_result(msg["id"], result)
