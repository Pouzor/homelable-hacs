"""Library tree helpers over the Store's ``{id: document}`` map (port of homelable #418)."""

from custom_components.homelable import doc_tree


def _docs(*rows: dict) -> dict[str, dict]:
    return {row["id"]: row for row in rows}


def test_slugify_keeps_only_url_safe_characters():
    assert doc_tree.slugify("VLAN plan / 2026!") == "vlan-plan-2026"
    assert doc_tree.slugify("   ") == "untitled"
    assert len(doc_tree.slugify("x" * 200)) == 80


def test_unique_slug_suffixes_a_taken_sibling():
    docs = _docs({"id": "a", "slug": "vlans", "parent_id": None})
    assert doc_tree.unique_slug(docs, "VLANs", parent_id=None) == "vlans-2"


def test_unique_slug_keeps_counting_past_the_second():
    docs = _docs(
        {"id": "a", "slug": "vlans", "parent_id": None},
        {"id": "b", "slug": "vlans-2", "parent_id": None},
    )
    assert doc_tree.unique_slug(docs, "VLANs", parent_id=None) == "vlans-3"


def test_unique_slug_only_looks_at_siblings():
    docs = _docs({"id": "a", "slug": "vlans", "parent_id": "folder"})
    assert doc_tree.unique_slug(docs, "VLANs", parent_id=None) == "vlans"


def test_unique_slug_ignores_the_document_being_renamed():
    docs = _docs({"id": "a", "slug": "vlans", "parent_id": None})
    assert doc_tree.unique_slug(docs, "VLANs", parent_id=None, exclude_id="a") == "vlans"


def test_frontmatter_parses_and_coerces_dates_to_strings():
    body = "---\ntitle: NAS\ncreated: 2026-09-05\ntags: [storage, zfs]\n---\n# NAS\n"
    front = doc_tree.parse_frontmatter(body)
    assert front == {"title": "NAS", "created": "2026-09-05", "tags": ["storage", "zfs"]}
    assert doc_tree.tags_from(front) == ["storage", "zfs"]


def test_frontmatter_that_does_not_parse_is_empty():
    assert doc_tree.parse_frontmatter("---\ntitle: [unclosed\n---\n") == {}
    assert doc_tree.parse_frontmatter("no frontmatter here") == {}
    assert doc_tree.parse_frontmatter("---\n- a list\n---\n") == {}


def test_an_unclosed_frontmatter_block_is_not_quadratic():
    # The shape a half-typed document has. Must return at once, not hang.
    assert doc_tree.parse_frontmatter("---\n" + "a: b\n" * 20000) == {}


def test_tags_accept_a_comma_separated_string():
    assert doc_tree.tags_from({"tags": "a, b ,, c"}) == ["a", "b", "c"]
    assert doc_tree.tags_from({"tags": 3}) == []


def _chain(depth: int) -> dict[str, dict]:
    return _docs(
        *(
            {"id": f"f{i}", "parent_id": f"f{i - 1}" if i else None, "kind": "folder"}
            for i in range(depth)
        )
    )


def test_is_ancestor_walks_the_whole_chain():
    docs = _chain(5)
    assert doc_tree.is_ancestor(docs, "f0", "f4")
    assert not doc_tree.is_ancestor(docs, "f4", "f0")


def test_a_document_is_its_own_ancestor():
    assert doc_tree.is_ancestor(_chain(1), "f0", "f0")


def test_is_ancestor_stops_on_a_loop():
    docs = _docs({"id": "a", "parent_id": "b"}, {"id": "b", "parent_id": "a"})
    assert not doc_tree.is_ancestor(docs, "x", "a")


def test_subtree_ids_collects_every_descendant():
    docs = _chain(4)
    docs["side"] = {"id": "side", "parent_id": "f1"}
    assert set(doc_tree.subtree_ids(docs, "f1")) == {"f1", "f2", "f3", "side"}
    assert doc_tree.subtree_ids(docs, "f1")[0] == "f1"


def test_subtree_ids_of_a_leaf_is_just_itself():
    assert doc_tree.subtree_ids(_chain(3), "f2") == ["f2"]
