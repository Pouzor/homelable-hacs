"""Search over documents, in memory.

The standalone answers this with SQLite FTS5 and a LIKE fallback (homelable
#418, #463). Here every document already lives in memory in the Store, and a
homelab's documentation is at most a few thousand pages, so a scan over title,
tags and body is instant and needs no index to keep in step with each write.

Matching follows the FTS5 query the standalone builds: every whitespace-separated
term must match, and a term matches the start of a word, so typing half a word
(``prox``) finds ``Proxmox`` while punctuation a user types (``192.168.1.1``,
``nas-01``) is matched literally. Results rank title hits above tag hits above
body hits, then by how often the terms appear, then most recently updated.

The engine reported to the panel is ``"like"``: the snippet is a plain excerpt
with no highlight markers, which is what the panel already expects on the
standalone's fallback path.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

ENGINE = "like"

_SNIPPET_WIDTH = 120


def _terms(query: str) -> list[str]:
    return [term.lower() for term in query.split() if term.strip()]


def _pattern(term: str) -> re.Pattern[str]:
    # A prefix match on a word boundary. `(?<!\w)` rather than `\b` so a term
    # that starts with punctuation (`.local`) still anchors.
    return re.compile(r"(?<!\w)" + re.escape(term), re.IGNORECASE)


def excerpt(body: str, term: str, width: int = _SNIPPET_WIDTH) -> str:
    """The text around the first match of ``term``, on one line."""
    flat = " ".join((body or "").split())
    match = _pattern(term).search(flat) if term else None
    if match is None:
        return flat[:width].strip()
    at = match.start()
    start = max(0, at - width // 2)
    end = min(len(flat), at + len(term) + width // 2)
    return ("…" if start else "") + flat[start:end].strip() + ("…" if end < len(flat) else "")


def search(
    docs: Iterable[Mapping[str, Any]], query: str, limit: int = 25
) -> list[dict[str, Any]]:
    """``[{doc_id, snippet, score}]`` best first, at most ``limit`` long."""
    terms = _terms(query)
    if not terms:
        return []
    patterns = [_pattern(term) for term in terms]

    scored: list[tuple[tuple[float, str], dict[str, Any]]] = []
    for doc in docs:
        title = str(doc.get("title") or "")
        tags = " ".join(str(tag) for tag in doc.get("tags") or [])
        body = str(doc.get("body") or "")
        score = 0.0
        matched_all = True
        for pattern in patterns:
            in_title = len(pattern.findall(title))
            in_tags = len(pattern.findall(tags))
            in_body = len(pattern.findall(body))
            if not (in_title or in_tags or in_body):
                matched_all = False
                break
            score += in_title * 100 + in_tags * 20 + min(in_body, 50)
        if not matched_all:
            continue
        # The first term found in the body anchors the snippet, else the body opens.
        anchor = next((t for t, p in zip(terms, patterns, strict=True) if p.search(body)), "")
        scored.append(
            (
                (-score, str(doc.get("updated_at") or "")),
                {"doc_id": doc["id"], "snippet": excerpt(body, anchor), "score": score},
            )
        )

    # Highest score first; within a score, the most recently updated first.
    scored.sort(key=lambda item: item[0][1], reverse=True)
    scored.sort(key=lambda item: item[0][0])
    return [hit for _, hit in scored[:limit]]
