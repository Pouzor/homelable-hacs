"""Z-Wave JS UI (zwavejs2mqtt) network fetcher backed by HA's MQTT integration.

Ported from homelable standalone (PR #224). Mirrors the Zigbee pipeline in
`zigbee.py`: Z-Wave JS UI exposes a request/response gateway over MQTT — publish
to ``<prefix>/_CLIENTS/ZWAVE_GATEWAY-<gateway>/api/getNodes/set`` and read the
answer from ``<prefix>/_CLIENTS/ZWAVE_GATEWAY-<gateway>/api/getNodes``.

Like the Zigbee module this uses `homeassistant.components.mqtt` rather than a
own MQTT client: broker config and TLS are owned by HA's MQTT integration, so
this module only needs the Z-Wave JS UI prefix + gateway name.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from homeassistant.components import mqtt
from homeassistant.core import HomeAssistant, callback

from .const import ZWAVE_NODES_TIMEOUT
from .zigbee import _find_parent_router, _mqtt_ready, merge_zigbee_properties

_LOGGER = logging.getLogger(__name__)

_REQUEST_TOPIC = "{prefix}/_CLIENTS/ZWAVE_GATEWAY-{gateway}/api/getNodes/set"
_RESPONSE_TOPIC = "{prefix}/_CLIENTS/ZWAVE_GATEWAY-{gateway}/api/getNodes"

# Reuse the zigbee merge logic verbatim — same NodeProperty shape + visibility.
merge_zwave_properties = merge_zigbee_properties


class ZwaveMqttNotReadyError(RuntimeError):
    """HA MQTT integration not loaded / not configured."""


def build_zwave_properties(
    ieee: str | None,
    vendor: str | None,
    model: str | None,
) -> list[dict[str, Any]]:
    """Build a NodeProperty list for a Z-Wave device (Z-Wave ID, Vendor, Model).

    Z-Wave has no LQI, so that row is omitted. Only includes a row when the
    value is non-empty. New props default to ``visible=False`` — users opt in
    to showing them on the canvas card from the right panel.
    """
    props: list[dict[str, Any]] = []
    if ieee:
        props.append({"key": "Z-Wave ID", "value": ieee, "icon": None, "visible": False})
    if vendor:
        props.append({"key": "Vendor", "value": vendor, "icon": None, "visible": False})
    if model:
        props.append({"key": "Model", "value": model, "icon": None, "visible": False})
    return props


def _zwave_type_to_homelable(raw: dict[str, Any]) -> str:
    """Map a Z-Wave node's role flags to a homelable node type.

    Controller → coordinator. Mains-powered / routing nodes → router.
    Everything else (battery sensors, etc.) → end device.
    """
    if raw.get("isControllerNode"):
        return "zwave_coordinator"
    if raw.get("isRouting"):
        return "zwave_router"
    return "zwave_enddevice"


def _role_label(node_type: str) -> str:
    """Human role string stored as ``device_type``."""
    return {
        "zwave_coordinator": "Controller",
        "zwave_router": "Router",
        "zwave_enddevice": "EndDevice",
    }.get(node_type, "EndDevice")


def _node_from_zwave(raw: dict[str, Any], home_id: str) -> dict[str, Any] | None:
    """Build a homelable node dict from a Z-Wave JS UI ``getNodes`` entry."""
    node_id = raw.get("id")
    if node_id is None:
        return None
    ieee = f"zwave-{home_id}-{node_id}"
    node_type = _zwave_type_to_homelable(raw)
    name = raw.get("name") or raw.get("loc") or f"Node {node_id}"
    model = raw.get("productLabel") or raw.get("productDescription") or None
    vendor = raw.get("manufacturer") or None
    return {
        "id": ieee,
        "label": name,
        "type": node_type,
        "ieee_address": ieee,
        "friendly_name": name,
        "device_type": _role_label(node_type),
        "node_id": node_id,
        "model": model,
        "vendor": vendor,
        "lqi": None,  # Z-Wave has no LQI; RSSI may be added later.
        "parent_id": None,
        "neighbors": raw.get("neighbors") or [],
    }


def _resolve_home_id(raw_nodes: list[dict[str, Any]]) -> str:
    """Pick a home id for the network: prefer the controller's, else any node's."""
    controller_home = None
    for entry in raw_nodes:
        if not isinstance(entry, dict):
            continue
        home = entry.get("homeId")
        if home is None:
            continue
        if entry.get("isControllerNode"):
            return str(home)
        if controller_home is None:
            controller_home = str(home)
    return controller_home or "0"


def parse_zwave_nodes(
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse a Z-Wave JS UI ``getNodes`` response into (nodes, edges).

    Expected shape::

        {"success": true, "result": [ {<node>}, ... ]}

    Edges are a strict coordinator → router → end-device tree, derived from
    each node's ``neighbors`` list (same approach as the Zigbee parser).
    """
    if payload.get("success") is False:
        raise ValueError("Z-Wave gateway reported failure")

    result = payload.get("result")
    if result is None:
        result = []
    if not isinstance(result, list):
        raise ValueError("Malformed getNodes response: 'result' is not a list")

    home_id = _resolve_home_id(result)

    nodes_list: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    coordinator_id: str | None = None
    # Map nodeId (int) → identity string, to translate neighbors → edges.
    id_by_node_id: dict[Any, str] = {}

    for entry in result:
        if not isinstance(entry, dict):
            continue
        node = _node_from_zwave(entry, home_id)
        if node is None or node["id"] in seen_ids:
            continue
        seen_ids.add(node["id"])
        id_by_node_id[node["node_id"]] = node["id"]
        nodes_list.append(node)
        if node["type"] == "zwave_coordinator":
            coordinator_id = node["id"]

    # Translate neighbor lists into candidate edges (only between known nodes).
    raw_edges: list[dict[str, Any]] = []
    for node in nodes_list:
        src = node["id"]
        for neighbor in node.get("neighbors") or []:
            tgt = id_by_node_id.get(neighbor)
            if tgt and tgt != src:
                raw_edges.append({"source": src, "target": tgt})

    # Build parent_id hierarchy: coordinator → routers → end devices.
    if coordinator_id:
        router_ids = {n["id"] for n in nodes_list if n["type"] == "zwave_router"}
        for node in nodes_list:
            if node["type"] == "zwave_router":
                node["parent_id"] = coordinator_id
            elif node["type"] == "zwave_enddevice":
                parent = _find_parent_router(node["id"], router_ids, raw_edges)
                node["parent_id"] = parent or coordinator_id

    # Final edges = strict parent → child tree (one edge per non-coordinator).
    edges_list: list[dict[str, Any]] = [
        {"source": node["parent_id"], "target": node["id"]}
        for node in nodes_list
        if node.get("parent_id")
    ]

    # Drop transient helper keys before returning.
    for node in nodes_list:
        node.pop("neighbors", None)
        node.pop("node_id", None)

    return nodes_list, edges_list


async def fetch_zwave_network(
    hass: HomeAssistant,
    prefix: str,
    gateway_name: str,
    timeout: float = ZWAVE_NODES_TIMEOUT,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Request the Z-Wave node list via HA MQTT and return (nodes, edges).

    Raises:
        ZwaveMqttNotReadyError: HA MQTT integration not loaded.
        TimeoutError: gateway did not deliver a response within `timeout`.
        ValueError: response payload is malformed or empty.
    """
    if not _mqtt_ready(hass):
        raise ZwaveMqttNotReadyError(
            "Home Assistant MQTT integration is not configured"
        )

    request_topic = _REQUEST_TOPIC.format(prefix=prefix, gateway=gateway_name)
    response_topic = _RESPONSE_TOPIC.format(prefix=prefix, gateway=gateway_name)

    loop = asyncio.get_running_loop()
    future: asyncio.Future[dict[str, Any]] = loop.create_future()

    @callback
    def _on_message(message: mqtt.ReceiveMessage) -> None:
        if future.done():
            return
        try:
            raw = message.payload
            payload_str = (
                raw.decode() if isinstance(raw, bytes | bytearray) else str(raw)
            )
            future.set_result(json.loads(payload_str))
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as exc:
            future.set_exception(ValueError(f"Malformed getNodes response: {exc}"))

    unsub = await mqtt.async_subscribe(hass, response_topic, _on_message)
    try:
        # Brief delay so SUBSCRIBE round-trips before we publish — otherwise
        # fast brokers can deliver the response before our subscription is live.
        await asyncio.sleep(0.1)
        await mqtt.async_publish(hass, request_topic, json.dumps({"args": []}))
        try:
            response_payload = await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError as exc:
            raise TimeoutError(
                "Timed out waiting for Z-Wave JS UI getNodes response"
            ) from exc
    finally:
        unsub()

    if not response_payload:
        raise ValueError("Empty getNodes response received")

    return parse_zwave_nodes(response_payload)
