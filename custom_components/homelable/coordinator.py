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

from . import scanner, status_checker, zigbee
from .const import (
    CONF_SCAN_RANGES,
    CONF_STATUS_INTERVAL,
    CONF_ZIGBEE_BASE_TOPIC,
    DEFAULT_DESIGN_ICON,
    DEFAULT_DESIGN_NAME,
    DEFAULT_DESIGN_TYPE,
    DEFAULT_SCAN_RANGES,
    DEFAULT_STATUS_INTERVAL,
    DEFAULT_ZIGBEE_BASE_TOPIC,
    DOMAIN,
    MAX_SCAN_RUNS,
    SCAN_SIGNAL,
    STORAGE_KEY_CANVAS,
    STORAGE_KEY_DESIGNS,
    STORAGE_KEY_PENDING,
    STORAGE_KEY_RUNS,
    STORAGE_VERSION_CANVAS,
    STORAGE_VERSION_DESIGNS,
    STORAGE_VERSION_PENDING,
    STORAGE_VERSION_RUNS,
)

_LOGGER = logging.getLogger(__name__)

_EMPTY_CANVAS = {"nodes": [], "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}}
_EMPTY_PENDING: dict[str, Any] = {"devices": []}


def build_mac_property(mac: str | None) -> list[dict[str, Any]]:
    """Build a NodeProperty list carrying a device MAC address.

    Shape matches the frontend ``NodeProperty`` type
    (``{key, value, icon, visible}``). Hidden by default — the user opts in to
    showing it on the canvas card from the right panel. Returns an empty list
    when no MAC is known.
    """
    if not mac:
        return []
    return [{"key": "MAC", "value": mac, "icon": None, "visible": False}]


def merge_mac_property(
    props: list[dict[str, Any]] | None, mac: str | None
) -> list[dict[str, Any]]:
    """Append a MAC NodeProperty to ``props`` unless one is already present.

    Preserves any user-supplied properties (and an existing MAC row's
    visibility) untouched. Used on approve so the scanned MAC is not lost.
    """
    out = [dict(p) for p in (props or [])]
    if not mac or any(p.get("key") == "MAC" for p in out):
        return out
    out.append({"key": "MAC", "value": mac, "icon": None, "visible": False})
    return out


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
        self.designs_store: Store = Store(
            hass, STORAGE_VERSION_DESIGNS, STORAGE_KEY_DESIGNS
        )
        self.pending_store: Store = Store(
            hass, STORAGE_VERSION_PENDING, STORAGE_KEY_PENDING
        )
        self.runs_store: Store = Store(
            hass, STORAGE_VERSION_RUNS, STORAGE_KEY_RUNS
        )
        # Multi-design canvas: `_canvases` maps design_id -> canvas dict;
        # `_designs` is the ordered list of design metadata. Both are loaded
        # (and legacy single-canvas data migrated) lazily via _ensure_loaded.
        self._designs: list[dict[str, Any]] | None = None
        self._canvases: dict[str, dict[str, Any]] | None = None
        self._pending: dict[str, Any] | None = None
        self._runs: list[dict[str, Any]] | None = None
        self._scan_run_id: str | None = None

    # ─── Status checks (periodic) ────────────────────────────────────────────

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """Run a status check on every canvas node across all designs.

        Returns {node_id: status_dict}. Node ids are unique across designs, but
        we de-dup defensively so a node copied into two canvases is checked once.
        """
        await self._ensure_loaded()
        # Hosts that resolve to loopback / link-local / multicast / reserved
        # IPs are only allowed if the admin explicitly opted into that subnet.
        allowed_networks = status_checker._parse_allowed_networks(
            self.get_scan_ranges()
        )
        results: dict[str, dict[str, Any]] = {}
        for node in self._all_canvas_nodes():
            node_id = node.get("id")
            if not node_id or node_id in results:
                continue
            # The frontend serializes nodes flat (top-level ip/hostname/...);
            # legacy/test data may put them under `data`. Read both.
            data = node.get("data") or {}
            node_type = node.get("type") or data.get("type") or ""
            if node_type.startswith("zigbee_"):
                # Zigbee devices are one-shot imports from Z2M; no live check.
                check = "none"
            else:
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

    # ─── Designs (multiple canvases) ─────────────────────────────────────────

    def _new_design(
        self, name: str, icon: str, design_type: str = DEFAULT_DESIGN_TYPE
    ) -> dict[str, Any]:
        now = _utc_now_iso()
        return {
            "id": uuid.uuid4().hex,
            "name": name,
            "design_type": design_type,
            "icon": icon,
            "created_at": now,
            "updated_at": now,
        }

    async def _ensure_loaded(self) -> None:
        """Load designs + per-design canvases, migrating legacy data once.

        Pre-multi-design installs stored a single canvas dict
        (``{nodes, edges, viewport}``) under STORAGE_KEY_CANVAS. On first load
        we seed a default design and move that canvas into it so existing HA
        users keep their topology. New installs get an empty default design.
        """
        if self._designs is not None and self._canvases is not None:
            return

        designs_raw = await self.designs_store.async_load()
        canvas_raw = await self.canvas_store.async_load()

        designs: list[dict[str, Any]] = []
        if isinstance(designs_raw, dict):
            designs = list(designs_raw.get("designs") or [])

        canvases: dict[str, dict[str, Any]] = {}
        legacy_canvas: dict[str, Any] | None = None
        if isinstance(canvas_raw, dict):
            if "canvases" in canvas_raw:
                canvases = dict(canvas_raw["canvases"])
            elif "nodes" in canvas_raw or "edges" in canvas_raw:
                # Legacy single-canvas blob.
                legacy_canvas = canvas_raw

        dirty = False
        if not designs:
            default = self._new_design(
                DEFAULT_DESIGN_NAME, DEFAULT_DESIGN_ICON, DEFAULT_DESIGN_TYPE
            )
            designs = [default]
            canvases = {
                default["id"]: legacy_canvas or copy.deepcopy(_EMPTY_CANVAS)
            }
            dirty = True
        else:
            # Guarantee every design has a canvas entry (defensive).
            for d in designs:
                if d["id"] not in canvases:
                    canvases[d["id"]] = copy.deepcopy(_EMPTY_CANVAS)
                    dirty = True

        self._designs = designs
        self._canvases = canvases
        if dirty:
            await self._save_designs()
            await self._save_canvases()

    async def _save_designs(self) -> None:
        await self.designs_store.async_save({"designs": self._designs})

    async def _save_canvases(self) -> None:
        await self.canvas_store.async_save({"canvases": self._canvases})

    async def _resolve_design_id(self, design_id: str | None) -> str | None:
        """Return a valid design id: the requested one if it exists, else the
        first (default) design. None only if no designs exist at all."""
        await self._ensure_loaded()
        assert self._designs is not None
        if design_id and any(d["id"] == design_id for d in self._designs):
            return design_id
        return self._designs[0]["id"] if self._designs else None

    async def list_designs(self) -> list[dict[str, Any]]:
        await self._ensure_loaded()
        assert self._designs is not None
        return self._designs

    async def create_design(
        self,
        name: str,
        icon: str = DEFAULT_DESIGN_ICON,
        design_type: str = DEFAULT_DESIGN_TYPE,
    ) -> dict[str, Any]:
        await self._ensure_loaded()
        assert self._designs is not None and self._canvases is not None
        design = self._new_design(name, icon, design_type)
        self._designs.append(design)
        self._canvases[design["id"]] = copy.deepcopy(_EMPTY_CANVAS)
        await self._save_designs()
        await self._save_canvases()
        return design

    async def update_design(
        self,
        design_id: str,
        *,
        name: str | None = None,
        icon: str | None = None,
    ) -> dict[str, Any] | None:
        await self._ensure_loaded()
        assert self._designs is not None
        for d in self._designs:
            if d["id"] == design_id:
                if name is not None:
                    d["name"] = name
                if icon is not None:
                    d["icon"] = icon
                d["updated_at"] = _utc_now_iso()
                await self._save_designs()
                return d
        return None

    async def delete_design(self, design_id: str) -> str:
        """Delete a design and its canvas.

        Returns ``"ok"`` on success, ``"last"`` if it's the only design (refused),
        or ``"not_found"`` if no such design exists.
        """
        await self._ensure_loaded()
        assert self._designs is not None and self._canvases is not None
        if not any(d["id"] == design_id for d in self._designs):
            return "not_found"
        if len(self._designs) <= 1:
            return "last"
        self._designs = [d for d in self._designs if d["id"] != design_id]
        self._canvases.pop(design_id, None)
        await self._save_designs()
        await self._save_canvases()
        return "ok"

    # ─── Canvas ──────────────────────────────────────────────────────────────

    async def get_canvas(self, design_id: str | None = None) -> dict[str, Any]:
        """Return the canvas for a design (default design if omitted)."""
        await self._ensure_loaded()
        assert self._canvases is not None
        did = await self._resolve_design_id(design_id)
        if did is None:
            return copy.deepcopy(_EMPTY_CANVAS)
        return self._canvases.setdefault(did, copy.deepcopy(_EMPTY_CANVAS))

    async def save_canvas(
        self, canvas: dict[str, Any], design_id: str | None = None
    ) -> None:
        """Persist the canvas under a design (default design if omitted)."""
        await self._ensure_loaded()
        assert self._canvases is not None
        did = await self._resolve_design_id(design_id)
        if did is None:
            # No designs at all (shouldn't happen after _ensure_loaded seeds a
            # default); create one and assign so data is never dropped.
            design = await self.create_design(
                DEFAULT_DESIGN_NAME, DEFAULT_DESIGN_ICON, DEFAULT_DESIGN_TYPE
            )
            did = design["id"]
        self._canvases[did] = canvas
        await self._save_canvases()

    def _all_canvas_nodes(self) -> list[dict[str, Any]]:
        """Flatten nodes across every design's canvas (status + scan exclusion)."""
        if not self._canvases:
            return []
        nodes: list[dict[str, Any]] = []
        for canvas in self._canvases.values():
            nodes.extend(canvas.get("nodes", []))
        return nodes

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

    async def list_pending(
        self, *, status: str = "pending", source: str | None = None
    ) -> list[dict[str, Any]]:
        """Return pending devices filtered by status and (optionally) source.

        Devices written before the `source` field existed are treated as "scan".
        Zigbee-specific fields (`ieee_address`, `friendly_name`, ...) are
        stored under `data_extras` to keep the schema additive; flatten them
        into the top-level dict for the wire so the frontend sees one shape.
        """
        store = await self._get_pending()
        out = [d for d in store["devices"] if d.get("status") == status]
        if source is not None:
            out = [d for d in out if (d.get("source") or "scan") == source]
        return [self._flatten_pending(d) for d in out]

    @staticmethod
    def _flatten_pending(device: dict[str, Any]) -> dict[str, Any]:
        extras = device.get("data_extras") or {}
        if not extras:
            return device
        merged = dict(device)
        # data_extras keys never collide with the base shape (ip/mac/hostname...);
        # if they ever do, the base wins to keep scan-discovered data primary.
        for k, v in extras.items():
            merged.setdefault(k, v)
        return merged

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

    async def restore_pending(self, device_id: str) -> bool:
        """Flip a hidden device back to pending. Returns True if found."""
        store = await self._get_pending()
        for d in store["devices"]:
            if d["id"] == device_id and d.get("status") == "hidden":
                d["status"] = "pending"
                await self._save_pending()
                return True
        return False

    async def _create_zigbee_parent_edge(
        self, child_node: dict[str, Any], design_id: str | None = None
    ) -> dict[str, Any] | None:
        """If child is a zigbee node with a known parent on the same design's
        canvas, append a parent → child edge to that canvas and return it.

        Idempotent: skips if an edge with the same source+target already exists.
        """
        # Nodes may be flat (top-level fields) or nested ({data: {...}}) depending
        # on whether they were just approved or round-tripped through the
        # frontend; read both shapes.
        data = child_node.get("data") or {}
        parent_ieee = child_node.get("parent_id") or data.get("parent_id")
        if not parent_ieee:
            return None
        design_id = await self._resolve_design_id(design_id)
        canvas = await self.get_canvas(design_id)
        parent = next(
            (
                n
                for n in canvas.get("nodes", [])
                if (n.get("ieee_address") or (n.get("data") or {}).get("ieee_address"))
                == parent_ieee
            ),
            None,
        )
        if parent is None:
            return None
        edge_id = f"e-{parent['id']}-{child_node['id']}"
        existing = canvas.setdefault("edges", [])
        if any(
            e.get("source") == parent["id"] and e.get("target") == child_node["id"]
            for e in existing
        ):
            return None
        edge = {
            "id": edge_id,
            "source": parent["id"],
            "target": child_node["id"],
            "sourceHandle": "bottom",
            "targetHandle": "top-t",
            "type": "iot",
            "data": {"type": "iot"},
        }
        existing.append(edge)
        await self.save_canvas(canvas, design_id)
        return edge

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
        # Approve onto the active design (falls back to the default design).
        # `design_id` is carried on overrides but never leaks into node fields —
        # the node dict below only copies explicit keys + data_extras + data.
        design_id = await self._resolve_design_id(overrides.get("design_id"))
        node_type = overrides.get("type") or device.get("suggested_type") or "generic"
        # Zigbee devices are imported one-shot from Z2M; no live status check
        # is possible, so default check_method to "none" (status_checker treats
        # "none" as always-online).
        is_zigbee = (
            device.get("source") == "zigbee" or node_type.startswith("zigbee_")
        )
        default_check = "none" if is_zigbee else "ping"
        # Zigbee devices report from Z2M as reachable, so they land online; the
        # status checker never polls them (check_method "none").
        default_status = "online" if is_zigbee else "unknown"
        # Zigbee devices carry their own canonical fields (ieee_address, model,
        # vendor, lqi, parent_id) under data_extras; merge them so the node on
        # the canvas has everything the zigbee node component renders.
        data_extras = device.get("data_extras") or {}
        # Surface IEEE/Vendor/Model/LQI as right-panel property rows (hidden by
        # default — users opt in to showing them on the canvas card).
        zigbee_props = (
            zigbee.build_zigbee_properties(
                data_extras.get("ieee_address"),
                data_extras.get("vendor"),
                data_extras.get("model"),
                data_extras.get("lqi"),
            )
            if is_zigbee
            else []
        )
        # Canvas nodes are stored FLAT (top-level ip/services/pos_x/...), to
        # match what the frontend serializes on Save and reads back on load
        # (deserializeApiNode). Building a nested {position, data:{...}} node
        # here would deserialize to an empty node (undefined ip/services/pos)
        # on the next reload, until the user happens to press Save and the
        # frontend rewrites every node flat. Keep it flat from the start.
        position = overrides.get("position") or {"x": 0, "y": 0}
        node = {
            "id": overrides.get("id")
            or data_extras.get("ieee_address")
            or f"node-{uuid.uuid4().hex[:8]}",
            "type": node_type,
            "label": overrides.get("label")
            or device.get("hostname")
            or device.get("ip")
            or data_extras.get("friendly_name"),
            "ip": device.get("ip"),
            "mac": device.get("mac"),
            "hostname": device.get("hostname"),
            "os": device.get("os"),
            "services": device.get("services", []),
            "status": overrides.get("status", default_status),
            "check_method": overrides.get("check_method", default_check),
            "properties": zigbee_props,
            "pos_x": position.get("x", 0),
            "pos_y": position.get("y", 0),
            **data_extras,
            **overrides.get("data", {}),
        }
        # Non-zigbee nodes carry the scanned MAC as a hidden property row so the
        # user can opt in to showing it on the canvas card. Merge (rather than
        # overwrite) to preserve any properties carried on the approve payload.
        if not is_zigbee:
            node["properties"] = merge_mac_property(
                node.get("properties"), device.get("mac")
            )

        canvas = await self.get_canvas(design_id)
        canvas.setdefault("nodes", []).append(node)
        await self.save_canvas(canvas, design_id)

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
        await self._ensure_loaded()
        pending = await self._get_pending()
        canvas_ips = {
            n.get("ip") or n.get("data", {}).get("ip")
            for n in self._all_canvas_nodes()
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
        for d in list(pending["devices"]):
            if d.get("status") == "discovering" and d["ip"] not in scanned_ips:
                pending["devices"].remove(d)

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

    # ─── Zigbee2MQTT ─────────────────────────────────────────────────────────

    def get_zigbee_base_topic(self) -> str:
        return self.entry.options.get(
            CONF_ZIGBEE_BASE_TOPIC,
            self.entry.data.get(CONF_ZIGBEE_BASE_TOPIC, DEFAULT_ZIGBEE_BASE_TOPIC),
        )

    async def fetch_zigbee_networkmap(
        self,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Trigger a Z2M networkmap request and return parsed (nodes, edges)."""
        return await zigbee.fetch_networkmap(self.hass, self.get_zigbee_base_topic())

    async def import_zigbee_devices(
        self, devices: list[dict[str, Any]]
    ) -> dict[str, int]:
        """Push selected Zigbee devices into the pending store.

        Each entry in `devices` is a parsed networkmap node dict from
        ``zigbee.parse_networkmap``. Already-pending IEEE addresses are skipped;
        already-approved (on-canvas) devices have their IEEE/Vendor/Model/LQI
        properties refreshed instead of being re-added.

        Returns: ``{"added": N, "skipped": M, "refreshed": K}``.
        """
        pending = await self._get_pending()
        await self._ensure_loaded()
        assert self._canvases is not None

        # IEEE addresses already represented anywhere — avoid duplicates.
        # Scan every design's canvas; nodes may be flat (top-level ieee_address)
        # or nested under `data`. Map ieee -> (design_id, node) so a refresh
        # saves the right canvas.
        canvas_by_ieee: dict[str, tuple[str, dict[str, Any]]] = {}
        for did, canvas in self._canvases.items():
            for n in canvas.get("nodes", []):
                ieee = n.get("ieee_address") or n.get("data", {}).get("ieee_address")
                if ieee:
                    canvas_by_ieee[ieee] = (did, n)
        on_canvas = set(canvas_by_ieee)
        already_pending = {
            d.get("data_extras", {}).get("ieee_address")
            for d in pending["devices"]
            if d.get("source") == "zigbee"
        }
        existing = on_canvas | already_pending

        added = 0
        skipped = 0
        refreshed = 0
        dirty_designs: set[str] = set()
        now = _utc_now_iso()
        for dev in devices:
            ieee = dev.get("ieee_address") or dev.get("id")
            if not ieee:
                skipped += 1
                continue
            # Already approved onto a canvas: refresh its IEEE/Vendor/Model/LQI
            # props (preserving the user's visibility choices) and skip creating
            # a pending row, so approved devices stay out of pending/hidden.
            entry = canvas_by_ieee.get(ieee)
            if entry is not None:
                did, node = entry
                props = zigbee.build_zigbee_properties(
                    ieee, dev.get("vendor"), dev.get("model"), dev.get("lqi")
                )
                node["properties"] = zigbee.merge_zigbee_properties(
                    node.get("properties"), props
                )
                dirty_designs.add(did)
                refreshed += 1
                continue
            if ieee in existing:
                skipped += 1
                continue
            pending["devices"].append(
                {
                    "id": f"pd-{uuid.uuid4().hex[:8]}",
                    "ip": None,
                    "mac": None,
                    "hostname": dev.get("friendly_name"),
                    "os": None,
                    "open_ports": [],
                    "services": [],
                    "suggested_type": dev.get("type"),
                    "discovery_source": "zigbee2mqtt",
                    "source": "zigbee",
                    "status": "pending",
                    "discovered_at": now,
                    "data_extras": {
                        "ieee_address": ieee,
                        "friendly_name": dev.get("friendly_name"),
                        "device_type": dev.get("device_type"),
                        "model": dev.get("model"),
                        "vendor": dev.get("vendor"),
                        "lqi": dev.get("lqi"),
                        "parent_id": dev.get("parent_id"),
                    },
                }
            )
            existing.add(ieee)
            added += 1

        if added:
            await self._save_pending()
        if dirty_designs:
            await self._save_canvases()
        return {"added": added, "skipped": skipped, "refreshed": refreshed}
