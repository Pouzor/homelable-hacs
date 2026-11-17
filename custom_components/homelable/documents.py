"""Documents — the markdown documentation space.

A document is either a page or a folder in the Library tree, or it describes one
thing elsewhere in the integration: a Device Inventory row, a piece of canvas
furniture (a zone, a group), or a whole canvas. The tree the user navigates is
*not* stored here: device documents are pivoted in the panel by zone, subnet,
type and so on, because every one of those groupings is derivable from data the
panel already holds and re-pivoting must be instant.

The generated header is written once, at creation; ``block`` hands the editor a
freshly generated section on request, and ``facts_snapshot`` is what lets the
panel say the device has moved on since the document was written. The one flow
that rewrites a body is ``update_from_device``, and only through the guided
three-way merge of ``doc_reconcile``: nothing is overwritten unless the device
simply moved on (automatic) or the user resolved the conflict explicitly.
``baseline_body`` records the body that was *generated* at the last sync — never
the merged one — so a value the user deliberately kept is surfaced again when
the device moves on once more.

Port of homelable #418, #421, #437, #458, #489, #509, #583
(``backend/app/api/routes/documents.py``). SQLAlchemy became one ``Store``:

    {"documents": {id: document},
     "revisions": {document_id: [revision, newest first]},
     "notes_migrated": bool}

What SQLite enforced with partial unique indexes (one document per device, node
or design; one homepage) is enforced here, in the write paths. Every link is
cleared rather than cascaded when the thing it names is deleted, so what the
user wrote outlives the inventory and the canvas.
"""

from __future__ import annotations

import base64
import copy
import logging
import re
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from . import doc_backlinks, doc_search, inventory_sync
from .const import STORAGE_KEY_DOCUMENTS, STORAGE_VERSION_DOCUMENTS
from .doc_export import ExportDoc, build_zip
from .doc_reconcile import Resolution, Result, preview_id, reconcile
from .doc_template import (
    BLOCKS,
    TEMPLATE_DEVICE,
    TEMPLATE_HOMEPAGE,
    TEMPLATE_IDS,
    as_row,
    facts_snapshot,
    render_block,
    render_device_document,
    render_homepage_document,
    render_library_document,
)
from .doc_tree import (
    DOCUMENT_KINDS,
    REVISION_LIMIT,
    TREE_KINDS,
    is_ancestor,
    parse_frontmatter,
    subtree_ids,
    tags_from,
    unique_slug,
)

if TYPE_CHECKING:
    from .coordinator import HomelableCoordinator

_LOGGER = logging.getLogger(__name__)

# The reasons a caller may put its own name to. Everything else a revision can
# say — "restore", "regenerate", "scaffold", "migrate", "sync" — belongs to the
# action that takes it, so no client can dress a plain edit up as one of those.
CLIENT_REVISION_REASONS = frozenset({"edit", "import"})

# The furniture a device can sit *in*, and so be located by. A `text`
# annotation is furniture but a caption, not a place (homelable #446).
_ZONE_TYPES = frozenset({"group", "groupRect"})

_INTERVAL = re.compile(r"^\s*(\d+)\s*([dwmy])\s*$", re.IGNORECASE)
_INTERVAL_DAYS = {"d": 1, "w": 7, "m": 30, "y": 365}

# Fields a summary carries. The body, the snapshot and the baseline stay off
# listings so the tree payload stays small.
_SUMMARY_FIELDS = (
    "id", "kind", "title", "slug", "icon", "parent_id", "sort_order",
    "device_id", "node_id", "design_id", "tags", "frontmatter", "starred",
    "is_homepage", "template_id", "reviewed_at", "edited_at",
    "facts_synced_at", "created_at", "updated_at",
)

_EMPTY: dict[str, Any] = {"documents": {}, "revisions": {}, "notes_migrated": False}


class DocumentError(Exception):
    """A request the documentation space refuses; ``code`` is the WS error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _parse_interval(raw: Any) -> timedelta | None:
    """``review_every: 6m`` → 180 days. Anything unparseable means "never due"."""
    match = _INTERVAL.match(str(raw or ""))
    if not match:
        return None
    return timedelta(days=int(match.group(1)) * _INTERVAL_DAYS[match.group(2).lower()])


def _device_name(device: dict[str, Any]) -> str:
    return (
        device.get("label")
        or device.get("friendly_name")
        or device.get("hostname")
        or device.get("ip")
        or "device"
    )


class DocumentManager:
    """Owns the ``homelable_documents`` Store and every document rule.

    Reads the inventory, the canvases and the racks through the coordinator,
    which stays the owner of those; this class never writes them.
    """

    def __init__(self, hass: HomeAssistant, coordinator: HomelableCoordinator) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self.store: Store = Store(hass, STORAGE_VERSION_DOCUMENTS, STORAGE_KEY_DOCUMENTS)
        self._data: dict[str, Any] | None = None

    # ── storage ─────────────────────────────────────────────────────────────

    async def _load(self) -> dict[str, Any]:
        if self._data is None:
            raw = await self.store.async_load()
            data = copy.deepcopy(_EMPTY)
            if isinstance(raw, dict):
                data["documents"] = dict(raw.get("documents") or {})
                data["revisions"] = dict(raw.get("revisions") or {})
                data["notes_migrated"] = bool(raw.get("notes_migrated"))
            self._data = data
        return self._data

    async def _docs(self) -> dict[str, dict[str, Any]]:
        return (await self._load())["documents"]

    async def _save(self) -> None:
        if self._data is not None:
            await self.store.async_save(self._data)

    async def _get(self, document_id: str) -> dict[str, Any]:
        doc = (await self._docs()).get(document_id)
        if doc is None:
            raise DocumentError("not_found", "Document not found")
        return doc

    # ── what the documents describe ─────────────────────────────────────────

    async def _devices(self) -> dict[str, dict[str, Any]]:
        """Inventory rows by id, in the shape the panel sees them.

        Mesh and Proxmox facts (``friendly_name``, ``vendor``, ``model``,
        ``ieee_address``…) are stored under ``data_extras``; flattened here so
        a generated document, its facts snapshot and the drift check read the
        same name and facts as the Device Inventory and the documentation tree.
        """
        pending = await self.coordinator._get_pending()  # noqa: SLF001
        flatten = self.coordinator._flatten_pending  # noqa: SLF001
        return {d["id"]: flatten(d) for d in pending.get("devices", []) if d.get("id")}

    async def _canvases(self) -> dict[str, dict[str, Any]]:
        await self.coordinator._ensure_loaded()  # noqa: SLF001
        return self.coordinator._canvases or {}  # noqa: SLF001

    async def _designs(self) -> list[dict[str, Any]]:
        await self.coordinator._ensure_loaded()  # noqa: SLF001
        return list(self.coordinator._designs or [])  # noqa: SLF001

    async def _node_exists(self, node_id: str) -> bool:
        return any(
            node.get("id") == node_id
            for canvas in (await self._canvases()).values()
            for node in canvas.get("nodes", [])
        )

    async def _device_contexts(self, device_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        """Zone, rack placement, canvas neighbours and scan ranges per device.

        All are optional: a device may be on no canvas, in no rack and in no
        zone, and the template drops those sections rather than printing them
        empty. A device drawn on several canvases is described by the first
        node found, in design order.
        """
        ranges = list(self.coordinator.get_scan_ranges())
        contexts: dict[str, dict[str, Any]] = {
            device_id: {"zone_label": None, "rack": None, "connections": [], "ranges": ranges}
            for device_id in device_ids
        }
        if not contexts:
            return contexts

        nodes: dict[str, dict[str, Any]] = {}
        node_of: dict[str, dict[str, Any]] = {}
        edges: list[dict[str, Any]] = []
        for canvas in (await self._canvases()).values():
            for node in canvas.get("nodes", []):
                if node.get("id"):
                    nodes[node["id"]] = node
                device_id = node.get("device_id")
                if device_id in contexts:
                    node_of.setdefault(device_id, node)
            edges.extend(canvas.get("edges", []))

        for device_id, node in node_of.items():
            seen: set[str] = set()
            parent_id = node.get("parent_id")
            while parent_id and parent_id not in seen:
                seen.add(parent_id)
                parent = nodes.get(parent_id)
                if parent is None:
                    break
                if inventory_sync.node_type(parent) in _ZONE_TYPES:
                    contexts[device_id]["zone_label"] = inventory_sync.node_value(parent, "label")
                    break
                parent_id = parent.get("parent_id")

        device_of_node = {node["id"]: device_id for device_id, node in node_of.items()}
        peers_of: dict[str, set[str]] = {device_id: set() for device_id in node_of}
        for edge in edges:
            source, target = edge.get("source"), edge.get("target")
            if source in device_of_node:
                peers_of[device_of_node[source]].add(target)
            if target in device_of_node:
                peers_of[device_of_node[target]].add(source)
        for device_id, peers in peers_of.items():
            labels = {
                inventory_sync.node_value(nodes[peer], "label")
                for peer in peers
                if peer in nodes
            }
            contexts[device_id]["connections"] = sorted(label for label in labels if label)

        rack_states = await self.coordinator._get_racks()  # noqa: SLF001
        for state in rack_states.values():
            racks_by_id = {r.get("id"): r for r in state.get("racks", [])}
            for mount in state.get("devices", []):
                device_id = mount.get("device_id")
                if device_id not in contexts or contexts[device_id]["rack"] is not None:
                    continue
                rack = racks_by_id.get(mount.get("rack_id")) or {}
                contexts[device_id]["rack"] = {
                    "name": rack.get("name"),
                    "u_start": mount.get("u_start"),
                    "u_height": mount.get("u_height"),
                    "col_span": mount.get("col_span"),
                }
        return contexts

    async def _device_context(self, device_id: str) -> dict[str, Any]:
        return (await self._device_contexts([device_id]))[device_id]

    # ── bodies ──────────────────────────────────────────────────────────────

    @staticmethod
    def _apply_body(doc: dict[str, Any], body: str) -> None:
        """Store a body and refresh the frontmatter/tags cache from it."""
        doc["body"] = body
        frontmatter = parse_frontmatter(body)
        doc["frontmatter"] = frontmatter
        doc["tags"] = tags_from(frontmatter)

    def _adopt_frontmatter_title(self, docs: dict[str, dict[str, Any]], doc: dict[str, Any]) -> None:
        """Follow the name the body gives, since the body is what the user edits."""
        front = doc.get("frontmatter") if isinstance(doc.get("frontmatter"), dict) else {}
        title = front.get("title")
        if not isinstance(title, str) or not title.strip() or title.strip() == doc["title"]:
            return
        doc["title"] = title.strip()
        doc["slug"] = unique_slug(docs, doc["title"], parent_id=doc.get("parent_id"), exclude_id=doc["id"])

    def _record_revision(self, doc: dict[str, Any], reason: str) -> None:
        """Snapshot the current body, then prune the document's oldest history."""
        assert self._data is not None
        history = self._data["revisions"].setdefault(doc["id"], [])
        history.insert(
            0,
            {
                "id": uuid.uuid4().hex,
                "document_id": doc["id"],
                "title": doc["title"],
                "body": doc.get("body") or "",
                "reason": reason,
                "saved_at": _iso(_now()),
            },
        )
        del history[REVISION_LIMIT:]

    async def _scaffold_body(self, doc: dict[str, Any], template_id: str | None) -> None:
        """Generate this document's initial body. Called at creation and regenerate."""
        if doc.get("device_id"):
            device = (await self._devices()).get(doc["device_id"])
            if device is not None:
                context = await self._device_context(device["id"])
                generated = render_device_document(as_row(device), **context)
                self._apply_body(doc, generated)
                doc["baseline_body"] = generated
                doc["facts_snapshot"] = facts_snapshot(as_row(device))
                doc["facts_synced_at"] = _iso(_now())
                doc["template_id"] = TEMPLATE_DEVICE
                return
        if template_id == TEMPLATE_HOMEPAGE:
            docs = [as_row(d) for d in (await self._docs()).values() if d["id"] != doc["id"]]
            devices = [as_row(d) for d in (await self._devices()).values() if d.get("status") != "hidden"]
            designs = [as_row(d) for d in await self._designs()]
            self._apply_body(
                doc, render_homepage_document(doc["title"], designs=designs, docs=docs, devices=devices)
            )
            doc["template_id"] = TEMPLATE_HOMEPAGE
            return
        self._apply_body(doc, render_library_document(template_id or "blank", doc["title"]))
        doc["template_id"] = template_id or "blank"

    # ── drift and the three-way merge ───────────────────────────────────────

    @staticmethod
    def _sync_snapshot(doc: dict[str, Any]) -> dict[str, Any] | None:
        snapshot = doc.get("facts_snapshot")
        return snapshot if isinstance(snapshot, dict) else None

    def _reconcile(
        self,
        doc: dict[str, Any],
        resolutions: list[dict[str, Any]],
        *,
        generated: str,
    ) -> Result:
        return reconcile(
            current=doc.get("body") or "",
            new=generated,
            baseline_body=doc.get("baseline_body"),
            snapshot=self._sync_snapshot(doc),
            resolutions=[
                Resolution(r["id"], r.get("choice") or "keep", r.get("custom"))
                for r in resolutions
            ],
        )

    async def _drifted_ids(self, docs: Iterable[dict[str, Any]]) -> set[str]:
        """The documents whose device has moved on in a way they do not show yet.

        A snapshot that differs is necessary, not sufficient: the body may
        already say what the device says now. Only those candidates pay for a
        generated body, and their render contexts are fetched together.
        """
        devices = await self._devices()
        candidates = [
            (doc, devices[doc["device_id"]])
            for doc in docs
            if doc.get("device_id")
            and doc.get("facts_snapshot")
            and doc["device_id"] in devices
            and doc["facts_snapshot"] != facts_snapshot(as_row(devices[doc["device_id"]]))
        ]
        if not candidates:
            return set()
        contexts = await self._device_contexts(device["id"] for _, device in candidates)
        drifted: set[str] = set()
        for doc, device in candidates:
            generated = render_device_document(as_row(device), **contexts[device["id"]])
            result = self._reconcile(doc, [], generated=generated)
            if result.auto or result.conflicts:
                drifted.add(doc["id"])
        return drifted

    async def _generated_body(self, device: dict[str, Any]) -> tuple[dict[str, Any], str]:
        """The render context for a device and the body it generates, read once."""
        context = await self._device_context(device["id"])
        return context, render_device_document(as_row(device), **context)

    @staticmethod
    def _live_binding(device: dict[str, Any], context: dict[str, Any], generated: str) -> dict[str, Any]:
        return {"facts": facts_snapshot(as_row(device)), "context": context, "generated": generated}

    def _preview_id(self, doc: dict[str, Any], live: dict[str, Any]) -> str:
        return preview_id(
            doc.get("updated_at") or "",
            self._sync_snapshot(doc),
            doc.get("body") or "",
            baseline_body=doc.get("baseline_body"),
            live=live,
        )

    async def _device_for_update(self, doc: dict[str, Any]) -> dict[str, Any]:
        if doc["kind"] == "folder":
            raise DocumentError("invalid", "A folder has no body to update from a device")
        device = (await self._devices()).get(doc.get("device_id") or "")
        if device is None:
            raise DocumentError("invalid", "This document has no device to update from")
        return device

    # ── wire shapes ─────────────────────────────────────────────────────────

    @staticmethod
    def _summary(doc: dict[str, Any], drifted: bool) -> dict[str, Any]:
        out = {key: doc.get(key) for key in _SUMMARY_FIELDS}
        out["tags"] = list(doc.get("tags") or [])
        out["frontmatter"] = dict(doc.get("frontmatter") or {})
        out["sort_order"] = doc.get("sort_order") or 0
        out["starred"] = bool(doc.get("starred"))
        out["is_homepage"] = bool(doc.get("is_homepage"))
        out["drifted"] = drifted
        return out

    async def _response(self, doc: dict[str, Any]) -> dict[str, Any]:
        out = self._summary(doc, doc["id"] in await self._drifted_ids([doc]))
        out["body"] = doc.get("body") or ""
        out["facts_snapshot"] = doc.get("facts_snapshot")
        return out

    # ── reads ───────────────────────────────────────────────────────────────

    async def list(
        self,
        *,
        kind: str | None = None,
        parent_id: str | None = None,
        device_id: str | None = None,
        tag: str | None = None,
    ) -> list[dict[str, Any]]:
        docs = list((await self._docs()).values())
        if kind:
            docs = [d for d in docs if d["kind"] == kind]
        if parent_id:
            docs = [d for d in docs if d.get("parent_id") == parent_id]
        if device_id:
            docs = [d for d in docs if d.get("device_id") == device_id]
        if tag:
            wanted = tag.lower()
            docs = [d for d in docs if any(str(t).lower() == wanted for t in d.get("tags") or [])]
        docs.sort(key=lambda d: (d.get("sort_order") or 0, d["title"]))
        drifted = await self._drifted_ids(docs)
        return [self._summary(d, d["id"] in drifted) for d in docs]

    async def get(self, document_id: str) -> dict[str, Any]:
        return await self._response(await self._get(document_id))

    async def coverage(self) -> dict[str, int]:
        """How much of the homelab is actually written down."""
        devices = [d for d in (await self._devices()).values() if d.get("status") != "hidden"]
        docs = list((await self._docs()).values())
        by_device = {d["device_id"]: d for d in docs if d.get("device_id")}
        drifted_ids = await self._drifted_ids(
            by_device[device["id"]] for device in devices if device["id"] in by_device
        )
        header_only = drifted = overdue = 0
        now = _now()
        for device in devices:
            doc = by_device.get(device["id"])
            if doc is None:
                continue
            if doc.get("edited_at") is None and doc.get("reviewed_at") is None:
                header_only += 1
            if doc["id"] in drifted_ids:
                drifted += 1
            interval = _parse_interval((doc.get("frontmatter") or {}).get("review_every"))
            since = _parse(doc.get("reviewed_at")) or _parse(doc.get("created_at"))
            if interval and since and now - since > interval:
                overdue += 1
        documented = sum(1 for device in devices if device["id"] in by_device)
        return {
            "devices": len(devices),
            "documented": documented,
            "header_only": header_only,
            "missing": len(devices) - documented,
            "drifted": drifted,
            "overdue": overdue,
            "notes_unmigrated": sum(
                1
                for device in devices
                if (device.get("notes") or "").strip() and device["id"] not in by_device
            ),
            "library_pages": sum(1 for d in docs if d["kind"] in TREE_KINDS),
        }

    async def search(self, query: str, limit: int = 25) -> dict[str, Any]:
        docs = await self._docs()
        rows = doc_search.search(docs.values(), query, limit)
        return {
            "engine": doc_search.ENGINE,
            "hits": [
                {
                    "doc_id": row["doc_id"],
                    "title": docs[row["doc_id"]]["title"],
                    "kind": docs[row["doc_id"]]["kind"],
                    "snippet": row["snippet"] or "",
                    "device_id": docs[row["doc_id"]].get("device_id"),
                }
                for row in rows
            ],
        }

    async def block(self, block: str, device_id: str) -> dict[str, str]:
        """Render one generated section, for the editor's ``/`` insert menu."""
        if block not in BLOCKS:
            raise DocumentError("invalid", f"block must be one of {sorted(BLOCKS)}")
        device = (await self._devices()).get(device_id)
        if device is None:
            raise DocumentError("not_found", "Device not found")
        context = await self._device_context(device_id) if block in {"rack", "network"} else {}
        return {"block": block, "markdown": render_block(block, as_row(device), **context)}

    async def export(self) -> dict[str, str]:
        """Every document as a zip of ``.md`` files mirroring the tree, base64."""
        docs = (await self._docs()).values()
        archive = build_zip(
            [
                ExportDoc(
                    id=d["id"],
                    kind=d["kind"],
                    title=d["title"],
                    slug=d.get("slug") or "",
                    parent_id=d.get("parent_id"),
                    body=d.get("body") or "",
                )
                for d in docs
            ]
        )
        return {
            "filename": f"homelable-documentation-{_now():%Y%m%d}.zip",
            "content": base64.b64encode(archive).decode("ascii"),
        }

    async def revisions(self, document_id: str) -> list[dict[str, Any]]:
        await self._get(document_id)
        history = (await self._load())["revisions"].get(document_id) or []
        return [
            {
                "id": r["id"],
                "document_id": r["document_id"],
                "title": r["title"],
                "reason": r["reason"],
                "saved_at": r["saved_at"],
                "size": len(r.get("body") or ""),
            }
            for r in history
        ]

    async def revision(self, revision_id: str) -> dict[str, Any]:
        for history in (await self._load())["revisions"].values():
            for r in history:
                if r["id"] == revision_id:
                    return {**r, "size": len(r.get("body") or "")}
        raise DocumentError("not_found", "Revision not found")

    async def backlinks(self, document_id: str) -> list[dict[str, Any]]:
        """The documents whose body links here."""
        await self._get(document_id)
        docs = sorted(
            (await self._docs()).values(),
            key=lambda d: (d.get("sort_order") or 0, d["title"]),
        )
        rows = [as_row(d) for d in docs]
        devices = (
            [as_row(d) for d in (await self._devices()).values()]
            if doc_backlinks.has_device_link(rows)
            else []
        )
        by_id = {d["id"]: d for d in docs}
        return [
            {
                "doc_id": hit.doc_id,
                "title": by_id[hit.doc_id]["title"],
                "kind": by_id[hit.doc_id]["kind"],
                "device_id": by_id[hit.doc_id].get("device_id"),
                "label": hit.label,
                "context": hit.context,
                "count": hit.count,
            }
            for hit in doc_backlinks.backlinks_for(document_id, rows, devices)
            if hit.doc_id in by_id
        ]

    # ── writes ──────────────────────────────────────────────────────────────

    def _new_doc(
        self,
        docs: dict[str, dict[str, Any]],
        *,
        kind: str,
        title: str,
        parent_id: str | None = None,
        icon: str | None = None,
        device_id: str | None = None,
        node_id: str | None = None,
        design_id: str | None = None,
    ) -> dict[str, Any]:
        now = _iso(_now())
        doc_id = uuid.uuid4().hex
        return {
            "id": doc_id,
            "kind": kind,
            "title": title,
            "slug": unique_slug(docs, title, parent_id=parent_id),
            "icon": icon,
            "parent_id": parent_id,
            "sort_order": 0,
            "device_id": device_id,
            "node_id": node_id,
            "design_id": design_id,
            "body": "",
            "frontmatter": {},
            "tags": [],
            "starred": False,
            "is_homepage": False,
            "template_id": None,
            "facts_snapshot": None,
            "facts_synced_at": None,
            "reviewed_at": None,
            "baseline_body": None,
            "edited_at": None,
            "created_at": now,
            "updated_at": now,
        }

    async def create(self, payload: dict[str, Any]) -> dict[str, Any]:
        kind = payload.get("kind") or "page"
        if kind not in DOCUMENT_KINDS:
            raise DocumentError("invalid", f"kind must be one of {sorted(DOCUMENT_KINDS)}")
        template_id = payload.get("template_id")
        if template_id is not None and template_id not in TEMPLATE_IDS:
            raise DocumentError("invalid", f"template_id must be one of {sorted(TEMPLATE_IDS)}")
        title = str(payload.get("title") or "").strip()
        if not title:
            raise DocumentError("invalid", "A document needs a title")

        device_id = payload.get("device_id") or None
        node_id = payload.get("node_id") or None
        design_id = payload.get("design_id") or None
        parent_id = payload.get("parent_id") or None
        links = [device_id, node_id, design_id]
        if sum(1 for link in links if link) > 1:
            raise DocumentError("invalid", "A document describes at most one device, node or design")
        if kind in TREE_KINDS and any(links):
            raise DocumentError("invalid", "A page or folder cannot also describe a device, node or design")
        if kind not in TREE_KINDS and not any(links):
            raise DocumentError("invalid", f"A '{kind}' document must name what it describes")
        if parent_id and kind not in TREE_KINDS:
            raise DocumentError("invalid", "Only pages and folders live in the Library tree")

        if device_id and device_id not in await self._devices():
            raise DocumentError("not_found", "Device not found")
        if node_id and not await self._node_exists(node_id):
            raise DocumentError("not_found", "Node not found")
        if design_id and not any(d["id"] == design_id for d in await self._designs()):
            raise DocumentError("not_found", "Design not found")

        docs = await self._docs()
        if parent_id:
            parent = docs.get(parent_id)
            if parent is None:
                raise DocumentError("not_found", "Parent folder not found")
            if parent["kind"] != "folder":
                raise DocumentError("invalid", "A document can only be filed under a folder")
        for field, value in (("device_id", device_id), ("node_id", node_id), ("design_id", design_id)):
            if value and any(d.get(field) == value for d in docs.values()):
                raise DocumentError("conflict", "That already has a document")

        doc = self._new_doc(
            docs,
            kind=kind,
            title=title,
            parent_id=parent_id,
            icon=payload.get("icon"),
            device_id=device_id,
            node_id=node_id,
            design_id=design_id,
        )
        if payload.get("body") is not None:
            self._apply_body(doc, str(payload["body"]))
            doc["template_id"] = template_id
        else:
            await self._scaffold_body(doc, template_id)
        docs[doc["id"]] = doc
        await self._save()
        return await self._response(doc)

    async def update(self, document_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Apply the fields present in ``payload``; absent fields are untouched."""
        docs = await self._docs()
        doc = await self._get(document_id)
        reason = payload.get("revision_reason")
        if reason is not None and reason not in CLIENT_REVISION_REASONS:
            raise DocumentError(
                "invalid", f"revision_reason must be one of {sorted(CLIENT_REVISION_REASONS)}"
            )

        if "parent_id" in payload:
            parent_id = payload["parent_id"] or None
            if doc["kind"] not in TREE_KINDS:
                raise DocumentError("invalid", "Only pages and folders live in the Library tree")
            if parent_id:
                parent = docs.get(parent_id)
                if parent is None:
                    raise DocumentError("not_found", "Parent folder not found")
                if parent["kind"] != "folder":
                    raise DocumentError("invalid", "A document can only be filed under a folder")
                if is_ancestor(docs, document_id, parent_id):
                    raise DocumentError("invalid", "A folder cannot be moved inside itself")
            doc["parent_id"] = parent_id
            doc["slug"] = unique_slug(docs, doc["title"], parent_id=parent_id, exclude_id=doc["id"])

        if payload.get("is_homepage") and doc["kind"] != "page":
            raise DocumentError("invalid", "Only a page can be the homepage")

        body = payload.get("body")
        if body is not None and body != doc.get("body"):
            self._record_revision(doc, reason or "edit")
            self._apply_body(doc, body)
            doc["edited_at"] = _iso(_now())
            # An explicit title in the same request still wins: it is applied below.
            self._adopt_frontmatter_title(docs, doc)

        if payload.get("title"):
            doc["title"] = str(payload["title"]).strip() or doc["title"]
            doc["slug"] = unique_slug(docs, doc["title"], parent_id=doc.get("parent_id"), exclude_id=doc["id"])
        if "icon" in payload:
            doc["icon"] = payload["icon"] or None
        if payload.get("sort_order") is not None:
            doc["sort_order"] = int(payload["sort_order"])
        if payload.get("starred") is not None:
            doc["starred"] = bool(payload["starred"])
        if payload.get("is_homepage") is not None:
            if payload["is_homepage"]:
                for other in docs.values():
                    other["is_homepage"] = False
            doc["is_homepage"] = bool(payload["is_homepage"])
        if payload.get("reviewed"):
            doc["reviewed_at"] = _iso(_now())
        if payload.get("resync_facts") and doc.get("device_id"):
            device = (await self._devices()).get(doc["device_id"])
            if device is not None:
                # Accepting the current facts also moves the merge baseline onto
                # the body those facts *would* generate, so the next device
                # change is still compared from a truthful common ancestor.
                _, generated = await self._generated_body(device)
                doc["baseline_body"] = generated
                doc["facts_snapshot"] = facts_snapshot(as_row(device))
                doc["facts_synced_at"] = _iso(_now())

        doc["updated_at"] = _iso(_now())
        await self._save()
        return await self._response(doc)

    async def restore(self, document_id: str, revision_id: str) -> dict[str, Any]:
        docs = await self._docs()
        doc = await self._get(document_id)
        history = (await self._load())["revisions"].get(document_id) or []
        revision = next((r for r in history if r["id"] == revision_id), None)
        if revision is None:
            raise DocumentError("not_found", "Revision not found")
        # The body being replaced becomes history too, so a restore is undoable.
        self._record_revision(doc, "restore")
        self._apply_body(doc, revision.get("body") or "")
        self._adopt_frontmatter_title(docs, doc)
        doc["updated_at"] = _iso(_now())
        await self._save()
        return await self._response(doc)

    async def regenerate(self, document_id: str) -> dict[str, Any]:
        """Throw the body away and scaffold it again. Undoable from history."""
        doc = await self._get(document_id)
        if doc["kind"] == "folder":
            raise DocumentError("invalid", "A folder has no body to regenerate")
        if doc.get("device_id") and doc["device_id"] not in await self._devices():
            raise DocumentError("not_found", "Device not found")
        self._record_revision(doc, "regenerate")
        await self._scaffold_body(doc, doc.get("template_id"))
        doc["edited_at"] = None
        doc["updated_at"] = _iso(_now())
        await self._save()
        return await self._response(doc)

    async def update_preview(
        self, document_id: str, resolutions: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Preview an update-from-device without changing anything."""
        doc = await self._get(document_id)
        device = await self._device_for_update(doc)
        context, generated = await self._generated_body(device)
        result = self._reconcile(doc, resolutions, generated=generated)
        return {
            "preview_id": self._preview_id(doc, self._live_binding(device, context, generated)),
            "changes": [
                {
                    "id": c.id,
                    "name": c.name,
                    "kind": c.kind,
                    "status": c.status,
                    "documented": c.documented,
                    "device": c.device,
                    "previous": c.previous,
                    "resolution": c.resolution,
                    "custom": c.custom,
                }
                for c in result.changes
            ],
            "proposed_body": result.proposed_body,
            "summary": list(result.summary),
            "unresolved": [c.id for c in result.unresolved],
        }

    async def update_from_device(
        self, document_id: str, preview: str, resolutions: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Apply a preview the user has reviewed.

        The merge is recomputed from the current state with the given
        resolutions. The echoed preview id guards the flow: if the document or
        the device changed while the preview was open, it no longer matches and
        nothing is saved. Every conflict must be resolved first.
        """
        docs = await self._docs()
        doc = await self._get(document_id)
        device = await self._device_for_update(doc)
        context, generated = await self._generated_body(device)
        if preview != self._preview_id(doc, self._live_binding(device, context, generated)):
            raise DocumentError(
                "conflict",
                "The document or device changed while the preview was open — review it again",
            )
        result = self._reconcile(doc, resolutions, generated=generated)
        if result.unresolved:
            raise DocumentError(
                "invalid",
                f"Resolve every conflict first: {', '.join(c.name for c in result.unresolved)}",
            )
        if resolutions or result.proposed_body != (doc.get("body") or ""):
            self._record_revision(doc, "sync")
            self._apply_body(doc, result.proposed_body)
        doc["baseline_body"] = generated
        doc["facts_snapshot"] = facts_snapshot(as_row(device))
        doc["facts_synced_at"] = _iso(_now())
        self._adopt_frontmatter_title(docs, doc)
        doc["updated_at"] = _iso(_now())
        await self._save()
        return await self._response(doc)

    async def scaffold(
        self,
        device_ids: list[str] | None = None,
        *,
        only_with_notes: bool = False,
    ) -> dict[str, Any]:
        """Create the device documents that do not exist yet.

        This is the migration off the inventory ``notes`` field: the old text is
        appended verbatim under ``## Notes`` and the field is left untouched, so
        nothing is lost and the move is repeatable.
        """
        docs = await self._docs()
        devices = [d for d in (await self._devices()).values() if d.get("status") != "hidden"]
        if device_ids:
            wanted = set(device_ids)
            devices = [d for d in devices if d["id"] in wanted]
        devices.sort(key=lambda d: str(d.get("discovered_at") or ""))
        existing = {d["device_id"] for d in docs.values() if d.get("device_id")}

        todo: list[dict[str, Any]] = []
        skipped = 0
        for device in devices:
            if device["id"] in existing or (only_with_notes and not (device.get("notes") or "").strip()):
                skipped += 1
                continue
            todo.append(device)

        contexts = await self._device_contexts(d["id"] for d in todo)
        created: list[dict[str, Any]] = []
        now = _iso(_now())
        for device in todo:
            generated = render_device_document(as_row(device), **contexts[device["id"]])
            doc = self._new_doc(docs, kind="device", title=_device_name(device), device_id=device["id"])
            self._apply_body(doc, generated)
            doc.update(
                template_id=TEMPLATE_DEVICE,
                baseline_body=generated,
                facts_snapshot=facts_snapshot(as_row(device)),
                facts_synced_at=now,
            )
            docs[doc["id"]] = doc
            self._record_revision(doc, "migrate" if (device.get("notes") or "").strip() else "scaffold")
            created.append(doc)

        if created:
            await self._save()
        return {"created": [self._summary(d, False) for d in created], "skipped": skipped}

    async def delete(self, document_id: str) -> None:
        """Delete a document; a folder takes its whole subtree with it."""
        docs = await self._docs()
        await self._get(document_id)
        revisions = (await self._load())["revisions"]
        for doc_id in reversed(subtree_ids(docs, document_id)):
            docs.pop(doc_id, None)
            revisions.pop(doc_id, None)
        await self._save()

    # ── lifecycle hooks from the coordinator ────────────────────────────────

    async def unlink(
        self,
        *,
        device_ids: Iterable[str] = (),
        node_ids: Iterable[str] = (),
        design_ids: Iterable[str] = (),
    ) -> int:
        """Orphan every document naming one of the given rows. Returns the count.

        Clearing rather than deleting is deliberate: what the user wrote outlives
        the inventory and the canvas. The document keeps its title and can be
        re-linked or filed into the Library.
        """
        targets = {
            "device_id": set(device_ids),
            "node_id": set(node_ids),
            "design_id": set(design_ids),
        }
        if not any(targets.values()):
            return 0
        changed = 0
        for doc in (await self._docs()).values():
            for field, ids in targets.items():
                if doc.get(field) and doc[field] in ids:
                    doc[field] = None
                    changed += 1
        if changed:
            await self._save()
        return changed

    async def migrate_notes(self) -> int:
        """Turn every device's old ``notes`` into a device document, once.

        Runs on the first setup after the upgrade that introduced documents.
        The flag is stored with the documents, so a user who later deletes a
        migrated document does not get it back on the next restart.
        """
        data = await self._load()
        if data.get("notes_migrated"):
            return 0
        result = await self.scaffold(only_with_notes=True)
        data["notes_migrated"] = True
        await self._save()
        created = len(result["created"])
        if created:
            _LOGGER.info("Moved the notes of %d device(s) into device documents", created)
        return created
