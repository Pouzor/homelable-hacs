"""Rack canvas: validation, inventory overlay and inventory resolution.

Port of the standalone `racks` route and schema (homelable #315, #323, #405,
#407). The standalone keeps three SQL tables; here the whole rack state of a
design is one JSON blob in the ``homelable_racks`` Store, so every function in
this module is pure — the coordinator owns loading and saving.

Stored shape, per design id::

    {"racks": [...], "devices": [...], "cables": [...], "viewport": {...}}

Racks, mounts and cables use the snake_case wire shape the panel's
`rackSerializer` sends, so what is saved is exactly what is loaded back.
"""

from __future__ import annotations

import copy
import uuid
from collections.abc import Iterable, Sequence
from typing import Any

# Kept in sync with the frontend rack model (frontend-src/src/types/rack.ts).
WIDTH_STANDARDS = {"19", "10"}
NUMBERINGS = {"bottom-up", "top-down"}
CABLE_TYPES = {"ethernet", "fiber"}
# When a mount draws its ports; "auto" leaves the faceplate in charge.
PORT_VISIBILITIES = {"auto", "always", "hover"}
# 12-column horizontal grid: full = 12, half = 6, third = 4, quarter = 3.
RACK_COLUMNS = 12
MIN_RACK_U = 1
MAX_RACK_U = 100

# Discovery source of an inventory entry created from a rack canvas. Such an
# entry describes a mount (a chassis, a patch panel, a shelf), never a host, so
# it is never approved onto a logical canvas.
RACK_SOURCE = "rack"

# Device kinds that cannot be racked. An exclusion list rather than an allow
# list: new hardware types should show up in the tray by default, and only the
# obviously virtual / mesh / annotation kinds opt out. Mirrors
# `UNRACKABLE_TYPES` in frontend-src/src/utils/rackable.ts — a test fails when
# the two drift.
UNRACKABLE_TYPES = frozenset(
    {
        "vm",
        "lxc",
        "docker_container",
        "mobile",
        "laptop",
        "light",
        "socket",
        "load",
        "groupRect",
        "group",
        "text",
        "zigbee_coordinator",
        "zigbee_router",
        "zigbee_enddevice",
        "zwave_coordinator",
        "zwave_router",
        "zwave_enddevice",
    }
)

EMPTY_STATE: dict[str, Any] = {
    "racks": [],
    "devices": [],
    "cables": [],
    "viewport": {"x": 0, "y": 0, "zoom": 1},
}

# A mount prints its services as a short list, not a port scan dump.
_MAX_SERVICES = 12
_COMMON_PORTS = {22, 80, 443}

# A mount's footprint in the rack grid: (u_start, u_height, col_start, col_span).
_Box = tuple[int, int, int, int]


def empty_state() -> dict[str, Any]:
    return copy.deepcopy(EMPTY_STATE)


def is_rack_only(device: dict[str, Any]) -> bool:
    """True for an inventory entry created from a rack canvas."""
    return device.get("discovery_source") == RACK_SOURCE or RACK_SOURCE in (
        device.get("discovery_sources") or []
    )


# ─── Save validation ─────────────────────────────────────────────────────────


def _int(value: Any, field: str, default: int | None = None) -> int:
    if value is None and default is not None:
        return default
    # bool is an int subclass; a `true` height is a client bug, not a 1U plate.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    if isinstance(value, float) and not value.is_integer():
        raise ValueError(f"{field} must be a whole number")
    return int(value)


def _num(value: Any, field: str, default: float = 0) -> float:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a number")
    return float(value)


def _str(value: Any, field: str, default: str | None = None) -> str:
    if value is None and default is not None:
        return default
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} is required")
    return value


def _opt_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _clean_rack(raw: dict[str, Any]) -> dict[str, Any]:
    rack_id = _str(raw.get("id"), "rack id")
    u_height = _int(raw.get("u_height"), "u_height", 42)
    if not MIN_RACK_U <= u_height <= MAX_RACK_U:
        raise ValueError(f"u_height must be between {MIN_RACK_U} and {MAX_RACK_U}")
    width = raw.get("width_standard") or "19"
    if width not in WIDTH_STANDARDS:
        raise ValueError(f"width_standard must be one of {sorted(WIDTH_STANDARDS)}")
    numbering = raw.get("numbering") or "bottom-up"
    if numbering not in NUMBERINGS:
        raise ValueError(f"numbering must be one of {sorted(NUMBERINGS)}")
    style = raw.get("style")
    return {
        "id": rack_id,
        "name": _str(raw.get("name"), "rack name", "Rack"),
        "u_height": u_height,
        "width_standard": width,
        "numbering": numbering,
        "location": _opt_str(raw.get("location")),
        "style": style if isinstance(style, dict) else {},
        "pos_x": _num(raw.get("pos_x"), "pos_x"),
        "pos_y": _num(raw.get("pos_y"), "pos_y"),
    }


def _clean_ports(raw: Any) -> list[dict[str, Any]]:
    """A port with no id is not a port: no cable could ever name it."""
    if not isinstance(raw, list):
        return []
    return [p for p in raw if isinstance(p, dict) and p.get("id")]


def _clean_device(raw: dict[str, Any]) -> dict[str, Any]:
    device_id = _str(raw.get("id"), "device id")
    u_start = _int(raw.get("u_start"), "u_start", 1)
    u_height = _int(raw.get("u_height"), "u_height", 1)
    if u_start < 1 or u_height < 1:
        raise ValueError("u_start and u_height are 1-based and must be >= 1")
    col_start = _int(raw.get("col_start"), "col_start", 0)
    col_span = _int(raw.get("col_span"), "col_span", RACK_COLUMNS)
    if not 0 <= col_start < RACK_COLUMNS:
        raise ValueError(f"col_start must be within the {RACK_COLUMNS}-column grid")
    if not 1 <= col_span <= RACK_COLUMNS:
        raise ValueError(f"col_span must be between 1 and {RACK_COLUMNS}")
    # Legal apart and still illegal together: 11 + 12 spans to column 23.
    if col_start + col_span > RACK_COLUMNS:
        raise ValueError(
            f"col_start + col_span must not exceed the {RACK_COLUMNS}-column grid"
        )
    visibility = raw.get("port_visibility")
    return {
        "id": device_id,
        "rack_id": _str(raw.get("rack_id"), "rack_id"),
        "device_id": _opt_str(raw.get("device_id")),
        "node_id": _opt_str(raw.get("node_id")),
        "label": _str(raw.get("label"), "label", "Device"),
        "u_start": u_start,
        "u_height": u_height,
        "col_start": col_start,
        "col_span": col_span,
        "faceplate_id": _str(raw.get("faceplate_id"), "faceplate_id", "blank-1u"),
        "color": _opt_str(raw.get("color")),
        "status": _str(raw.get("status"), "status", "unknown"),
        # Anything unknown — including a missing key — is `auto`.
        "port_visibility": visibility if visibility in PORT_VISIBILITIES else "auto",
        "ports": _clean_ports(raw.get("ports")),
    }


def _clean_cable(raw: dict[str, Any]) -> dict[str, Any]:
    cable_type = raw.get("type") or "ethernet"
    if cable_type not in CABLE_TYPES:
        raise ValueError(f"type must be one of {sorted(CABLE_TYPES)}")
    props = raw.get("properties")
    return {
        "id": _str(raw.get("id"), "cable id"),
        "from_device_id": _str(raw.get("from_device_id"), "from_device_id"),
        "from_port_id": _str(raw.get("from_port_id"), "from_port_id"),
        "to_device_id": _str(raw.get("to_device_id"), "to_device_id"),
        "to_port_id": _str(raw.get("to_port_id"), "to_port_id"),
        "type": cable_type,
        "color": _str(raw.get("color"), "color", "#39d353"),
        "label": _opt_str(raw.get("label")),
        "label_visible": bool(raw.get("label_visible")),
        # Keep only usable records — a property with no key draws an empty badge.
        "properties": [
            p
            for p in (props if isinstance(props, list) else [])
            if isinstance(p, dict) and str(p.get("key", "")).strip()
        ],
    }


def _overlaps(box: _Box, others: Iterable[_Box]) -> bool:
    """Whether a footprint (u_start, u_height, col_start, col_span) hits any other."""
    u_start, u_height, col_start, col_span = box
    return any(
        u_start < o_u + o_uh
        and o_u < u_start + u_height
        and col_start < o_c + o_cs
        and o_c < col_start + col_span
        for o_u, o_uh, o_c, o_cs in others
    )


def _box(device: dict[str, Any]) -> _Box:
    return (
        device["u_start"],
        device["u_height"],
        device["col_start"],
        device["col_span"],
    )


def clean_save(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a full rack save and return the state to store.

    Raises ``ValueError`` with a user-facing reason. The rules are the ones the
    standalone enforces in its pydantic schema and route: every mount lands in
    a rack of the payload and fits under its top rail, no two mounts share a
    square, and every cable joins two mounts of the payload — otherwise the
    prune a full save implies would orphan them.
    """
    racks = [_clean_rack(r) for r in payload.get("racks") or [] if isinstance(r, dict)]
    devices = [
        _clean_device(d) for d in payload.get("devices") or [] if isinstance(d, dict)
    ]
    cables = [_clean_cable(c) for c in payload.get("cables") or [] if isinstance(c, dict)]

    for kind, rows in (("rack", racks), ("device", devices), ("cable", cables)):
        ids = [r["id"] for r in rows]
        if len(ids) != len(set(ids)):
            raise ValueError(f"Duplicate {kind} id in payload")

    heights = {r["id"]: r["u_height"] for r in racks}
    for device in devices:
        rack_height = heights.get(device["rack_id"])
        if rack_height is None:
            raise ValueError(
                f"Device {device['id']} references unknown rack {device['rack_id']}"
            )
        top = device["u_start"] + device["u_height"] - 1
        if top > rack_height:
            raise ValueError(
                f"Device {device['id']} does not fit in rack {device['rack_id']}: "
                f"U {device['u_start']}–{top} of {rack_height}U"
            )

    by_rack: dict[str, list[dict[str, Any]]] = {}
    for device in devices:
        by_rack.setdefault(device["rack_id"], []).append(device)
    for mounts in by_rack.values():
        for i, a in enumerate(mounts):
            if _overlaps(_box(a), [_box(b) for b in mounts[i + 1 :]]):
                other = next(b for b in mounts[i + 1 :] if _overlaps(_box(a), [_box(b)]))
                raise ValueError(
                    f"Devices {a['id']} and {other['id']} overlap in rack {a['rack_id']}"
                )

    device_ids = {d["id"] for d in devices}
    for cable in cables:
        if (
            cable["from_device_id"] not in device_ids
            or cable["to_device_id"] not in device_ids
        ):
            raise ValueError(
                f"Cable {cable['id']} references a device outside this payload"
            )

    viewport = payload.get("viewport")
    return {
        "racks": racks,
        "devices": devices,
        "cables": cables,
        "viewport": dict(viewport) if isinstance(viewport, dict) else {"x": 0, "y": 0, "zoom": 1},
    }


# ─── Inventory-owned front panel ─────────────────────────────────────────────


def rack_model(entry: dict[str, Any]) -> dict[str, Any] | None:
    """The rack modelisation an inventory row owns, or None when never modelled.

    `rack_faceplate_id` is the flag: a device that was never mounted carries
    none, and the mount's own denormalized copy then stands unchanged.
    """
    if not entry.get("rack_faceplate_id"):
        return None
    return {
        "faceplate_id": entry["rack_faceplate_id"],
        "u_height": entry.get("rack_u_height"),
        "col_span": entry.get("rack_col_span"),
        "color": entry.get("rack_color"),
        "ports": _clean_ports(entry.get("rack_ports")),
    }


def _height_fits(u_start: int, u_height: Any, rack_u_height: int | None) -> bool:
    if not isinstance(u_height, int) or u_height < 1:
        return False
    return rack_u_height is None or u_start + u_height - 1 <= rack_u_height


def _span_fits(col_start: int, col_span: Any) -> bool:
    if not isinstance(col_span, int) or col_span < 1:
        return False
    return col_start + col_span <= RACK_COLUMNS


def _with_model(
    device: dict[str, Any],
    model: dict[str, Any] | None,
    rack_u_height: int | None,
    neighbours: Sequence[_Box],
) -> dict[str, Any]:
    """Overlay the inventory's rack modelisation onto a mount.

    Geometry is global, but a rack is not: a device grown to 4U in one rack may
    no longer fit where it sits in another. Height and width are therefore
    applied only when they still fit at the mount's own start *and* land on no
    neighbour. The plate, its colour and its ports always are, since none of
    them can overflow anything.
    """
    row = dict(device)
    if model is None:
        return row
    row["faceplate_id"] = model["faceplate_id"]
    row["ports"] = copy.deepcopy(model["ports"])
    row["color"] = model["color"]
    u_height = row["u_height"]
    if _height_fits(row["u_start"], model["u_height"], rack_u_height) and not _overlaps(
        (row["u_start"], model["u_height"], row["col_start"], row["col_span"]),
        neighbours,
    ):
        u_height = model["u_height"]
        row["u_height"] = u_height
    if _span_fits(row["col_start"], model["col_span"]) and not _overlaps(
        (row["u_start"], u_height, row["col_start"], model["col_span"]), neighbours
    ):
        row["col_span"] = model["col_span"]
    return row


def overlay_models(
    state: dict[str, Any], inventory: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """Return a copy of a stored rack state with the inventory's plates applied.

    Footprints start as stored and are replaced by what the overlay applied, so
    a rack of devices that all grew elsewhere resolves in one deterministic pass
    instead of each one being measured against stale neighbours.
    """
    out = copy.deepcopy(state)
    devices = out.get("devices", [])
    heights = {r["id"]: r.get("u_height") for r in out.get("racks", [])}
    boxes: dict[str, _Box] = {d["id"]: _box(d) for d in devices}
    placed_rows: list[dict[str, Any]] = []
    for d in devices:
        entry = inventory.get(d.get("device_id") or "")
        placed = _with_model(
            d,
            rack_model(entry) if entry else None,
            heights.get(d["rack_id"]),
            [boxes[o["id"]] for o in devices if o["id"] != d["id"] and o["rack_id"] == d["rack_id"]],
        )
        boxes[d["id"]] = _box(placed)
        placed_rows.append(placed)
    out["devices"] = placed_rows
    return out


def write_through(
    devices: Iterable[dict[str, Any]],
    previous: dict[str, dict[str, Any]],
    inventory: dict[str, dict[str, Any]],
) -> bool:
    """Copy each mount's front panel onto the inventory row it stands for.

    Every rack showing the same device then picks the change up on its next
    load. Size, unlike the plate, is not written back blind: the load overlays
    it only where it still fits that rack, so a mount can legitimately hold less
    than the row does — and saving that back would shrink the device in every
    other rack. Only a size this save actually changes travels.

    `previous` maps mount id → the mount as stored before this save. Returns
    True when any inventory row changed.
    """
    changed = False
    for device in devices:
        entry = inventory.get(device.get("device_id") or "")
        if entry is None:
            continue
        was = previous.get(device["id"])
        patch: dict[str, Any] = {
            "rack_faceplate_id": device["faceplate_id"],
            "rack_color": device["color"],
            "rack_ports": copy.deepcopy(device["ports"]),
        }
        if was is None or device["u_height"] != was.get("u_height"):
            patch["rack_u_height"] = device["u_height"]
        if was is None or device["col_span"] != was.get("col_span"):
            patch["rack_col_span"] = device["col_span"]
        # A never-modelled row takes the mount's size even on an echo, or a
        # later load would have a plate with no size to overlay.
        if entry.get("rack_u_height") is None:
            patch["rack_u_height"] = device["u_height"]
        if entry.get("rack_col_span") is None:
            patch["rack_col_span"] = device["col_span"]
        for key, value in patch.items():
            if entry.get(key) != value:
                entry[key] = value
                changed = True
    return changed


def copy_state(state: dict[str, Any]) -> dict[str, Any]:
    """Duplicate a design's rack state under fresh ids.

    Racks, mounts and cables all get new ids; the inventory and node links are
    carried over untouched, since a copy documents the same hardware.
    """
    src = copy.deepcopy(state)
    rack_ids = {r["id"]: uuid.uuid4().hex for r in src.get("racks", [])}
    racks = [{**r, "id": rack_ids[r["id"]]} for r in src.get("racks", [])]
    device_ids = {
        d["id"]: uuid.uuid4().hex for d in src.get("devices", []) if d["rack_id"] in rack_ids
    }
    devices = [
        {**d, "id": device_ids[d["id"]], "rack_id": rack_ids[d["rack_id"]]}
        for d in src.get("devices", [])
        if d["id"] in device_ids
    ]
    cables = [
        {
            **c,
            "id": uuid.uuid4().hex,
            "from_device_id": device_ids[c["from_device_id"]],
            "to_device_id": device_ids[c["to_device_id"]],
        }
        for c in src.get("cables", [])
        if c["from_device_id"] in device_ids and c["to_device_id"] in device_ids
    ]
    return {
        "racks": racks,
        "devices": devices,
        "cables": cables,
        "viewport": src.get("viewport") or {"x": 0, "y": 0, "zoom": 1},
    }


# ─── Inventory offered to the rack tray ──────────────────────────────────────


def _service_name(device: dict[str, Any]) -> str | None:
    """Name a device after the app it runs, the way the Device Inventory does.

    Ports everyone exposes (ssh, http, https) say nothing, and the generic web
    category loses to a real match, so "jellyfin" wins over "http".
    """
    candidates = [
        s
        for s in (device.get("services") or [])
        if isinstance(s, dict)
        and s.get("category")
        and s.get("service_name")
        and s.get("port") is not None
        and s.get("port") not in _COMMON_PORTS
    ]
    if not candidates:
        return None
    named = next(
        (s for s in candidates if str(s["category"]).lower() != "web"), candidates[0]
    )
    name = named.get("service_name")
    return str(name) if name else None


def device_label(device: dict[str, Any]) -> str:
    """Inventory naming, mirrored: friendly name, host, app, IP, IEEE, id."""
    return (
        device.get("friendly_name")
        or device.get("hostname")
        or _service_name(device)
        or device.get("ip")
        or device.get("ieee_address")
        or device["id"]
    )


def services(raw: Any) -> list[dict[str, Any]]:
    """Fingerprinted services, deduped and trimmed for the rack's info panel."""
    out: list[dict[str, Any]] = []
    seen: set[tuple[int | None, str | None]] = set()
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        raw_port = entry.get("port")
        port = raw_port if isinstance(raw_port, int) and not isinstance(raw_port, bool) else None
        raw_name = entry.get("service_name") or entry.get("name")
        name = str(raw_name) if raw_name else None
        if port is None and name is None:
            continue
        if (port, name) in seen:
            continue
        seen.add((port, name))
        out.append({"port": port, "name": name})
        if len(out) == _MAX_SERVICES:
            break
    return out
