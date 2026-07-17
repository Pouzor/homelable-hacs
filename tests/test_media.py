"""Tests for the media upload/serve/delete endpoint (PR #207 floor-plan port)."""
import pytest
from aiohttp import FormData
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.homelable.media import (
    NAME_RE,
    async_register_media,
    delete_media,
    media_path,
)
from custom_components.homelable.websocket import async_register_websocket_commands

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPG = b"\xff\xd8\xff" + b"\x00" * 32


@pytest.fixture
async def media_client(hass: HomeAssistant, hass_client):  # noqa: ANN001
    """HTTP client with http set up and the media view/static path registered."""
    assert await async_setup_component(hass, "http", {})
    await async_register_media(hass)
    await hass.async_block_till_done()
    return await hass_client()


async def _upload(client, data: bytes, content_type: str, filename: str = "plan.png"):  # noqa: ANN001
    form = FormData()
    form.add_field("file", data, filename=filename, content_type=content_type)
    return await client.post("/api/homelable/media/upload", data=form)


# ─── Name validation / delete_media unit ──────────────────────────────────────


def test_name_re_only_matches_server_generated_names() -> None:
    assert NAME_RE.match("0123456789abcdef0123456789abcdef.png")
    assert NAME_RE.match("0123456789abcdef0123456789abcdef.jpg")
    assert not NAME_RE.match("../../etc/passwd")
    assert not NAME_RE.match("evil.png")
    assert not NAME_RE.match("0123456789abcdef0123456789abcdef.gif")


async def test_delete_media_rejects_bad_names(hass: HomeAssistant) -> None:
    assert delete_media(hass, "../secret") is False
    assert delete_media(hass, "not-a-uuid.png") is False


# ─── Upload / serve ───────────────────────────────────────────────────────────


async def test_upload_stores_file_and_serves_it(
    hass: HomeAssistant, media_client  # noqa: ANN001
) -> None:
    resp = await _upload(media_client, PNG, "image/png")
    assert resp.status == 200
    body = await resp.json()
    assert body["url"] == f"/homelable_media/{body['filename']}"
    assert NAME_RE.match(body["filename"])

    # Written to disk under the config dir.
    on_disk = media_path(hass) / body["filename"]
    assert on_disk.is_file()
    assert on_disk.read_bytes() == PNG

    # And served back over the public static path.
    served = await media_client.get(body["url"])
    assert served.status == 200
    assert await served.read() == PNG


async def test_upload_accepts_jpeg(hass: HomeAssistant, media_client) -> None:  # noqa: ANN001
    resp = await _upload(media_client, JPG, "image/jpeg", filename="p.jpg")
    assert resp.status == 200
    assert (await resp.json())["filename"].endswith(".jpg")


async def test_upload_rejects_unsupported_type(media_client) -> None:  # noqa: ANN001
    resp = await _upload(media_client, b"GIF89a", "image/gif", filename="x.gif")
    assert resp.status == 415


async def test_upload_rejects_content_not_matching_type(media_client) -> None:  # noqa: ANN001
    # Declares PNG but the bytes are not a PNG (magic-byte check).
    resp = await _upload(media_client, b"not-a-real-png", "image/png")
    assert resp.status == 415


async def test_upload_rejects_empty_file(media_client) -> None:  # noqa: ANN001
    resp = await _upload(media_client, b"", "image/png")
    assert resp.status == 400


# ─── WebSocket delete ─────────────────────────────────────────────────────────


async def test_ws_media_delete_removes_file(
    hass: HomeAssistant, hass_ws_client  # noqa: ANN001
) -> None:
    async_register_websocket_commands(hass)
    # Seed a real file with a valid server-style name (avoids mixing an HTTP and
    # a WS client in one test, which freezes the aiohttp router).
    media_dir = media_path(hass)
    media_dir.mkdir(parents=True, exist_ok=True)
    filename = "0123456789abcdef0123456789abcdef.png"
    (media_dir / filename).write_bytes(PNG)

    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "homelable/media/delete", "filename": filename})
    msg = await ws.receive_json()
    assert msg["success"] is True
    assert msg["result"]["ok"] is True
    assert not (media_dir / filename).is_file()


async def test_ws_media_delete_bad_name_is_noop(
    hass: HomeAssistant, hass_ws_client  # noqa: ANN001
) -> None:
    async_register_websocket_commands(hass)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "homelable/media/delete", "filename": "../evil"})
    msg = await ws.receive_json()
    assert msg["success"] is True
    assert msg["result"]["ok"] is False
