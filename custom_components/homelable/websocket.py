"""WebSocket API commands for Homelable."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN


@callback
def async_register_websocket_commands(hass: HomeAssistant) -> None:
    """Register all Homelable WS commands."""
    websocket_api.async_register_command(hass, ws_get_canvas)
    websocket_api.async_register_command(hass, ws_save_canvas)
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


def _coordinator(hass: HomeAssistant):
    """Return the first (and only) coordinator instance."""
    entries = hass.data.get(DOMAIN, {})
    if not entries:
        return None
    return next(iter(entries.values()))


def _send_not_setup(connection, msg_id: int) -> None:
    connection.send_error(msg_id, "not_setup", "Homelable not configured")


# ─── Canvas ──────────────────────────────────────────────────────────────────

@websocket_api.websocket_command({vol.Required("type"): "homelable/get_canvas"})
@websocket_api.async_response
async def ws_get_canvas(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return
    canvas = await coord.get_canvas()
    connection.send_result(msg["id"], canvas)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homelable/save_canvas",
        vol.Required("canvas"): dict,
    }
)
@websocket_api.async_response
async def ws_save_canvas(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return
    await coord.save_canvas(msg["canvas"])
    connection.send_result(msg["id"], {"ok": True})


# ─── Scan ────────────────────────────────────────────────────────────────────

@websocket_api.websocket_command({vol.Required("type"): "homelable/scan/start"})
@websocket_api.async_response
async def ws_scan_start(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return
    result = await coord.trigger_scan()
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command({vol.Required("type"): "homelable/scan/cancel"})
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
    devices = await coord.list_pending(status=msg["status"])
    connection.send_result(msg["id"], {"devices": devices})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homelable/scan/approve",
        vol.Required("device_id"): str,
        vol.Optional("overrides", default={}): dict,
    }
)
@websocket_api.async_response
async def ws_scan_approve(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    coord = _coordinator(hass)
    if coord is None:
        _send_not_setup(connection, msg["id"])
        return
    node = await coord.approve_pending(msg["device_id"], msg["overrides"])
    if node is None:
        connection.send_error(msg["id"], "not_found", "Device not found")
        return
    connection.send_result(msg["id"], {"node": node})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homelable/scan/hide",
        vol.Required("device_id"): str,
    }
)
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
