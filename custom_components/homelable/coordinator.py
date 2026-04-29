"""DataUpdateCoordinator for Homelable."""
from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from . import scanner, status_checker
from .const import (
    CONF_SCAN_RANGES,
    CONF_STATUS_INTERVAL,
    DEFAULT_SCAN_RANGES,
    DEFAULT_STATUS_INTERVAL,
    DOMAIN,
    STORAGE_KEY_CANVAS,
    STORAGE_KEY_PENDING,
    STORAGE_VERSION_CANVAS,
    STORAGE_VERSION_PENDING,
)

_LOGGER = logging.getLogger(__name__)

_EMPTY_CANVAS = {"nodes": [], "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}}
_EMPTY_PENDING: dict[str, Any] = {"devices": []}


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
        self.canvas_store: Store = Store(
            hass, STORAGE_VERSION_CANVAS, STORAGE_KEY_CANVAS
        )
        self.pending_store: Store = Store(
            hass, STORAGE_VERSION_PENDING, STORAGE_KEY_PENDING
        )
        self._canvas: dict[str, Any] | None = None
        self._pending: dict[str, Any] | None = None
        self._scan_run_id: str | None = None

    # ─── Status checks (periodic) ────────────────────────────────────────────

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """Run a status check on every canvas node. Return {node_id: status_dict}."""
        canvas = await self.get_canvas()
        results: dict[str, dict[str, Any]] = {}
        for node in canvas.get("nodes", []):
            data = node.get("data", {})
            check = data.get("check_method", "ping")
            target = data.get("target") or data.get("hostname")
            ip = data.get("ip")
            try:
                results[node["id"]] = await status_checker.check_node(
                    check, target, ip
                )
            except Exception as exc:
                _LOGGER.debug("Status check error for %s: %s", node.get("id"), exc)
                results[node["id"]] = {
                    "status": "unknown",
                    "response_time_ms": None,
                }
        return results

    # ─── Canvas ──────────────────────────────────────────────────────────────

    async def get_canvas(self) -> dict[str, Any]:
        if self._canvas is None:
            self._canvas = (await self.canvas_store.async_load()) or dict(
                _EMPTY_CANVAS
            )
        return self._canvas

    async def save_canvas(self, canvas: dict[str, Any]) -> None:
        self._canvas = canvas
        await self.canvas_store.async_save(canvas)

    # ─── Pending devices ─────────────────────────────────────────────────────

    async def _get_pending(self) -> dict[str, Any]:
        if self._pending is None:
            self._pending = (await self.pending_store.async_load()) or dict(
                _EMPTY_PENDING
            )
        return self._pending

    async def _save_pending(self) -> None:
        if self._pending is not None:
            await self.pending_store.async_save(self._pending)

    async def list_pending(self, *, status: str = "pending") -> list[dict[str, Any]]:
        store = await self._get_pending()
        return [d for d in store["devices"] if d.get("status") == status]

    async def hide_pending(self, device_id: str) -> bool:
        """Mark a pending device as hidden. Returns True if found."""
        store = await self._get_pending()
        for d in store["devices"]:
            if d["id"] == device_id:
                d["status"] = "hidden"
                await self._save_pending()
                return True
        return False

    async def remove_pending(self, device_id: str) -> bool:
        """Remove a pending device from the store. Returns True if found."""
        store = await self._get_pending()
        before = len(store["devices"])
        store["devices"] = [d for d in store["devices"] if d["id"] != device_id]
        if len(store["devices"]) < before:
            await self._save_pending()
            return True
        return False

    async def approve_pending(
        self, device_id: str, node_overrides: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """Move a pending device onto the canvas as a new node.

        Returns the created node, or None if device not found.
        """
        pending = await self._get_pending()
        device = next(
            (d for d in pending["devices"] if d["id"] == device_id), None
        )
        if device is None:
            return None

        overrides = node_overrides or {}
        node_type = overrides.get("type") or device.get("suggested_type") or "generic"
        node = {
            "id": overrides.get("id") or f"node-{uuid.uuid4().hex[:8]}",
            "type": node_type,
            "position": overrides.get("position") or {"x": 0, "y": 0},
            "data": {
                "label": overrides.get("label")
                or device.get("hostname")
                or device.get("ip"),
                "ip": device.get("ip"),
                "mac": device.get("mac"),
                "hostname": device.get("hostname"),
                "os": device.get("os"),
                "services": device.get("services", []),
                "check_method": overrides.get("check_method", "ping"),
                **overrides.get("data", {}),
            },
        }

        canvas = await self.get_canvas()
        canvas.setdefault("nodes", []).append(node)
        await self.save_canvas(canvas)

        await self.remove_pending(device_id)
        return node

    # ─── Scan ────────────────────────────────────────────────────────────────

    async def trigger_scan(self) -> dict[str, Any]:
        """Run a scan and merge results into the pending store.

        Returns {run_id, devices_found, new_devices}.
        """
        if self._scan_run_id is not None:
            return {
                "run_id": self._scan_run_id,
                "status": "already_running",
                "devices_found": 0,
                "new_devices": 0,
            }

        ranges = self.entry.options.get(
            CONF_SCAN_RANGES,
            self.entry.data.get(CONF_SCAN_RANGES, ",".join(DEFAULT_SCAN_RANGES)),
        )
        if isinstance(ranges, str):
            ranges = [r.strip() for r in ranges.split(",") if r.strip()]

        canvas = await self.get_canvas()
        pending = await self._get_pending()
        canvas_ips = {n["data"]["ip"] for n in canvas.get("nodes", []) if n.get("data", {}).get("ip")}
        hidden_ips = {d["ip"] for d in pending["devices"] if d.get("status") == "hidden"}
        existing_pending_ips = {
            d["ip"] for d in pending["devices"] if d.get("status") == "pending"
        }
        exclude = canvas_ips | hidden_ips

        run_id = uuid.uuid4().hex
        self._scan_run_id = run_id
        try:
            devices = await scanner.run_scan(
                ranges, run_id=run_id, exclude_ips=exclude
            )
        finally:
            self._scan_run_id = None

        new_count = 0
        now = datetime.now(UTC).isoformat()
        for dev in devices:
            if dev["ip"] in existing_pending_ips:
                # Update in place
                for stored in pending["devices"]:
                    if stored["ip"] == dev["ip"] and stored.get("status") == "pending":
                        stored.update(
                            {
                                "mac": dev.get("mac") or stored.get("mac"),
                                "hostname": dev.get("hostname") or stored.get("hostname"),
                                "os": dev.get("os") or stored.get("os"),
                                "open_ports": dev.get("open_ports", []),
                                "services": dev.get("services", []),
                                "suggested_type": dev.get("suggested_type"),
                                "discovery_source": dev.get("discovery_source"),
                            }
                        )
                        break
            else:
                pending["devices"].append(
                    {
                        "id": f"pd-{uuid.uuid4().hex[:8]}",
                        "ip": dev["ip"],
                        "mac": dev.get("mac"),
                        "hostname": dev.get("hostname"),
                        "os": dev.get("os"),
                        "open_ports": dev.get("open_ports", []),
                        "services": dev.get("services", []),
                        "suggested_type": dev.get("suggested_type"),
                        "discovery_source": dev.get("discovery_source"),
                        "status": "pending",
                        "discovered_at": now,
                    }
                )
                new_count += 1

        await self._save_pending()
        return {
            "run_id": run_id,
            "status": "done",
            "devices_found": len(devices),
            "new_devices": new_count,
        }

    def cancel_scan(self) -> bool:
        if self._scan_run_id is None:
            return False
        scanner.request_cancel(self._scan_run_id)
        return True
