"""Nodes recorded as their own parent (port of homelable #390).

A ``parent_id == id`` row is fatal on the canvas: every parent walk in the
panel assumes an acyclic tree, so dragging the node overflowed the stack inside
the change reducer and the move was silently dropped — the node selected but
would not move.

The save guard stops new rows; the load repair clears the ones already sitting
in users' Stores.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.homelable.const import (
    STORAGE_KEY_CANVAS,
    STORAGE_KEY_DESIGNS,
)
from custom_components.homelable.coordinator import HomelableCoordinator


def _mock_entry() -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "test_entry"
    entry.data = {"scan_ranges": "192.168.1.0/24", "status_interval": 60}
    entry.options = {}
    return entry


@pytest.fixture
def coord(hass: HomeAssistant) -> HomelableCoordinator:
    return HomelableCoordinator(hass, _mock_entry())


def _node(node_id: str, parent_id: str | None = None, **extra) -> dict:  # noqa: ANN003
    return {"id": node_id, "type": "lxc", "label": node_id, "parent_id": parent_id, **extra}


def _seed(hass_storage, nodes: list[dict]) -> None:  # noqa: ANN001
    hass_storage[STORAGE_KEY_DESIGNS] = {
        "version": 1, "key": STORAGE_KEY_DESIGNS,
        "data": {"designs": [
            {"id": "d1", "name": "d1", "design_type": "network", "icon": "network"},
        ]},
    }
    hass_storage[STORAGE_KEY_CANVAS] = {
        "version": 1, "key": STORAGE_KEY_CANVAS,
        "data": {"canvases": {"d1": {"nodes": nodes, "edges": [], "viewport": {}}}},
    }


def _parents(canvas: dict) -> dict[str, str | None]:
    return {n["id"]: n.get("parent_id") for n in canvas["nodes"]}


# ── save guard ───────────────────────────────────────────────────────────────


async def test_save_canvas_drops_a_node_parented_to_itself(coord: HomelableCoordinator) -> None:
    # Dropped rather than rejected: a canvas that already carries the bad row
    # must still be able to save.
    await coord.save_canvas({"nodes": [_node("pihole", "pihole")], "edges": [], "viewport": {}})

    assert _parents(await coord.get_canvas()) == {"pihole": None}


async def test_save_canvas_keeps_a_real_parent(coord: HomelableCoordinator) -> None:
    host = _node("pve", type="proxmox", container_mode=True)
    child = _node("pihole", "pve")

    await coord.save_canvas({"nodes": [host, child], "edges": [], "viewport": {}})

    assert _parents(await coord.get_canvas()) == {"pve": None, "pihole": "pve"}


# ── load repair ──────────────────────────────────────────────────────────────


async def test_load_detaches_a_self_parented_node(
    coord: HomelableCoordinator, hass_storage  # noqa: ANN001
) -> None:
    _seed(hass_storage, [_node("pihole", "pihole")])

    assert _parents(await coord.get_canvas("d1")) == {"pihole": None}
    # Persisted, not just patched in memory.
    stored = hass_storage[STORAGE_KEY_CANVAS]["data"]["canvases"]["d1"]
    assert _parents(stored) == {"pihole": None}


async def test_load_repair_leaves_real_parents_alone(
    coord: HomelableCoordinator, hass_storage  # noqa: ANN001
) -> None:
    _seed(hass_storage, [
        _node("pve", type="proxmox", container_mode=True),
        _node("vm", "pve"),
        _node("broken", "broken"),
    ])

    assert _parents(await coord.get_canvas("d1")) == {"pve": None, "vm": "pve", "broken": None}


async def test_load_repair_is_idempotent(
    hass: HomeAssistant, hass_storage  # noqa: ANN001
) -> None:
    _seed(hass_storage, [_node("a", "a"), _node("b")])

    await HomelableCoordinator(hass, _mock_entry()).get_canvas("d1")
    # A fresh coordinator reloads the repaired Store: nothing left to repair.
    second = HomelableCoordinator(hass, _mock_entry())

    assert _parents(await second.get_canvas("d1")) == {"a": None, "b": None}
    assert second._repair_self_parent_nodes() is False


async def test_load_repair_does_not_touch_a_two_node_cycle(
    coord: HomelableCoordinator, hass_storage  # noqa: ANN001
) -> None:
    """Only the exact self-parent case is repairable here.

    A longer cycle has no single right answer for which link to cut, and the
    panel's cycle guards keep the canvas usable, so the rows are left as they
    are rather than guessed at.
    """
    _seed(hass_storage, [_node("a", "b"), _node("b", "a")])

    assert _parents(await coord.get_canvas("d1")) == {"a": "b", "b": "a"}
