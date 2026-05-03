"""DataUpdateCoordinator for Homelable."""
from __future__ import annotations

import copy
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from . import scanner, status_checker
from .const import (
    CONF_SCAN_RANGES,
    CONF_STATUS_INTERVAL,
    DEFAULT_SCAN_RANGES,
    DEFAULT_STATUS_INTERVAL,
    DOMAIN,
    MAX_SCAN_RUNS,
    SCAN_SIGNAL,
    STORAGE_KEY_CANVAS,
    STORAGE_KEY_PENDING,
    STORAGE_KEY_RUNS,
    STORAGE_VERSION_CANVAS,
    STORAGE_VERSION_PENDING,
    STORAGE_VERSION_RUNS,
)

_LOGGER = logging.getLogger(__name__)

_EMPTY_CANVAS = {"nodes": [], "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}}
_EMPTY_PENDING: dict[str, Any] = {"devices": []}


def _utc_now_iso() -> str:
    """ISO-8601 UTC with trailing 'Z' (frontend Date() expects this form)."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


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
        self.runs_store: Store = Store(
            hass, STORAGE_VERSION_RUNS, STORAGE_KEY_RUNS
        )
        self._canvas: dict[str, Any] | None = None
        self._pending: dict[str, Any] | None = None
        self._runs: list[dict[str, Any]] | None = None
        self._scan_run_id: str | None = None

    # ─── Status checks (periodic) ────────────────────────────────────────────

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """Run a status check on every canvas node. Return {node_id: status_dict}."""
        canvas = await self.get_canvas()
        # Hosts that resolve to loopback / link-local / multicast / reserved
        # IPs are only allowed if the admin explicitly opted into that subnet.
        allowed_networks = status_checker._parse_allowed_networks(
            self.get_scan_ranges()
        )
        results: dict[str, dict[str, Any]] = {}
        for node in canvas.get("nodes", []):
            node_id = node.get("id")
            if not node_id:
                continue
            # The frontend serializes nodes flat (top-level ip/hostname/...);
            # legacy/test data may put them under `data`. Read both.
            data = node.get("data") or {}
            check = node.get("check_method") or data.get("check_method") or "ping"
            target = (
                node.get("check_target")
                or data.get("target")
                or node.get("hostname")
                or data.get("hostname")
            )
            ip = node.get("ip") or data.get("ip")
            try:
                results[node_id] = await status_checker.check_node(
                    check, target, ip, allowed_networks=allowed_networks
                )
            except Exception as exc:
                _LOGGER.debug("Status check error for %s: %s", node_id, exc)
                results[node_id] = {
                    "status": "unknown",
                    "response_time_ms": None,
                }
        return results

    # ─── Canvas ──────────────────────────────────────────────────────────────

    async def get_canvas(self) -> dict[str, Any]:
        if self._canvas is None:
            self._canvas = (await self.canvas_store.async_load()) or copy.deepcopy(
                _EMPTY_CANVAS
            )
        return self._canvas

    async def save_canvas(self, canvas: dict[str, Any]) -> None:
        self._canvas = canvas
        await self.canvas_store.async_save(canvas)

    # ─── Pending devices ─────────────────────────────────────────────────────

    async def _get_pending(self) -> dict[str, Any]:
        if self._pending is None:
            self._pending = (await self.pending_store.async_load()) or copy.deepcopy(
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

    async def clear_pending(self) -> int:
        """Drop all devices currently in `pending` status. Returns count removed."""
        store = await self._get_pending()
        before = len(store["devices"])
        store["devices"] = [d for d in store["devices"] if d.get("status") != "pending"]
        removed = before - len(store["devices"])
        if removed:
            await self._save_pending()
        return removed

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

    async def _load_runs(self) -> list[dict[str, Any]]:
        if self._runs is None:
            stored = await self.runs_store.async_load()
            self._runs = list(stored) if isinstance(stored, list) else []
        return self._runs

    async def list_runs(self) -> list[dict[str, Any]]:
        # Newest-first for the UI.
        return list(reversed(await self._load_runs()))

    async def _record_run(self, run: dict[str, Any]) -> None:
        """Insert or update a run entry (matched by id), trim to MAX_SCAN_RUNS."""
        runs = await self._load_runs()
        for i, r in enumerate(runs):
            if r["id"] == run["id"]:
                runs[i] = run
                break
        else:
            runs.append(run)
            if len(runs) > MAX_SCAN_RUNS:
                del runs[: len(runs) - MAX_SCAN_RUNS]
        await self.runs_store.async_save(runs)

    def get_scan_ranges(self) -> list[str]:
        """Return configured scan ranges (options → data → defaults)."""
        ranges = self.entry.options.get(
            CONF_SCAN_RANGES,
            self.entry.data.get(CONF_SCAN_RANGES, ",".join(DEFAULT_SCAN_RANGES)),
        )
        if isinstance(ranges, str):
            ranges = [r.strip() for r in ranges.split(",") if r.strip()]
        return list(ranges)

    async def trigger_scan(self) -> dict[str, Any]:
        """Kick off a scan in the background. Returns immediately.

        Response: {run_id, status: "running"|"already_running", devices_found: 0, new_devices: 0}.
        UI polls history for progress / completion.
        """
        if self._scan_run_id is not None:
            return {
                "run_id": self._scan_run_id,
                "status": "already_running",
                "devices_found": 0,
                "new_devices": 0,
            }

        ranges = self.get_scan_ranges()
        canvas = await self.get_canvas()
        pending = await self._get_pending()
        canvas_ips = {
            n.get("ip") or n.get("data", {}).get("ip")
            for n in canvas.get("nodes", [])
            if n.get("ip") or n.get("data", {}).get("ip")
        }
        hidden_ips = {d["ip"] for d in pending["devices"] if d.get("status") == "hidden"}
        exclude = canvas_ips | hidden_ips

        run_id = uuid.uuid4().hex
        self._scan_run_id = run_id
        started_at = _utc_now_iso()
        await self._record_run(
            {
                "id": run_id,
                "status": "running",
                "ranges": list(ranges),
                "devices_found": 0,
                "started_at": started_at,
                "finished_at": None,
                "error": None,
            }
        )

        self.hass.async_create_task(
            self._run_scan_task(run_id, ranges, exclude, started_at)
        )
        return {
            "run_id": run_id,
            "status": "running",
            "devices_found": 0,
            "new_devices": 0,
        }

    async def _handle_scan_event(
        self, run_id: str, payload: dict[str, Any]
    ) -> None:
        """Apply a scanner event to in-memory pending state and broadcast it.

        Mutates the pending dict in place so list_pending and the WS subscriber
        see consistent state during the scan. Persisted once at scan end via
        _save_pending — one event-per-host on a /24 would otherwise hammer Store.
        """
        event = payload.get("event")
        device = payload.get("device") or {}
        ip = device.get("ip")

        # Augment payload with run_id so subscribers can filter overlapping runs.
        out = {**payload, "run_id": run_id}

        if event == "device_discovered" and ip:
            pending = await self._get_pending()
            existing = next(
                (d for d in pending["devices"] if d.get("ip") == ip),
                None,
            )
            if existing is None:
                pending["devices"].append(
                    {
                        "id": f"pd-{uuid.uuid4().hex[:8]}",
                        "ip": ip,
                        "mac": device.get("mac"),
                        "hostname": device.get("hostname"),
                        "os": None,
                        "open_ports": [],
                        "services": [],
                        "suggested_type": None,
                        "discovery_source": device.get("discovery_source"),
                        "status": "discovering",
                        "discovered_at": _utc_now_iso(),
                    }
                )
            elif existing.get("status") in ("discovering", "pending"):
                # Refresh meta if we got better info this run.
                existing["mac"] = device.get("mac") or existing.get("mac")
                existing["hostname"] = (
                    device.get("hostname") or existing.get("hostname")
                )

        elif event == "device_enriched" and ip:
            pending = await self._get_pending()
            existing = next(
                (d for d in pending["devices"] if d.get("ip") == ip),
                None,
            )
            if existing is None:
                # mDNS-only path can land here without a prior discovery event
                # for hosts that didn't answer ping. Create the entry directly.
                pending["devices"].append(
                    {
                        "id": f"pd-{uuid.uuid4().hex[:8]}",
                        "ip": ip,
                        "mac": device.get("mac"),
                        "hostname": device.get("hostname"),
                        "os": device.get("os"),
                        "open_ports": device.get("open_ports", []),
                        "services": device.get("services", []),
                        "suggested_type": device.get("suggested_type"),
                        "discovery_source": device.get("discovery_source"),
                        "status": "pending",
                        "discovered_at": _utc_now_iso(),
                    }
                )
            elif existing.get("status") in ("discovering", "pending"):
                existing.update(
                    {
                        "mac": device.get("mac") or existing.get("mac"),
                        "hostname": device.get("hostname") or existing.get("hostname"),
                        "os": device.get("os") or existing.get("os"),
                        "open_ports": device.get("open_ports", []),
                        "services": device.get("services", []),
                        "suggested_type": device.get("suggested_type"),
                        "discovery_source": device.get("discovery_source"),
                        "status": "pending",
                    }
                )
            # Echo the stored device id back so the frontend can reconcile.
            stored = next(
                (d for d in pending["devices"] if d.get("ip") == ip), None
            )
            if stored is not None:
                out["device"] = {**device, "id": stored["id"]}
                _LOGGER.info(
                    "[trace] handle_scan_event enriched ip=%s stored_services=%d status=%s",
                    ip,
                    len(stored.get("services", [])),
                    stored.get("status"),
                )

        async_dispatcher_send(self.hass, SCAN_SIGNAL, out)

    async def _run_scan_task(
        self,
        run_id: str,
        ranges: list[str],
        exclude: set[str],
        started_at: str,
    ) -> None:
        """Background scan body. Records run state, merges into pending store."""
        async def _on_event(payload: dict[str, Any]) -> None:
            await self._handle_scan_event(run_id, payload)

        try:
            devices = await scanner.run_scan(
                ranges,
                run_id=run_id,
                exclude_ips=exclude,
                on_event=_on_event,
                hass=self.hass,
            )
        except Exception as exc:  # noqa: BLE001 — record any failure, then exit
            _LOGGER.exception("Scan %s failed", run_id)
            async_dispatcher_send(
                self.hass,
                SCAN_SIGNAL,
                {"event": "scan_error", "run_id": run_id, "error": str(exc)},
            )
            await self._save_pending()
            await self._record_run(
                {
                    "id": run_id,
                    "status": "error",
                    "ranges": list(ranges),
                    "devices_found": 0,
                    "started_at": started_at,
                    "finished_at": _utc_now_iso(),
                    "error": str(exc),
                }
            )
            return
        finally:
            self._scan_run_id = None

        # Streaming events have already mutated the pending store as the scan
        # ran. Reconcile here as a safety net for hosts that didn't go through
        # the event path (defensive — should be a no-op in the happy path).
        pending = await self._get_pending()
        now = _utc_now_iso()
        scanned_ips = {dev["ip"] for dev in devices}
        for dev in devices:
            existing = next(
                (d for d in pending["devices"] if d.get("ip") == dev["ip"]),
                None,
            )
            if existing is None:
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
            elif existing.get("status") in ("discovering", "pending"):
                existing.update(
                    {
                        "mac": dev.get("mac") or existing.get("mac"),
                        "hostname": dev.get("hostname") or existing.get("hostname"),
                        "os": dev.get("os") or existing.get("os"),
                        "open_ports": dev.get("open_ports", []),
                        "services": dev.get("services", []),
                        "suggested_type": dev.get("suggested_type"),
                        "discovery_source": dev.get("discovery_source"),
                        "status": "pending",
                    }
                )

        # Promote any leftover `discovering` entries from this scan that we
        # have data for, and drop ones that never enriched (cancelled mid-run).
        dropped = 0
        for d in list(pending["devices"]):
            if d.get("status") == "discovering" and d["ip"] not in scanned_ips:
                pending["devices"].remove(d)
                dropped += 1

        pending_count = sum(
            1 for d in pending["devices"] if d.get("status") == "pending"
        )
        services_total = sum(
            len(d.get("services", []))
            for d in pending["devices"]
            if d.get("status") == "pending"
        )
        _LOGGER.info(
            "[trace] scan_done devices_returned=%d pending_in_store=%d "
            "services_total=%d discovering_dropped=%d",
            len(devices),
            pending_count,
            services_total,
            dropped,
        )

        await self._save_pending()
        await self._record_run(
            {
                "id": run_id,
                "status": "done",
                "ranges": list(ranges),
                "devices_found": len(devices),
                "started_at": started_at,
                "finished_at": _utc_now_iso(),
                "error": None,
            }
        )
        async_dispatcher_send(
            self.hass,
            SCAN_SIGNAL,
            {
                "event": "scan_finished",
                "run_id": run_id,
                "devices_found": len(devices),
            },
        )

    def cancel_scan(self) -> bool:
        if self._scan_run_id is None:
            return False
        scanner.request_cancel(self._scan_run_id)
        return True
