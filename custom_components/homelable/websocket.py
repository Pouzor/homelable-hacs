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


def _coordinator(hass: HomeAssistant):
    """Return the first (and only) coordinator instance."""
    entries = hass.data.get(DOMAIN, {})
    if not entries:
        return None
    return next(iter(entries.values()))


@websocket_api.websocket_command({vol.Required("type"): "homelable/get_canvas"})
@websocket_api.async_response
async def ws_get_canvas(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Return the persisted canvas."""
    coord = _coordinator(hass)
    if coord is None:
        connection.send_error(msg["id"], "not_setup", "Homelable not configured")
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
    """Persist the canvas."""
    coord = _coordinator(hass)
    if coord is None:
        connection.send_error(msg["id"], "not_setup", "Homelable not configured")
        return
    await coord.save_canvas(msg["canvas"])
    connection.send_result(msg["id"], {"ok": True})


@websocket_api.websocket_command({vol.Required("type"): "homelable/scan/start"})
@websocket_api.async_response
async def ws_scan_start(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """Trigger a scan."""
    coord = _coordinator(hass)
    if coord is None:
        connection.send_error(msg["id"], "not_setup", "Homelable not configured")
        return
    await coord.trigger_scan()
    connection.send_result(msg["id"], {"ok": True})
