"""DataUpdateCoordinator for Homelable."""
from __future__ import annotations

import asyncio
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

from . import inventory_sync, proxmox, racks, scanner, status_checker, zha, zigbee, zwave
from .const import (
    CONF_PROXMOX_HOST,
    CONF_PROXMOX_PORT,
    CONF_PROXMOX_SYNC_ENABLED,
    CONF_PROXMOX_SYNC_INTERVAL,
    CONF_PROXMOX_TOKEN_ID,
    CONF_PROXMOX_TOKEN_SECRET,
    CONF_PROXMOX_VERIFY_TLS,
    CONF_SCAN_AUTO_ENABLED,
    CONF_SCAN_INTERVAL,
    CONF_SCAN_RANGES,
    CONF_SERVICE_CHECK_ENABLED,
    CONF_SERVICE_CHECK_INTERVAL,
    CONF_STATUS_INTERVAL,
    CONF_ZIGBEE_BASE_TOPIC,
    CONF_ZIGBEE_SOURCE,
    CONF_ZWAVE_GATEWAY,
    CONF_ZWAVE_PREFIX,
    DEFAULT_DESIGN_ICON,
    DEFAULT_DESIGN_NAME,
    DEFAULT_DESIGN_TYPE,
    DEFAULT_PROXMOX_PORT,
    DEFAULT_PROXMOX_SYNC_ENABLED,
    DEFAULT_PROXMOX_SYNC_INTERVAL,
    DEFAULT_PROXMOX_VERIFY_TLS,
    DEFAULT_SCAN_AUTO_ENABLED,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SCAN_RANGES,
    DEFAULT_SERVICE_CHECK_ENABLED,
    DEFAULT_SERVICE_CHECK_INTERVAL,
    DEFAULT_STATUS_INTERVAL,
    DEFAULT_ZIGBEE_BASE_TOPIC,
    DEFAULT_ZIGBEE_SOURCE,
    DEFAULT_ZWAVE_GATEWAY,
    DEFAULT_ZWAVE_PREFIX,
    DOMAIN,
    LAST_SEEN_PERSIST_INTERVAL,
    LAST_SEEN_SAVE_DELAY,
    MAX_SCAN_RUNS,
    MIN_PROXMOX_SYNC_INTERVAL,
    MIN_SCAN_INTERVAL,
    MIN_SERVICE_CHECK_INTERVAL,
    PROXMOX_SOURCE,
    SCAN_SIGNAL,
    SERVICE_STATUS_SIGNAL,
    STATUS_CHECK_CONCURRENCY,
    STORAGE_KEY_CANVAS,
    STORAGE_KEY_DESIGNS,
    STORAGE_KEY_PENDING,
    STORAGE_KEY_RACKS,
    STORAGE_KEY_RUNS,
    STORAGE_VERSION_CANVAS,
    STORAGE_VERSION_DESIGNS,
    STORAGE_VERSION_PENDING,
    STORAGE_VERSION_RACKS,
    STORAGE_VERSION_RUNS,
    ZIGBEE_SOURCE_Z2M,
    ZIGBEE_SOURCE_ZHA,
    ZIGBEE_SOURCES,
)

_LOGGER = logging.getLogger(__name__)

_EMPTY_CANVAS = {"nodes": [], "edges": [], "viewport": {"x": 0, "y": 0, "zoom": 1}}
_EMPTY_PENDING: dict[str, Any] = {"devices": []}

# Inventory row fields a user may edit (update_pending) or seed on a manual add.
# Lifecycle (`status`), discovery bookkeeping (`discovery_source(s)`,
# `discovered_at`) and the ieee identity stay owned by approve / hide and the
# importers.
_EDITABLE_FIELDS = frozenset(
    {
        "ip", "mac", "hostname", "os", "label", "type", "suggested_type",
        "friendly_name", "device_subtype", "model", "vendor", "services",
        "properties", "notes", "cpu_count", "cpu_model", "ram_gb", "disk_gb",
        "show_hardware", "check_method", "check_target",
    }
)


def _editable_fields(fields: dict[str, Any]) -> dict[str, Any]:
    """The subset of ``fields`` a client may write onto an inventory row."""
    return {k: v for k, v in fields.items() if k in _EDITABLE_FIELDS}

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


def _ip_tokens(ip: str | None) -> list[str]:
    """Split a node/device ``ip`` field into individual, trimmed addresses.

    The canvas stores several addresses in one comma-separated string once a
    user edits a node to add e.g. an IPv6 address (``"fe80::1, 192.168.1.5"``).
    Matching a scanned device against that field must compare per-address, or
    the device looks absent from the canvas (issue #258).
    """
    return [t.strip() for t in ip.split(",") if t.strip()] if ip else []


def _match_pending_by_ip_or_mac(
    devices: list[dict[str, Any]], ip: str | None, mac: str | None
) -> dict[str, Any] | None:
    """First non-hidden pending device matching ``ip`` OR normalized ``mac``.

    The MAC join lets a re-scan reconcile with a device previously imported from
    Proxmox (which may have no IP but a known NIC MAC) instead of doubling up.
    IP is compared per address: a row drawn on a canvas may hold several
    (``"fe80::1, 192.168.1.5"``), and a scan of either is that same device.
    """
    norm = proxmox.normalize_mac(mac)
    for d in devices:
        if d.get("status") == "hidden":
            continue
        if ip and ip in _ip_tokens(d.get("ip")):
            return d
        if norm and proxmox.normalize_mac(d.get("mac")) == norm:
            return d
    return None


def _iso(moment: datetime) -> str:
    """ISO-8601 UTC with trailing 'Z' (frontend Date() expects this form)."""
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _utc_now() -> datetime:
    """Current UTC time. A seam so tests can drive the clock."""
    return datetime.now(UTC)


def _utc_now_iso() -> str:
    """Now, as ISO-8601 UTC with trailing 'Z'."""
    return _iso(_utc_now())


def _is_stale(stamp: Any, now: datetime, max_age: int) -> bool:
    """True when `stamp` is missing, unparseable, or older than `max_age` seconds.

    Unparseable counts as stale so a hand-edited or legacy value gets rewritten
    once rather than pinning the node to a value nothing can compare against.
    """
    if not isinstance(stamp, str):
        return True
    try:
        previous = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return True
    if previous.tzinfo is None:
        previous = previous.replace(tzinfo=UTC)
    # A stamp in the future (clock skew, restored backup) is treated as stale so
    # the next online check corrects it.
    return abs((now - previous).total_seconds()) >= max_age


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
        self.racks_store: Store = Store(
            hass, STORAGE_VERSION_RACKS, STORAGE_KEY_RACKS
        )
        # Multi-design canvas: `_canvases` maps design_id -> canvas dict;
        # `_designs` is the ordered list of design metadata. Both are loaded
        # (and legacy single-canvas data migrated) lazily via _ensure_loaded.
        self._designs: list[dict[str, Any]] | None = None
        self._canvases: dict[str, dict[str, Any]] | None = None
        self._pending: dict[str, Any] | None = None
        self._runs: list[dict[str, Any]] | None = None
        # Rack canvases, design_id -> {racks, devices, cables, viewport}.
        self._racks: dict[str, dict[str, Any]] | None = None
        self._scan_run_id: str | None = None
        self._service_check_unsub: Callable[[], None] | None = None
        self._periodic_scan_unsub: Callable[[], None] | None = None
        self._proxmox_sync_unsub: Callable[[], None] | None = None

    # ─── Status checks (periodic) ────────────────────────────────────────────

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """Check every device once and fan the result out to its nodes.

        Device-scoped, not node-scoped (homelable #339): a host drawn on three
        canvases used to be pinged three times and could report three states.
        Returns ``{node_id: status_dict}`` — the panel's wire shape is unchanged
        — and records the live status on the inventory row itself.
        """
        await self._ensure_loaded()
        # Hosts that resolve to loopback / link-local / multicast / reserved
        # IPs are only allowed if the admin explicitly opted into that subnet.
        allowed_networks = status_checker._parse_allowed_networks(
            self.get_scan_ranges()
        )
        # Build the checks first, then run them concurrently but bounded.
        # Sequential awaits let a handful of offline hosts stack their timeouts
        # and blow past HA's 60s setup deadline (issue #51 → CancelledError at
        # startup); fully unbounded forks one `ping` per node at once, which
        # starves small hosts and gets HA restarted by the watchdog (issue #73).
        sem = asyncio.Semaphore(STATUS_CHECK_CONCURRENCY)

        async def _bounded(check: str, target: Any, ip: Any) -> dict[str, Any]:
            async with sem:
                return await status_checker.check_node(
                    check, target, ip, allowed_networks=allowed_networks
                )

        targets = self._check_targets()
        tasks = []
        for _, facts, _ in targets:
            if _is_wireless(facts.get("type")):
                # Zigbee / Z-Wave devices are one-shot mesh imports; no live check.
                check = "none"
            else:
                check = facts.get("check_method") or "ping"
            target = facts.get("check_target") or facts.get("hostname")
            tasks.append(_bounded(check, target, facts.get("ip")))
        checked = await asyncio.gather(*tasks, return_exceptions=True) if tasks else []

        # Advance last_seen on whatever a check just found up, but only once the
        # current value has aged past LAST_SEEN_PERSIST_INTERVAL, and then through
        # a debounced save. Writing on every poll where anything is online burns
        # SD-card life for a timestamp read at minute resolution (issue #73). The
        # in-memory dict *is* the Store's payload, so the stamp only moves when
        # it is also being written; last_seen lags real time by up to
        # LAST_SEEN_PERSIST_INTERVAL, the resolution the inventory displays.
        now_dt = _utc_now()
        now = _iso(now_dt)
        rows_stale = nodes_stale = False
        results: dict[str, dict[str, Any]] = {}
        for (node_ids, _facts, row), res in zip(targets, checked, strict=True):
            if isinstance(res, Exception):
                _LOGGER.debug("Status check error for %s: %s", node_ids or row, res)
                res = {"status": "unknown", "response_time_ms": None}
            for node_id in node_ids:
                results[node_id] = res
            if row is not None:
                row["status_live"] = res.get("status") or "unknown"
                row["response_time_ms"] = res.get("response_time_ms")
                if res.get("status") == "online" and _is_stale(
                    row.get("last_seen"), now_dt, LAST_SEEN_PERSIST_INTERVAL
                ):
                    row["last_seen"] = now
                    rows_stale = True
            elif res.get("status") == "online":
                # A legacy node the backfill could not link still keeps its own
                # stamp, as before the split.
                for node in self._all_canvas_nodes():
                    if node.get("id") in node_ids and _is_stale(
                        node.get("last_seen"), now_dt, LAST_SEEN_PERSIST_INTERVAL
                    ):
                        node["last_seen"] = now
                        nodes_stale = True
        if rows_stale:
            self._save_pending_debounced()
        if nodes_stale:
            self._save_canvases_debounced()
        return results

    def _check_targets(
        self,
    ) -> list[tuple[list[str], dict[str, Any], dict[str, Any] | None]]:
        """What the status and service checks probe: ``(node_ids, facts, row)``.

        One entry per device, whose result lights up every node drawing it. A
        device drawn nowhere is still checked when it carries an explicit check
        method and is not hidden — the inventory shows its live status, and
        deleting a node no longer silently stops monitoring the host. A drawn
        device is always checked, hidden or not: its node is on screen. A legacy
        node the backfill could not link is checked on its own facts. Canvas
        furniture is never checked.
        """
        rows = self._device_index()
        by_device: dict[str, list[str]] = {}
        out: list[tuple[list[str], dict[str, Any], dict[str, Any] | None]] = []
        seen: set[str] = set()
        for node in self._all_canvas_nodes():
            node_id = node.get("id")
            if not node_id or node_id in seen:
                continue
            seen.add(node_id)
            if inventory_sync.is_furniture(inventory_sync.node_type(node)):
                continue
            device_id = node.get("device_id")
            if device_id in rows:
                by_device.setdefault(device_id, []).append(node_id)
            else:
                out.append(([node_id], inventory_sync.facts_of_node(node), None))
        for device_id, row in rows.items():
            node_ids = by_device.get(device_id)
            if node_ids is None and (
                row.get("status") == "hidden" or not row.get("check_method")
            ):
                continue
            facts = self._flatten_pending(row)
            out.append(
                (
                    node_ids or [],
                    {
                        **facts,
                        "type": self._device_type(row),
                        "ieee_address": inventory_sync.device_ieee(row),
                    },
                    row,
                )
            )
        return out

    @staticmethod
    def _device_type(row: dict[str, Any]) -> str | None:
        """A row's type: curated, else the discovery guess, else its mesh source."""
        if row.get("type") or row.get("suggested_type"):
            return row.get("type") or row.get("suggested_type")
        if row.get("source") in ("zigbee", "zwave"):
            return f"{row['source']}_enddevice"
        return None

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
        """Check every service of every drawn device; dispatch per-node results.

        Device-scoped like the status check: the services belong to the device,
        so one pass serves every node drawing it, and each of those nodes gets
        its own event (the panel's wire shape is unchanged). Unlike the status
        check, a device drawn nowhere is skipped — nothing would display it.

        Mirrors the node-status SSRF policy: hosts that resolve to unsafe
        addresses outside the configured scan ranges are reported offline.
        """
        await self._ensure_loaded()
        allowed_networks = status_checker._parse_allowed_networks(
            self.get_scan_ranges()
        )
        now = _utc_now_iso()
        for node_ids, facts, _row in self._check_targets():
            services = facts.get("services") or []
            if not node_ids or not services:
                continue
            host = self._node_host(facts.get("ip"), facts.get("hostname"))
            try:
                statuses = await status_checker.check_services(
                    host, services, allowed_networks
                )
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("Service checks failed for %s: %s", node_ids, exc)
                continue
            for node_id in node_ids:
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
        await self._link_nodes_to_inventory()

    async def _link_nodes_to_inventory(self) -> None:
        """Move every node's device facts onto its Device Inventory row, once.

        Port of the homelable #339 backfill, hardened as in #352 / #354: a node
        that cannot be linked is left as it was (its facts still on it, so it
        keeps rendering) and costs only itself. Idempotent — once every node is
        linked and stripped this touches nothing, so running it on every load
        is what makes the migration self-healing.
        """
        assert self._canvases is not None
        pending = await self._get_pending()
        before_nodes = copy.deepcopy(self._canvases)
        before_rows = copy.deepcopy(pending["devices"])
        stats = inventory_sync.backfill_node_devices(
            self._canvases, pending["devices"], now=_utc_now_iso()
        )
        if stats["linked"] or stats["filled"] or stats["skipped"]:
            _LOGGER.info(
                "Device Inventory backfill: %d linked (%d created, %d merged), "
                "%d filled, %d could not be linked",
                stats["linked"], stats["created"], stats["merged"],
                stats["filled"], stats["skipped"],
            )
        seeded = inventory_sync.seed_node_views(self._canvases, pending["devices"])
        if seeded:
            _LOGGER.info("Seeded the service/property view of %d node(s)", seeded)
        if pending["devices"] != before_rows:
            await self._save_pending()
        if self._canvases != before_nodes:
            await self._save_canvases()

    async def _save_designs(self) -> None:
        await self.designs_store.async_save({"designs": self._designs})

    async def _save_canvases(self) -> None:
        await self.canvas_store.async_save({"canvases": self._canvases})

    @callback
    def _save_canvases_debounced(self) -> None:
        """Queue a delayed canvas write.

        Used for background bookkeeping (last_seen) that must survive a restart
        but doesn't justify a synchronous write per poll. Store flushes pending
        delayed saves on HA shutdown, and a later immediate `_save_canvases`
        supersedes the pending one.
        """
        self.canvas_store.async_delay_save(
            lambda: {"canvases": self._canvases}, LAST_SEEN_SAVE_DELAY
        )

    @callback
    def _save_pending_debounced(self) -> None:
        """Queue a delayed inventory write — the row's last_seen, as above."""
        self.pending_store.async_delay_save(lambda: self._pending, LAST_SEEN_SAVE_DELAY)

    # ─── Device links (node → inventory row) ─────────────────────────────────

    def _device_index(self) -> dict[str, dict[str, Any]]:
        """Inventory rows by id. Empty until the pending store is loaded."""
        return {d["id"]: d for d in (self._pending or {}).get("devices", []) if d.get("id")}

    def _nodes_by_device(self) -> dict[str, list[tuple[str, dict[str, Any]]]]:
        """device_id → every ``(design_id, stored node)`` drawing it."""
        out: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        for design_id, canvas in (self._canvases or {}).items():
            for node in canvas.get("nodes", []):
                if node.get("device_id"):
                    out.setdefault(node["device_id"], []).append((design_id, node))
        return out

    def is_drawn(self, device_id: str) -> bool:
        """True while at least one canvas node draws this inventory row."""
        return any(
            node.get("device_id") == device_id for node in self._all_canvas_nodes()
        )

    def _hydrated(
        self, node: dict[str, Any], rows: dict[str, dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        """A stored node with its device facts read back off its row."""
        rows = self._device_index() if rows is None else rows
        return inventory_sync.hydrate_node(node, rows.get(node.get("device_id") or ""))

    async def _resolve_design_id(self, design_id: str | None) -> str | None:
        """Return a valid design id: the requested one if it exists, else the
        first (default) design. None only if no designs exist at all."""
        await self._ensure_loaded()
        assert self._designs is not None
        if design_id and any(d["id"] == design_id for d in self._designs):
            return design_id
        return self._designs[0]["id"] if self._designs else None

    # Node.type values that are canvas annotations rather than real devices.
    # Kept in sync with the frontend (Sidebar counts, canvasSerializer types).
    _GROUP_TYPE = "groupRect"
    _TEXT_TYPE = "text"

    def _design_counts(self, design_id: str) -> dict[str, int]:
        """node / group / text counts for a design's canvas (feeds the copy picker)."""
        counts = {"node_count": 0, "group_count": 0, "text_count": 0}
        canvas = (self._canvases or {}).get(design_id) or {}
        for n in canvas.get("nodes", []):
            node_type = n.get("type") or (n.get("data") or {}).get("type") or ""
            if node_type == self._GROUP_TYPE:
                counts["group_count"] += 1
            elif node_type == self._TEXT_TYPE:
                counts["text_count"] += 1
            else:
                counts["node_count"] += 1
        return counts

    async def list_designs(self) -> list[dict[str, Any]]:
        await self._ensure_loaded()
        assert self._designs is not None
        # Attach per-design counts transiently so the "copy from existing" picker
        # can show what each canvas holds. Never persisted into the designs Store.
        return [{**d, **self._design_counts(d["id"])} for d in self._designs]

    async def copy_design(
        self,
        source_id: str,
        name: str,
        icon: str = DEFAULT_DESIGN_ICON,
    ) -> dict[str, Any] | None:
        """Create a new design that deep-copies the source's canvas.

        Node ids are unique across designs, so every copied node gets a fresh id;
        edges and parent/nesting links are re-pointed at the copy. Viewport, custom
        style and any floor-plan config (carried on the canvas) are cloned as-is.
        Returns the new design, or ``None`` when the source design is missing.
        """
        await self._ensure_loaded()
        assert self._designs is not None and self._canvases is not None
        source = next((d for d in self._designs if d["id"] == source_id), None)
        if source is None:
            return None

        src_canvas = self._canvases.get(source_id) or copy.deepcopy(_EMPTY_CANVAS)
        design = self._new_design(name, icon, source.get("design_type", DEFAULT_DESIGN_TYPE))

        # Fresh id per source node so edges and parent links can be re-pointed.
        id_map = {n["id"]: uuid.uuid4().hex for n in src_canvas.get("nodes", []) if n.get("id")}

        new_nodes: list[dict[str, Any]] = []
        for n in src_canvas.get("nodes", []):
            nn = copy.deepcopy(n)
            if nn.get("id") in id_map:
                nn["id"] = id_map[nn["id"]]
            # Stored nodes carry nesting as a top-level ``parent_id`` (group rect
            # membership and container/wireless parenting alike). Re-point it at the
            # copied parent; when it references a node outside this canvas (dangling)
            # drop it so React Flow doesn't render the child at an unresolved
            # position. Every same-canvas parent — including a zigbee coordinator
            # whose id is its ieee — is in ``id_map``.
            parent = nn.get("parent_id")
            if parent in id_map:
                nn["parent_id"] = id_map[parent]
            elif parent is not None:
                nn["parent_id"] = None
            new_nodes.append(nn)

        new_edges: list[dict[str, Any]] = []
        for e in src_canvas.get("edges", []):
            src, tgt = e.get("source"), e.get("target")
            # Skip edges whose endpoints aren't part of this canvas (dangling).
            if src not in id_map or tgt not in id_map:
                continue
            ne = copy.deepcopy(e)
            ne["id"] = uuid.uuid4().hex
            ne["source"] = id_map[src]
            ne["target"] = id_map[tgt]
            new_edges.append(ne)

        # Clone the canvas wholesale (keeps viewport, customStyle, floor plan) then
        # swap in the remapped nodes/edges.
        new_canvas = copy.deepcopy(src_canvas)
        new_canvas["nodes"] = new_nodes
        new_canvas["edges"] = new_edges

        self._designs.append(design)
        self._canvases[design["id"]] = new_canvas
        await self._save_designs()
        await self._save_canvases()
        # Copy the rack canvas, if the source has one, under fresh ids.
        rack_states = await self._get_racks()
        if source_id in rack_states:
            rack_states[design["id"]] = racks.copy_state(rack_states[source_id])
            await self._save_racks()
        return design

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
        rack_states = await self._get_racks()
        if rack_states.pop(design_id, None) is not None:
            await self._save_racks()
        return "ok"

    # ─── Rack canvases ───────────────────────────────────────────────────────

    async def _get_racks(self) -> dict[str, dict[str, Any]]:
        if self._racks is None:
            raw = await self.racks_store.async_load()
            designs = raw.get("designs") if isinstance(raw, dict) else None
            self._racks = dict(designs) if isinstance(designs, dict) else {}
        return self._racks

    async def _save_racks(self) -> None:
        if self._racks is not None:
            await self.racks_store.async_save({"designs": self._racks})

    async def _design_exists(self, design_id: str) -> bool:
        await self._ensure_loaded()
        assert self._designs is not None
        return any(d["id"] == design_id for d in self._designs)

    async def get_racks(self, design_id: str) -> dict[str, Any] | None:
        """Rack state of a design, with each device's inventory plate applied.

        The inventory row owns the front panel (plate, size, colour, ports), so
        it wins over the mount's own copy. Returns None for an unknown design.
        """
        if not await self._design_exists(design_id):
            return None
        state = (await self._get_racks()).get(design_id) or racks.empty_state()
        pending = await self._get_pending()
        inventory = {d["id"]: d for d in pending["devices"]}
        return racks.overlay_models(state, inventory)

    async def save_racks(
        self, design_id: str, payload: dict[str, Any]
    ) -> bool:
        """Persist the full rack state of one design, replacing what was there.

        Raises ``ValueError`` on an invalid payload. Returns False for an
        unknown design. Each mount's front panel is written through onto the
        inventory row it stands for.
        """
        if not await self._design_exists(design_id):
            return False
        state = racks.clean_save(payload)
        rack_states = await self._get_racks()
        previous = {
            d["id"]: d for d in (rack_states.get(design_id) or {}).get("devices", [])
        }
        pending = await self._get_pending()
        inventory = {d["id"]: d for d in pending["devices"]}
        if racks.write_through(state["devices"], previous, inventory):
            await self._save_pending()
        rack_states[design_id] = state
        await self._save_racks()
        return True

    def _node_status(
        self, node: dict[str, Any], row: dict[str, Any] | None = None
    ) -> str | None:
        """Live status of a canvas node: the last check, else what is recorded."""
        live = (self.data or {}).get(node.get("id") or "") or {}
        if live.get("status"):
            return live["status"]
        if row is not None:
            return row.get("status_live")
        return inventory_sync.node_value(node, "status")

    async def rack_inventory(self, design_id: str) -> list[dict[str, Any]] | None:
        """Device Inventory entries that can be racked, for the rack tray.

        Each entry is flagged with whether it is already mounted in this design
        and resolved to a logical-canvas node — the one a mount pinned by hand,
        else a node drawing this device — so a mount can follow that node's
        status and print what the logical view knows about it. The link is
        explicit now (``device_id``), so there is no IEEE/MAC/IP guessing.
        Returns None for an unknown design.
        """
        if not await self._design_exists(design_id):
            return None
        assert self._designs is not None
        state = (await self._get_racks()).get(design_id) or racks.empty_state()
        mounts = state.get("devices", [])
        mounted = {m["device_id"] for m in mounts if m.get("device_id")}
        # A mount can name its canvas node itself, when the user linked one.
        # That beats the node that merely draws the same device.
        pinned = {
            m["device_id"]: m["node_id"]
            for m in mounts
            if m.get("device_id") and m.get("node_id")
        }
        design_names = {d["id"]: d.get("name") for d in self._designs}
        nodes_by_id: dict[str, tuple[str, dict[str, Any]]] = {}
        for did, canvas in (self._canvases or {}).items():
            for n in canvas.get("nodes", []):
                if n.get("id"):
                    nodes_by_id.setdefault(n["id"], (did, n))
        drawn_by = {
            device_id: links[0] for device_id, links in self._nodes_by_device().items()
        }

        pending = await self._get_pending()
        rows = self._device_index()
        items: list[dict[str, Any]] = []
        for raw in pending["devices"]:
            if raw.get("status") not in ("pending", "approved"):
                continue
            device = self._flatten_pending(raw)
            if device.get("suggested_type") in racks.UNRACKABLE_TYPES:
                continue
            linked = nodes_by_id.get(pinned.get(device["id"]) or "") or drawn_by.get(
                device["id"]
            )
            node_design, node = linked if linked else (None, None)
            # A pinned node may draw a *different* device than the mount names,
            # so its view is read off its own row, not the mount's.
            node_row = rows.get(node.get("device_id") or "") if node else None
            view = self._hydrated(node, rows) if node else {}
            items.append(
                {
                    "id": device["id"],
                    "label": racks.device_label(device),
                    "suggested_type": device.get("suggested_type"),
                    "ip": device.get("ip"),
                    "status": device.get("status"),
                    "discovery_source": device.get("discovery_source"),
                    "mac": device.get("mac") or device.get("ieee_address"),
                    "hostname": device.get("hostname"),
                    "os": device.get("os"),
                    "services": racks.services(device.get("services")),
                    "node_id": node.get("id") if node else None,
                    "node_status": self._node_status(node, node_row) if node else None,
                    "node_label": view.get("label"),
                    "node_type": view.get("type"),
                    "node_ip": view.get("ip"),
                    "node_mac": view.get("mac") or view.get("ieee_address"),
                    "node_hostname": view.get("hostname"),
                    "node_os": view.get("os"),
                    "node_check_method": view.get("check_method"),
                    "node_design_id": node_design,
                    "node_design_name": design_names.get(node_design) if node_design else None,
                    "node_last_seen": view.get("last_seen"),
                    "racked": device["id"] in mounted,
                    "rack_faceplate_id": device.get("rack_faceplate_id"),
                    "rack_u_height": device.get("rack_u_height"),
                    "rack_col_span": device.get("rack_col_span"),
                    "rack_color": device.get("rack_color"),
                    "rack_ports": [
                        p
                        for p in (device.get("rack_ports") or [])
                        if isinstance(p, dict) and p.get("id")
                    ],
                }
            )
        return items

    async def add_manual_pending(
        self,
        *,
        hostname: str,
        ip: str | None = None,
        mac: str | None = None,
        suggested_type: str | None = None,
        discovery_source: str = "manual",
        fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Add an inventory entry by hand, for hardware no scan can discover.

        Lands as ``status="pending"`` like a discovery would, so the existing
        hide / restore / ignore flows apply unchanged. ``fields`` carries the
        curated facts the edit modal shows, so a hand-made entry needs no
        create-then-update round trip.

        One device is one row: a manual entry for a host already known by ip or
        mac fills in what that row is missing instead of splitting it in two,
        and un-hides it — adding a device by hand is asking for it. Rack gear
        (``discovery_source="rack"``) is exempt: it documents a mount, the rack
        may discard it again, and it must never swallow a scanned host's row.
        """
        await self._ensure_loaded()
        store = await self._get_pending()
        extra = _editable_fields(fields or {})
        norm_mac = proxmox.normalize_mac(mac)
        now = _utc_now_iso()
        existing = (
            inventory_sync.find_device_for(store["devices"], ip=ip, mac=norm_mac, ieee=None)
            if discovery_source != racks.RACK_SOURCE and (ip or norm_mac)
            else None
        )
        if existing is not None:
            offered = {
                "hostname": hostname,
                "ip": ip,
                "suggested_type": suggested_type,
                **{k: v for k, v in extra.items() if k not in ("services", "properties")},
            }
            for field, value in offered.items():
                if value not in (None, "") and existing.get(field) in (None, ""):
                    existing[field] = value
            existing["mac"] = existing.get("mac") or norm_mac
            existing["properties"] = inventory_sync.merge_properties(
                existing.get("properties"), extra.get("properties")
            )
            existing["services"] = inventory_sync.merge_services(
                existing.get("services"), extra.get("services")
            )
            existing["discovery_sources"] = _add_source(
                existing.get("discovery_sources"), discovery_source
            )
            if existing.get("status") == "hidden":
                existing["status"] = "approved" if self.is_drawn(existing["id"]) else "pending"
            existing["updated_at"] = now
            await self._save_pending()
            return (await self._wire_rows([existing]))[0]

        device = {
            "id": f"pd-{uuid.uuid4().hex[:8]}",
            "ip": ip or None,
            # Canonical form, like the scan: dedup compares MACs by equality.
            "mac": norm_mac,
            "hostname": hostname,
            "os": None,
            "open_ports": [],
            "services": [],
            "suggested_type": suggested_type or None,
            "status": "pending",
            "discovery_source": discovery_source,
            "discovery_sources": [discovery_source],
            "discovered_at": now,
            **extra,
        }
        if device.get("mac"):
            device["mac"] = proxmox.normalize_mac(device["mac"])
        store["devices"].append(device)
        await self._save_pending()
        return device

    async def update_pending(
        self, device_id: str, fields: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Edit an inventory row — the device facts, not its lifecycle.

        The write half of the device detail modal (homelable #339). Applies only
        the fields actually sent, so editing one field never clears the rest.
        Lifecycle (``status``) and discovery bookkeeping (sources, discovered_at,
        the ieee identity) are not editable here: approve / hide and the
        importers own those. Every canvas drawing the device reads the new facts
        on its next load. Returns the row as ``list_pending`` reports it, or
        None when there is no such row.
        """
        await self._ensure_loaded()
        store = await self._get_pending()
        row = next((d for d in store["devices"] if d.get("id") == device_id), None)
        if row is None:
            return None
        data = _editable_fields(fields)
        if "mac" in data:
            data["mac"] = proxmox.normalize_mac(data["mac"])
        if "show_hardware" in data:
            data["show_hardware"] = bool(data["show_hardware"])
        row.update(copy.deepcopy(data))
        row["updated_at"] = _utc_now_iso()
        await self._save_pending()
        return (await self._wire_rows([row]))[0]

    # ─── Canvas ──────────────────────────────────────────────────────────────

    async def _stored_canvas(self, design_id: str | None = None) -> dict[str, Any]:
        """The canvas dict held in the Store for a design (default if omitted).

        Nodes here carry no device facts — only ``device_id`` — so this is for
        internal edits that write straight back with ``_save_canvases``. The
        panel reads through ``get_canvas``.
        """
        await self._ensure_loaded()
        assert self._canvases is not None
        did = await self._resolve_design_id(design_id)
        if did is None:
            return copy.deepcopy(_EMPTY_CANVAS)
        return self._canvases.setdefault(did, copy.deepcopy(_EMPTY_CANVAS))

    async def get_canvas(self, design_id: str | None = None) -> dict[str, Any]:
        """The canvas as the panel reads it: every node hydrated from its row.

        The device facts live on the inventory row; they are read back onto the
        node here so the wire shape the panel consumes is unchanged.
        """
        stored = await self._stored_canvas(design_id)
        rows = self._device_index()
        return {
            **stored,
            "nodes": [self._hydrated(n, rows) for n in stored.get("nodes", [])],
        }

    async def save_canvas(
        self, canvas: dict[str, Any], design_id: str | None = None
    ) -> None:
        """Persist a canvas from the panel under a design (default if omitted).

        Each node's device facts are routed to the inventory row that owns them
        — an edit made on this canvas is an edit to the device itself, and every
        other canvas showing it follows — then stripped off the stored node.
        The payload carries a full copy of each device, hydrated when the canvas
        loaded, so the write is narrowed to what this canvas actually edited
        (``changed_facts`` from the panel, plus a diff against the row): a save
        made for nothing but a moved node cannot revert an edit made meanwhile
        in the inventory.
        """
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
        prior = self._canvases.get(did)
        prior_links = {
            n.get("id"): n.get("device_id")
            for n in (prior or {}).get("nodes", [])
            if n.get("id") and n.get("device_id")
        }
        prior_views = {
            n.get("id"): n[inventory_sync.VIEW_KEY]
            for n in (prior or {}).get("nodes", [])
            if n.get("id") and inventory_sync.VIEW_KEY in n
        }
        devices = (await self._get_pending())["devices"]
        now = _utc_now_iso()
        edited: set[str] = set()
        for node in canvas.get("nodes", []):
            changed = node.pop("changed_facts", None)
            if inventory_sync.is_furniture(inventory_sync.node_type(node)):
                node.pop("device_id", None)
                continue
            # A client that did not round-trip the link keeps the one on record.
            if not node.get("device_id") and node.get("id") in prior_links:
                node["device_id"] = prior_links[node["id"]]
            # The view is derived, never accepted: it is read back out of the
            # services and properties the payload carries, on top of the one on
            # record for a list the payload left out.
            node.pop(inventory_sync.VIEW_KEY, None)
            if node.get("id") in prior_views:
                node[inventory_sync.VIEW_KEY] = copy.deepcopy(prior_views[node["id"]])
            facts = inventory_sync.facts_of_node(node)
            # The panel never authors observations: last_seen / last_scan /
            # response_time_ms belong to the checker and the scanner, so a
            # stale (or injected) copy in the payload is not written back.
            for observed in ("last_seen", "last_scan", "response_time_ms"):
                facts.pop(observed, None)
            # A node not yet linked to a row is joining one, if it matches a
            # device the scanner or an import already knows. Its lists are
            # whatever it was drawn with — empty, for a node drawn by hand — not
            # a curated answer, so they merge into the row instead of replacing
            # it; otherwise drawing a scanned device erases its services and
            # properties. Once linked, a list save is an edit and replaces.
            joining = node.get("device_id") not in {d.get("id") for d in devices}
            _, row_changed = inventory_sync.link_facts(
                devices,
                node,
                facts,
                now=now,
                overwrite_scalars=True,
                replace_lists=not joining,
                only_changed=True,
                changed_fields=changed if isinstance(changed, list) else None,
            )
            if row_changed:
                edited.add(node.get("id"))
            inventory_sync.strip_facts(node)
        self._reconcile_node_timestamps(canvas, prior, edited)
        self._canvases[did] = canvas
        await self._save_canvases()
        if edited:
            await self._save_pending()

    @staticmethod
    def _reconcile_node_timestamps(
        canvas: dict[str, Any],
        prior: dict[str, Any] | None,
        edited: set[str] | None = None,
    ) -> None:
        """Stamp/preserve node lifecycle timestamps on an incoming canvas save.

        The frontend round-trips every node on Save but never authors the
        timestamps, so the Store stays authoritative:

        - ``created_at`` is copied back from the previously stored node
          (matched by id) — a stale frontend value can't clobber it.
        - a node with no prior (freshly drawn on the canvas) gets
          ``created_at = updated_at = now``.
        - ``updated_at`` bumps to now only when the node's content actually
          changed, or when this save edited the device it draws (``edited``);
          an unrelated save (pan, another node moved) leaves it alone.

        ``last_scan`` / ``last_seen`` are observations of the device and live
        on its inventory row, not here.
        """
        edited = edited or set()
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
                continue
            # Store is authoritative for this — re-apply from the stored node.
            node["created_at"] = old.get("created_at") or now
            changed = (
                node.get("id") in edited
                or _node_content(node) != _node_content(old)
            )
            node["updated_at"] = now if changed else (old.get("updated_at") or now)

    def _all_canvas_nodes(self) -> list[dict[str, Any]]:
        """Flatten stored nodes across every design's canvas."""
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

    def _design_placed_index(
        self, design_id: str | None
    ) -> tuple[dict[str, str], dict[str, str], dict[str, str], dict[str, str]]:
        """(device_id, ip-token, mac, ieee) → node id already on a design's canvas.

        ``device_id`` is the real link. The address indexes (read off each
        node's row) still catch a node drawing a *different* row that describes
        the same host, and a legacy node the backfill could not link. IPs are
        indexed per comma-separated token (issue #258); IEEE lowercased. The
        node id lets bulk-approve point the user at what it skipped.
        """
        by_device: dict[str, str] = {}
        by_ip: dict[str, str] = {}
        by_mac: dict[str, str] = {}
        by_ieee: dict[str, str] = {}
        if not design_id:
            return by_device, by_ip, by_mac, by_ieee
        rows = self._device_index()
        canvas = (self._canvases or {}).get(design_id) or {}
        for n in canvas.get("nodes", []):
            nid = n.get("id")
            view = self._hydrated(n, rows)
            if n.get("device_id"):
                by_device.setdefault(n["device_id"], nid)
            for tok in _ip_tokens(view.get("ip")):
                by_ip.setdefault(tok, nid)
            if view.get("mac"):
                by_mac.setdefault(view["mac"], nid)
            if view.get("ieee_address"):
                by_ieee.setdefault(str(view["ieee_address"]).lower(), nid)
        return by_device, by_ip, by_mac, by_ieee

    def _find_duplicate_node(
        self,
        design_id: str | None,
        ip: str | None,
        mac: str | None,
        ieee: str | None = None,
        device_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Conflict details if this device already sits on ``design_id``, else ``None``.

        A node drawing the same inventory row (``device_id``) is a duplicate;
        so is one whose row carries the same ieee, ip OR mac (a second row for
        the same host, or a legacy unlinked node). Scoped to a single design on
        purpose: the same device may legitimately appear on several canvases
        (one node per design). The approve path turns this into a prompt so the
        UI can offer "go to existing" vs "add duplicate anyway".

        IP matching is per-token and whole-address: a node at ``10.0.0.40`` is
        not a duplicate of a device at ``10.0.0.4``. Reports ieee > ip > mac.
        """
        if not design_id:
            return None
        ip_toks = _ip_tokens(ip)
        rows = self._device_index()
        canvas = (self._canvases or {}).get(design_id) or {}
        for n in canvas.get("nodes", []):
            view = self._hydrated(n, rows)
            n_ieee = view.get("ieee_address")
            n_toks = set(_ip_tokens(view.get("ip")))
            match: str | None = None
            value: str | None = None
            if ieee and inventory_sync.same_ieee(n_ieee, ieee):
                match, value = "ieee", n_ieee
            else:
                hit = next((t for t in ip_toks if t in n_toks), None)
                if hit is not None:
                    match, value = "ip", hit
                elif mac and view.get("mac") == mac:
                    match, value = "mac", mac
                elif device_id and n.get("device_id") == device_id:
                    # Same row, no address in common with this approve (the row
                    # was edited): report what the device is known by.
                    match, value = (
                        ("ieee", ieee) if ieee
                        else ("ip", ip_toks[0]) if ip_toks
                        else ("mac", mac) if mac
                        else ("ip", None)
                    )
            if match is not None:
                return {
                    "duplicate": True,
                    "existing_node_id": n.get("id"),
                    "existing_label": view.get("label"),
                    "match": match,
                    "value": value,
                }
        return None

    async def list_pending(
        self, *, status: str = "pending", source: str | None = None
    ) -> list[dict[str, Any]]:
        """Return inventory devices filtered by status and (optionally) source.

        `status="pending"` is the Device Inventory view: it returns every
        non-hidden device — freshly discovered (`pending`) AND already approved
        onto a canvas (`approved`) — each badged with a `canvas_count` of how
        many canvases draw it. `status="hidden"` returns hidden devices.

        Devices written before the `source` field existed are treated as "scan".
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
        return await self._wire_rows(out)

    async def _wire_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Inventory rows as the panel reads them.

        Mesh / Proxmox fields stored under ``data_extras`` are flattened to the
        top level so the frontend sees one shape. Each row is badged with the
        number of canvases drawing it and the timestamps of those nodes —
        created (oldest) and last modified (newest). ``last_scan`` /
        ``last_seen`` are observations of the device, so they come off the row
        itself. A node names the row it draws, so this is a group-by on
        ``device_id`` rather than the ip/mac/ieee guesswork it used to be.
        """
        drawn = self._nodes_by_device()
        wire: list[dict[str, Any]] = []
        for d in rows:
            # Copy so the transient fields never leak back into the store.
            fd = dict(self._flatten_pending(d))
            links = drawn.get(d.get("id") or "", [])
            nodes = [n for _, n in links]
            fd["canvas_count"] = len({did for did, _ in links})
            fd["node_created_at"] = self._agg_timestamp(
                [n.get("created_at") for n in nodes], newest=False
            )
            fd["node_last_modified"] = self._agg_timestamp(
                [n.get("updated_at") for n in nodes], newest=True
            )
            fd["node_last_scan"] = d.get("last_scan")
            fd["node_last_seen"] = d.get("last_seen")
            fd["status_live"] = d.get("status_live") or "unknown"
            fd["show_hardware"] = bool(d.get("show_hardware"))
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
        """Drop all devices currently in `pending` status. Returns count removed.

        A row a canvas node still draws is kept whatever its status: it holds
        that node's facts, and dropping it would blank the node.
        """
        await self._ensure_loaded()
        store = await self._get_pending()
        drawn = self._nodes_by_device()
        before = len(store["devices"])
        store["devices"] = [
            d
            for d in store["devices"]
            if d.get("status") != "pending" or d.get("id") in drawn
        ]
        removed = before - len(store["devices"])
        if removed:
            await self._save_pending()
        return removed

    async def remove_pending(self, device_id: str) -> bool:
        """Remove a pending device from the store. Returns True if found.

        Callers check ``is_drawn`` first: a row a canvas node draws holds that
        node's facts and must not be dropped (the WS handler refuses it).
        """
        store = await self._get_pending()
        before = len(store["devices"])
        store["devices"] = [d for d in store["devices"] if d["id"] != device_id]
        if len(store["devices"]) < before:
            await self._save_pending()
            return True
        return False

    async def restore_pending(self, device_id: str) -> bool:
        """Flip a hidden device back to the inventory. Returns True if found.

        A device a canvas still draws comes back ``approved`` — it is on a
        canvas — so a later "clear pending" cannot take it.
        """
        await self._ensure_loaded()
        store = await self._get_pending()
        for d in store["devices"]:
            if d["id"] == device_id and d.get("status") == "hidden":
                d["status"] = "approved" if self.is_drawn(device_id) else "pending"
                await self._save_pending()
                return True
        return False

    def _node_ieee(
        self, node: dict[str, Any], rows: dict[str, dict[str, Any]]
    ) -> str | None:
        """A stored node's IEEE / pve identity, read off the row it draws."""
        return self._hydrated(node, rows).get("ieee_address")

    async def _create_wireless_parent_edge(
        self, child_node: dict[str, Any], design_id: str | None = None
    ) -> dict[str, Any] | None:
        """If child is a zigbee/zwave node with a known parent on the same
        design's canvas, append a parent → child edge to that canvas and return
        it.

        Idempotent: skips if an edge with the same source+target already exists.
        """
        # `parent_id` rides on the node (mesh import extras); the parent's ieee
        # is a device fact, read off the row each candidate draws.
        data = child_node.get("data") or {}
        parent_ieee = child_node.get("parent_id") or data.get("parent_id")
        if not parent_ieee:
            return None
        design_id = await self._resolve_design_id(design_id)
        canvas = await self._stored_canvas(design_id)
        rows = self._device_index()
        parent = next(
            (
                n
                for n in canvas.get("nodes", [])
                if inventory_sync.same_ieee(self._node_ieee(n, rows), parent_ieee)
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
        await self._save_canvases()
        return edge

    async def _create_proxmox_edges(
        self, node: dict[str, Any], design_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Materialize Proxmox link edges for a just-approved node.

        Two shapes, both resolved against nodes already on the same design's
        canvas (a peer not yet approved is skipped and picked up when it lands):
          - host→guest: the guest carries ``proxmox_parent`` (host ieee) →
            a vertical ``virtual`` edge (bottom → top).
          - host↔host: a cluster host carries directed ``cluster_links``
            (``{source, target}`` ieees) → horizontal ``cluster`` edges rendered
            source.right → target.left. Direction is preserved from the import so
            a middle host chains (target on its left, source on its right) rather
            than firing both its edges from the same handle. Every endpoint gets a
            left + right handle so the connection points exist.
        Idempotent: an edge is skipped if one already joins the two nodes.
        """
        data = node.get("data") or {}
        parent_ieee = node.get("proxmox_parent") or data.get("proxmox_parent")
        links = node.get("cluster_links") or data.get("cluster_links") or []
        if not parent_ieee and not links:
            return []

        design_id = await self._resolve_design_id(design_id)
        canvas = await self._stored_canvas(design_id)
        nodes = canvas.get("nodes", [])
        rows = self._device_index()

        def _by_ieee(ieee: str | None) -> dict[str, Any] | None:
            return next(
                (n for n in nodes if inventory_sync.same_ieee(self._node_ieee(n, rows), ieee)),
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

        for link in links:
            src = _by_ieee(link.get("source"))
            tgt = _by_ieee(link.get("target"))
            if src is None or tgt is None or _linked(src["id"], tgt["id"]):
                continue
            # Source uses its right handle, target its left — grant both to each
            # endpoint so the connection points exist regardless of chain position.
            for host in (src, tgt):
                host["left_handles"] = max(int(host.get("left_handles") or 0), 1)
                host["right_handles"] = max(int(host.get("right_handles") or 0), 1)
            edge = {
                "id": f"e-{src['id']}-{tgt['id']}",
                "source": src["id"],
                "target": tgt["id"],
                "sourceHandle": "right",
                "targetHandle": "left",
                "type": "cluster",
                "data": {"type": "cluster"},
            }
            edges.append(edge)
            created.append(edge)

        if created:
            await self._save_canvases()
        return created

    @staticmethod
    def _device_label(device: dict[str, Any]) -> str:
        """What to call a device: its curated label, else what discovery saw."""
        extras = device.get("data_extras") or {}
        return (
            device.get("label")
            or device.get("hostname")
            or extras.get("friendly_name")
            or device.get("friendly_name")
            or device.get("ip")
            or inventory_sync.device_ieee(device)
            or "device"
        )

    async def approve_pending(
        self, device_id: str, node_overrides: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """Place an inventory device on a canvas as a new node.

        The approve dialog is an edit of the device, so its values land on the
        inventory row; the node that follows only says where the device is
        drawn (homelable #339). A blank field never clears what discovery found.

        Returns the created node (hydrated, as the panel reads it), a
        ``{"duplicate": ...}`` / ``{"rack_only": True}`` refusal, or None if
        the device is not found.
        """
        await self._ensure_loaded()
        pending = await self._get_pending()
        device = next(
            (d for d in pending["devices"] if d["id"] == device_id), None
        )
        if device is None:
            return None
        # Rack gear documents a mount, not a host: it belongs to a rack canvas
        # and is never placed on a logical one.
        if racks.is_rack_only(device):
            return {"rack_only": True}

        overrides = node_overrides or {}
        # Approve onto the active design (falls back to the default design).
        # `design_id` is carried on overrides but never leaks into node fields.
        design_id = await self._resolve_design_id(overrides.get("design_id"))
        node_type = (
            overrides.get("type")
            or device.get("type")
            or device.get("suggested_type")
            or "generic"
        )
        # Zigbee / Z-Wave devices are imported one-shot from their mesh gateway;
        # no live status check is possible, so default check_method to "none"
        # (status_checker treats "none" as always-online).
        is_wireless = device.get("source") in ("zigbee", "zwave") or _is_wireless(node_type)
        is_zwave = device.get("source") == "zwave" or node_type.startswith("zwave_")
        data_extras = device.get("data_extras") or {}
        ieee = inventory_sync.device_ieee(device)
        # A device already on THIS design (the same row, or one matching by
        # ieee, ip OR mac) is NOT placed again automatically: the user might
        # genuinely want a second card, or might be re-approving by mistake.
        # Return the conflict + the existing node so the panel can ask.
        # force=True (set after the user confirms) skips this. The same device
        # on a *different* design is valid (one node per canvas).
        if not overrides.get("force"):
            conflict = self._find_duplicate_node(
                design_id,
                overrides.get("ip") or device.get("ip"),
                overrides.get("mac") or device.get("mac"),
                ieee,
                device["id"],
            )
            if conflict is not None:
                return {"duplicate": conflict}

        # ── The row: what the device is ──
        device["label"] = overrides.get("label") or self._device_label(device)
        device["type"] = node_type
        device["ip"] = overrides.get("ip") or device.get("ip")
        device["mac"] = device.get("mac") or proxmox.normalize_mac(overrides.get("mac"))
        device["hostname"] = overrides.get("hostname") or device.get("hostname")
        device["services"] = inventory_sync.merge_services(
            device.get("services"), overrides.get("services")
        )
        # Surface Identity/Vendor/Model/LQI as property rows (hidden by default
        # — users opt in to showing them on the canvas card). Z-Wave has no LQI
        # row. Non-mesh devices keep what the row carries (e.g. Proxmox specs)
        # and gain the scanned MAC as a hidden row.
        if not is_wireless:
            device["properties"] = merge_mac_property(
                device.get("properties"), device.get("mac")
            )
        elif is_zwave:
            device["properties"] = zigbee.merge_zigbee_properties(
                device.get("properties"),
                zwave.build_zwave_properties(
                    ieee, data_extras.get("vendor"), data_extras.get("model")
                ),
            )
        else:
            device["properties"] = zigbee.merge_zigbee_properties(
                device.get("properties"),
                zigbee.build_zigbee_properties(
                    ieee,
                    data_extras.get("vendor"),
                    data_extras.get("model"),
                    data_extras.get("lqi"),
                ),
            )
        if is_wireless:
            # A mesh device answers no ICMP; being in the mesh is the liveness.
            device["check_method"] = "none"
            device["check_target"] = None
            device["status_live"] = "online"
        else:
            # A Proxmox guest imported without an IP (stopped VM / no guest
            # agent) has nothing to ping, so it falls back to "none" rather
            # than a check that always reports offline.
            device["check_method"] = (
                overrides.get("check_method")
                or device.get("check_method")
                or ("ping" if device.get("ip") else "none")
            )
            device["check_target"] = overrides.get("check_target") or device.get(
                "check_target"
            )
            if device.get("status_live") in (None, "", "unknown"):
                device["status_live"] = overrides.get("status") or "unknown"
        now = _utc_now_iso()
        device["updated_at"] = now
        # Device Inventory: keep the row, flip it to "approved" rather than
        # deleting it, so the device stays listed and gets badged with the
        # number of canvases drawing it.
        device["status"] = "approved"

        # ── The node: where it is drawn ──
        # Canvas nodes are stored FLAT (top-level pos_x/...), to match what the
        # frontend serializes on Save and reads back on load. Mesh / Proxmox
        # extras (parent_id, proxmox_parent, cluster_links, model, …) ride on
        # the node for the edge builders; the device facts do not.
        position = overrides.get("position") or {"x": 0, "y": 0}
        node: dict[str, Any] = {
            "id": overrides.get("id") or ieee or f"node-{uuid.uuid4().hex[:8]}",
            "type": node_type,
            "label": device["label"],
            "pos_x": position.get("x", 0),
            "pos_y": position.get("y", 0),
            **{k: v for k, v in data_extras.items() if k != "ieee_address"},
            **overrides.get("data", {}),
            "device_id": device["id"],
            # Lifecycle timestamps (authoritative — set after the spreads so a
            # frontend-supplied `data` blob can never inject them).
            "created_at": now,
            "updated_at": now,
        }
        inventory_sync.strip_facts(node)
        # A new node shows what the row holds today; whatever a later scan adds
        # arrives hidden instead of appearing on it unasked.
        node[inventory_sync.VIEW_KEY] = inventory_sync.view_of_device(device)

        canvas = await self._stored_canvas(design_id)
        canvas.setdefault("nodes", []).append(node)
        await self._save_canvases()
        await self._save_pending()
        return self._hydrated(node)

    async def approve_batch(
        self, device_ids: list[str], overrides: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Approve several devices onto the active design in one pass.

        Skips any device already placed on that design — a node drawing the
        same row, or one matching by ip-token, mac or ieee_address — including
        a duplicate selection within this same batch, so a re-approve or a
        doubled id never creates a duplicate node. Canvas membership is
        per-design, so a device already approved onto *another* canvas is still
        placed here.

        Bulk can't prompt per-device the way single approve does, so each skip
        is also reported in ``skipped_devices`` (with the identifier that matched
        and the existing node id) instead of being silently dropped.
        """
        overrides = overrides or {}
        await self._ensure_loaded()
        design_id = await self._resolve_design_id(overrides.get("design_id"))
        placed_device, placed_ip, placed_mac, placed_ieee = self._design_placed_index(
            design_id
        )
        pending = await self._get_pending()
        by_id = {d["id"]: d for d in pending["devices"]}
        # This method has already decided per-device whether to place, so tell
        # approve_pending to skip its own duplicate guard (force) — otherwise it
        # would refuse the very nodes we chose to add.
        force_overrides = {**overrides, "force": True}

        nodes: list[dict[str, Any]] = []
        node_ids: list[str] = []
        approved_ids: list[str] = []
        edges: list[dict[str, Any]] = []
        skipped: list[str] = []
        skipped_devices: list[dict[str, Any]] = []
        not_found: list[str] = []

        def _skip(device: dict[str, Any], match: str, value: Any, node_id: Any) -> None:
            skipped.append(device["id"])
            skipped_devices.append({
                "device_id": device["id"], "label": self._device_label(device),
                "match": match, "value": value, "existing_node_id": node_id,
            })

        for device_id in device_ids:
            device = by_id.get(device_id)
            if device is None:
                not_found.append(device_id)
                continue
            # Rack-only gear belongs to a rack canvas, never to a logical one.
            if racks.is_rack_only(device):
                _skip(device, "rack", "rack device", None)
                continue
            ip = device.get("ip")
            mac = device.get("mac")
            ieee = inventory_sync.device_ieee(device)
            ieee_key = ieee.lower() if ieee else None
            # Record which identifier collided so the caller can explain each
            # skip and link to the node already there (ip > ieee > mac).
            ip_hit = next((t for t in _ip_tokens(ip) if t in placed_ip), None)
            if ip_hit is not None:
                _skip(device, "ip", ip_hit, placed_ip[ip_hit])
                continue
            if ieee_key and ieee_key in placed_ieee:
                _skip(device, "ieee", ieee, placed_ieee[ieee_key])
                continue
            if mac and mac in placed_mac:
                _skip(device, "mac", mac, placed_mac[mac])
                continue
            if device_id in placed_device:
                # Drawn here already, by a node whose row no longer shares an
                # address with it (the row was edited). Report what it is known by.
                match, value = (
                    ("ip", _ip_tokens(ip)[0]) if ip
                    else ("ieee", ieee) if ieee
                    else ("mac", mac)
                )
                _skip(device, match, value, placed_device[device_id])
                continue
            node = await self.approve_pending(device_id, force_overrides)
            if node is None:
                not_found.append(device_id)
                continue
            nodes.append(node)
            node_ids.append(node["id"])
            approved_ids.append(device_id)
            # Track within the batch so a repeated device isn't placed twice.
            placed_device[device_id] = node["id"]
            for tok in _ip_tokens(ip):
                placed_ip[tok] = node["id"]
            if mac:
                placed_mac[mac] = node["id"]
            if ieee_key:
                placed_ieee[ieee_key] = node["id"]
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
            "skipped_devices": skipped_devices,
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

    # ─── Periodic scan (opt-in) ──────────────────────────────────────────────

    def get_scan_auto_enabled(self) -> bool:
        """Whether scans run on a timer (options → data → default False)."""
        return bool(
            self.entry.options.get(
                CONF_SCAN_AUTO_ENABLED,
                self.entry.data.get(
                    CONF_SCAN_AUTO_ENABLED, DEFAULT_SCAN_AUTO_ENABLED
                ),
            )
        )

    def get_scan_interval(self) -> int:
        """Periodic-scan interval in seconds, floored at MIN_SCAN_INTERVAL."""
        raw = self.entry.options.get(
            CONF_SCAN_INTERVAL,
            self.entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )
        try:
            return max(MIN_SCAN_INTERVAL, int(raw))
        except (TypeError, ValueError):
            return DEFAULT_SCAN_INTERVAL

    @callback
    def async_start_periodic_scan(self) -> None:
        """Schedule the recurring scan if the user opted in.

        Off by default: a sweep is the heaviest job here, so it only runs on a
        timer when explicitly enabled. Reload on options change recreates the
        coordinator, so the flag and interval are always picked up fresh.
        """
        if not self.get_scan_auto_enabled():
            return
        interval = timedelta(seconds=self.get_scan_interval())
        self._periodic_scan_unsub = async_track_time_interval(
            self.hass, self._run_periodic_scan, interval
        )
        _LOGGER.debug("Periodic scan every %ds", self.get_scan_interval())

    @callback
    def async_stop_periodic_scan(self) -> None:
        """Cancel the recurring scan, if scheduled."""
        if self._periodic_scan_unsub is not None:
            self._periodic_scan_unsub()
            self._periodic_scan_unsub = None

    async def _run_periodic_scan(self, _now: datetime | None = None) -> None:
        """Timer body: kick a scan unless one is already in flight.

        trigger_scan already returns `already_running` rather than stacking, so
        a slow sweep can never pile up behind the timer.
        """
        result = await self.trigger_scan()
        if result["status"] == "already_running":
            _LOGGER.debug("Periodic scan skipped: run %s still going", result["run_id"])

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

        # Background task: a sweep runs for minutes. As a tracked task HA would
        # block shutdown waiting on it (and the forced stop reads as a
        # spontaneous restart — issue #73); as a background task HA cancels it.
        self.hass.async_create_background_task(
            self._run_scan_task(run_id, ranges, exclude, started_at, deep_scan),
            f"{DOMAIN}_scan_{run_id}",
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
                        # The row owns the curated services now: merge what the
                        # scan saw instead of replacing the list with it.
                        "services": inventory_sync.merge_services(
                            existing.get("services"),
                            device.get("services", []),
                            discovered=True,
                        ),
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
                        "services": inventory_sync.merge_services(
                            existing.get("services"),
                            dev.get("services", []),
                            discovered=True,
                        ),
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

        # Stamp last_scan on every row this run observed, so each canvas drawing
        # the device shows when the scanner last saw it. The row is the one
        # place that fact belongs; a node only draws it.
        for dev in devices:
            row = _match_pending_by_ip_or_mac(pending["devices"], dev["ip"], dev.get("mac"))
            if row is not None:
                row["last_scan"] = now

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

    # ─── Zigbee (Zigbee2MQTT / ZHA) ──────────────────────────────────────────

    def get_zigbee_base_topic(self) -> str:
        return self.entry.options.get(
            CONF_ZIGBEE_BASE_TOPIC,
            self.entry.data.get(CONF_ZIGBEE_BASE_TOPIC, DEFAULT_ZIGBEE_BASE_TOPIC),
        )

    def get_zigbee_source(self) -> str:
        """The configured Zigbee gateway: ``"auto"``, ``"zha"`` or ``"z2m"``."""
        value = self.entry.options.get(
            CONF_ZIGBEE_SOURCE,
            self.entry.data.get(CONF_ZIGBEE_SOURCE, DEFAULT_ZIGBEE_SOURCE),
        )
        return value if value in ZIGBEE_SOURCES else DEFAULT_ZIGBEE_SOURCE

    def zigbee_gateway(self) -> dict[str, Any]:
        """What the panel needs to name the gateway it is about to use.

        ``source`` is the user's setting, ``resolved`` is what an import would
        actually talk to, and ``zha_detected`` says whether "auto" had anything
        to detect. Deliberately no "is Z2M available" flag: HA's MQTT
        integration being loaded says nothing about Zigbee2MQTT running — it is
        just as likely to be Tasmota or ESPHome — so Z2M is only ever claimed
        because the user configured it (or because no ZHA was found).
        """
        return {
            "source": self.get_zigbee_source(),
            "resolved": self.resolve_zigbee_backend(),
            "zha_detected": zha.zha_available(self.hass),
        }

    def resolve_zigbee_backend(self, requested: str | None = None) -> str:
        """Pick the Zigbee data source — ``"zha"`` or ``"z2m"``.

        An explicit per-call ``requested`` wins (the WS override), then the
        configured source. ``"auto"`` prefers ZHA when its integration is set
        up: it needs no broker and carries the real neighbour tables. Otherwise
        Zigbee2MQTT, which stays the default for every install that never had
        ZHA.
        """
        if requested in (ZIGBEE_SOURCE_ZHA, ZIGBEE_SOURCE_Z2M):
            return requested
        source = self.get_zigbee_source()
        if source in (ZIGBEE_SOURCE_ZHA, ZIGBEE_SOURCE_Z2M):
            return source
        return (
            ZIGBEE_SOURCE_ZHA
            if zha.zha_available(self.hass)
            else ZIGBEE_SOURCE_Z2M
        )

    def _zigbee_run_target(self, backend: str) -> list[str]:
        """What to show in Scan History's `ranges` column for this backend."""
        if backend == "zha":
            return ["zha"]
        base = self.get_zigbee_base_topic()
        return [base] if base else []

    async def fetch_zigbee_networkmap(
        self, backend: str | None = None
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Fetch the Zigbee mesh and return parsed (nodes, edges).

        ZHA reads straight from the running integration; Z2M does an MQTT
        networkmap round-trip. Both return the same node/edge shape.
        """
        if self.resolve_zigbee_backend(backend) == "zha":
            return await zha.fetch_zha_network(self.hass)
        return await zigbee.fetch_networkmap(self.hass, self.get_zigbee_base_topic())

    async def trigger_zigbee_import(
        self, backend: str | None = None
    ) -> dict[str, Any]:
        """Kick off a Zigbee import in the background. Returns immediately.

        Records a ``kind="zigbee"`` scan run (running) and spawns the actual
        network fetch + pending-store write, so the import surfaces under
        Scan History with a live running → done transition — mirroring IP
        scans. The Z2M round-trip (which can take minutes on large meshes)
        runs in the background instead of blocking the UI; ZHA is instant but
        takes the same path so both report identically. The panel polls
        history for progress / completion.

        Response: ``{run_id, status: "running", devices_found: 0, backend}``.
        """
        run_id = uuid.uuid4().hex
        started_at = _utc_now_iso()
        resolved = self.resolve_zigbee_backend(backend)
        await self._record_run(
            {
                "id": run_id,
                "status": "running",
                "kind": "zigbee",
                "ranges": self._zigbee_run_target(resolved),
                "devices_found": 0,
                "started_at": started_at,
                "finished_at": None,
                "error": None,
            }
        )
        self.hass.async_create_background_task(
            self._run_zigbee_import_task(run_id, started_at, resolved),
            f"{DOMAIN}_zigbee_import_{run_id}",
        )
        return {
            "run_id": run_id,
            "status": "running",
            "devices_found": 0,
            "backend": resolved,
        }

    async def _run_zigbee_import_task(
        self, run_id: str, started_at: str, backend: str = "z2m"
    ) -> None:
        """Background Zigbee import body: fetch network map, import, record."""
        ranges = self._zigbee_run_target(backend)
        try:
            nodes, _edges = await self.fetch_zigbee_networkmap(backend)
            await self.import_zigbee_devices(nodes, backend=backend)
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
        self, devices: list[dict[str, Any]], *, backend: str = "z2m"
    ) -> dict[str, int]:
        """Push selected Zigbee devices into the pending store.

        Each entry in `devices` is a mesh node dict from
        ``zigbee.parse_networkmap`` or ``zha.fetch_zha_network`` — the two
        share a shape. Already-pending IEEE addresses are skipped;
        already-approved (on-canvas) devices have their IEEE/Vendor/Model/LQI
        properties refreshed instead of being re-added.

        ``source`` stays ``"zigbee"`` whichever backend produced the devices,
        so dedup and the panel's Zigbee filter treat them as one inventory;
        only ``discovery_source`` records which gateway saw them.

        Returns: ``{"added": N, "skipped": M, "refreshed": K}``.
        """
        return await self._import_wireless_devices(
            devices,
            source="zigbee",
            discovery_source="zha" if backend == "zha" else "zigbee2mqtt",
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
        A device a canvas already draws has its property rows refreshed (via
        ``build_props``) on its inventory row — properties belong to the row
        now, so one refresh serves every canvas drawing it, preserving the
        user's visibility choices. Any other device already in the inventory
        (whatever its status: a hidden one stays hidden) is skipped; a new one
        lands as pending.

        Returns: ``{"added": N, "skipped": M, "refreshed": K}``.
        """
        await self._ensure_loaded()
        pending = await self._get_pending()
        drawn = self._nodes_by_device()

        added = 0
        skipped = 0
        refreshed = 0
        dirty = False
        now = _utc_now_iso()
        for dev in devices:
            ieee = dev.get("ieee_address") or dev.get("id")
            if not ieee:
                skipped += 1
                continue
            row = next(
                (
                    d
                    for d in pending["devices"]
                    if inventory_sync.same_ieee(inventory_sync.device_ieee(d), ieee)
                ),
                None,
            )
            if row is not None and row.get("id") in drawn:
                row["properties"] = zigbee.merge_zigbee_properties(
                    row.get("properties"),
                    build_props(ieee, dev.get("vendor"), dev.get("model"), dev.get("lqi")),
                )
                refreshed += 1
                dirty = True
                continue
            if row is not None:
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
            added += 1
            dirty = True

        if dirty:
            await self._save_pending()
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
        self.hass.async_create_background_task(
            self._run_zwave_import_task(run_id, started_at),
            f"{DOMAIN}_zwave_import_{run_id}",
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

    async def import_proxmox_pending(
        self,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        cluster_pairs: list[tuple[str, str]] | None = None,
    ) -> dict[str, int]:
        """Upsert a fetched Proxmox inventory into the pending store.

        Each guest / host is matched to its inventory row by ieee > ip > MAC
        (the cross-source key — a stopped VM has no IP but its NIC MAC matches an
        ARP-scanned row), merged into it (union discovery sources, property
        rows refreshed), or added as pending. The row owns the facts, so the
        import merges into it whether or not the device is drawn anywhere; a
        drawn device keeps its lifecycle, and its nodes only get their cluster
        handles adjusted — the one part of this that is about how a node is
        drawn. An approved row no canvas draws any more is revived to pending.

        Host→guest and host↔host relationships are recorded on each pending
        device's ``data_extras`` (``proxmox_parent`` / ``cluster_peers``) and
        materialized as ``virtual`` / ``cluster`` edges on approve.

        Returns ``{"created": N, "updated": M, "device_count": K}``.
        """
        cluster_pairs = cluster_pairs or []
        await self._ensure_loaded()
        pending = await self._get_pending()

        guest_parent = {e["target"]: e["source"] for e in edges}
        # Directed cluster links: a pair (a, b) is rendered a.right -> b.left, so
        # direction must survive to approve time. Both endpoints carry the same
        # link dict; whichever host is approved second materializes the edge.
        # Using the direction (not a symmetric peer list) keeps a middle host a
        # target on its LEFT handle and a source on its RIGHT handle — a real
        # chain instead of both edges leaving the same handle.
        peers_by_ieee: dict[str, list[str]] = {}
        links_by_ieee: dict[str, list[dict[str, str]]] = {}
        for a, b in cluster_pairs:
            peers_by_ieee.setdefault(a, []).append(b)
            peers_by_ieee.setdefault(b, []).append(a)
            link = {"source": a, "target": b}
            links_by_ieee.setdefault(a, []).append(link)
            links_by_ieee.setdefault(b, []).append(link)
        cluster_members = set(peers_by_ieee)

        drawn = self._nodes_by_device()

        def _find_pending(
            ieee: str, ip: str | None, mac: str | None
        ) -> dict[str, Any] | None:
            return inventory_sync.find_device_for(
                pending["devices"], ip=ip, mac=mac, ieee=ieee
            )

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
                "cluster_links": links_by_ieee.get(ieee, []),
            }

            existing = _find_pending(ieee, ip, mac)
            if existing is None:
                pending["devices"].append(
                    self._new_proxmox_pending(ieee, ip, mac, n, props, extras, "pending", now)
                )
                created += 1
            else:
                existing["discovery_sources"] = _sources_after_merge(existing)
                self._refresh_proxmox_pending(existing, ieee, ip, mac, n, props, extras)
                links = drawn.get(existing["id"], [])
                if links and ieee in cluster_members:
                    for did, cnode in links:
                        cnode["left_handles"] = max(int(cnode.get("left_handles") or 0), 1)
                        cnode["right_handles"] = max(int(cnode.get("right_handles") or 0), 1)
                        dirty_designs.add(did)
                if not links and existing.get("status") == "approved":
                    # Approved earlier but no canvas draws it any more — revive.
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
        de["cluster_links"] = extras.get("cluster_links") or de.get("cluster_links") or []
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
        self.hass.async_create_background_task(
            self._run_proxmox_import_task(run_id, started_at, req),
            f"{DOMAIN}_proxmox_import_{run_id}",
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
