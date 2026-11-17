"""The documentation space: DocumentManager, its coordinator hooks and WS commands.

Port of homelable #418, #421, #437, #458, #489, #509 and #583, on a ``Store``
instead of SQLite.
"""
from __future__ import annotations

import base64
import io
import zipfile
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.homelable.const import (
    DOMAIN,
    STORAGE_KEY_CANVAS,
    STORAGE_KEY_DESIGNS,
    STORAGE_KEY_DOCUMENTS,
    STORAGE_KEY_PENDING,
)
from custom_components.homelable.coordinator import HomelableCoordinator
from custom_components.homelable.doc_tree import REVISION_LIMIT
from custom_components.homelable.documents import DocumentError
from custom_components.homelable.websocket import async_register_websocket_commands


def _mock_entry() -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "docs_entry"
    entry.data = {"scan_ranges": "192.168.0.0/23", "status_interval": 60}
    entry.options = {}
    return entry


NAS = {
    "id": "dev-nas",
    "status": "approved",
    "hostname": "nas",
    "ip": "192.168.1.20",
    "suggested_type": "nas",
    "notes": "Spare disk in the drawer",
    "discovered_at": "2026-09-01T10:00:00Z",
    "discovery_source": "scan",
    "services": [{"port": 5000, "protocol": "tcp", "service_name": "dsm"}],
}
ROUTER = {
    "id": "dev-router",
    "status": "approved",
    "hostname": "router",
    "ip": "192.168.1.1",
    "suggested_type": "router",
    "discovered_at": "2026-08-01T10:00:00Z",
}
PENDING = {"id": "dev-pending", "status": "pending", "ip": "192.168.1.99", "notes": "maybe a printer"}
HIDDEN = {"id": "dev-hidden", "status": "hidden", "ip": "192.168.1.98", "notes": "ignore me"}


def _seed(hass_storage) -> None:  # noqa: ANN001
    hass_storage[STORAGE_KEY_DESIGNS] = {
        "version": 1,
        "key": STORAGE_KEY_DESIGNS,
        "data": {
            "designs": [
                {"id": "net", "name": "Home network", "design_type": "network", "icon": "network"},
                {"id": "lab", "name": "Lab", "design_type": "network", "icon": "network"},
            ]
        },
    }
    hass_storage[STORAGE_KEY_CANVAS] = {
        "version": 1,
        "key": STORAGE_KEY_CANVAS,
        "data": {
            "canvases": {
                "net": {
                    "nodes": [
                        {"id": "zone", "type": "groupRect", "label": "Attic"},
                        {"id": "n-nas", "type": "nas", "label": "NAS", "device_id": "dev-nas", "parent_id": "zone"},
                        {"id": "n-router", "type": "router", "label": "Router", "device_id": "dev-router"},
                    ],
                    "edges": [{"id": "e1", "source": "n-router", "target": "n-nas"}],
                    "viewport": {},
                },
                "lab": {
                    "nodes": [{"id": "lab-zone", "type": "group", "label": "Bench"}],
                    "edges": [],
                    "viewport": {},
                },
            }
        },
    }
    hass_storage[STORAGE_KEY_PENDING] = {
        "version": 1,
        "key": STORAGE_KEY_PENDING,
        "data": {"devices": [dict(NAS), dict(ROUTER), dict(PENDING), dict(HIDDEN)]},
    }


@pytest.fixture
async def coord(hass: HomeAssistant, hass_storage) -> HomelableCoordinator:  # noqa: ANN001
    _seed(hass_storage)
    coordinator = HomelableCoordinator(hass, _mock_entry())
    await coordinator._ensure_loaded()
    return coordinator


@pytest.fixture
async def setup_ws(hass: HomeAssistant, coord: HomelableCoordinator) -> HomelableCoordinator:
    hass.data.setdefault(DOMAIN, {})[coord.entry.entry_id] = coord
    async_register_websocket_commands(hass)
    return coord


async def _device_doc(coord: HomelableCoordinator, device_id: str = "dev-nas") -> dict:
    return await coord.documents.create({"kind": "device", "title": "NAS", "device_id": device_id})


async def _set_device(coord: HomelableCoordinator, device_id: str, **fields) -> None:
    pending = await coord._get_pending()
    row = next(d for d in pending["devices"] if d["id"] == device_id)
    row.update(fields)


# ── create ──────────────────────────────────────────────────────────────────


async def test_a_page_is_scaffolded_from_its_template(coord) -> None:  # noqa: ANN001
    doc = await coord.documents.create({"title": "Restore the NAS", "template_id": "runbook"})
    assert doc["kind"] == "page"
    assert doc["slug"] == "restore-the-nas"
    assert doc["template_id"] == "runbook"
    assert "## Rollback" in doc["body"]
    assert doc["frontmatter"]["template"] == "runbook"
    assert doc["tags"] == []
    assert doc["drifted"] is False


async def test_a_body_given_at_creation_is_kept_verbatim(coord) -> None:  # noqa: ANN001
    doc = await coord.documents.create({"title": "Mine", "body": "---\ntags: [a]\n---\nhello"})
    assert doc["body"] == "---\ntags: [a]\n---\nhello"
    assert doc["tags"] == ["a"]


async def test_a_device_document_reads_the_live_facts_and_its_place(coord) -> None:  # noqa: ANN001
    coord._racks = {
        "rack-design": {
            "racks": [{"id": "r1", "name": "Main rack"}],
            "devices": [{"id": "m1", "rack_id": "r1", "device_id": "dev-nas", "u_start": 3, "u_height": 2, "col_span": 12}],
            "cables": [],
        }
    }
    doc = await _device_doc(coord)
    body = doc["body"]
    assert doc["template_id"] == "device"
    assert "| IP | 192.168.1.20 |" in body
    assert "| First discovered | 2026-09-01 (scan) |" in body
    assert "### dsm — `5000/tcp`" in body
    # Zone from the parent group, peer from the edge, subnet from the scan range.
    assert "Zone **Attic**." in body
    assert "`Router`" in body
    assert "192.168.0.0/23" in body
    assert "Rack **Main rack**, U3–U4, full width." in body
    # The old notes come along verbatim.
    assert "Spare disk in the drawer" in body
    assert doc["facts_snapshot"]["ip"] == "192.168.1.20"


async def test_one_document_per_device(coord) -> None:  # noqa: ANN001
    await _device_doc(coord)
    with pytest.raises(DocumentError) as err:
        await _device_doc(coord)
    assert err.value.code == "conflict"


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"title": "x", "kind": "page", "device_id": "dev-nas"}, "invalid"),
        ({"title": "x", "kind": "device"}, "invalid"),
        ({"title": "x", "kind": "device", "device_id": "dev-nas", "design_id": "net"}, "invalid"),
        ({"title": "x", "kind": "device", "device_id": "nope"}, "not_found"),
        # homelable #458: a canvas document for a canvas that does not exist.
        ({"title": "x", "kind": "design", "design_id": "nope"}, "not_found"),
        ({"title": "x", "kind": "node", "node_id": "nope"}, "not_found"),
        ({"title": "x", "parent_id": "nope"}, "not_found"),
        ({"title": "x", "kind": "weird"}, "invalid"),
        ({"title": "x", "template_id": "weird"}, "invalid"),
        ({"title": "  "}, "invalid"),
    ],
)
async def test_create_refuses_a_malformed_link(coord, payload, code) -> None:  # noqa: ANN001
    with pytest.raises(DocumentError) as err:
        await coord.documents.create(payload)
    assert err.value.code == code


async def test_node_and_design_documents_link_to_what_exists(coord) -> None:  # noqa: ANN001
    zone = await coord.documents.create({"title": "Attic", "kind": "node", "node_id": "zone"})
    canvas = await coord.documents.create({"title": "Home", "kind": "design", "design_id": "net"})
    assert zone["node_id"] == "zone"
    assert canvas["design_id"] == "net"


async def test_a_page_can_only_be_filed_under_a_folder(coord) -> None:  # noqa: ANN001
    page = await coord.documents.create({"title": "Page"})
    with pytest.raises(DocumentError):
        await coord.documents.create({"title": "Child", "parent_id": page["id"]})
    folder = await coord.documents.create({"title": "Runbooks", "kind": "folder"})
    child = await coord.documents.create({"title": "Child", "parent_id": folder["id"]})
    assert child["parent_id"] == folder["id"]


# ── update / history ────────────────────────────────────────────────────────


async def test_editing_the_body_records_history_and_follows_its_title(coord) -> None:  # noqa: ANN001
    doc = await coord.documents.create({"title": "Draft", "body": "one"})
    updated = await coord.documents.update(doc["id"], {"body": "---\ntitle: Final name\n---\ntwo"})
    assert updated["title"] == "Final name"
    assert updated["slug"] == "final-name"
    assert updated["edited_at"] is not None
    history = await coord.documents.revisions(doc["id"])
    assert [r["reason"] for r in history] == ["edit"]
    revision = await coord.documents.revision(history[0]["id"])
    assert revision["body"] == "one"


async def test_an_unchanged_body_records_nothing(coord) -> None:  # noqa: ANN001
    doc = await coord.documents.create({"title": "Same", "body": "x"})
    await coord.documents.update(doc["id"], {"body": "x", "starred": True})
    assert await coord.documents.revisions(doc["id"]) == []


async def test_history_is_capped(coord) -> None:  # noqa: ANN001
    doc = await coord.documents.create({"title": "Busy", "body": "0"})
    for i in range(REVISION_LIMIT + 5):
        await coord.documents.update(doc["id"], {"body": str(i + 1)})
    history = await coord.documents.revisions(doc["id"])
    assert len(history) == REVISION_LIMIT
    # Newest first.
    assert (await coord.documents.revision(history[0]["id"]))["body"] == str(REVISION_LIMIT + 4)


async def test_a_client_cannot_claim_an_action_reason(coord) -> None:  # noqa: ANN001
    doc = await coord.documents.create({"title": "x"})
    with pytest.raises(DocumentError):
        await coord.documents.update(doc["id"], {"body": "y", "revision_reason": "regenerate"})


async def test_restore_brings_a_body_back_and_is_itself_undoable(coord) -> None:  # noqa: ANN001
    doc = await coord.documents.create({"title": "R", "body": "first"})
    await coord.documents.update(doc["id"], {"body": "second"})
    first = (await coord.documents.revisions(doc["id"]))[0]
    restored = await coord.documents.restore(doc["id"], first["id"])
    assert restored["body"] == "first"
    reasons = [r["reason"] for r in await coord.documents.revisions(doc["id"])]
    assert reasons == ["restore", "edit"]
    with pytest.raises(DocumentError):
        await coord.documents.restore(doc["id"], "nope")


async def test_regenerate_rescaffolds_and_keeps_the_old_body(coord) -> None:  # noqa: ANN001
    doc = await _device_doc(coord)
    await coord.documents.update(doc["id"], {"body": "my own words"})
    again = await coord.documents.regenerate(doc["id"])
    assert "## Device Information" in again["body"]
    assert again["edited_at"] is None
    assert (await coord.documents.revisions(doc["id"]))[0]["reason"] == "regenerate"


async def test_a_folder_cannot_move_inside_itself(coord) -> None:  # noqa: ANN001
    outer = await coord.documents.create({"title": "Outer", "kind": "folder"})
    inner = await coord.documents.create({"title": "Inner", "kind": "folder", "parent_id": outer["id"]})
    with pytest.raises(DocumentError):
        await coord.documents.update(outer["id"], {"parent_id": inner["id"]})
    moved = await coord.documents.update(inner["id"], {"parent_id": None})
    assert moved["parent_id"] is None


async def test_a_device_document_is_not_filed_in_the_library(coord) -> None:  # noqa: ANN001
    doc = await _device_doc(coord)
    folder = await coord.documents.create({"title": "F", "kind": "folder"})
    with pytest.raises(DocumentError):
        await coord.documents.update(doc["id"], {"parent_id": folder["id"]})


# ── homepage (homelable #583) ───────────────────────────────────────────────


async def test_the_homepage_template_indexes_the_homelab(coord) -> None:  # noqa: ANN001
    await coord.documents.create({"title": "Runbook", "kind": "page"})
    await _device_doc(coord, "dev-router")
    home = await coord.documents.create({"title": "Home", "template_id": "homepage"})
    body = home["body"]
    assert "[Home network](?design=net)" in body
    assert "Runbook" in body
    assert "[[device:dev-router|router]]" in body


async def test_there_is_only_ever_one_homepage(coord) -> None:  # noqa: ANN001
    a = await coord.documents.create({"title": "A"})
    b = await coord.documents.create({"title": "B"})
    await coord.documents.update(a["id"], {"is_homepage": True})
    await coord.documents.update(b["id"], {"is_homepage": True})
    docs = {d["id"]: d for d in await coord.documents.list()}
    assert docs[a["id"]]["is_homepage"] is False
    assert docs[b["id"]]["is_homepage"] is True
    folder = await coord.documents.create({"title": "F", "kind": "folder"})
    with pytest.raises(DocumentError):
        await coord.documents.update(folder["id"], {"is_homepage": True})


# ── drift and update-from-device (homelable #489) ───────────────────────────


async def test_a_moved_device_flags_its_document_until_resynced(coord) -> None:  # noqa: ANN001
    doc = await _device_doc(coord)
    assert (await coord.documents.get(doc["id"]))["drifted"] is False
    await _set_device(coord, "dev-nas", ip="192.168.1.21")
    assert (await coord.documents.get(doc["id"]))["drifted"] is True
    listed = await coord.documents.list(kind="device")
    assert listed[0]["drifted"] is True
    resynced = await coord.documents.update(doc["id"], {"resync_facts": True})
    assert resynced["drifted"] is False
    assert "192.168.1.20" in resynced["body"]  # the body is left alone


async def test_update_from_device_applies_an_untouched_fact(coord) -> None:  # noqa: ANN001
    doc = await _device_doc(coord)
    await _set_device(coord, "dev-nas", ip="192.168.1.21")
    preview = await coord.documents.update_preview(doc["id"], [])
    assert any(c["status"] == "auto" for c in preview["changes"])
    assert preview["unresolved"] == []
    applied = await coord.documents.update_from_device(doc["id"], preview["preview_id"], [])
    assert "| IP | 192.168.1.21 |" in applied["body"]
    assert applied["drifted"] is False
    assert (await coord.documents.revisions(doc["id"]))[0]["reason"] == "sync"


async def test_a_stale_preview_is_refused(coord) -> None:  # noqa: ANN001
    doc = await _device_doc(coord)
    await _set_device(coord, "dev-nas", ip="192.168.1.21")
    preview = await coord.documents.update_preview(doc["id"], [])
    await _set_device(coord, "dev-nas", ip="192.168.1.22")
    with pytest.raises(DocumentError) as err:
        await coord.documents.update_from_device(doc["id"], preview["preview_id"], [])
    assert err.value.code == "conflict"


async def test_a_page_has_no_device_to_update_from(coord) -> None:  # noqa: ANN001
    page = await coord.documents.create({"title": "P"})
    with pytest.raises(DocumentError):
        await coord.documents.update_preview(page["id"], [])


# ── reads ───────────────────────────────────────────────────────────────────


async def test_backlinks_find_every_way_of_linking(coord) -> None:  # noqa: ANN001
    target = await _device_doc(coord)
    await coord.documents.create({"title": "By device", "body": "See [[device:nas|the NAS]] twice [[device:dev-nas]]"})
    await coord.documents.create({"title": "By title", "body": "See [[NAS]]"})
    await coord.documents.create({"title": "Unrelated", "body": "[[Elsewhere]]"})
    hits = {h["title"]: h for h in await coord.documents.backlinks(target["id"])}
    assert set(hits) == {"By device", "By title"}
    assert hits["By device"]["count"] == 2
    assert hits["By device"]["label"] == "the NAS"


async def test_search_finds_body_text(coord) -> None:  # noqa: ANN001
    doc = await coord.documents.create({"title": "Backups", "body": "Restic runs nightly"})
    result = await coord.documents.search("restic")
    assert result["engine"] == "like"
    assert [h["doc_id"] for h in result["hits"]] == [doc["id"]]


async def test_block_renders_one_section(coord) -> None:  # noqa: ANN001
    block = await coord.documents.block("network", "dev-nas")
    assert "192.168.0.0/23" in block["markdown"]
    with pytest.raises(DocumentError):
        await coord.documents.block("nope", "dev-nas")
    with pytest.raises(DocumentError):
        await coord.documents.block("services", "nope")


async def test_export_is_a_zip_mirroring_the_tree(coord) -> None:  # noqa: ANN001
    folder = await coord.documents.create({"title": "Runbooks", "kind": "folder", "body": "index"})
    await coord.documents.create({"title": "Reboot", "parent_id": folder["id"], "body": "# Reboot\n"})
    await _device_doc(coord)
    out = await coord.documents.export()
    assert out["filename"].startswith("homelable-documentation-")
    with zipfile.ZipFile(io.BytesIO(base64.b64decode(out["content"]))) as archive:
        names = set(archive.namelist())
        assert archive.read("runbooks/reboot.md").decode() == "# Reboot\n"
    assert {"runbooks/index.md", "runbooks/reboot.md", "devices/nas.md"} <= names


async def test_list_filters_by_tag_and_kind(coord) -> None:  # noqa: ANN001
    await coord.documents.create({"title": "Tagged", "body": "---\ntags: [ZFS]\n---\n"})
    await coord.documents.create({"title": "Plain"})
    assert [d["title"] for d in await coord.documents.list(tag="zfs")] == ["Tagged"]
    assert await coord.documents.list(kind="device") == []
    # A summary never carries the body.
    assert "body" not in (await coord.documents.list())[0]


async def test_coverage_counts_what_is_written_down(coord) -> None:  # noqa: ANN001
    doc = await _device_doc(coord)
    await coord.documents.create({"title": "Page"})
    cov = await coord.documents.coverage()
    # The hidden row is not counted; the pending one is.
    assert cov["devices"] == 3
    assert cov["documented"] == 1
    assert cov["missing"] == 2
    assert cov["header_only"] == 1
    assert cov["notes_unmigrated"] == 1  # the pending printer
    assert cov["library_pages"] == 1
    await coord.documents.update(doc["id"], {"body": "edited"})
    assert (await coord.documents.coverage())["header_only"] == 0


async def test_delete_takes_a_folder_subtree_and_its_history(coord) -> None:  # noqa: ANN001
    folder = await coord.documents.create({"title": "F", "kind": "folder"})
    child = await coord.documents.create({"title": "C", "parent_id": folder["id"]})
    await coord.documents.update(child["id"], {"body": "x"})
    await coord.documents.delete(folder["id"])
    assert await coord.documents.list() == []
    assert coord.documents._data["revisions"] == {}
    with pytest.raises(DocumentError):
        await coord.documents.delete(folder["id"])


async def test_documents_persist_in_their_own_store(hass, coord, hass_storage) -> None:  # noqa: ANN001
    doc = await coord.documents.create({"title": "Kept"})
    stored = hass_storage[STORAGE_KEY_DOCUMENTS]["data"]
    assert doc["id"] in stored["documents"]
    # A fresh coordinator reads them back.
    again = HomelableCoordinator(hass, _mock_entry())
    assert [d["title"] for d in await again.documents.list()] == ["Kept"]


# ── notes migration ─────────────────────────────────────────────────────────


async def test_migrate_notes_runs_once(coord) -> None:  # noqa: ANN001
    assert await coord.documents.migrate_notes() == 2  # the NAS and the pending row
    docs = await coord.documents.list(kind="device")
    assert {d["device_id"] for d in docs} == {"dev-nas", "dev-pending"}
    history = await coord.documents.revisions(docs[0]["id"])
    assert history[0]["reason"] == "migrate"
    # Deleting a migrated document does not bring it back on the next setup.
    await coord.documents.delete(docs[0]["id"])
    assert await coord.documents.migrate_notes() == 0
    assert len(await coord.documents.list(kind="device")) == 1


async def test_scaffold_skips_what_already_has_a_document(coord) -> None:  # noqa: ANN001
    await _device_doc(coord)
    result = await coord.documents.scaffold()
    assert result["skipped"] == 1
    assert {d["device_id"] for d in result["created"]} == {"dev-router", "dev-pending"}


# ── coordinator hooks ───────────────────────────────────────────────────────


async def test_removing_a_device_orphans_its_document(coord) -> None:  # noqa: ANN001
    await coord.documents.create({"title": "Printer", "kind": "device", "device_id": "dev-pending"})
    assert await coord.remove_pending("dev-pending")
    doc = (await coord.documents.list())[0]
    assert doc["device_id"] is None
    assert doc["title"] == "Printer"


async def test_clearing_pending_keeps_an_approved_devices_document(coord) -> None:  # noqa: ANN001
    # homelable #509: only the rows actually dropped lose their link.
    nas = await _device_doc(coord)
    printer = await coord.documents.create({"title": "Printer", "kind": "device", "device_id": "dev-pending"})
    assert await coord.clear_pending() == 1
    docs = {d["id"]: d for d in await coord.documents.list()}
    assert docs[nas["id"]]["device_id"] == "dev-nas"
    assert docs[printer["id"]]["device_id"] is None


async def test_deleting_a_canvas_orphans_its_documents(coord) -> None:  # noqa: ANN001
    canvas = await coord.documents.create({"title": "Lab", "kind": "design", "design_id": "lab"})
    zone = await coord.documents.create({"title": "Bench", "kind": "node", "node_id": "lab-zone"})
    attic = await coord.documents.create({"title": "Attic", "kind": "node", "node_id": "zone"})
    assert await coord.delete_design("lab") == "ok"
    docs = {d["id"]: d for d in await coord.documents.list()}
    assert docs[canvas["id"]]["design_id"] is None
    assert docs[zone["id"]]["node_id"] is None
    assert docs[attic["id"]]["node_id"] == "zone"


# ── WebSocket ───────────────────────────────────────────────────────────────


async def test_ws_create_get_list_and_delete(hass, hass_ws_client, setup_ws) -> None:  # noqa: ANN001
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "homelable/docs/create", "title": "VLANs", "template_id": "network"})
    created = await client.receive_json()
    assert created["success"], created
    doc_id = created["result"]["id"]

    await client.send_json({"id": 2, "type": "homelable/docs/get", "document_id": doc_id})
    got = await client.receive_json()
    assert got["result"]["body"].startswith("---")

    await client.send_json({"id": 3, "type": "homelable/docs/list"})
    listed = await client.receive_json()
    assert [d["id"] for d in listed["result"]] == [doc_id]

    await client.send_json(
        {"id": 4, "type": "homelable/docs/update", "document_id": doc_id, "body": "new", "starred": True}
    )
    updated = await client.receive_json()
    assert updated["result"]["starred"] is True

    await client.send_json({"id": 5, "type": "homelable/docs/delete", "document_id": doc_id})
    assert (await client.receive_json())["result"] == {"deleted": True}


async def test_ws_maps_refusals_to_error_codes(hass, hass_ws_client, setup_ws) -> None:  # noqa: ANN001
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "homelable/docs/get", "document_id": "nope"})
    msg = await client.receive_json()
    assert msg["success"] is False
    assert msg["error"]["code"] == "not_found"

    await client.send_json({"id": 2, "type": "homelable/docs/create", "title": "x", "kind": "device"})
    msg = await client.receive_json()
    assert msg["error"]["code"] == "invalid"


async def test_ws_reads_and_merges(hass, hass_ws_client, setup_ws) -> None:  # noqa: ANN001
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "homelable/docs/scaffold", "device_ids": ["dev-nas"]})
    scaffolded = await client.receive_json()
    doc_id = scaffolded["result"]["created"][0]["id"]

    for i, extra in enumerate(
        (
            {"type": "homelable/docs/coverage"},
            {"type": "homelable/docs/search", "q": "dsm"},
            {"type": "homelable/docs/block", "block": "services", "device_id": "dev-nas"},
            {"type": "homelable/docs/export"},
            {"type": "homelable/docs/revisions", "document_id": doc_id},
            {"type": "homelable/docs/backlinks", "document_id": doc_id},
            {"type": "homelable/docs/regenerate", "document_id": doc_id},
        ),
        start=2,
    ):
        await client.send_json({"id": i, **extra})
        msg = await client.receive_json()
        assert msg["success"], (extra, msg)

    await _set_device(setup_ws, "dev-nas", ip="192.168.1.30")
    await client.send_json({"id": 20, "type": "homelable/docs/update_preview", "document_id": doc_id})
    preview = (await client.receive_json())["result"]
    await client.send_json(
        {
            "id": 21,
            "type": "homelable/docs/update_from_device",
            "document_id": doc_id,
            "preview_id": preview["preview_id"],
        }
    )
    applied = await client.receive_json()
    assert applied["success"], applied
    assert "192.168.1.30" in applied["result"]["body"]


async def test_ws_writes_need_an_admin(hass, hass_ws_client, hass_read_only_access_token, setup_ws) -> None:  # noqa: ANN001
    client = await hass_ws_client(hass, hass_read_only_access_token)
    await client.send_json({"id": 1, "type": "homelable/docs/create", "title": "x"})
    msg = await client.receive_json()
    assert msg["success"] is False
    assert msg["error"]["code"] == "unauthorized"
    # Reads stay open.
    await client.send_json({"id": 2, "type": "homelable/docs/list"})
    assert (await client.receive_json())["success"] is True


# ── HACS: facts kept under data_extras ─────────────────────────────────────


async def test_a_device_document_reads_the_facts_kept_in_data_extras(coord) -> None:  # noqa: ANN001
    # Proxmox / mesh imports store the name, vendor and model under data_extras;
    # the panel shows them flattened, and the document must name the device the
    # same way (found in the browser: the tree said `proxmox-1`, the body an IP).
    pending = await coord._get_pending()
    pending["devices"].append(
        {
            "id": "dev-pve",
            "status": "pending",
            "hostname": "192.168.1.134",
            "ip": "192.168.1.134",
            "suggested_type": "proxmox",
            "data_extras": {"friendly_name": "proxmox-1", "vendor": "Proxmox", "model": "VE 8"},
        }
    )
    doc = await coord.documents.create({"kind": "device", "title": "proxmox-1", "device_id": "dev-pve"})
    assert doc["frontmatter"]["title"] == "proxmox-1"
    assert "# proxmox-1" in doc["body"]
    assert "| Vendor / Model | Proxmox / VE 8 |" in doc["body"]
    assert doc["facts_snapshot"]["label"] == "proxmox-1"
    # The stored row is left in its own shape.
    assert "friendly_name" not in pending["devices"][-1]
    # And it is not seen as drifted straight away.
    assert (await coord.documents.get(doc["id"]))["drifted"] is False
