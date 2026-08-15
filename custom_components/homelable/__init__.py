"""Homelable integration for Home Assistant."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN, PLATFORMS
from .coordinator import HomelableCoordinator
from .media import async_register_media
from .panel import async_register_panel, async_unregister_panel
from .websocket import async_register_websocket_commands

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the Homelable component (YAML setup not supported)."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Homelable from a config entry."""
    coordinator = HomelableCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    async_register_websocket_commands(hass)
    await async_register_panel(hass)
    await async_register_media(hass)

    if PLATFORMS:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Optional independent per-service status checks (off by default). Reload on
    # options change recreates the coordinator, so the timer always reflects the
    # latest setting.
    coordinator.async_start_service_checks()
    entry.async_on_unload(coordinator.async_stop_service_checks)

    # Optional periodic network scan (off by default — a sweep is heavy, so it
    # only runs on a timer when the user opts in).
    coordinator.async_start_periodic_scan()
    entry.async_on_unload(coordinator.async_stop_periodic_scan)

    # Optional Proxmox auto-sync: re-imports the inventory into pending on the
    # configured interval. Reload on options change recreates the coordinator,
    # so enable/interval are always picked up fresh.
    coordinator.async_start_proxmox_sync()
    entry.async_on_unload(coordinator.async_stop_proxmox_sync)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = True
    if PLATFORMS:
        unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        if not hass.data[DOMAIN]:
            async_unregister_panel(hass)

    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    await hass.config_entries.async_reload(entry.entry_id)
