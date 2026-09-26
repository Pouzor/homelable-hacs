"""Keep a canvas node and its Device Inventory row in step.

The inventory row (a ``homelable_pending_devices`` entry) owns what a device
*is* — addresses, services, properties, notes, hardware, check method, live
status. A node owns only how that device is drawn on one canvas, and names the
row it draws through ``device_id``. This module holds the matching and merging
rules shared by:

* the one-off backfill that links nodes written before the split to a row,
* the approve path, which links instead of copying,
* the canvas save write-through, which pushes a node edit back to the row,
* ``get_canvas``, which hydrates the facts back into the node so the panel's
  wire shape is unchanged.

Everything here is pure: plain dicts in, plain dicts out, no Store I/O. The
coordinator owns persistence.

Port of homelable #339 (``backend/app/services/inventory_sync.py``), with the
backfill hardening of #352 / #354 folded in. The SQLite-only parts of those
(NOT NULL relaxation, table rebuilds, text→datetime coercion) have no analogue
in a JSON Store and are not ported.
"""
from __future__ import annotations

import copy
import logging
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from .proxmox import normalize_mac

_LOGGER = logging.getLogger(__name__)

# Canvas furniture: annotations, not hardware. These never get an inventory row.
FURNITURE_TYPES = frozenset({"group", "groupRect", "text"})

# Source tag for a device that only ever existed as a canvas node — the backfill
# or a canvas save mints its inventory row.
CANVAS_SOURCE = "canvas"

# Scalar facts the inventory row owns.
DEVICE_SCALARS = (
    "hostname",
    "ip",
    "mac",
    "os",
    "notes",
    "cpu_count",
    "cpu_model",
    "ram_gb",
    "disk_gb",
    "check_method",
    "check_target",
)

# Observations rather than edits: the checker and the scanner write these, so a
# client never lists them as changed and they survive a `changed_fields` filter.
LIVE_FACT_FIELDS = frozenset({"status", "last_seen", "last_scan", "response_time_ms"})

# Every device fact as it appears on a node. These are stripped off a stored
# node once it is linked, and hydrated back from the row on the way out.
# `label` / `type` are not here: the node keeps its own copy (so a node whose
# row cannot be read still renders), while the row's value wins on hydrate.
NODE_FACT_FIELDS = (
    *DEVICE_SCALARS,
    "show_hardware",
    "services",
    "properties",
    "ieee_address",
    *sorted(LIVE_FACT_FIELDS),
)

# Service fields a user curates. A discovery merge refreshes the port facts of
# an entry but never renames or re-icons one the user already set.
_CURATED_SERVICE_FIELDS = ("service_name", "icon", "category")


def is_furniture(node_type: str | None) -> bool:
    return (node_type or "") in FURNITURE_TYPES


def ip_tokens(ip: str | None) -> list[str]:
    """Split an ``ip`` field into individual, trimmed addresses.

    A node or device may carry several comma-separated addresses (an IPv6 added
    before the IPv4), so identity matching compares per token: ``10.0.0.4`` must
    never match ``10.0.0.40``.
    """
    return [t.strip() for t in ip.split(",") if t.strip()] if isinstance(ip, str) else []


def _blank(value: Any) -> bool:
    return value is None or value == ""


def _norm_mac(mac: Any) -> str | None:
    """Canonical MAC, the form every write path stores (see ``proxmox``)."""
    return normalize_mac(mac) if isinstance(mac, str) else None


def same_ieee(left: Any, right: Any) -> bool:
    """IEEE addresses compare case-insensitively — the same radio either way."""
    return (
        isinstance(left, str)
        and isinstance(right, str)
        and bool(left)
        and left.lower() == right.lower()
    )


def device_ieee(row: Mapping[str, Any]) -> str | None:
    """The IEEE / pve identity of a row.

    Mesh and Proxmox imports keep it under ``data_extras`` (flattened onto the
    wire by the coordinator); read the top level too so either shape resolves.
    """
    extras = row.get("data_extras") or {}
    return row.get("ieee_address") or extras.get("ieee_address") or None


def set_device_ieee(row: dict[str, Any], ieee: str) -> None:
    row.setdefault("data_extras", {})["ieee_address"] = ieee


def node_value(node: Mapping[str, Any], key: str) -> Any:
    """A field of a stored node: top level, else under a legacy ``data`` blob."""
    if key in node:
        return node[key]
    return (node.get("data") or {}).get(key)


def node_type(node: Mapping[str, Any]) -> str:
    return str(node_value(node, "type") or "")


def _parse_ts(value: Any) -> datetime | None:
    """An ISO timestamp as a datetime, or ``None``.

    An unparseable stamp becomes ``None`` rather than an error: the lesson of
    homelable #354, where one legacy value in an unexpected form aborted the
    whole backfill. The checker and the scanner refresh both stamps anyway.
    """
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _newer(incoming: Any, current: Any) -> bool:
    """True when ``incoming`` is a valid stamp later than ``current``."""
    new = _parse_ts(incoming)
    if new is None:
        return False
    old = _parse_ts(current)
    if old is None:
        return True
    try:
        return new > old
    except TypeError:  # naive vs aware — compare as written
        return str(incoming) > str(current)


# ─── Matching ────────────────────────────────────────────────────────────────


def find_device_for(
    devices: list[dict[str, Any]],
    *,
    ip: str | None,
    mac: str | None,
    ieee: str | None,
) -> dict[str, Any] | None:
    """The inventory row describing this host, or ``None``.

    Precedence is ieee > ip > mac, so a device is identified the same way
    everywhere. Hidden rows are eligible: a hidden device is still that device,
    and silently minting a second row for it would resurrect the duplicate the
    user hid. Among several matches the oldest row wins (store order).
    """
    ip_toks = set(ip_tokens(ip))
    norm = _norm_mac(mac)
    if ieee:
        for d in devices:
            if same_ieee(device_ieee(d), ieee):
                return d
    if ip_toks:
        for d in devices:
            if ip_toks & set(ip_tokens(d.get("ip"))):
                return d
    if norm:
        for d in devices:
            if _norm_mac(d.get("mac")) == norm:
                return d
    return None


def _ieee_owner(
    devices: list[dict[str, Any]], ieee: str, *, other_than: dict[str, Any]
) -> dict[str, Any] | None:
    """The row already holding ``ieee``, if it is not ``other_than``."""
    for d in devices:
        if d is not other_than and same_ieee(device_ieee(d), ieee):
            return d
    return None


# ─── Merging ─────────────────────────────────────────────────────────────────


def merge_properties(base: list[Any] | None, incoming: list[Any] | None) -> list[Any]:
    """Union two property lists on ``key`` (case-insensitive); incoming wins.

    Order-stable: existing keys keep their position, new ones are appended, so a
    user's arrangement survives a merge.
    """
    out: list[Any] = [dict(p) if isinstance(p, dict) else p for p in (base or [])]
    index: dict[str, int] = {}
    for i, prop in enumerate(out):
        if isinstance(prop, dict) and prop.get("key") is not None:
            index[str(prop["key"]).lower()] = i

    for prop in incoming or []:
        if not isinstance(prop, dict) or prop.get("key") is None:
            if prop not in out:
                out.append(prop)
            continue
        key = str(prop["key"]).lower()
        pos = index.get(key)
        if pos is None:
            out.append(dict(prop))
            index[key] = len(out) - 1
            continue
        current = out[pos]
        if not isinstance(current, dict):
            out[pos] = dict(prop)
            continue
        merged = {**current, **{k: v for k, v in prop.items() if not _blank(v)}}
        # Keys match case-insensitively but the display spelling is the user's —
        # "rack" arriving must not rewrite their "Rack".
        merged["key"] = current.get("key", prop["key"])
        # `visible` is a real False, not an empty value — carry it explicitly.
        if "visible" in prop:
            merged["visible"] = prop["visible"]
        out[pos] = merged
    return out


def _service_key(svc: Any) -> Any:
    if not isinstance(svc, dict):
        return repr(svc)
    return (svc.get("port"), svc.get("protocol"), (svc.get("service_name") or "").lower())


def _port_key(svc: Any) -> Any:
    if not isinstance(svc, dict) or svc.get("port") is None:
        return None
    return (svc.get("port"), svc.get("protocol"))


# ─── Per-node view of the device's list facts ────────────────────────────────
#
# The row owns the services and the properties; a node owns which of them it
# shows and in what order, under ``node["display_view"]``:
#
#   {"services": [{"key": "443|tcp|https", "visible": True}, …],
#    "properties": [{"key": "rack", "visible": False}, …]}
#
# Both are keyed by a stable string so the view survives an edit to a service's
# path or a property's value. Port of homelable #357.

VIEW_KEY = "display_view"
VIEW_LISTS = ("services", "properties")


def _service_view_key(svc: Any) -> str:
    port, protocol, name = _service_key(svc) if isinstance(svc, dict) else (None, None, repr(svc))
    return f"{port}|{protocol}|{name}"


def _property_view_key(prop: Any) -> str:
    if not isinstance(prop, dict):
        return repr(prop)
    return str(prop.get("key") or "").lower()


_VIEW_KEY_OF = {"services": _service_view_key, "properties": _property_view_key}


def view_entries(items: list[Any] | None, kind: str) -> list[dict[str, Any]]:
    """One list of device facts as a node's view of it: order plus visibility.

    A service carries no ``visible`` of its own — one that reached a canvas was
    always drawn — so it defaults to shown. A property carries an explicit flag
    and keeps it. Duplicate keys collapse: the view addresses the row, and the
    row holds one entry per key.
    """
    key_of = _VIEW_KEY_OF[kind]
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items or []:
        key = key_of(item)
        if key in seen:
            continue
        seen.add(key)
        visible = bool(item.get("visible", True)) if isinstance(item, dict) else True
        out.append({"key": key, "visible": visible})
    return out


def view_from_facts(facts: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """The view a node payload implies — only for the lists it actually sent.

    The wire shape has no separate view: the panel sends its services and its
    properties in display order, each with its ``visible`` flag, exactly as it
    draws them. That *is* the view, so it is read back out here rather than
    asking the panel for a second field.
    """
    return {kind: view_entries(facts[kind], kind) for kind in VIEW_LISTS if kind in facts}


def view_of_device(device: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """A view showing everything the row currently holds — the seed for a new node."""
    return {kind: view_entries(device.get(kind), kind) for kind in VIEW_LISTS}


def next_view(
    current: Mapping[str, Any] | None,
    incoming: Mapping[str, Any],
    device: Mapping[str, Any],
    *,
    strict: bool = False,
) -> dict[str, Any]:
    """This node's view after a write: what it sent, then the row for the rest.

    A list the write did not carry keeps the view it had. A linked node always
    ends up with both lists, so "not in the view" can mean one thing only: this
    canvas does not show it. That is what keeps a service a later scan discovers
    off every canvas until someone turns it on.

    A node getting its *first* view is the exception: an empty list there means
    the writer had nothing to say about it, not that the user hid everything —
    a node drawn for an already-scanned device sends no services and must still
    draw the ones the row holds. ``strict`` turns that off for the one caller
    whose empty list is a real answer: the backfill, where the node's own fields
    are the whole of what that canvas used to show.
    """
    out: dict[str, Any] = dict(current or {})
    first_view = current is None
    seed = view_of_device(device)
    for kind in VIEW_LISTS:
        entries = incoming.get(kind)
        if entries or (entries is not None and (strict or not first_view)):
            out[kind] = entries
        elif kind not in out:
            out[kind] = seed[kind]
    return out


def apply_view(items: list[Any] | None, entries: Any, kind: str) -> list[Any]:
    """The row's facts as one node draws them: its order, its visibility.

    Without a view — a node linked before views existed — everything shows, in
    the row's own order. With one, an item the view does not list is appended
    hidden rather than dropped, so a service a scan added is one toggle away
    instead of invisible.
    """
    key_of = _VIEW_KEY_OF[kind]
    facts = copy.deepcopy(list(items or []))
    if not isinstance(entries, list):
        return facts

    by_key: dict[str, Any] = {}
    for item in facts:
        by_key.setdefault(key_of(item), item)

    out: list[Any] = []
    taken: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key"))
        item = by_key.get(key)
        if item is None or key in taken:
            continue  # Deleted from the row since — the view catches up on write.
        taken.add(key)
        out.append(_stamped(item, bool(entry.get("visible", True))))
    for item in facts:
        if key_of(item) not in taken:
            out.append(_stamped(item, False))
    return out


def _stamped(item: Any, visible: bool) -> Any:
    """``item`` carrying this node's verdict on whether it is drawn.

    A shown item that never had a ``visible`` key does not gain one: services
    have always travelled without it, and readers treat its absence as shown.
    Only hiding is news, and properties keep the explicit flag they arrived with.
    """
    if not isinstance(item, dict):
        return item
    if visible and "visible" not in item:
        return dict(item)
    return {**item, "visible": visible}


def merge_services(
    base: list[Any] | None,
    incoming: list[Any] | None,
    *,
    discovered: bool = False,
) -> list[Any]:
    """Union two service lists on (port, protocol, name); incoming wins.

    ``discovered`` marks ``incoming`` as scanner output rather than a user edit.
    A scan knows a port, not the name a user gave the service on it, so an
    unmatched discovered entry falls back to the port: it refreshes that entry
    instead of appending a second, and never renames or re-icons it. Without
    this, a rescan would either duplicate every renamed service or — replacing
    the list, as the scan did before the inventory owned the facts — erase the
    user's curation.
    """
    out: list[Any] = [dict(s) if isinstance(s, dict) else s for s in (base or [])]
    index = {_service_key(s): i for i, s in enumerate(out)}
    by_port: dict[Any, list[int]] = {}
    for i, s in enumerate(out):
        pk = _port_key(s)
        if pk is not None:
            by_port.setdefault(pk, []).append(i)
    used: set[int] = set()

    for svc in incoming or []:
        key = _service_key(svc)
        pos = index.get(key)
        if pos is None and discovered:
            pos = next((p for p in by_port.get(_port_key(svc), ()) if p not in used), None)
        if pos is None:
            out.append(dict(svc) if isinstance(svc, dict) else svc)
            pos = len(out) - 1
            index[key] = pos
            pk = _port_key(svc)
            if pk is not None:
                by_port.setdefault(pk, []).append(pos)
            used.add(pos)
            continue
        used.add(pos)
        if isinstance(svc, dict) and isinstance(out[pos], dict):
            merged = {**out[pos], **svc}
            if discovered:
                for field in _CURATED_SERVICE_FIELDS:
                    if out[pos].get(field):
                        merged[field] = out[pos][field]
            out[pos] = merged
        else:
            out[pos] = svc
    return out


def changed_facts(device: Mapping[str, Any], facts: Mapping[str, Any]) -> dict[str, Any]:
    """The subset of ``facts`` that actually differs from the row.

    A canvas save sends a *full* copy of the device — the facts were hydrated
    into the node when the canvas loaded — so a save triggered by nothing but a
    node being dragged would otherwise rewrite the row from a snapshot that may
    be hours old, silently reverting an edit made meanwhile in the inventory, on
    another canvas, or by the scanner. Narrowing to what differs turns the
    write-through from "push my whole snapshot" into "push my edit".

    Mirrors :func:`merge_facts_into_device`: a blank incoming value is not a
    change (it never clears an established one), and a list counts as changed
    only when it would actually be replaced by a different one.
    """
    out: dict[str, Any] = {}
    for field in (*DEVICE_SCALARS, "label", "type"):
        incoming = facts.get(field)
        if _blank(incoming) or incoming == device.get(field):
            continue
        out[field] = incoming

    if not _blank(facts.get("ieee_address")) and _blank(device_ieee(device)):
        out["ieee_address"] = facts["ieee_address"]
    if "show_hardware" in facts and bool(facts["show_hardware"]) != bool(
        device.get("show_hardware")
    ):
        out["show_hardware"] = facts["show_hardware"]

    for field in ("properties", "services"):
        if field in facts and list(facts[field] or []) != list(device.get(field) or []):
            out[field] = facts[field]

    # Live observations, not edits: carried only where the merge would use them.
    if facts.get("status") and device.get("status_live") in (None, "", "unknown"):
        out["status"] = facts["status"]
    for field in ("last_seen", "last_scan", "response_time_ms"):
        if facts.get(field) is not None:
            out[field] = facts[field]
    return out


def merge_facts_into_device(
    device: dict[str, Any],
    facts: Mapping[str, Any],
    *,
    devices: list[dict[str, Any]],
    overwrite_scalars: bool,
    replace_lists: bool,
) -> None:
    """Fold one view of a device into its inventory row, in place.

    Two independent knobs, because the callers need three combinations:

    * ``overwrite_scalars`` — a non-blank incoming value replaces the row's.
      True for the backfill (nodes are visited oldest-edit-first, so the most
      recently edited canvas is the last writer and wins) and for a user's save.
      A blank *never* clears an established value in either mode.
    * ``replace_lists`` — properties/services are taken wholesale rather than
      unioned. True only for a user's save: otherwise a property they deleted
      would come straight back. The backfill unions, so nothing any canvas
      recorded is lost. It applies list by list: a list absent from ``facts`` is
      left alone even in replace mode.

    ``show_hardware`` is a real boolean: a user save may turn it off, any other
    merge only ever turns it on.
    """
    for field in (*DEVICE_SCALARS, "label", "type"):
        incoming = facts.get(field)
        if _blank(incoming):
            continue
        if field == "mac":
            incoming = _norm_mac(incoming)
        if overwrite_scalars or _blank(device.get(field)):
            device[field] = incoming

    # Identity, never overwritten — two IEEEs mean two devices. And never
    # adopted when another row already owns it (homelable #352): that row *is*
    # the radio, and two rows claiming one IEEE is how the standalone backfill
    # used to fail.
    ieee = facts.get("ieee_address")
    if (
        not _blank(ieee)
        and _blank(device_ieee(device))
        and _ieee_owner(devices, str(ieee), other_than=device) is None
    ):
        set_device_ieee(device, str(ieee))

    if "show_hardware" in facts:
        if replace_lists:
            device["show_hardware"] = bool(facts["show_hardware"])
        elif facts["show_hardware"]:
            device["show_hardware"] = True

    # Per list, and only for one the caller actually sent: a partial update
    # carrying properties alone must leave services as they were.
    if replace_lists and "properties" in facts:
        device["properties"] = copy.deepcopy(list(facts["properties"] or []))
    elif facts.get("properties"):
        device["properties"] = merge_properties(device.get("properties"), facts["properties"])
    if replace_lists and "services" in facts:
        device["services"] = copy.deepcopy(list(facts["services"] or []))
    elif facts.get("services"):
        device["services"] = merge_services(device.get("services"), facts["services"])

    # Live status: keep the freshest observation rather than the last writer.
    last_seen, status, last_scan = (
        facts.get("last_seen"),
        facts.get("status"),
        facts.get("last_scan"),
    )
    if _newer(last_seen, device.get("last_seen")):
        device["last_seen"] = last_seen
        device["status_live"] = status or device.get("status_live") or "unknown"
        if facts.get("response_time_ms") is not None:
            device["response_time_ms"] = facts["response_time_ms"]
    elif device.get("status_live") in (None, "", "unknown") and status:
        device["status_live"] = status
    if _newer(last_scan, device.get("last_scan")):
        device["last_scan"] = last_scan


def fill_missing(device: dict[str, Any], facts: Mapping[str, Any]) -> bool:
    """Fill only what the row lacks from ``facts``. Returns True if it changed.

    homelable #354: a canvas saved while an earlier migration was stuck minted a
    blank row and linked the node to it, then the node's facts were dropped. A
    row that already holds a fact is never overwritten here, so an edit made
    after the link survives and a second pass is a no-op.
    """
    before = copy.deepcopy(device)
    for field in (*DEVICE_SCALARS, "label", "type"):
        value = facts.get(field)
        if not _blank(value) and _blank(device.get(field)):
            device[field] = _norm_mac(value) if field == "mac" else value
    if facts.get("show_hardware") and not device.get("show_hardware"):
        device["show_hardware"] = True
    for field in ("properties", "services"):
        if facts.get(field) and not device.get(field):
            device[field] = copy.deepcopy(list(facts[field]))
    if device.get("status_live") in (None, "", "unknown") and facts.get("status"):
        device["status_live"] = facts["status"]
    for field in ("last_seen", "last_scan", "response_time_ms"):
        if facts.get(field) is not None and device.get(field) is None:
            device[field] = facts[field]
    return device != before


def device_from_facts(facts: Mapping[str, Any], *, now: str) -> dict[str, Any]:
    """Mint the inventory row for a device that has none.

    Tagged ``canvas`` so the inventory filters can tell hand-drawn gear apart
    from anything a scan or import found. It is on a canvas already, so it is
    past the pending queue: ``approved``.
    """
    row: dict[str, Any] = {
        "id": f"pd-{uuid.uuid4().hex[:8]}",
        "label": facts.get("label"),
        "type": facts.get("type"),
        "hostname": facts.get("hostname"),
        "ip": facts.get("ip"),
        "mac": _norm_mac(facts.get("mac")),
        "os": facts.get("os"),
        "open_ports": [],
        "services": copy.deepcopy(list(facts.get("services") or [])),
        "properties": copy.deepcopy(list(facts.get("properties") or [])),
        "notes": facts.get("notes"),
        "cpu_count": facts.get("cpu_count"),
        "cpu_model": facts.get("cpu_model"),
        "ram_gb": facts.get("ram_gb"),
        "disk_gb": facts.get("disk_gb"),
        "show_hardware": bool(facts.get("show_hardware")),
        "check_method": facts.get("check_method"),
        "check_target": facts.get("check_target"),
        "suggested_type": facts.get("type"),
        "status": "approved",
        "status_live": facts.get("status") or "unknown",
        "last_seen": facts.get("last_seen") if _parse_ts(facts.get("last_seen")) else None,
        "last_scan": facts.get("last_scan") if _parse_ts(facts.get("last_scan")) else None,
        "response_time_ms": facts.get("response_time_ms"),
        "discovery_source": CANVAS_SOURCE,
        "discovery_sources": [CANVAS_SOURCE],
        "discovered_at": now,
        "updated_at": now,
    }
    if facts.get("ieee_address"):
        set_device_ieee(row, str(facts["ieee_address"]))
    return row


def _add_source(sources: list[str] | None, source: str) -> list[str]:
    out = [s for s in (sources or []) if s]
    if source not in out:
        out.append(source)
    return out


def link_facts(
    devices: list[dict[str, Any]],
    node: dict[str, Any],
    facts: Mapping[str, Any],
    *,
    now: str,
    overwrite_scalars: bool = False,
    replace_lists: bool = False,
    only_changed: bool = False,
    changed_fields: list[str] | None = None,
    strict_view: bool = False,
) -> tuple[dict[str, Any] | None, bool]:
    """Point one node at its inventory row, creating or merging as needed.

    ``facts`` is this node's view of the device — what a canvas save sent, or a
    legacy node's own fields during the backfill. Sets ``node["device_id"]`` and
    returns ``(row, changed)``; ``(None, False)`` for canvas furniture. A newly
    minted row is appended to ``devices``.

    Two narrowings turn a save from "push my whole snapshot" into "push my
    edit", and they compose. Identity matching always uses the full ``facts`` —
    the row has to be found before it can be narrowed against.

    * ``changed_fields`` — what the sender says it edited since it loaded the
      device. Authoritative: a fact absent from the list is not written even
      when it differs, because the difference means the *row* moved on.
    * ``only_changed`` — drop facts already equal to the row, so a no-op save
      writes nothing at all.

    Neither narrows the node's ``display_view``: order and visibility are this
    node's own, read off the full ``facts``. The narrowing exists to protect the
    *shared* row from a stale snapshot, and a node's view has no other writer.
    ``strict_view`` is :func:`next_view`'s ``strict``.
    """
    if is_furniture(str(facts.get("type") or node_type(node))):
        node.pop("device_id", None)
        return None, False

    device = None
    if node.get("device_id"):
        device = next((d for d in devices if d.get("id") == node["device_id"]), None)
    if device is None:
        device = find_device_for(
            devices,
            ip=facts.get("ip"),
            mac=facts.get("mac"),
            ieee=facts.get("ieee_address"),
        )

    if device is None:
        device = device_from_facts(facts, now=now)
        devices.append(device)
        node["device_id"] = device["id"]
        _record_view(node, facts, device, strict=strict_view)
        return device, True

    before = copy.deepcopy(device)
    merged: Mapping[str, Any] = facts
    if changed_fields is not None:
        keep = set(changed_fields) | LIVE_FACT_FIELDS
        merged = {k: v for k, v in merged.items() if k in keep}
    if only_changed:
        merged = changed_facts(device, merged)
    merge_facts_into_device(
        device,
        merged,
        devices=devices,
        overwrite_scalars=overwrite_scalars,
        replace_lists=replace_lists,
    )
    # Did the facts move? Measured before the source tag, which is bookkeeping:
    # it must not make a save that only moved a node read as a device edit.
    changed = device != before
    device["discovery_sources"] = _add_source(device.get("discovery_sources"), CANVAS_SOURCE)
    node["device_id"] = device["id"]
    _record_view(node, facts, device, strict=strict_view)
    if changed:
        device["updated_at"] = now
    return device, changed


def _record_view(
    node: dict[str, Any], facts: Mapping[str, Any], device: Mapping[str, Any], *, strict: bool
) -> None:
    node[VIEW_KEY] = next_view(node.get(VIEW_KEY), view_from_facts(facts), device, strict=strict)


# ─── Node shape ──────────────────────────────────────────────────────────────


def facts_of_node(node: Mapping[str, Any]) -> dict[str, Any]:
    """The device facts a stored or incoming node carries, plus label/type.

    Only the keys actually present are returned, so an absent list is never
    mistaken for an empty one by a partial merge.
    """
    data = node.get("data") or {}
    facts: dict[str, Any] = {}
    for field in NODE_FACT_FIELDS:
        if field in node:
            facts[field] = node[field]
        elif field in data:
            facts[field] = data[field]
    facts["label"] = node_value(node, "label")
    facts["type"] = node_type(node) or None
    return facts


def carries_facts(node: Mapping[str, Any]) -> bool:
    """True while a stored node still holds any device fact of its own."""
    data = node.get("data") or {}
    return any(f in node or f in data for f in NODE_FACT_FIELDS)


def strip_facts(node: dict[str, Any]) -> None:
    """Drop the device facts off a stored node, in place — the row owns them."""
    for field in NODE_FACT_FIELDS:
        node.pop(field, None)
    data = node.get("data")
    if isinstance(data, dict):
        for field in NODE_FACT_FIELDS:
            data.pop(field, None)


def hydrate_node(node: Mapping[str, Any], device: Mapping[str, Any] | None) -> dict[str, Any]:
    """A node as the panel reads it: presentation from the node, facts from the row.

    The device fields stay on the wire exactly where they have always been
    (flat on the node), so every reader is unaffected by the split. A node with
    no row — furniture, or a legacy node the backfill could not link — is
    returned as stored. Services and properties come out in this node's order,
    with its own visibility (:func:`apply_view`).
    """
    out = copy.deepcopy(dict(node))
    # Not part of the wire shape: the view is reported *through* the services
    # and properties it orders, each carrying its `visible` flag.
    view = out.pop(VIEW_KEY, None) or {}
    if device is None:
        return out
    for field in DEVICE_SCALARS:
        out[field] = device.get(field)
    out["label"] = device.get("label") or node_value(node, "label")
    out["type"] = device.get("type") or node_type(node) or None
    out["services"] = apply_view(device.get("services"), view.get("services"), "services")
    out["properties"] = apply_view(device.get("properties"), view.get("properties"), "properties")
    out["show_hardware"] = bool(device.get("show_hardware"))
    out["ieee_address"] = device_ieee(device)
    out["status"] = device.get("status_live") or "unknown"
    out["last_seen"] = device.get("last_seen")
    out["last_scan"] = device.get("last_scan")
    out["response_time_ms"] = device.get("response_time_ms")
    return out


# ─── Backfill ────────────────────────────────────────────────────────────────


def _edit_order(item: tuple[str, dict[str, Any]]) -> tuple[str, str, str]:
    _, node = item
    return (
        str(node.get("updated_at") or ""),
        str(node.get("created_at") or ""),
        str(node.get("id") or ""),
    )


def backfill_node_devices(
    canvases: Mapping[str, Mapping[str, Any]],
    devices: list[dict[str, Any]],
    *,
    now: str,
    on_error: Callable[[dict[str, Any], Exception], None] | None = None,
) -> dict[str, int]:
    """Link every non-furniture node to a Device Inventory row, in place.

    Two kinds of node need work:

    * **unlinked** — no ``device_id``, or one naming a row that no longer
      exists. It is matched to a row by ieee > ip > mac, or a row is minted,
      and its facts merge in: scalars last-writer-wins (nodes are visited
      oldest-edit-first, so the most recently edited canvas wins), properties
      and services unioned — nothing any canvas recorded is lost.
    * **linked but still carrying facts** — the #354 case. Only what the row
      is missing is filled; a fact the row already holds is never overwritten.

    Either way the node's lists become its ``display_view`` (homelable #357) and
    its facts are then stripped. Each node is isolated: one
    that fails for *any* reason is logged, left exactly as it was (its facts
    still on it, so it keeps rendering), and costs only itself — homelable #352
    and #354 were both one node aborting the whole run. Never deletes a node or
    a row. A second pass finds nothing to do.

    Returns ``{linked, created, merged, filled, skipped}`` for the log.
    """
    stats = {"linked": 0, "created": 0, "merged": 0, "filled": 0, "skipped": 0}
    by_id = {d.get("id"): d for d in devices}
    todo: list[tuple[str, dict[str, Any]]] = []
    for design_id, canvas in canvases.items():
        for node in canvas.get("nodes", []) or []:
            if not isinstance(node, dict) or is_furniture(node_type(node)):
                continue
            linked = node.get("device_id") in by_id
            if not linked or carries_facts(node):
                todo.append((design_id, node))

    for design_id, node in sorted(todo, key=_edit_order):
        snapshot_node = copy.deepcopy(node)
        snapshot_devices = copy.deepcopy(devices)
        try:
            facts = facts_of_node(node)
            row = by_id.get(node.get("device_id"))
            if row is not None:
                if fill_missing(row, facts):
                    row["updated_at"] = now
                    stats["filled"] += 1
                # The node's own lists are what this canvas drew — they define
                # its view outright, whatever the row gained meanwhile.
                _record_view(node, facts, row, strict=True)
            else:
                node.pop("device_id", None)
                existed = find_device_for(
                    devices,
                    ip=facts.get("ip"),
                    mac=facts.get("mac"),
                    ieee=facts.get("ieee_address"),
                )
                row, _ = link_facts(
                    devices,
                    node,
                    facts,
                    now=now,
                    overwrite_scalars=True,
                    # The node's own lists are exactly what this canvas drew,
                    # empty ones included, so they define its view outright.
                    # Anything else the row carries (a service a scan
                    # fingerprinted, a property another canvas added) stays
                    # hidden here rather than appearing on every canvas.
                    strict_view=True,
                )
                if row is None:  # pragma: no cover - furniture filtered above
                    continue
                by_id[row["id"]] = row
                stats["linked"] += 1
                if existed is None:
                    stats["created"] += 1
                    _LOGGER.info(
                        "Inventory backfill: node %s (%s) created device %s",
                        node.get("id"), facts.get("label"), row["id"],
                    )
                else:
                    stats["merged"] += 1
                    _LOGGER.info(
                        "Inventory backfill: node %s (%s, design %s) merged into device %s",
                        node.get("id"), facts.get("label"), design_id, row["id"],
                    )
            strip_facts(node)
        except Exception as exc:  # noqa: BLE001 — one bad node costs only itself
            node.clear()
            node.update(snapshot_node)
            devices[:] = snapshot_devices
            by_id = {d.get("id"): d for d in devices}
            stats["skipped"] += 1
            if on_error is not None:
                on_error(node, exc)
            _LOGGER.warning(
                "Inventory backfill skipped node %s: %s", node.get("id"), exc
            )
    return stats


def seed_node_views(
    canvases: Mapping[str, Mapping[str, Any]], devices: list[dict[str, Any]]
) -> int:
    """Give every linked node with no view one built from its row, in place.

    Only a node linked before views existed lacks one — the backfill, the
    approve path and every canvas save record it. Without it the node falls
    through to "no view, show everything" and the next scan pushes a newly
    fingerprinted service onto it. Seeding from the row keeps the canvas showing
    exactly what it shows today; only what the row gains *later* is held back.
    Idempotent: returns how many nodes it seeded, 0 on every later load.
    """
    by_id = {d.get("id"): d for d in devices}
    seeded = 0
    for canvas in canvases.values():
        for node in canvas.get("nodes", []) or []:
            if not isinstance(node, dict) or VIEW_KEY in node:
                continue
            row = by_id.get(node.get("device_id"))
            if row is None or is_furniture(node_type(node)):
                continue
            node[VIEW_KEY] = view_of_device(row)
            seeded += 1
    return seeded
