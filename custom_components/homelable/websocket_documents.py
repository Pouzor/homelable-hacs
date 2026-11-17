"""WebSocket commands for the documentation space (``homelable/docs/*``).

The HA-side counterpart of the standalone ``/api/v1/documents`` routes (homelable
#418 and the PRs built on it). Every rule lives in ``documents.DocumentManager``;
these handlers only validate the message shape and map a ``DocumentError`` to a
WS error. Reads are open to any panel user; writes need an admin, like every
other canvas write.

Registered from ``websocket.async_register_websocket_commands``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .documents import DocumentError, DocumentManager

_OPT_STR = vol.Any(str, None)

_RESOLUTION = vol.Schema(
    {
        vol.Required("id"): str,
        vol.Optional("choice", default="keep"): vol.In(("keep", "device", "custom")),
        vol.Optional("custom"): _OPT_STR,
    }
)


@callback
def async_register_document_commands(hass: HomeAssistant) -> None:
    for handler in (
        ws_docs_list,
        ws_docs_get,
        ws_docs_coverage,
        ws_docs_search,
        ws_docs_block,
        ws_docs_export,
        ws_docs_revisions,
        ws_docs_revision,
        ws_docs_backlinks,
        ws_docs_create,
        ws_docs_update,
        ws_docs_restore,
        ws_docs_regenerate,
        ws_docs_update_preview,
        ws_docs_update_from_device,
        ws_docs_scaffold,
        ws_docs_delete,
    ):
        websocket_api.async_register_command(hass, handler)


def _documents(hass: HomeAssistant) -> DocumentManager | None:
    entries = hass.data.get(DOMAIN, {})
    if not entries:
        return None
    return next(iter(entries.values())).documents


async def _run(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
    call: Callable[[DocumentManager], Awaitable[Any]],
) -> None:
    documents = _documents(hass)
    if documents is None:
        connection.send_error(msg["id"], "not_setup", "Homelable not configured")
        return
    try:
        result = await call(documents)
    except DocumentError as exc:
        connection.send_error(msg["id"], exc.code, str(exc))
        return
    connection.send_result(msg["id"], result)


# ── reads ───────────────────────────────────────────────────────────────────


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homelable/docs/list",
        vol.Optional("kind"): _OPT_STR,
        vol.Optional("parent_id"): _OPT_STR,
        vol.Optional("device_id"): _OPT_STR,
        vol.Optional("tag"): _OPT_STR,
    }
)
@websocket_api.async_response
async def ws_docs_list(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    await _run(
        hass,
        connection,
        msg,
        lambda d: d.list(
            kind=msg.get("kind"),
            parent_id=msg.get("parent_id"),
            device_id=msg.get("device_id"),
            tag=msg.get("tag"),
        ),
    )


@websocket_api.websocket_command(
    {vol.Required("type"): "homelable/docs/get", vol.Required("document_id"): str}
)
@websocket_api.async_response
async def ws_docs_get(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    await _run(hass, connection, msg, lambda d: d.get(msg["document_id"]))


@websocket_api.websocket_command({vol.Required("type"): "homelable/docs/coverage"})
@websocket_api.async_response
async def ws_docs_coverage(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    await _run(hass, connection, msg, lambda d: d.coverage())


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homelable/docs/search",
        vol.Required("q"): str,
        vol.Optional("limit", default=25): vol.All(int, vol.Range(min=1, max=100)),
    }
)
@websocket_api.async_response
async def ws_docs_search(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    await _run(hass, connection, msg, lambda d: d.search(msg["q"], msg["limit"]))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homelable/docs/block",
        vol.Required("block"): str,
        vol.Required("device_id"): str,
    }
)
@websocket_api.async_response
async def ws_docs_block(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    await _run(hass, connection, msg, lambda d: d.block(msg["block"], msg["device_id"]))


@websocket_api.websocket_command({vol.Required("type"): "homelable/docs/export"})
@websocket_api.async_response
async def ws_docs_export(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    await _run(hass, connection, msg, lambda d: d.export())


@websocket_api.websocket_command(
    {vol.Required("type"): "homelable/docs/revisions", vol.Required("document_id"): str}
)
@websocket_api.async_response
async def ws_docs_revisions(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    await _run(hass, connection, msg, lambda d: d.revisions(msg["document_id"]))


@websocket_api.websocket_command(
    {vol.Required("type"): "homelable/docs/revision", vol.Required("revision_id"): str}
)
@websocket_api.async_response
async def ws_docs_revision(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    await _run(hass, connection, msg, lambda d: d.revision(msg["revision_id"]))


@websocket_api.websocket_command(
    {vol.Required("type"): "homelable/docs/backlinks", vol.Required("document_id"): str}
)
@websocket_api.async_response
async def ws_docs_backlinks(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    await _run(hass, connection, msg, lambda d: d.backlinks(msg["document_id"]))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homelable/docs/update_preview",
        vol.Required("document_id"): str,
        vol.Optional("resolutions", default=list): [_RESOLUTION],
    }
)
@websocket_api.async_response
async def ws_docs_update_preview(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Read-only: the merge a device update would make, resolutions folded in."""
    await _run(
        hass, connection, msg, lambda d: d.update_preview(msg["document_id"], msg["resolutions"])
    )


# ── writes ──────────────────────────────────────────────────────────────────


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homelable/docs/create",
        vol.Required("title"): str,
        vol.Optional("kind", default="page"): str,
        vol.Optional("icon"): _OPT_STR,
        vol.Optional("parent_id"): _OPT_STR,
        vol.Optional("device_id"): _OPT_STR,
        vol.Optional("node_id"): _OPT_STR,
        vol.Optional("design_id"): _OPT_STR,
        vol.Optional("template_id"): _OPT_STR,
        vol.Optional("body"): _OPT_STR,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_docs_create(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    payload = {k: v for k, v in msg.items() if k not in ("id", "type")}
    await _run(hass, connection, msg, lambda d: d.create(payload))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homelable/docs/update",
        vol.Required("document_id"): str,
        vol.Optional("title"): _OPT_STR,
        vol.Optional("icon"): _OPT_STR,
        vol.Optional("body"): _OPT_STR,
        vol.Optional("parent_id"): _OPT_STR,
        vol.Optional("sort_order"): vol.Any(int, None),
        vol.Optional("starred"): vol.Any(bool, None),
        vol.Optional("is_homepage"): vol.Any(bool, None),
        vol.Optional("reviewed"): vol.Any(bool, None),
        vol.Optional("resync_facts"): vol.Any(bool, None),
        vol.Optional("revision_reason"): _OPT_STR,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_docs_update(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Partial: only the keys sent are applied. ``parent_id: null`` files to the root."""
    payload = {k: v for k, v in msg.items() if k not in ("id", "type", "document_id")}
    await _run(hass, connection, msg, lambda d: d.update(msg["document_id"], payload))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homelable/docs/restore",
        vol.Required("document_id"): str,
        vol.Required("revision_id"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_docs_restore(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    await _run(hass, connection, msg, lambda d: d.restore(msg["document_id"], msg["revision_id"]))


@websocket_api.websocket_command(
    {vol.Required("type"): "homelable/docs/regenerate", vol.Required("document_id"): str}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_docs_regenerate(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    await _run(hass, connection, msg, lambda d: d.regenerate(msg["document_id"]))


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homelable/docs/update_from_device",
        vol.Required("document_id"): str,
        vol.Required("preview_id"): str,
        vol.Optional("resolutions", default=list): [_RESOLUTION],
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_docs_update_from_device(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    await _run(
        hass,
        connection,
        msg,
        lambda d: d.update_from_device(msg["document_id"], msg["preview_id"], msg["resolutions"]),
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "homelable/docs/scaffold",
        vol.Optional("device_ids"): vol.Any([str], None),
        vol.Optional("only_with_notes", default=False): bool,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_docs_scaffold(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    await _run(
        hass,
        connection,
        msg,
        lambda d: d.scaffold(msg.get("device_ids"), only_with_notes=msg["only_with_notes"]),
    )


@websocket_api.websocket_command(
    {vol.Required("type"): "homelable/docs/delete", vol.Required("document_id"): str}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_docs_delete(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    async def call(d: DocumentManager) -> dict[str, bool]:
        await d.delete(msg["document_id"])
        return {"deleted": True}

    await _run(hass, connection, msg, call)
