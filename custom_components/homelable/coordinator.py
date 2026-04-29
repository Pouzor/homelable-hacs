"""DataUpdateCoordinator for Homelable."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_STATUS_INTERVAL,
    DEFAULT_STATUS_INTERVAL,
    DOMAIN,
    STORAGE_KEY_CANVAS,
    STORAGE_VERSION_CANVAS,
)

_LOGGER = logging.getLogger(__name__)


class HomelableCoordinator(DataUpdateCoordinator):
    """Coordinator running scanner + status checks for Homelable."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        interval = entry.options.get(
            CONF_STATUS_INTERVAL,
            entry.data.get(CONF_STATUS_INTERVAL, DEFAULT_STATUS_INTERVAL),
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=interval),
        )
        self.canvas_store: Store = Store(hass, STORAGE_VERSION_CANVAS, STORAGE_KEY_CANVAS)
        self._canvas_cache: dict[str, Any] | None = None

    async def _async_update_data(self) -> dict[str, Any]:
        """Run periodic status checks. Returns latest status map."""
        # TODO Phase 1: call status_checker.check_node() for each canvas node
        # TODO Phase 2: surface as sensor/binary_sensor entities
        return {}

    async def get_canvas(self) -> dict[str, Any]:
        """Load canvas state from HA storage."""
        if self._canvas_cache is None:
            self._canvas_cache = (await self.canvas_store.async_load()) or {
                "nodes": [],
                "edges": [],
                "viewport": {"x": 0, "y": 0, "zoom": 1},
            }
        return self._canvas_cache

    async def save_canvas(self, canvas: dict[str, Any]) -> None:
        """Persist canvas state to HA storage."""
        self._canvas_cache = canvas
        await self.canvas_store.async_save(canvas)

    async def trigger_scan(self) -> None:
        """Trigger a network scan in executor."""
        # TODO Phase 1: import scanner.py, run via async_add_executor_job
        _LOGGER.info("Scan triggered (not yet implemented)")
