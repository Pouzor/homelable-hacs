"""Device Inventory as the source of truth — the pure matching / merge rules.

Port of homelable #339 (``inventory_sync``) with the backfill hardening of
#352 / #354. Everything under test here is plain dicts; the coordinator wiring
is covered in ``test_inventory.py``.
"""
from __future__ import annotations

from unittest.mock import patch

from custom_components.homelable import inventory_sync as sync

NOW = "2026-09-26T12:00:00Z"


def _row(**kw) -> dict:
    return {"id": kw.pop("id", "pd-1"), "status": "approved", **kw}


# ─── Matching ────────────────────────────────────────────────────────────────


def test_find_device_prefers_ieee_then_ip_then_mac() -> None:
    by_mac = _row(id="mac", mac="aa:bb:cc:dd:ee:ff")
    by_ip = _row(id="ip", ip="10.0.0.5")
    by_ieee = _row(id="ieee", data_extras={"ieee_address": "0xABC"})
    rows = [by_mac, by_ip, by_ieee]
    assert sync.find_device_for(rows, ip="10.0.0.5", mac="AA:BB:CC:DD:EE:FF", ieee="0xabc") is by_ieee
    assert sync.find_device_for(rows, ip="10.0.0.5", mac="AA-BB-CC-DD-EE-FF", ieee=None) is by_ip
    assert sync.find_device_for(rows, ip=None, mac="AA-BB-CC-DD-EE-FF", ieee=None) is by_mac


def test_find_device_matches_whole_ip_tokens_only() -> None:
    rows = [_row(ip="fe80::1, 10.0.0.40")]
    assert sync.find_device_for(rows, ip="10.0.0.4", mac=None, ieee=None) is None
    assert sync.find_device_for(rows, ip="10.0.0.40", mac=None, ieee=None) is rows[0]


def test_find_device_includes_hidden_rows() -> None:
    """A hidden device is still that device — no second row for it."""
    hidden = _row(ip="10.0.0.5", status="hidden")
    assert sync.find_device_for([hidden], ip="10.0.0.5", mac=None, ieee=None) is hidden


# ─── Merging ─────────────────────────────────────────────────────────────────


def test_merge_properties_unions_on_key_and_keeps_the_users_spelling() -> None:
    base = [{"key": "Rack", "value": "A", "visible": True}]
    incoming = [
        {"key": "rack", "value": "B", "visible": False},
        {"key": "CPU", "value": "4"},
    ]
    out = sync.merge_properties(base, incoming)
    assert out == [
        {"key": "Rack", "value": "B", "visible": False},
        {"key": "CPU", "value": "4"},
    ]


def test_discovered_services_refresh_a_renamed_entry_instead_of_duplicating() -> None:
    """A rescan knows the port, not the name a user gave it."""
    base = [{"port": 3000, "protocol": "tcp", "service_name": "Grafana", "icon": "grafana"}]
    scanned = [
        {"port": 3000, "protocol": "tcp", "service_name": "http", "icon": None, "banner": "x"},
        {"port": 22, "protocol": "tcp", "service_name": "ssh"},
    ]
    out = sync.merge_services(base, scanned, discovered=True)
    assert out == [
        {"port": 3000, "protocol": "tcp", "service_name": "Grafana", "icon": "grafana", "banner": "x"},
        {"port": 22, "protocol": "tcp", "service_name": "ssh"},
    ]


def test_a_blank_icon_on_a_user_edit_does_not_clear_an_established_one() -> None:
    """homelable #363: an absent field is silence, not a reset."""
    base = [{"port": 80, "protocol": "tcp", "service_name": "web", "icon": "nginx", "category": "web"}]
    edit = [{"port": 80, "protocol": "tcp", "service_name": "web", "icon": None, "path": "/admin"}]
    out = sync.merge_services(base, edit)
    assert out == [
        {"port": 80, "protocol": "tcp", "service_name": "web", "icon": "nginx",
         "category": "web", "path": "/admin"},
    ]


def test_a_user_edit_still_changes_the_icon() -> None:
    base = [{"port": 80, "protocol": "tcp", "service_name": "web", "icon": "nginx"}]
    out = sync.merge_services(base, [{"port": 80, "protocol": "tcp", "service_name": "web", "icon": "caddy"}])
    assert out[0]["icon"] == "caddy"


def test_changed_facts_ignores_blanks_and_equal_values() -> None:
    row = _row(ip="10.0.0.5", hostname="nas", services=[{"port": 22}])
    facts = {"ip": "10.0.0.5", "hostname": "", "os": "DSM", "services": [{"port": 22}]}
    assert sync.changed_facts(row, facts) == {"os": "DSM"}


def test_ieee_owned_by_another_row_is_never_adopted() -> None:
    """homelable #352: two rows claiming one radio broke the backfill."""
    owner = _row(id="a", data_extras={"ieee_address": "0x00124B0001"})
    other = _row(id="b", ip="10.0.0.5")
    sync.merge_facts_into_device(
        other,
        {"ieee_address": "0x00124b0001"},
        devices=[owner, other],
        overwrite_scalars=True,
        replace_lists=False,
    )
    assert sync.device_ieee(other) is None


def test_link_facts_mints_a_canvas_row_when_nothing_matches() -> None:
    devices: list[dict] = []
    node = {"id": "n1", "type": "server", "label": "Box"}
    row, changed = sync.link_facts(
        devices, node, {"label": "Box", "type": "server", "ip": "10.0.0.9"}, now=NOW
    )
    assert changed is True
    assert devices == [row]
    assert node["device_id"] == row["id"]
    assert row["status"] == "approved"
    assert row["discovery_sources"] == ["canvas"]
    assert row["ip"] == "10.0.0.9"


def test_link_facts_approves_a_pending_row_a_node_draws() -> None:
    """A row a node draws is past the pending queue (homelable #388)."""
    devices = [_row(ip="10.0.0.5", status="pending")]
    node = {"id": "n1", "type": "server"}
    row, _ = sync.link_facts(devices, node, {"type": "server", "ip": "10.0.0.5"}, now=NOW)
    assert row is devices[0]
    assert row["status"] == "approved"


def test_link_facts_leaves_a_hidden_row_hidden() -> None:
    """The user hid it on purpose — drawing it does not undo that."""
    devices = [_row(ip="10.0.0.5", status="hidden")]
    node = {"id": "n1", "type": "server"}
    row, _ = sync.link_facts(devices, node, {"type": "server", "ip": "10.0.0.5"}, now=NOW)
    assert row is devices[0]
    assert row["status"] == "hidden"


def test_attach_device_ids_stamps_copies_of_matched_nodes() -> None:
    devices = [_row(id="by-ieee", data_extras={"ieee_address": "pve-a-100"})]
    nodes = [{"ieee_address": "pve-a-100"}, {"ieee_address": "pve-a-999"}]
    out = sync.attach_device_ids(devices, nodes)
    assert out[0]["device_id"] == "by-ieee"
    assert "device_id" not in out[1]
    assert "device_id" not in nodes[0]  # the caller's payload is left untouched


def test_link_facts_leaves_furniture_unlinked() -> None:
    devices: list[dict] = []
    node = {"id": "g", "type": "groupRect", "device_id": "stale"}
    assert sync.link_facts(devices, node, {"type": "groupRect"}, now=NOW) == (None, False)
    assert "device_id" not in node
    assert devices == []


def test_a_stale_snapshot_does_not_revert_an_edit_made_elsewhere() -> None:
    """The canvas loaded ip .5, the inventory then changed it to .6; this canvas
    only moved its node. Its save names no changed fact, so .6 survives."""
    row = _row(ip="10.0.0.6", hostname="nas")
    node = {"id": "n1", "type": "nas", "device_id": "pd-1"}
    _, changed = sync.link_facts(
        [row],
        node,
        {"ip": "10.0.0.5", "hostname": "nas", "label": "nas", "type": "nas"},
        now=NOW,
        overwrite_scalars=True,
        replace_lists=True,
        only_changed=True,
        changed_fields=[],
    )
    assert changed is False
    assert row["ip"] == "10.0.0.6"


def test_a_listed_edit_is_written_and_a_deleted_property_stays_deleted() -> None:
    row = _row(ip="10.0.0.5", properties=[{"key": "A"}, {"key": "B"}])
    node = {"id": "n1", "type": "nas", "device_id": "pd-1"}
    sync.link_facts(
        [row],
        node,
        {"ip": "10.0.0.7", "properties": [{"key": "A"}], "type": "nas"},
        now=NOW,
        overwrite_scalars=True,
        replace_lists=True,
        only_changed=True,
        changed_fields=["ip", "properties"],
    )
    assert row["ip"] == "10.0.0.7"
    assert row["properties"] == [{"key": "A"}]
    assert row["updated_at"] == NOW


# ─── Hydrate / strip ─────────────────────────────────────────────────────────


def test_hydrate_reads_facts_off_the_row() -> None:
    node = {"id": "n1", "type": "server", "label": "old", "device_id": "pd-1", "pos_x": 3}
    row = _row(
        label="Box", ip="10.0.0.5", services=[{"port": 22}], status_live="online",
        data_extras={"ieee_address": "pve-node-1"},
    )
    out = sync.hydrate_node(node, row)
    assert out["label"] == "Box"
    assert out["ip"] == "10.0.0.5"
    assert out["services"] == [{"port": 22}]
    assert out["status"] == "online"
    assert out["ieee_address"] == "pve-node-1"
    assert out["pos_x"] == 3
    # The stored node is untouched.
    assert "ip" not in node


def test_hydrate_returns_an_unlinked_node_as_stored() -> None:
    node = {"id": "n1", "ip": "10.0.0.5"}
    assert sync.hydrate_node(node, None) == node


def test_strip_removes_facts_at_top_level_and_under_legacy_data() -> None:
    node = {"id": "n1", "ip": "x", "pos_x": 1, "data": {"services": [], "label": "L"}}
    sync.strip_facts(node)
    assert node == {"id": "n1", "pos_x": 1, "data": {"label": "L"}}


# ─── Backfill ────────────────────────────────────────────────────────────────


def _canvases(**designs: list[dict]) -> dict:
    return {did: {"nodes": nodes, "edges": []} for did, nodes in designs.items()}


def test_backfill_converges_two_canvases_on_one_row() -> None:
    """Most recently edited scalar wins; lists union — nothing is lost."""
    older = {
        "id": "a", "type": "nas", "label": "NAS", "ip": "10.0.0.5", "os": "DSM 6",
        "properties": [{"key": "Rack", "value": "1"}], "updated_at": "2026-01-01T00:00:00Z",
    }
    newer = {
        "id": "b", "type": "nas", "label": "NAS", "ip": "10.0.0.5", "os": "DSM 7",
        "properties": [{"key": "CPU", "value": "4"}], "updated_at": "2026-02-01T00:00:00Z",
    }
    canvases = _canvases(d1=[newer], d2=[older])
    devices: list[dict] = []
    stats = sync.backfill_node_devices(canvases, devices, now=NOW)

    assert stats == {"linked": 2, "created": 1, "merged": 1, "filled": 0, "skipped": 0}
    assert len(devices) == 1
    row = devices[0]
    assert row["os"] == "DSM 7"
    assert [p["key"] for p in row["properties"]] == ["Rack", "CPU"]
    # Each canvas keeps showing only what it drew: the union is on the row.
    assert newer == {
        "id": "b", "type": "nas", "label": "NAS",
        "updated_at": "2026-02-01T00:00:00Z", "device_id": row["id"],
        "display_view": {"services": [], "properties": [{"key": "cpu", "visible": True}]},
    }
    assert older["device_id"] == row["id"]
    assert older["display_view"]["properties"] == [{"key": "rack", "visible": True}]


def test_backfill_links_to_an_existing_scanned_row() -> None:
    scanned = _row(id="pd-scan", ip="10.0.0.5", status="pending", hostname="nas")
    node = {"id": "a", "type": "nas", "label": "Synology", "ip": "10.0.0.5", "notes": "attic"}
    devices = [scanned]
    sync.backfill_node_devices(_canvases(d1=[node]), devices, now=NOW)
    assert devices == [scanned]
    assert node["device_id"] == "pd-scan"
    assert scanned["notes"] == "attic"
    assert scanned["label"] == "Synology"


def test_backfill_leaves_furniture_alone() -> None:
    group = {"id": "g", "type": "groupRect", "label": "Zone", "status": "unknown"}
    devices: list[dict] = []
    stats = sync.backfill_node_devices(_canvases(d1=[group]), devices, now=NOW)
    assert stats["linked"] == 0
    assert devices == []
    assert group == {"id": "g", "type": "groupRect", "label": "Zone", "status": "unknown"}


def test_backfill_is_a_no_op_the_second_time() -> None:
    node = {"id": "a", "type": "server", "ip": "10.0.0.5"}
    canvases = _canvases(d1=[node])
    devices: list[dict] = []
    sync.backfill_node_devices(canvases, devices, now=NOW)
    snapshot = (dict(node), [dict(d) for d in devices])
    stats = sync.backfill_node_devices(canvases, devices, now=NOW)
    assert stats == {"linked": 0, "created": 0, "merged": 0, "filled": 0, "skipped": 0}
    assert (node, devices) == snapshot


def test_one_failing_node_costs_only_itself() -> None:
    """homelable #354: catching two chosen exception classes was the defect —
    any failure on one node must leave the others linked and itself intact."""
    good = {"id": "good", "type": "server", "ip": "10.0.0.5"}
    bad = {"id": "bad", "type": "server", "ip": "10.0.0.6", "notes": "keep me"}
    real = sync.link_facts

    def _link(devices, node, facts, **kw):  # noqa: ANN001, ANN003
        if node["id"] == "bad":
            raise TypeError("boom")
        return real(devices, node, facts, **kw)

    devices: list[dict] = []
    with patch.object(sync, "link_facts", _link):
        stats = sync.backfill_node_devices(_canvases(d1=[bad, good]), devices, now=NOW)

    assert stats["skipped"] == 1
    assert stats["linked"] == 1
    assert good["device_id"] == devices[0]["id"]
    # The failed node kept every fact, so it still renders — and is retried
    # on the next load.
    assert bad == {"id": "bad", "type": "server", "ip": "10.0.0.6", "notes": "keep me"}
    assert len(devices) == 1


def test_a_blank_linked_row_is_filled_without_overwriting() -> None:
    """homelable #354: a node linked to a row minted blank (while an earlier
    migration was stuck) still carries its facts; they fill only the gaps."""
    blank = _row(id="pd-blank", label="edited later", ip=None, services=[])
    node = {
        "id": "a", "type": "server", "device_id": "pd-blank", "label": "old",
        "ip": "10.0.0.5", "services": [{"port": 22}],
    }
    devices = [blank]
    stats = sync.backfill_node_devices(_canvases(d1=[node]), devices, now=NOW)
    assert stats["filled"] == 1
    assert blank["ip"] == "10.0.0.5"
    assert blank["services"] == [{"port": 22}]
    assert blank["label"] == "edited later"
    assert "ip" not in node


def test_unparseable_legacy_timestamps_become_none() -> None:
    """homelable #354 hit on text timestamps; here a bad stamp is dropped."""
    node = {
        "id": "a", "type": "server", "ip": "10.0.0.5",
        "last_seen": "yesterday-ish", "last_scan": "2026-01-01T00:00:00Z",
    }
    devices: list[dict] = []
    stats = sync.backfill_node_devices(_canvases(d1=[node]), devices, now=NOW)
    assert stats["skipped"] == 0
    assert devices[0]["last_seen"] is None
    assert devices[0]["last_scan"] == "2026-01-01T00:00:00Z"


def test_a_dangling_link_is_relinked() -> None:
    """A node naming a row that was since deleted gets a row again."""
    node = {"id": "a", "type": "server", "label": "Box", "device_id": "gone"}
    devices: list[dict] = []
    sync.backfill_node_devices(_canvases(d1=[node]), devices, now=NOW)
    assert node["device_id"] == devices[0]["id"]
    assert devices[0]["label"] == "Box"


# ─── Per-node view (homelable #357) ──────────────────────────────────────────

SSH = {"port": 22, "protocol": "tcp", "service_name": "ssh"}
KUMA = {"port": 3001, "protocol": "tcp", "service_name": "Uptime Kuma"}


def test_a_view_orders_the_rows_services_and_hides_what_it_does_not_list() -> None:
    row = _row(services=[SSH, KUMA])
    node = {"id": "a", "type": "server", "device_id": "pd-1",
            "display_view": {"services": [{"key": "3001|tcp|uptime kuma", "visible": True}],
                             "properties": []}}
    hydrated = sync.hydrate_node(node, row)
    # Not listed means "this canvas does not show it" — hidden, not dropped.
    assert hydrated["services"] == [KUMA, {**SSH, "visible": False}]
    assert "display_view" not in hydrated
    assert row["services"] == [SSH, KUMA]


def test_a_node_without_a_view_still_shows_everything() -> None:
    """No view — a node linked before views existed — is not "hide all"."""
    row = _row(services=[SSH, KUMA], properties=[{"key": "Rack", "value": "A1", "visible": True}])
    hydrated = sync.hydrate_node({"id": "a", "type": "server", "device_id": "pd-1"}, row)
    assert hydrated["services"] == [SSH, KUMA]
    assert hydrated["properties"] == row["properties"]


def test_the_view_is_keyed_on_identity_not_on_values() -> None:
    """Editing a property's value or a service's path keeps it where it was."""
    row = _row(
        services=[{**SSH, "path": "/admin"}],
        properties=[{"key": "RACK", "value": "B2", "visible": True}],
    )
    node = {"id": "a", "type": "server", "device_id": "pd-1", "display_view": {
        "services": [{"key": "22|tcp|ssh", "visible": False}],
        "properties": [{"key": "rack", "visible": True}],
    }}
    hydrated = sync.hydrate_node(node, row)
    assert hydrated["services"] == [{**SSH, "path": "/admin", "visible": False}]
    assert hydrated["properties"] == [{"key": "RACK", "value": "B2", "visible": True}]


def test_a_first_view_reads_an_empty_list_as_nothing_to_say() -> None:
    """A node drawn for an already-scanned device sends no services."""
    view = sync.next_view(None, {"services": []}, _row(services=[SSH]))
    assert view == {"services": [{"key": "22|tcp|ssh", "visible": True}], "properties": []}


def test_a_strict_first_view_keeps_an_empty_list() -> None:
    """The backfill: that canvas drew no service, so it keeps drawing none."""
    view = sync.next_view(None, {"services": []}, _row(services=[SSH]), strict=True)
    assert view["services"] == []


def test_a_later_write_keeps_the_lists_it_did_not_carry() -> None:
    current = {"services": [{"key": "22|tcp|ssh", "visible": False}], "properties": []}
    view = sync.next_view(current, {"properties": []}, _row(services=[SSH, KUMA]))
    assert view["services"] == current["services"]


def test_link_facts_records_the_view_even_when_no_fact_changed() -> None:
    """`changed_fields` guards the shared row, not the node's own view."""
    row = _row(ip="10.0.0.5", services=[SSH, KUMA])
    node = {"id": "a", "type": "server", "device_id": "pd-1"}
    sync.link_facts(
        [row], node, {"ip": "10.0.0.5", "services": [SSH, {**KUMA, "visible": False}]},
        now=NOW, replace_lists=True, only_changed=True, changed_fields=[],
    )
    assert row["services"] == [SSH, KUMA]
    assert node["display_view"]["services"] == [
        {"key": "22|tcp|ssh", "visible": True},
        {"key": "3001|tcp|uptime kuma", "visible": False},
    ]


def test_backfill_keeps_a_node_that_drew_no_service_drawing_none() -> None:
    scanned = _row(id="pd-scan", ip="10.0.0.5", services=[KUMA])
    node = {"id": "a", "type": "nas", "label": "NAS", "ip": "10.0.0.5", "services": []}
    sync.backfill_node_devices(_canvases(d1=[node]), [scanned], now=NOW)
    assert sync.hydrate_node(node, scanned)["services"] == [{**KUMA, "visible": False}]


def test_seed_gives_a_linked_node_without_a_view_the_rows_lists() -> None:
    row = _row(services=[SSH])
    node = {"id": "a", "type": "server", "device_id": "pd-1"}
    group = {"id": "g", "type": "groupRect", "label": "Zone"}
    dangling = {"id": "b", "type": "server", "device_id": "pd-gone"}
    canvases = _canvases(d1=[node, group, dangling])

    assert sync.seed_node_views(canvases, [row]) == 1
    assert node["display_view"] == {"services": [{"key": "22|tcp|ssh", "visible": True}],
                                    "properties": []}
    assert "display_view" not in group
    assert "display_view" not in dangling
    assert sync.seed_node_views(canvases, [row]) == 0

    # What the row gains after the seed stays off this canvas.
    row["services"].append(KUMA)
    assert sync.hydrate_node(node, row)["services"] == [SSH, {**KUMA, "visible": False}]
