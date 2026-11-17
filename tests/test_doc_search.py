"""In-memory document search — the HACS stand-in for the standalone's FTS5 index."""

from custom_components.homelable import doc_search


def _doc(doc_id: str, *, title: str = "", body: str = "", tags=None, updated_at: str = "") -> dict:
    return {
        "id": doc_id,
        "title": title or doc_id,
        "body": body,
        "tags": tags or [],
        "updated_at": updated_at,
    }


def test_an_empty_query_finds_nothing():
    assert doc_search.search([_doc("a", body="anything")], "   ") == []


def test_every_term_must_match():
    docs = [_doc("a", body="proxmox backup"), _doc("b", body="proxmox only")]
    assert [h["doc_id"] for h in doc_search.search(docs, "proxmox backup")] == ["a"]


def test_a_term_matches_the_start_of_a_word():
    docs = [_doc("a", body="The Proxmox host"), _doc("b", body="unproxied")]
    assert [h["doc_id"] for h in doc_search.search(docs, "prox")] == ["a"]


def test_punctuation_in_a_term_is_matched_literally():
    docs = [_doc("a", body="lives at 192.168.1.20"), _doc("b", body="192x168")]
    assert [h["doc_id"] for h in doc_search.search(docs, "192.168.1.20")] == ["a"]
    # A regex metacharacter is data, not syntax.
    assert doc_search.search(docs, "(") == []


def test_title_beats_tags_beats_body():
    docs = [
        _doc("body", title="Other", body="zfs"),
        _doc("tags", title="Other 2", tags=["zfs"]),
        _doc("title", title="ZFS pools"),
    ]
    assert [h["doc_id"] for h in doc_search.search(docs, "zfs")] == ["title", "tags", "body"]


def test_ties_go_to_the_most_recently_updated():
    docs = [
        _doc("old", body="vlan", updated_at="2026-01-01T00:00:00Z"),
        _doc("new", body="vlan", updated_at="2026-06-01T00:00:00Z"),
    ]
    assert [h["doc_id"] for h in doc_search.search(docs, "vlan")] == ["new", "old"]


def test_limit_caps_the_hits():
    docs = [_doc(str(i), body="dns") for i in range(10)]
    assert len(doc_search.search(docs, "dns", limit=3)) == 3


def test_the_snippet_is_the_text_around_the_match():
    body = "x " * 200 + "the backup runs nightly " + "y " * 200
    snippet = doc_search.search([_doc("a", body=body)], "backup")[0]["snippet"]
    assert "backup runs nightly" in snippet
    assert snippet.startswith("…") and snippet.endswith("…")


def test_a_title_only_match_snippets_the_start_of_the_body():
    hit = doc_search.search([_doc("a", title="Router", body="Opening line.")], "router")[0]
    assert hit["snippet"] == "Opening line."
