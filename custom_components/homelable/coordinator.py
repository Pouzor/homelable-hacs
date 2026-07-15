"""DataUpdateCoordinator for Homelable."""
from __future__ import annotations

import copy
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from . import proxmox, scanner, status_checker, zigbee, zwave
from .const import (
    CONF_PROXMOX_HOST,
    CONF_PROXMOX_PORT,
    CONF_PROXMOX_SYNC_ENABLED,
    CONF_PROXMOX_SYNC_INTERVAL,
    CONF_PROXMOX_TOKEN_ID,
    CONF_PROXMOX_TOKEN_SECRET,
    CONF_PROXMOX_VERIFY_TLS,
    CONF_SCAN_RANGES,
    CONF_SERVICE_CHECK_ENABLED,
    CONF_SERVICE_CHECK_INTERVAL,
    CONF_STATUS_INTERVAL,
    CONF_ZIGBEE_BASE_TOPIC,
    CONF_ZWAVE_GATEWAY,
    CONF_ZWAVE_PREFIX,
    DEFAULT_DESIGN_ICON,
    DEFAULT_DESIGN_NAME,
    DEFAULT_DESIGN_TYPE,
    DEFAULT_PROXMOX_PORT,
    DEFAULT_PROXMOX_SYNC_ENABLED,
    DEFAULT_PROXMOX_SYNC_INTERVAL,
    DEFAULT_PROXMOX_VERIFY_TLS,
    DEFAULT_SCAN_RANGES,
    DEFAULT_SERVICE_CHECK_ENABLED,
    DEFAULT_SERVICE_CHECK_INTERVAL,
    DEFAULT_STATUS_INTERVAL,
    DEFAULT_ZIGBEE_BASE_TOPIC,
    DEFAULT_ZWAVE_GATEWAY,
    DEFAULT_ZWAVE_PREFIX,
    DOMAIN,
    MAX_SCAN_RUNS,
    MIN_PROXMOX_SYNC_INTERVAL,
    MIN_SERVICE_CHECK_INTERVAL,
    PROXMOX_SOURCE,
    SCAN_SIGNAL,
    SERVICE_STATUS_SIGNAL,
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

# Node lifecycle timestamps managed server-side (in the Store), never authored
# by the frontend. Stripped when comparing a node's user-editable content so a
# canvas save only bumps updated_at on a real change, and re-applied from the
# stored node so a frontend round-trip can't clobber them.
_NODE_TIMESTAMP_FIELDS = ("created_at", "updated_at", "last_scan", "last_seen")


def _node_content(node: dict[str, Any]) -> dict[str, Any]:
    """A node's user-editable content — everything except the server-managed
    lifecycle timestamps. Used to decide whether a save really changed a node."""
    return {k: v for k, v in node.items() if k not in _NODE_TIMESTAMP_FIELDS}


def _is_wireless(node_type: str | None) -> bool:
    """Zigbee + Z-Wave mesh devices share online status / no ICMP check."""
    return bool(node_type) and (
        node_type.startswith("zigbee_") or node_type.startswith("zwave_")
    )


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


def _add_source(sources: list[str] | None, source: str | None) -> list[str]:
    """Return ``sources`` with ``source`` appended if not already present.

    Backs the multi-valued ``discovery_sources`` set: a device found by more than
    one path (e.g. an IP scan *and* a Proxmox import) accumulates every source
    that has seen it, so it surfaces under each matching inventory filter. Order
    is preserved (origin first) and duplicates are dropped.
    """
    out = [s for s in (sources or []) if s]
    if source and source not in out:
        out.append(source)
    return out


def _match_pending_by_ip_or_mac(
    devices: list[dict[str, Any]], ip: str | None, mac: str | None
) -> dict[str, Any] | None:
    """First non-hidden pending device matching ``ip`` OR normalized ``mac``.

    The MAC join lets a re-scan reconcile with a device previously imported from
    Proxmox (which may have no IP but a known NIC MAC) instead of doubling up.
    """
    norm = proxmox.normalize_mac(mac)
    for d in devices:
        if d.get("status") == "hidden":
            continue
        if ip and d.get("ip") == ip:
            return d
        if norm and proxmox.normalize_mac(d.get("mac")) == norm:
            return d
    return None


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
        self._service_check_unsub: Callable[[], None] | None = None
        self._proxmox_sync_unsub: Callable[[], None] | None = None

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
            if _is_wireless(node_type):
                # Zigbee / Z-Wave devices are one-shot mesh imports; no live check.
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

        # Persist last_seen on every node a check just found up (handles nodes
        # copied across designs — matched by id, not the de-duped loop above).
        # This writes the canvas Store on any poll where something is online;
        # accepted so the inventory can surface a real "Last Seen".
        now = _utc_now_iso()
        dirty = False
        for node in self._all_canvas_nodes():
            res = results.get(node.get("id"))
            if res and res.get("status") == "online":
                node["last_seen"] = now
                dirty = True
        if dirty:
            await self._save_canvases()
        return results

    # ─── Per-service status checks (periodic, independent) ────────────────────

    def get_service_check_enabled(self) -> bool:
        """Whether per-service checks are enabled (options → data → default)."""
        return bool(
            self.entry.options.get(
                CONF_SERVICE_CHECK_ENABLED,
                self.entry.data.get(
                    CONF_SERVICE_CHECK_ENABLED, DEFAULT_SERVICE_CHECK_ENABLED
                ),
            )
        )

    def get_service_check_interval(self) -> int:
        """Service-check interval in seconds, floored at MIN_SERVICE_CHECK_INTERVAL."""
        raw = self.entry.options.get(
            CONF_SERVICE_CHECK_INTERVAL,
            self.entry.data.get(
                CONF_SERVICE_CHECK_INTERVAL, DEFAULT_SERVICE_CHECK_INTERVAL
            ),
        )
        try:
            return max(MIN_SERVICE_CHECK_INTERVAL, int(raw))
        except (TypeError, ValueError):
            return DEFAULT_SERVICE_CHECK_INTERVAL

    @callback
    def async_start_service_checks(self) -> None:
        """Schedule the periodic service-check job if enabled.

        Returns nothing; the unsub is stored and released by
        async_stop_service_checks (wired to entry unload in __init__.py).
        Reload on options change recreates the coordinator, so the interval is
        always picked up fresh — no live reschedule needed.
        """
        if not self.get_service_check_enabled():
            return
        interval = timedelta(seconds=self.get_service_check_interval())
        self._service_check_unsub = async_track_time_interval(
            self.hass, self._run_service_checks, interval
        )
        _LOGGER.debug(
            "Service checks every %ds", self.get_service_check_interval()
        )

    @callback
    def async_stop_service_checks(self) -> None:
        """Cancel the periodic service-check job, if running."""
        if self._service_check_unsub is not None:
            self._service_check_unsub()
            self._service_check_unsub = None

    async def _run_service_checks(self, _now: datetime | None = None) -> None:
        """Check every service of every canvas node; dispatch per-node results.

        Mirrors the node-status SSRF policy: hosts that resolve to unsafe
        addresses outside the configured scan ranges are reported offline.
        """
        await self._ensure_loaded()
        allowed_networks = status_checker._parse_allowed_networks(
            self.get_scan_ranges()
        )
        now = _utc_now_iso()
        seen: set[str] = set()
        for node in self._all_canvas_nodes():
            node_id = node.get("id")
            if not node_id or node_id in seen:
                continue
            seen.add(node_id)
            data = node.get("data") or {}
            services = node.get("services") or data.get("services") or []
            if not services:
                continue
            host = self._node_host(
                node.get("ip") or data.get("ip"),
                node.get("hostname") or data.get("hostname"),
            )
            try:
                statuses = await status_checker.check_services(
                    host, services, allowed_networks
                )
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("Service checks failed for %s: %s", node_id, exc)
                continue
            async_dispatcher_send(
                self.hass,
                SERVICE_STATUS_SIGNAL,
                {"node_id": node_id, "services": statuses, "checked_at": now},
            )

    @staticmethod
    def _node_host(ip: str | None, hostname: str | None) -> str | None:
        """Pick the address to probe services on: first IP, else hostname."""
        if ip:
            first = ip.split(",")[0].strip()
            if first:
                return first
        return hostname or None

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
        self._reconcile_node_timestamps(canvas, self._canvases.get(did))
        self._canvases[did] = canvas
        await self._save_canvases()

    @staticmethod
    def _reconcile_node_timestamps(
        canvas: dict[str, Any], prior: dict[str, Any] | None
    ) -> None:
        """Stamp/preserve node lifecycle timestamps on an incoming canvas save.

        The frontend round-trips every node on Save but never authors the
        timestamps, so the Store stays authoritative:

        - ``created_at`` / ``last_scan`` / ``last_seen`` are copied back from the
          previously stored node (matched by id) — a stale frontend value can't
          clobber them.
        - a node with no prior (freshly drawn on the canvas) gets
          ``created_at = updated_at = now`` and null scan/seen.
        - ``updated_at`` bumps to now only when the node's user-editable content
          actually changed; an unrelated save (pan, another node moved) leaves
          untouched nodes' ``updated_at`` alone.
        """
        prior_by_id = {
            n.get("id"): n
            for n in (prior or {}).get("nodes", [])
            if n.get("id")
        }
        now = _utc_now_iso()
        for node in canvas.get("nodes", []):
            old = prior_by_id.get(node.get("id"))
            if old is None:
                node["created_at"] = node.get("created_at") or now
                node["updated_at"] = now
                node.setdefault("last_scan", None)
                node.setdefault("last_seen", None)
                continue
            # Store is authoritative for these — re-apply from the stored node.
            node["created_at"] = old.get("created_at") or now
            node["last_scan"] = old.get("last_scan")
            node["last_seen"] = old.get("last_seen")
            changed = _node_content(node) != _node_content(old)
            node["updated_at"] = now if changed else (old.get("updated_at") or now)

    def _all_canvas_nodes(self) -> list[dict[str, Any]]:
        """Flatten nodes across every design's canvas (status + scan exclusion)."""
        if not self._canvases:
            return []
        nodes: list[dict[str, Any]] = []
        for canvas in self._canvases.values():
            nodes.extend(canvas.get("nodes", []))
        return nodes

    def _stamp_last_scan(self, devices: list[dict[str, Any]], now: str) -> bool:
        """Stamp ``last_scan`` on every canvas node a scan observed.

        A node matches a scanned device by ip or mac (nodes are stored flat but
        may carry the values under ``data`` after a frontend round-trip, so read
        both). Returns True if any node was stamped, so the caller can persist
        the canvas Store once.
        """
        scanned_ips = {d.get("ip") for d in devices if d.get("ip")}
        scanned_macs = {d.get("mac") for d in devices if d.get("mac")}
        changed = False
        for node in self._all_canvas_nodes():
            data = node.get("data") or {}
            ip = node.get("ip") or data.get("ip")
            mac = node.get("mac") or data.get("mac")
            if (ip and ip in scanned_ips) or (mac and mac in scanned_macs):
                node["last_scan"] = now
                changed = True
        return changed

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

    def _canvas_node_index(
        self,
    ) -> tuple[
        dict[str, list[tuple[str, dict[str, Any]]]],
        dict[str, list[tuple[str, dict[str, Any]]]],
    ]:
        """Index canvas nodes by ip / ieee_address → list of (design_id, node).

        Backs both the canvas-count badge and the linked-node timestamps on the
        inventory. Nodes are stored flat (top-level ip/ieee_address) but may also
        carry the values under `data` after a frontend round-trip, so read both.
        """
        by_ip: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        by_ieee: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        for design_id, canvas in (self._canvases or {}).items():
            for n in canvas.get("nodes", []):
                data = n.get("data") or {}
                ip = n.get("ip") or data.get("ip")
                ieee = n.get("ieee_address") or data.get("ieee_address")
                if ip:
                    by_ip.setdefault(ip, []).append((design_id, n))
                if ieee:
                    by_ieee.setdefault(ieee, []).append((design_id, n))
        return by_ip, by_ieee

    @staticmethod
    def _agg_timestamp(values: list[str | None], *, newest: bool) -> str | None:
        """Pick the newest (max) or oldest (min) ISO timestamp, or None.

        Parses to datetime so a missing-microseconds string can't misorder
        lexicographically; returns the chosen value's original string.
        """
        parsed: list[tuple[datetime, str]] = []
        for v in values:
            if not v:
                continue
            try:
                parsed.append((datetime.fromisoformat(v.replace("Z", "+00:00")), v))
            except (ValueError, AttributeError):
                continue
        if not parsed:
            return None
        return (max(parsed) if newest else min(parsed))[1]

    def _design_placed_keys(
        self, design_id: str | None
    ) -> tuple[set[str], set[str]]:
        """(ips, ieee_addresses) already placed on a design's canvas."""
        ips: set[str] = set()
        ieees: set[str] = set()
        if not design_id:
            return ips, ieees
        canvas = (self._canvases or {}).get(design_id) or {}
        for n in canvas.get("nodes", []):
            data = n.get("data") or {}
            ip = n.get("ip") or data.get("ip")
            ieee = n.get("ieee_address") or data.get("ieee_address")
            if ip:
                ips.add(ip)
            if ieee:
                ieees.add(ieee)
        return ips, ieees

    async def list_pending(
        self, *, status: str = "pending", source: str | None = None
    ) -> list[dict[str, Any]]:
        """Return inventory devices filtered by status and (optionally) source.

        `status="pending"` is the Device Inventory view: it returns every
        non-hidden device — freshly discovered (`pending`) AND already approved
        onto a canvas (`approved`) — each badged with a `canvas_count` of how
        many canvases it appears on. `status="hidden"` returns hidden devices.

        Devices written before the `source` field existed are treated as "scan".
        Zigbee-specific fields (`ieee_address`, `friendly_name`, ...) are
        stored under `data_extras` to keep the schema additive; flatten them
        into the top-level dict for the wire so the frontend sees one shape.
        """
        await self._ensure_loaded()
        store = await self._get_pending()
        if status == "pending":
            # Inventory view: pending + approved. Transient "discovering" rows
            # (mid-scan, not yet enriched) and hidden rows are excluded.
            out = [
                d for d in store["devices"] if d.get("status") in ("pending", "approved")
            ]
        else:
            # Exact-status query (e.g. "hidden", "discovering").
            out = [d for d in store["devices"] if d.get("status") == status]
        if source is not None:
            out = [d for d in out if (d.get("source") or "scan") == source]

        by_ip, by_ieee = self._canvas_node_index()
        wire: list[dict[str, Any]] = []
        for d in out:
            # Copy so the transient fields never leak back into the store.
            fd = dict(self._flatten_pending(d))
            matched: list[tuple[str, dict[str, Any]]] = []
            ieee = fd.get("ieee_address")
            if ieee:
                matched += by_ieee.get(ieee, [])
            if fd.get("ip"):
                matched += by_ip.get(fd["ip"], [])
            # De-duplicate nodes matched by both ip and ieee_address.
            matched = list({id(n): (did, n) for did, n in matched}.values())
            designs = {did for did, _ in matched}
            nodes = [n for _, n in matched]
            fd["canvas_count"] = len(designs)
            # Linked-node timestamps: created = oldest across matches; last_scan
            # / last_modified / last_seen = newest. Null when not on any canvas.
            fd["node_created_at"] = self._agg_timestamp(
                [n.get("created_at") for n in nodes], newest=False
            )
            fd["node_last_scan"] = self._agg_timestamp(
                [n.get("last_scan") for n in nodes], newest=True
            )
            fd["node_last_modified"] = self._agg_timestamp(
                [n.get("updated_at") for n in nodes], newest=True
            )
            fd["node_last_seen"] = self._agg_timestamp(
                [n.get("last_seen") for n in nodes], newest=True
            )
            wire.append(fd)
        return wire

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

    async def _create_wireless_parent_edge(
        self, child_node: dict[str, Any], design_id: str | None = None
    ) -> dict[str, Any] | None:
        """If child is a zigbee/zwave node with a known parent on the same
        design's canvas, append a parent → child edge to that canvas and return
        it.

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

    async def _create_proxmox_edges(
        self, node: dict[str, Any], design_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Materialize Proxmox link edges for a just-approved node.

        Two shapes, both resolved against nodes already on the same design's
        canvas (a peer not yet approved is skipped and picked up when it lands):
          - host→guest: the guest carries ``proxmox_parent`` (host ieee) →
            a vertical ``virtual`` edge (bottom → top).
          - host↔host: a cluster host carries ``cluster_peers`` (host ieees) →
            horizontal ``cluster`` edges (right → left). Both endpoints get a
            left + right handle so the connection points exist.
        Idempotent: an edge is skipped if one already joins the two nodes.
        """
        data = node.get("data") or {}
        parent_ieee = node.get("proxmox_parent") or data.get("proxmox_parent")
        peers = node.get("cluster_peers") or data.get("cluster_peers") or []
        if not parent_ieee and not peers:
            return []

        design_id = await self._resolve_design_id(design_id)
        canvas = await self.get_canvas(design_id)
        nodes = canvas.get("nodes", [])

        def _by_ieee(ieee: str) -> dict[str, Any] | None:
            return next(
                (
                    n
                    for n in nodes
                    if (n.get("ieee_address") or (n.get("data") or {}).get("ieee_address"))
                    == ieee
                ),
                None,
            )

        edges = canvas.setdefault("edges", [])

        def _linked(a_id: str, b_id: str) -> bool:
            return any(
                (e.get("source") == a_id and e.get("target") == b_id)
                or (e.get("source") == b_id and e.get("target") == a_id)
                for e in edges
            )

        created: list[dict[str, Any]] = []

        if parent_ieee:
            parent = _by_ieee(parent_ieee)
            if parent is not None and not _linked(parent["id"], node["id"]):
                edge = {
                    "id": f"e-{parent['id']}-{node['id']}",
                    "source": parent["id"],
                    "target": node["id"],
                    "sourceHandle": "bottom",
                    "targetHandle": "top",
                    "type": "virtual",
                    "data": {"type": "virtual"},
                }
                edges.append(edge)
                created.append(edge)

        for peer_ieee in peers:
            peer = _by_ieee(peer_ieee)
            if peer is None or _linked(node["id"], peer["id"]):
                continue
            # Either host can be source or target of a cluster edge, so grant both
            # a left and right connection point.
            for host in (node, peer):
                host["left_handles"] = max(int(host.get("left_handles") or 0), 1)
                host["right_handles"] = max(int(host.get("right_handles") or 0), 1)
            edge = {
                "id": f"e-{node['id']}-{peer['id']}",
                "source": node["id"],
                "target": peer["id"],
                "sourceHandle": "right",
                "targetHandle": "left",
                "type": "cluster",
                "data": {"type": "cluster"},
            }
            edges.append(edge)
            created.append(edge)

        if created:
            await self.save_canvas(canvas, design_id)
        return created

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
        # Zigbee / Z-Wave devices are imported one-shot from their mesh gateway;
        # no live status check is possible, so default check_method to "none"
        # (status_checker treats "none" as always-online).
        is_wireless = (
            device.get("source") in ("zigbee", "zwave")
            or node_type.startswith("zigbee_")
            or node_type.startswith("zwave_")
        )
        is_zwave = device.get("source") == "zwave" or node_type.startswith("zwave_")
        # Non-wireless devices get a ping check, but a Proxmox guest imported
        # without an IP (stopped VM / no guest agent) has nothing to ping, so
        # fall back to "none" rather than a check that always reports offline.
        default_check = "none" if is_wireless else ("ping" if device.get("ip") else "none")
        # Mesh devices report from their gateway as reachable, so they land
        # online; the status checker never polls them (check_method "none").
        default_status = "online" if is_wireless else "unknown"
        # Mesh devices carry their own canonical fields (ieee_address, model,
        # vendor, lqi, parent_id) under data_extras; merge them so the node on
        # the canvas has everything the zigbee/zwave node component renders.
        data_extras = device.get("data_extras") or {}
        # Surface Identity/Vendor/Model/LQI as right-panel property rows (hidden
        # by default — users opt in to showing them on the canvas card). Z-Wave
        # has no LQI row.
        if not is_wireless:
            # Proxmox carries display specs (CPU/RAM/Disk/VMID) on the pending
            # row; scan devices carry none. Copy so store rows aren't aliased.
            wireless_props: list[dict[str, Any]] = [
                dict(p) for p in (device.get("properties") or [])
            ]
        elif is_zwave:
            wireless_props = zwave.build_zwave_properties(
                data_extras.get("ieee_address"),
                data_extras.get("vendor"),
                data_extras.get("model"),
            )
        else:
            wireless_props = zigbee.build_zigbee_properties(
                data_extras.get("ieee_address"),
                data_extras.get("vendor"),
                data_extras.get("model"),
                data_extras.get("lqi"),
            )
        # Canvas nodes are stored FLAT (top-level ip/services/pos_x/...), to
        # match what the frontend serializes on Save and reads back on load
        # (deserializeApiNode). Building a nested {position, data:{...}} node
        # here would deserialize to an empty node (undefined ip/services/pos)
        # on the next reload, until the user happens to press Save and the
        # frontend rewrites every node flat. Keep it flat from the start.
        position = overrides.get("position") or {"x": 0, "y": 0}
        now = _utc_now_iso()
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
            "properties": wireless_props,
            "pos_x": position.get("x", 0),
            "pos_y": position.get("y", 0),
            **data_extras,
            **overrides.get("data", {}),
            # Inventory lifecycle timestamps (authoritative — set after the
            # spreads so a frontend-supplied `data` blob can never inject them).
            # created_at / updated_at start equal; last_scan is stamped when a
            # scan observes this node, last_seen when a status check finds it up.
            "created_at": now,
            "updated_at": now,
            "last_scan": None,
            "last_seen": None,
        }
        # Non-mesh nodes carry the scanned MAC as a hidden property row so the
        # user can opt in to showing it on the canvas card. Merge (rather than
        # overwrite) to preserve any properties carried on the approve payload.
        if not is_wireless:
            node["properties"] = merge_mac_property(
                node.get("properties"), device.get("mac")
            )

        canvas = await self.get_canvas(design_id)
        canvas.setdefault("nodes", []).append(node)
        await self.save_canvas(canvas, design_id)

        # Device Inventory: keep the row, flip it to "approved" rather than
        # deleting it, so the device stays listed and gets badged with the
        # number of canvases it appears on (see list_pending / _canvas_node_index).
        device["status"] = "approved"
        await self._save_pending()
        return node

    async def approve_batch(
        self, device_ids: list[str], overrides: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Approve several devices onto the active design in one pass.

        Skips any device already placed on that design (matched by ip or
        ieee_address) — including a duplicate selection within this same batch —
        so a re-approve or a doubled id never creates a duplicate node. Canvas
        membership is per-design, so a device already approved onto *another*
        canvas is still placed here.
        """
        overrides = overrides or {}
        await self._ensure_loaded()
        design_id = await self._resolve_design_id(overrides.get("design_id"))
        placed_ips, placed_ieee = self._design_placed_keys(design_id)
        pending = await self._get_pending()
        by_id = {d["id"]: d for d in pending["devices"]}

        nodes: list[dict[str, Any]] = []
        node_ids: list[str] = []
        approved_ids: list[str] = []
        edges: list[dict[str, Any]] = []
        skipped: list[str] = []
        not_found: list[str] = []
        for device_id in device_ids:
            device = by_id.get(device_id)
            if device is None:
                not_found.append(device_id)
                continue
            ip = device.get("ip")
            ieee = (device.get("data_extras") or {}).get("ieee_address")
            if (ip and ip in placed_ips) or (ieee and ieee in placed_ieee):
                skipped.append(device_id)
                continue
            node = await self.approve_pending(device_id, overrides)
            if node is None:
                not_found.append(device_id)
                continue
            nodes.append(node)
            node_ids.append(node["id"])
            approved_ids.append(device_id)
            # Track within the batch so a repeated ip/ieee isn't placed twice.
            if ip:
                placed_ips.add(ip)
            if ieee:
                placed_ieee.add(ieee)
            auto_edge = await self._create_wireless_parent_edge(node, design_id)
            if auto_edge:
                edges.append(auto_edge)
            edges.extend(await self._create_proxmox_edges(node, design_id))
        return {
            "approved": len(nodes),
            "nodes": nodes,
            "device_ids": approved_ids,
            "node_ids": node_ids,
            "edges": edges,
            "edges_created": len(edges),
            "skipped": skipped,
            "not_found": not_found,
        }

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

    async def trigger_scan(
        self,
        *,
        http_ranges: list[str] | None = None,
        http_probe_enabled: bool = False,
        verify_tls: bool = False,
    ) -> dict[str, Any]:
        """Kick off a scan in the background. Returns immediately.

        Response: {run_id, status: "running"|"already_running", devices_found: 0, new_devices: 0}.
        UI polls history for progress / completion.

        Deep-scan options (per-scan; not persisted) extend the port list and run
        an HTTP probe so services on custom ports can be identified.
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
        # Device Inventory: on-canvas devices are intentionally NOT excluded any
        # more — they stay in the inventory and are badged with a canvas count.
        # Only user-hidden devices are suppressed.
        hidden_ips = {d["ip"] for d in pending["devices"] if d.get("status") == "hidden"}
        exclude = hidden_ips

        deep_scan = scanner.DeepScanOptions(
            http_ranges=list(http_ranges or []),
            http_probe_enabled=bool(http_probe_enabled),
            verify_tls=bool(verify_tls),
        )

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
            self._run_scan_task(run_id, ranges, exclude, started_at, deep_scan)
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
            src = device.get("discovery_source")
            norm_mac = proxmox.normalize_mac(device.get("mac"))
            existing = _match_pending_by_ip_or_mac(
                pending["devices"], ip, device.get("mac")
            )
            if existing is None:
                pending["devices"].append(
                    {
                        "id": f"pd-{uuid.uuid4().hex[:8]}",
                        "ip": ip,
                        "mac": norm_mac,
                        "hostname": device.get("hostname"),
                        "os": None,
                        "open_ports": [],
                        "services": [],
                        "suggested_type": None,
                        "discovery_source": src,
                        "discovery_sources": [src] if src else [],
                        "status": "discovering",
                        "discovered_at": _utc_now_iso(),
                    }
                )
            elif existing.get("status") in ("discovering", "pending", "approved"):
                # Refresh meta if we got better info this run (approved rows keep
                # their status — they just get fresher fields). Fill an IP a
                # Proxmox import lacked and union the scan source.
                existing["ip"] = existing.get("ip") or ip
                existing["mac"] = norm_mac or existing.get("mac")
                existing["hostname"] = (
                    device.get("hostname") or existing.get("hostname")
                )
                existing["discovery_sources"] = _add_source(
                    existing.get("discovery_sources"), src
                )

        elif event == "device_enriched" and ip:
            pending = await self._get_pending()
            src = device.get("discovery_source")
            norm_mac = proxmox.normalize_mac(device.get("mac"))
            existing = _match_pending_by_ip_or_mac(
                pending["devices"], ip, device.get("mac")
            )
            if existing is None:
                # mDNS-only path can land here without a prior discovery event
                # for hosts that didn't answer ping. Create the entry directly.
                pending["devices"].append(
                    {
                        "id": f"pd-{uuid.uuid4().hex[:8]}",
                        "ip": ip,
                        "mac": norm_mac,
                        "hostname": device.get("hostname"),
                        "os": device.get("os"),
                        "open_ports": device.get("open_ports", []),
                        "services": device.get("services", []),
                        "suggested_type": device.get("suggested_type"),
                        "discovery_source": src,
                        "discovery_sources": [src] if src else [],
                        "status": "pending",
                        "discovered_at": _utc_now_iso(),
                    }
                )
            elif existing.get("status") in ("discovering", "pending", "approved"):
                # Approved (on-canvas) devices keep their status on re-scan; only
                # their scanned fields refresh. Don't downgrade a Proxmox-typed
                # guest (vm/lxc) to the generic scan guess — the importer knows
                # the true type. Fill an IP a Proxmox import lacked; union source.
                keep_approved = existing.get("status") == "approved"
                is_pve = str(existing.get("ieee_address") or "").startswith("pve-")
                existing.update(
                    {
                        "ip": existing.get("ip") or ip,
                        "mac": norm_mac or existing.get("mac"),
                        "hostname": device.get("hostname") or existing.get("hostname"),
                        "os": device.get("os") or existing.get("os"),
                        "open_ports": device.get("open_ports", []),
                        "services": device.get("services", []),
                        "suggested_type": existing.get("suggested_type")
                        if is_pve
                        else device.get("suggested_type"),
                        "status": "approved" if keep_approved else "pending",
                    }
                )
                existing["discovery_sources"] = _add_source(
                    existing.get("discovery_sources"), src
                )
            # Echo the stored device id back so the frontend can reconcile.
            stored = _match_pending_by_ip_or_mac(
                pending["devices"], ip, device.get("mac")
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
        deep_scan: scanner.DeepScanOptions | None = None,
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
                deep_scan=deep_scan,
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
            src = dev.get("discovery_source")
            norm_mac = proxmox.normalize_mac(dev.get("mac"))
            existing = _match_pending_by_ip_or_mac(
                pending["devices"], dev["ip"], dev.get("mac")
            )
            if existing is None:
                pending["devices"].append(
                    {
                        "id": f"pd-{uuid.uuid4().hex[:8]}",
                        "ip": dev["ip"],
                        "mac": norm_mac,
                        "hostname": dev.get("hostname"),
                        "os": dev.get("os"),
                        "open_ports": dev.get("open_ports", []),
                        "services": dev.get("services", []),
                        "suggested_type": dev.get("suggested_type"),
                        "discovery_source": src,
                        "discovery_sources": [src] if src else [],
                        "status": "pending",
                        "discovered_at": now,
                    }
                )
            elif existing.get("status") in ("discovering", "pending", "approved"):
                # Approved (on-canvas) devices keep their status; fields refresh.
                # Preserve a Proxmox-typed guest's type + fill a missing IP; union
                # the scan source so the row shows under both inventory filters.
                keep_approved = existing.get("status") == "approved"
                is_pve = str(existing.get("ieee_address") or "").startswith("pve-")
                existing.update(
                    {
                        "ip": existing.get("ip") or dev["ip"],
                        "mac": norm_mac or existing.get("mac"),
                        "hostname": dev.get("hostname") or existing.get("hostname"),
                        "os": dev.get("os") or existing.get("os"),
                        "open_ports": dev.get("open_ports", []),
                        "services": dev.get("services", []),
                        "suggested_type": existing.get("suggested_type")
                        if is_pve
                        else dev.get("suggested_type"),
                        "status": "approved" if keep_approved else "pending",
                    }
                )
                existing["discovery_sources"] = _add_source(
                    existing.get("discovery_sources"), src
                )

        # Promote any leftover `discovering` entries from this scan that we
        # have data for, and drop ones that never enriched (cancelled mid-run).
        for d in list(pending["devices"]):
            if d.get("status") == "discovering" and d["ip"] not in scanned_ips:
                pending["devices"].remove(d)

        await self._save_pending()

        # Stamp last_scan on any canvas node this run observed (matched by ip or
        # mac). Done once here, at scan end, to match the "persist once" pattern
        # above rather than writing the canvas Store per host.
        if self._stamp_last_scan(devices, now):
            await self._save_canvases()

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

    async def trigger_zigbee_import(self) -> dict[str, Any]:
        """Kick off a Zigbee import in the background. Returns immediately.

        Records a ``kind="zigbee"`` scan run (running) and spawns the actual
        network-map fetch + pending-store write, so the import surfaces under
        Scan History with a live running → done transition — mirroring IP
        scans. The MQTT round-trip (which can take minutes on large meshes)
        runs in the background instead of blocking the UI. The panel polls
        history for progress / completion.

        Response: ``{run_id, status: "running", devices_found: 0}``.
        """
        run_id = uuid.uuid4().hex
        started_at = _utc_now_iso()
        base = self.get_zigbee_base_topic()
        await self._record_run(
            {
                "id": run_id,
                "status": "running",
                "kind": "zigbee",
                "ranges": [base] if base else [],
                "devices_found": 0,
                "started_at": started_at,
                "finished_at": None,
                "error": None,
            }
        )
        self.hass.async_create_task(
            self._run_zigbee_import_task(run_id, started_at)
        )
        return {"run_id": run_id, "status": "running", "devices_found": 0}

    async def _run_zigbee_import_task(
        self, run_id: str, started_at: str
    ) -> None:
        """Background Zigbee import body: fetch network map, import, record."""
        base = self.get_zigbee_base_topic()
        ranges = [base] if base else []
        try:
            nodes, _edges = await self.fetch_zigbee_networkmap()
            await self.import_zigbee_devices(nodes)
        except Exception as exc:  # noqa: BLE001 — record any failure, then exit
            _LOGGER.exception("Zigbee import %s failed", run_id)
            await self._record_run(
                {
                    "id": run_id,
                    "status": "error",
                    "kind": "zigbee",
                    "ranges": ranges,
                    "devices_found": 0,
                    "started_at": started_at,
                    "finished_at": _utc_now_iso(),
                    "error": str(exc),
                }
            )
            async_dispatcher_send(
                self.hass,
                SCAN_SIGNAL,
                {"event": "scan_error", "run_id": run_id, "error": str(exc)},
            )
            return
        await self._record_run(
            {
                "id": run_id,
                "status": "done",
                "kind": "zigbee",
                "ranges": ranges,
                "devices_found": len(nodes),
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
                "devices_found": len(nodes),
            },
        )

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
        return await self._import_wireless_devices(
            devices,
            source="zigbee",
            discovery_source="zigbee2mqtt",
            build_props=lambda ieee, vendor, model, lqi: zigbee.build_zigbee_properties(
                ieee, vendor, model, lqi
            ),
        )

    async def _import_wireless_devices(
        self,
        devices: list[dict[str, Any]],
        *,
        source: str,
        discovery_source: str,
        build_props,
    ) -> dict[str, int]:
        """Push selected Zigbee/Z-Wave mesh devices into the pending store.

        Shared body behind ``import_zigbee_devices`` / ``import_zwave_devices``.
        Already-pending identities (matched by source) are skipped;
        already-approved (on-canvas) devices have their property rows refreshed
        (via ``build_props``) instead of being re-added.

        Returns: ``{"added": N, "skipped": M, "refreshed": K}``.
        """
        pending = await self._get_pending()
        await self._ensure_loaded()
        assert self._canvases is not None

        # Identities already represented anywhere — avoid duplicates.
        # Scan every design's canvas; nodes may be flat (top-level ieee_address)
        # or nested under `data`. Map ieee -> [(design_id, node), ...]: the same
        # device is legitimately placed on more than one canvas (one node per
        # design), so a refresh must touch every matching node, not just one.
        canvas_by_ieee: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        for did, canvas in self._canvases.items():
            for n in canvas.get("nodes", []):
                ieee = n.get("ieee_address") or n.get("data", {}).get("ieee_address")
                if ieee:
                    canvas_by_ieee.setdefault(ieee, []).append((did, n))
        on_canvas = set(canvas_by_ieee)
        already_pending = {
            d.get("data_extras", {}).get("ieee_address")
            for d in pending["devices"]
            if d.get("source") == source
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
            # Already approved onto a canvas: refresh its property rows on
            # *every* canvas it sits on (preserving the user's visibility
            # choices) and skip creating a pending row, so approved devices stay
            # out of pending/hidden.
            matches = canvas_by_ieee.get(ieee)
            if matches:
                props = build_props(
                    ieee, dev.get("vendor"), dev.get("model"), dev.get("lqi")
                )
                for did, node in matches:
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
                    "discovery_source": discovery_source,
                    "source": source,
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

    # ─── Z-Wave JS UI ────────────────────────────────────────────────────────

    def get_zwave_config(self) -> tuple[str, str]:
        """Return the configured (prefix, gateway_name) for Z-Wave JS UI."""
        prefix = self.entry.options.get(
            CONF_ZWAVE_PREFIX,
            self.entry.data.get(CONF_ZWAVE_PREFIX, DEFAULT_ZWAVE_PREFIX),
        )
        gateway = self.entry.options.get(
            CONF_ZWAVE_GATEWAY,
            self.entry.data.get(CONF_ZWAVE_GATEWAY, DEFAULT_ZWAVE_GATEWAY),
        )
        return prefix, gateway

    async def fetch_zwave_network(
        self,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Trigger a Z-Wave getNodes request and return parsed (nodes, edges)."""
        prefix, gateway = self.get_zwave_config()
        return await zwave.fetch_zwave_network(self.hass, prefix, gateway)

    async def trigger_zwave_import(self) -> dict[str, Any]:
        """Kick off a Z-Wave import in the background. Returns immediately.

        Mirrors ``trigger_zigbee_import``: records a ``kind="zwave"`` scan run
        (running) and spawns the getNodes fetch + pending-store write so the
        import surfaces under Scan History with a running → done transition.

        Response: ``{run_id, status: "running", devices_found: 0}``.
        """
        run_id = uuid.uuid4().hex
        started_at = _utc_now_iso()
        prefix, gateway = self.get_zwave_config()
        target = f"{prefix}/{gateway}"
        await self._record_run(
            {
                "id": run_id,
                "status": "running",
                "kind": "zwave",
                "ranges": [target],
                "devices_found": 0,
                "started_at": started_at,
                "finished_at": None,
                "error": None,
            }
        )
        self.hass.async_create_task(
            self._run_zwave_import_task(run_id, started_at)
        )
        return {"run_id": run_id, "status": "running", "devices_found": 0}

    async def _run_zwave_import_task(
        self, run_id: str, started_at: str
    ) -> None:
        """Background Z-Wave import body: fetch node list, import, record."""
        prefix, gateway = self.get_zwave_config()
        ranges = [f"{prefix}/{gateway}"]
        try:
            nodes, _edges = await self.fetch_zwave_network()
            await self.import_zwave_devices(nodes)
        except Exception as exc:  # noqa: BLE001 — record any failure, then exit
            _LOGGER.exception("Z-Wave import %s failed", run_id)
            await self._record_run(
                {
                    "id": run_id,
                    "status": "error",
                    "kind": "zwave",
                    "ranges": ranges,
                    "devices_found": 0,
                    "started_at": started_at,
                    "finished_at": _utc_now_iso(),
                    "error": str(exc),
                }
            )
            async_dispatcher_send(
                self.hass,
                SCAN_SIGNAL,
                {"event": "scan_error", "run_id": run_id, "error": str(exc)},
            )
            return
        await self._record_run(
            {
                "id": run_id,
                "status": "done",
                "kind": "zwave",
                "ranges": ranges,
                "devices_found": len(nodes),
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
                "devices_found": len(nodes),
            },
        )

    async def import_zwave_devices(
        self, devices: list[dict[str, Any]]
    ) -> dict[str, int]:
        """Push selected Z-Wave devices into the pending store.

        Each entry is a parsed node dict from ``zwave.parse_zwave_nodes``.
        Z-Wave has no LQI, so the property builder omits that row.

        Returns: ``{"added": N, "skipped": M, "refreshed": K}``.
        """
        return await self._import_wireless_devices(
            devices,
            source="zwave",
            discovery_source="zwavejs2mqtt",
            build_props=lambda ieee, vendor, model, lqi: zwave.build_zwave_properties(
                ieee, vendor, model
            ),
        )

    # ─── Proxmox VE ──────────────────────────────────────────────────────────

    def _proxmox_opt(self, key: str, default: Any) -> Any:
        return self.entry.options.get(key, self.entry.data.get(key, default))

    def get_proxmox_credentials(self) -> tuple[str, str]:
        """(token_id, token_secret) from the config entry. Never sent to clients."""
        return (
            str(self._proxmox_opt(CONF_PROXMOX_TOKEN_ID, "") or ""),
            str(self._proxmox_opt(CONF_PROXMOX_TOKEN_SECRET, "") or ""),
        )

    def get_proxmox_sync_enabled(self) -> bool:
        return bool(
            self._proxmox_opt(CONF_PROXMOX_SYNC_ENABLED, DEFAULT_PROXMOX_SYNC_ENABLED)
        )

    def get_proxmox_sync_interval(self) -> int:
        """Auto-sync interval in seconds, floored at MIN_PROXMOX_SYNC_INTERVAL."""
        try:
            raw = int(
                self._proxmox_opt(
                    CONF_PROXMOX_SYNC_INTERVAL, DEFAULT_PROXMOX_SYNC_INTERVAL
                )
            )
        except (TypeError, ValueError):
            return DEFAULT_PROXMOX_SYNC_INTERVAL
        return max(MIN_PROXMOX_SYNC_INTERVAL, raw)

    def get_proxmox_config(self) -> dict[str, Any]:
        """Non-secret Proxmox config for the panel. Never includes the token —
        only whether one is configured (``token_configured``)."""
        token_id, token_secret = self.get_proxmox_credentials()
        return {
            "host": str(self._proxmox_opt(CONF_PROXMOX_HOST, "") or ""),
            "port": int(self._proxmox_opt(CONF_PROXMOX_PORT, DEFAULT_PROXMOX_PORT)),
            "verify_tls": bool(
                self._proxmox_opt(CONF_PROXMOX_VERIFY_TLS, DEFAULT_PROXMOX_VERIFY_TLS)
            ),
            "sync_enabled": self.get_proxmox_sync_enabled(),
            "sync_interval": self.get_proxmox_sync_interval(),
            "token_configured": bool(token_id and token_secret),
        }

    def resolve_proxmox_request(
        self,
        host: str | None = None,
        port: int | None = None,
        token_id: str | None = None,
        token_secret: str | None = None,
        verify_tls: bool | None = None,
    ) -> dict[str, Any]:
        """Merge a request's connection params over the configured defaults.

        A blank token in the request falls back to the entry-stored credential —
        the HA-native analogue of the standalone env-token fallback, so the panel
        never has to hold the secret. Raises ``ValueError`` when no host/token can
        be resolved.
        """
        cfg = self.get_proxmox_config()
        cfg_id, cfg_secret = self.get_proxmox_credentials()
        resolved = {
            "host": host or cfg["host"],
            "port": int(port or cfg["port"]),
            "token_id": token_id or cfg_id,
            "token_secret": token_secret or cfg_secret,
            "verify_tls": cfg["verify_tls"] if verify_tls is None else bool(verify_tls),
        }
        if not resolved["host"]:
            raise ValueError("No Proxmox host provided or configured.")
        if not resolved["token_id"] or not resolved["token_secret"]:
            raise ValueError(
                "No Proxmox API token provided or configured on the integration."
            )
        return resolved

    def _canvas_index_by_mac(self) -> dict[str, list[tuple[str, dict[str, Any]]]]:
        by_mac: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        for did, canvas in (self._canvases or {}).items():
            for n in canvas.get("nodes", []):
                data = n.get("data") or {}
                mac = proxmox.normalize_mac(n.get("mac") or data.get("mac"))
                if mac:
                    by_mac.setdefault(mac, []).append((did, n))
        return by_mac

    async def import_proxmox_pending(
        self,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        cluster_pairs: list[tuple[str, str]] | None = None,
    ) -> dict[str, int]:
        """Upsert a fetched Proxmox inventory into the pending store.

        Two-tier identity (order matters), mirroring the standalone importer:
          1. Match a device already on a canvas by ieee OR ip OR MAC (the
             cross-source key — a stopped VM has no IP but its NIC MAC matches an
             ARP-scanned node). Refresh its property rows in place and keep an
             inventory row so it stays listed.
          2. Else upsert the pending inventory row, merging onto a scanned row of
             the same device when one exists (union discovery sources).

        Host→guest and host↔host relationships are recorded on each pending
        device's ``data_extras`` (``proxmox_parent`` / ``cluster_peers``) and
        materialized as ``virtual`` / ``cluster`` edges on approve.

        Returns ``{"created": N, "updated": M, "device_count": K}``.
        """
        cluster_pairs = cluster_pairs or []
        await self._ensure_loaded()
        pending = await self._get_pending()

        guest_parent = {e["target"]: e["source"] for e in edges}
        peers_by_ieee: dict[str, list[str]] = {}
        for a, b in cluster_pairs:
            peers_by_ieee.setdefault(a, []).append(b)
            peers_by_ieee.setdefault(b, []).append(a)
        cluster_members = set(peers_by_ieee)

        by_ip, by_ieee = self._canvas_node_index()
        by_mac = self._canvas_index_by_mac()

        def _find_pending(
            ieee: str, ip: str | None, mac: str | None
        ) -> dict[str, Any] | None:
            for d in pending["devices"]:
                if (d.get("data_extras") or {}).get("ieee_address") == ieee:
                    return d
                if ip and d.get("ip") == ip:
                    return d
                if mac and proxmox.normalize_mac(d.get("mac")) == mac:
                    return d
            return None

        def _sources_after_merge(row: dict[str, Any]) -> list[str]:
            # Compute BEFORE the pve ieee is adopted, so the pre-merge origin is
            # visible. Preserve a scanned row's IP-source tag (incl. legacy rows
            # with no discovery_sources) so it survives the Proxmox merge.
            sources = _add_source(row.get("discovery_sources"), row.get("discovery_source"))
            was_scanned = not str(
                (row.get("data_extras") or {}).get("ieee_address") or ""
            ).startswith("pve-")
            if (
                was_scanned
                and row.get("ip")
                and not any(s in ("arp", "mdns", "tcp") for s in sources)
            ):
                sources = _add_source(sources, "arp")
            return _add_source(sources, PROXMOX_SOURCE)

        created = 0
        updated = 0
        now = _utc_now_iso()
        dirty_designs: set[str] = set()
        pending_dirty = False

        for n in nodes:
            ieee = n.get("ieee_address")
            if not ieee:
                continue
            ip = n.get("ip")
            mac = proxmox.normalize_mac(n.get("mac"))
            props = proxmox.build_proxmox_properties(n)
            extras = {
                "ieee_address": ieee,
                "friendly_name": n.get("label"),
                "vendor": n.get("vendor"),
                "model": n.get("model"),
                "proxmox_parent": guest_parent.get(ieee),
                "cluster_peers": peers_by_ieee.get(ieee, []),
            }

            # 1) Already on a canvas — refresh in place, don't duplicate.
            matches: list[tuple[str, dict[str, Any]]] = []
            seen_ids: set[int] = set()
            for bucket in (
                by_ieee.get(ieee, []),
                by_ip.get(ip, []) if ip else [],
                by_mac.get(mac, []) if mac else [],
            ):
                for did, cnode in bucket:
                    if id(cnode) in seen_ids:
                        continue
                    seen_ids.add(id(cnode))
                    matches.append((did, cnode))
            if matches:
                for did, cnode in matches:
                    cnode["properties"] = zigbee.merge_zigbee_properties(
                        cnode.get("properties"), props
                    )
                    cdata = cnode.get("data") or {}
                    if not (cnode.get("ieee_address") or cdata.get("ieee_address")):
                        cnode["ieee_address"] = ieee
                    if ip and not cnode.get("ip"):
                        cnode["ip"] = ip
                    if mac and not cnode.get("mac"):
                        cnode["mac"] = mac
                    if not cnode.get("hostname"):
                        cnode["hostname"] = n.get("hostname")
                    if ieee in cluster_members:
                        cnode["left_handles"] = max(int(cnode.get("left_handles") or 0), 1)
                        cnode["right_handles"] = max(int(cnode.get("right_handles") or 0), 1)
                    dirty_designs.add(did)
                # Keep an inventory row (approved) so the device stays listed.
                inv = _find_pending(ieee, ip, mac)
                if inv is None:
                    pending["devices"].append(
                        self._new_proxmox_pending(ieee, ip, mac, n, props, extras, "approved", now)
                    )
                else:
                    inv["discovery_sources"] = _sources_after_merge(inv)
                    self._refresh_proxmox_pending(inv, ieee, ip, mac, n, props, extras)
                pending_dirty = True
                updated += 1
                continue

            # 2) Not on a canvas — upsert the pending inventory row.
            existing = _find_pending(ieee, ip, mac)
            if existing is None:
                pending["devices"].append(
                    self._new_proxmox_pending(ieee, ip, mac, n, props, extras, "pending", now)
                )
                created += 1
            else:
                existing["discovery_sources"] = _sources_after_merge(existing)
                self._refresh_proxmox_pending(existing, ieee, ip, mac, n, props, extras)
                if existing.get("status") == "approved":
                    # Approved earlier but the canvas node is gone — revive it.
                    existing["status"] = "pending"
                updated += 1
            pending_dirty = True

        if pending_dirty:
            await self._save_pending()
        if dirty_designs:
            await self._save_canvases()
        return {"created": created, "updated": updated, "device_count": len(nodes)}

    @staticmethod
    def _new_proxmox_pending(
        ieee: str,
        ip: str | None,
        mac: str | None,
        n: dict[str, Any],
        props: list[dict[str, Any]],
        extras: dict[str, Any],
        status: str,
        now: str,
    ) -> dict[str, Any]:
        return {
            "id": f"pd-{uuid.uuid4().hex[:8]}",
            "ip": ip,
            "mac": mac,
            "hostname": n.get("hostname"),
            "os": None,
            "open_ports": [],
            "services": [],
            "suggested_type": n.get("type"),
            "discovery_source": PROXMOX_SOURCE,
            "discovery_sources": [PROXMOX_SOURCE],
            "source": PROXMOX_SOURCE,
            "status": status,
            "discovered_at": now,
            "properties": props,
            "data_extras": dict(extras),
        }

    @staticmethod
    def _refresh_proxmox_pending(
        row: dict[str, Any],
        ieee: str,
        ip: str | None,
        mac: str | None,
        n: dict[str, Any],
        props: list[dict[str, Any]],
        extras: dict[str, Any],
    ) -> None:
        """Merge a re-imported Proxmox device onto an existing inventory row.

        ``discovery_sources`` must already have been recomputed by the caller
        (it needs the pre-merge origin, before the pve ieee is adopted here).
        """
        de = row.setdefault("data_extras", {})
        if not de.get("ieee_address"):
            de["ieee_address"] = ieee
        de["proxmox_parent"] = extras.get("proxmox_parent") or de.get("proxmox_parent")
        de["cluster_peers"] = extras.get("cluster_peers") or de.get("cluster_peers") or []
        for key in ("friendly_name", "vendor", "model"):
            if extras.get(key) and not de.get(key):
                de[key] = extras[key]
        row["ip"] = row.get("ip") or ip
        row["mac"] = row.get("mac") or mac
        row["hostname"] = row.get("hostname") or n.get("hostname")
        row["suggested_type"] = row.get("suggested_type") or n.get("type")
        row["source"] = row.get("source") or PROXMOX_SOURCE
        row["properties"] = zigbee.merge_zigbee_properties(row.get("properties"), props)

    async def trigger_proxmox_import(
        self,
        host: str | None = None,
        port: int | None = None,
        token_id: str | None = None,
        token_secret: str | None = None,
        verify_tls: bool | None = None,
    ) -> dict[str, Any]:
        """Kick off a Proxmox import in the background (kind="proxmox").

        Records a running scan run and spawns the fetch + pending-store write, so
        the import surfaces under Scan History with a live running → done
        transition (mirroring IP scans and mesh imports). Blank connection params
        fall back to the configured defaults. Returns
        ``{run_id, status: "running", devices_found: 0}``.

        Raises ``ValueError`` when no host/token can be resolved.
        """
        req = self.resolve_proxmox_request(
            host, port, token_id, token_secret, verify_tls
        )
        run_id = uuid.uuid4().hex
        started_at = _utc_now_iso()
        await self._record_run(
            {
                "id": run_id,
                "status": "running",
                "kind": "proxmox",
                "ranges": [f"{req['host']}:{req['port']}"],
                "devices_found": 0,
                "started_at": started_at,
                "finished_at": None,
                "error": None,
            }
        )
        self.hass.async_create_task(
            self._run_proxmox_import_task(run_id, started_at, req)
        )
        return {"run_id": run_id, "status": "running", "devices_found": 0}

    async def _run_proxmox_import_task(
        self, run_id: str, started_at: str, req: dict[str, Any]
    ) -> None:
        """Background Proxmox import body: fetch inventory, upsert, record."""
        ranges = [f"{req['host']}:{req['port']}"]
        try:
            nodes, edges = await proxmox.fetch_proxmox_inventory(
                self.hass,
                req["host"],
                req["port"],
                req["token_id"],
                req["token_secret"],
                req["verify_tls"],
            )
            cluster_pairs = proxmox.build_proxmox_cluster_links(nodes)
            await self.import_proxmox_pending(nodes, edges, cluster_pairs)
        except Exception as exc:  # noqa: BLE001 — record any failure, then exit
            _LOGGER.exception("Proxmox import %s failed", run_id)
            await self._record_run(
                {
                    "id": run_id,
                    "status": "error",
                    "kind": "proxmox",
                    "ranges": ranges,
                    "devices_found": 0,
                    "started_at": started_at,
                    "finished_at": _utc_now_iso(),
                    "error": str(exc)[:500],
                }
            )
            async_dispatcher_send(
                self.hass,
                SCAN_SIGNAL,
                {"event": "scan_error", "run_id": run_id, "error": str(exc)[:500]},
            )
            return
        # A done run carrying a non-fatal advisory (hosts imported, no guests
        # visible) renders amber in Scan History, distinct from a red failure.
        advisory = proxmox.guest_visibility_advisory(nodes)
        await self._record_run(
            {
                "id": run_id,
                "status": "done",
                "kind": "proxmox",
                "ranges": ranges,
                "devices_found": len(nodes),
                "started_at": started_at,
                "finished_at": _utc_now_iso(),
                "error": advisory,
            }
        )
        async_dispatcher_send(
            self.hass,
            SCAN_SIGNAL,
            {
                "event": "scan_finished",
                "run_id": run_id,
                "devices_found": len(nodes),
            },
        )

    @callback
    def async_start_proxmox_sync(self) -> None:
        """Schedule the periodic Proxmox auto-sync job if enabled + configured.

        Reload on options change recreates the coordinator, so the interval and
        enable flag are always picked up fresh — no live reschedule needed.
        """
        if not self.get_proxmox_sync_enabled():
            return
        token_id, token_secret = self.get_proxmox_credentials()
        if not (self.get_proxmox_config()["host"] and token_id and token_secret):
            _LOGGER.warning(
                "Proxmox auto-sync enabled but host/token not fully configured; "
                "skipping schedule"
            )
            return
        interval = timedelta(seconds=self.get_proxmox_sync_interval())
        self._proxmox_sync_unsub = async_track_time_interval(
            self.hass, self._run_proxmox_sync, interval
        )
        _LOGGER.debug("Proxmox auto-sync every %ds", self.get_proxmox_sync_interval())

    @callback
    def async_stop_proxmox_sync(self) -> None:
        """Cancel the periodic Proxmox auto-sync job, if running."""
        if self._proxmox_sync_unsub is not None:
            self._proxmox_sync_unsub()
            self._proxmox_sync_unsub = None

    async def _run_proxmox_sync(self, _now: datetime | None = None) -> None:
        try:
            await self.trigger_proxmox_import()
        except Exception as exc:  # noqa: BLE001 — never let the timer die
            _LOGGER.debug("Proxmox auto-sync skipped: %s", exc)
