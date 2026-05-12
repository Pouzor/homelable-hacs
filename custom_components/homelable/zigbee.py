"""Zigbee2MQTT network map fetcher backed by HA's MQTT integration.

Ported from homelable standalone (PR #128). Uses `homeassistant.components.mqtt`
instead of aiomqtt — broker config and TLS are owned by HA's MQTT integration,
so this module only needs the Z2M base topic.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from homeassistant.components import mqtt
from homeassistant.core import HomeAssistant, callback

from .const import ZIGBEE_NETWORKMAP_TIMEOUT

_LOGGER = logging.getLogger(__name__)

_NETWORKMAP_REQUEST_TOPIC = "{base_topic}/bridge/request/networkmap"
_NETWORKMAP_RESPONSE_TOPIC = "{base_topic}/bridge/response/networkmap"


class ZigbeeMqttNotReadyError(RuntimeError):
    """HA MQTT integration not loaded / not configured."""


def _z2m_type_to_homelable(device_type: str) -> str:
    return {
        "Coordinator": "zigbee_coordinator",
        "Router": "zigbee_router",
        "EndDevice": "zigbee_enddevice",
    }.get(device_type, "zigbee_enddevice")


def _node_from_z2m(raw: dict[str, Any]) -> dict[str, Any] | None:
    ieee: str = raw.get("ieeeAddr") or raw.get("ieee_address") or ""
    if not ieee:
        return None
    device_type: str = raw.get("type") or "EndDevice"
    friendly_name: str = (
        raw.get("friendlyName") or raw.get("friendly_name") or ieee
    )
    definition: dict[str, Any] = raw.get("definition") or {}
    model: str | None = (
        raw.get("modelID") or raw.get("model") or definition.get("model") or None
    )
    vendor: str | None = raw.get("vendor") or definition.get("vendor") or None
    return {
        "id": ieee,
        "label": friendly_name,
        "type": _z2m_type_to_homelable(device_type),
        "ieee_address": ieee,
        "friendly_name": friendly_name,
        "device_type": device_type,
        "model": model,
        "vendor": vendor,
        "lqi": None,
        "parent_id": None,
    }


def _find_parent_router(
    device_id: str,
    router_ids: set[str],
    edges: list[dict[str, Any]],
) -> str | None:
    for edge in edges:
        src, tgt = edge["source"], edge["target"]
        if tgt == device_id and src in router_ids:
            return src
        if src == device_id and tgt in router_ids:
            return tgt
    return None


def parse_networkmap(
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse Z2M networkmap payload into (nodes, edges).

    Edges are emitted as a strict coordinator → router → end device tree,
    not the bidirectional mesh, to avoid duplicate edges in the canvas.
    """
    data: dict[str, Any] = payload.get("data") or {}
    value = data.get("value")
    container: dict[str, Any] = value if isinstance(value, dict) else data

    raw_nodes: list[dict[str, Any]] = container.get("nodes") or []
    raw_links: list[dict[str, Any]] = container.get("links") or []

    if not isinstance(raw_nodes, list):
        raise ValueError("Malformed networkmap: 'nodes' is not a list")
    if not isinstance(raw_links, list):
        raise ValueError("Malformed networkmap: 'links' is not a list")

    nodes_list: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    coordinator_id: str | None = None

    for entry in raw_nodes:
        if not isinstance(entry, dict):
            continue
        node = _node_from_z2m(entry)
        if node is None or node["id"] in seen_ids:
            continue
        seen_ids.add(node["id"])
        nodes_list.append(node)
        if node["device_type"] == "Coordinator":
            coordinator_id = node["id"]

    raw_edges: list[dict[str, Any]] = []
    lqi_by_id: dict[str, int] = {}

    for link in raw_links:
        if not isinstance(link, dict):
            continue
        src_obj = link.get("source") or {}
        tgt_obj = link.get("target") or {}
        src = src_obj.get("ieeeAddr") if isinstance(src_obj, dict) else None
        tgt = tgt_obj.get("ieeeAddr") if isinstance(tgt_obj, dict) else None
        if not src or not tgt:
            continue
        if src not in seen_ids or tgt not in seen_ids:
            continue
        raw_edges.append({"source": src, "target": tgt})
        lqi = link.get("lqi") or link.get("linkquality")
        if isinstance(lqi, int) and tgt not in lqi_by_id:
            lqi_by_id[tgt] = lqi

    for node in nodes_list:
        if node["id"] in lqi_by_id:
            node["lqi"] = lqi_by_id[node["id"]]

    if coordinator_id:
        router_ids = {n["id"] for n in nodes_list if n["device_type"] == "Router"}
        for node in nodes_list:
            if node["device_type"] == "Router":
                node["parent_id"] = coordinator_id
            elif node["device_type"] == "EndDevice":
                parent = _find_parent_router(node["id"], router_ids, raw_edges)
                node["parent_id"] = parent or coordinator_id

    edges_list: list[dict[str, Any]] = [
        {"source": node["parent_id"], "target": node["id"]}
        for node in nodes_list
        if node.get("parent_id")
    ]

    return nodes_list, edges_list


def _mqtt_ready(hass: HomeAssistant) -> bool:
    """True iff HA's MQTT integration is loaded and a client is available."""
    return "mqtt" in hass.config.components


async def fetch_networkmap(
    hass: HomeAssistant,
    base_topic: str,
    timeout: float = ZIGBEE_NETWORKMAP_TIMEOUT,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Request the Z2M networkmap via HA MQTT and return (nodes, edges).

    Raises:
        ZigbeeMqttNotReady: HA MQTT integration not loaded.
        TimeoutError: broker did not deliver a response within `timeout`.
        ValueError: response payload is malformed or empty.
    """
    if not _mqtt_ready(hass):
        raise ZigbeeMqttNotReadyError(
            "Home Assistant MQTT integration is not configured"
        )

    request_topic = _NETWORKMAP_REQUEST_TOPIC.format(base_topic=base_topic)
    response_topic = _NETWORKMAP_RESPONSE_TOPIC.format(base_topic=base_topic)

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
            future.set_exception(ValueError(f"Malformed networkmap response: {exc}"))

    unsub = await mqtt.async_subscribe(hass, response_topic, _on_message)
    try:
        # Brief delay so SUBSCRIBE round-trips before we publish — otherwise
        # fast brokers can deliver the response before our subscription is live.
        await asyncio.sleep(0.1)
        await mqtt.async_publish(
            hass,
            request_topic,
            json.dumps({"type": "raw", "routes": False}),
        )
        try:
            response_payload = await asyncio.wait_for(future, timeout=timeout)
        except TimeoutError as exc:
            raise TimeoutError(
                "Timed out waiting for Zigbee2MQTT networkmap response"
            ) from exc
    finally:
        unsub()

    if not response_payload:
        raise ValueError("Empty networkmap response received")

    return parse_networkmap(response_payload)
