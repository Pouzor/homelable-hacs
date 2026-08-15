"""ZHA network fetcher backed by Home Assistant's own ZHA integration.

Answers issue #50: ZHA users have no Zigbee2MQTT broker, so the MQTT
round-trip in `zigbee.py` has nothing to talk to and the only workaround was
an HA automation faking Z2M responses over MQTT. ZHA already holds the whole
mesh in memory — `ZHADeviceProxy.zha_device_info` is exactly the payload ZHA's
own `zha/devices` websocket command returns, neighbour tables included — so
this module reads it directly. No broker, no re-pairing.

The returned shape is identical to `zigbee.parse_networkmap`, so everything
downstream (property rows, the pending import, canvas node types) is unchanged.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .zigbee import _z2m_type_to_homelable

_LOGGER = logging.getLogger(__name__)

ZHA_DOMAIN = "zha"

# zigpy neighbour relationship names, as stringified onto
# `zha_device_info["neighbors"][*]["relationship"]`.
_REL_CHILD = "Child"
_REL_PARENT = "Parent"


class ZhaNotReadyError(RuntimeError):
    """HA's ZHA integration is not set up, or its gateway is unreachable."""


def zha_available(hass: HomeAssistant) -> bool:
    """True iff the ZHA integration is loaded."""
    return ZHA_DOMAIN in hass.config.components


def _device_infos(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Return one `zha_device_info` dict per device known to the ZHA gateway.

    The import is lazy: the `zha` library ships as a ZHA requirement, so it is
    only importable once the user has actually set ZHA up — importing it at
    module level would break every install without ZHA.
    """
    try:
        from homeassistant.components.zha.helpers import (  # noqa: PLC0415
            get_zha_gateway_proxy,
        )
    except ImportError as exc:  # ZHA not installed, or restructured again
        raise ZhaNotReadyError("ZHA integration is not installed") from exc

    try:
        gateway_proxy = get_zha_gateway_proxy(hass)
        proxies = gateway_proxy.device_proxies.values()
        return [proxy.zha_device_info for proxy in proxies]
    except (ValueError, KeyError, AttributeError) as exc:
        raise ZhaNotReadyError("ZHA gateway is not available") from exc


def _coerce_int(value: Any) -> int | None:
    """Neighbour LQI/depth arrive as strings; the device's own LQI is an int."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _node_from_zha(info: dict[str, Any]) -> dict[str, Any] | None:
    """Map one `zha_device_info` dict to a homelable mesh node."""
    raw_ieee = info.get("ieee")
    if not raw_ieee:
        return None
    ieee = str(raw_ieee)

    # ZHA's `device_type` already uses the Coordinator / Router / EndDevice
    # names that `_z2m_type_to_homelable` maps, so no separate table is needed.
    device_type = str(info.get("device_type") or "EndDevice")
    if info.get("active_coordinator"):
        device_type = "Coordinator"

    name = str(info.get("name") or ieee)
    return {
        "id": ieee,
        "label": name,
        "type": _z2m_type_to_homelable(device_type),
        "ieee_address": ieee,
        "friendly_name": name,
        "device_type": device_type,
        "model": info.get("model") or None,
        "vendor": info.get("manufacturer") or None,
        "lqi": _coerce_int(info.get("lqi")),
        "parent_id": None,
    }


def _break_cycles(
    parent_of: dict[str, str], coordinator_id: str | None
) -> dict[str, str]:
    """Cut parent links that would close a loop.

    Zigbee parenting is a tree, but neighbour tables go stale: two routers can
    each still list the other as their parent. Walk up from every node and cut
    the first repeat, re-homing on the coordinator when there is one, so the
    canvas always gets a tree.
    """
    out = dict(parent_of)
    if coordinator_id:
        out.pop(coordinator_id, None)
    for start in list(out):
        seen = {start}
        current = start
        while (nxt := out.get(current)) is not None:
            if nxt in seen:
                if coordinator_id and current != coordinator_id:
                    out[current] = coordinator_id
                else:
                    out.pop(current, None)
                break
            seen.add(nxt)
            current = nxt
    return out


def build_topology(
    infos: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build (nodes, edges) from a list of `zha_device_info` dicts.

    Edges are a strict parent → child tree, matching what
    `zigbee.parse_networkmap` emits, so the canvas never gets the duplicate
    edges a bidirectional mesh would produce.
    """
    nodes_list: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    coordinator_id: str | None = None

    for info in infos:
        if not isinstance(info, dict):
            continue
        node = _node_from_zha(info)
        if node is None or node["id"] in seen_ids:
            continue
        seen_ids.add(node["id"])
        nodes_list.append(node)
        if node["device_type"] == "Coordinator" and coordinator_id is None:
            coordinator_id = node["id"]

    parent_of: dict[str, str] = {}
    lqi_by_id: dict[str, int] = {}

    for info in infos:
        if not isinstance(info, dict):
            continue
        ieee = str(info.get("ieee") or "")
        if ieee not in seen_ids:
            continue
        for neighbor in info.get("neighbors") or []:
            if not isinstance(neighbor, dict):
                continue
            other = str(neighbor.get("ieee") or "")
            if other == ieee or other not in seen_ids:
                continue
            relationship = str(neighbor.get("relationship") or "")
            if relationship == _REL_CHILD:
                child, parent = other, ieee
            elif relationship == _REL_PARENT:
                child, parent = ieee, other
            else:
                # Sibling / None_of_the_above carry no parenting information.
                continue
            parent_of.setdefault(child, parent)
            lqi = _coerce_int(neighbor.get("lqi"))
            if lqi is not None:
                lqi_by_id.setdefault(child, lqi)

    parent_of = _break_cycles(parent_of, coordinator_id)

    for node in nodes_list:
        node_id = node["id"]
        if node_id in lqi_by_id:
            node["lqi"] = lqi_by_id[node_id]
        if node_id == coordinator_id:
            continue
        # A device whose neighbours told us nothing still belongs on the mesh;
        # hang it off the coordinator rather than leaving it floating.
        node["parent_id"] = parent_of.get(node_id) or coordinator_id

    edges_list: list[dict[str, Any]] = [
        {"source": node["parent_id"], "target": node["id"]}
        for node in nodes_list
        if node.get("parent_id")
    ]

    return nodes_list, edges_list


def _registry_names(hass: HomeAssistant) -> dict[str, str]:
    """Map ZHA IEEE → the name the user gave the device in HA, if any."""
    registry = dr.async_get(hass)
    names: dict[str, str] = {}
    for entry in registry.devices.values():
        for domain, identifier in entry.identifiers:
            if domain != ZHA_DOMAIN:
                continue
            name = entry.name_by_user or entry.name
            if name:
                names[str(identifier)] = name
    return names


def _apply_registry_names(hass: HomeAssistant, nodes: list[dict[str, Any]]) -> None:
    """Prefer the user's HA device name over the raw ZHA one."""
    names = _registry_names(hass)
    for node in nodes:
        name = names.get(node["id"])
        if name:
            node["label"] = name
            node["friendly_name"] = name


def _fallback_from_registry(
    hass: HomeAssistant,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Flat device list read from the HA device registry.

    Used when ZHA is loaded but its gateway object is unreachable. ZHA's
    internals are private API and have been restructured before (the `zha`
    library was split out of core), while the registry identifiers
    ``("zha", <ieee>)`` are public and stable. Devices still import with their
    name, vendor and model; only the mesh topology and LQI are lost.
    """
    registry = dr.async_get(hass)
    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in registry.devices.values():
        ieee = next(
            (str(i) for d, i in entry.identifiers if d == ZHA_DOMAIN),
            None,
        )
        if not ieee or ieee in seen:
            continue
        seen.add(ieee)
        name = entry.name_by_user or entry.name or ieee
        nodes.append(
            {
                "id": ieee,
                "label": name,
                "type": _z2m_type_to_homelable("EndDevice"),
                "ieee_address": ieee,
                "friendly_name": name,
                "device_type": "EndDevice",
                "model": entry.model or None,
                "vendor": entry.manufacturer or None,
                "lqi": None,
                "parent_id": None,
            }
        )
    return nodes, []


async def fetch_zha_network(
    hass: HomeAssistant,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read the ZHA mesh and return (nodes, edges).

    Async for symmetry with the MQTT fetchers; ZHA's data is already in
    memory, so nothing is awaited and nothing blocks the event loop.

    Raises:
        ZhaNotReadyError: the ZHA integration is not set up.
    """
    if not zha_available(hass):
        raise ZhaNotReadyError("Home Assistant's ZHA integration is not set up")

    try:
        infos = _device_infos(hass)
    except ZhaNotReadyError:
        _LOGGER.warning(
            "ZHA is set up but its gateway is unreachable; falling back to the "
            "device registry (no mesh topology or LQI)"
        )
        return _fallback_from_registry(hass)

    nodes, edges = build_topology(infos)
    _apply_registry_names(hass, nodes)
    return nodes, edges
