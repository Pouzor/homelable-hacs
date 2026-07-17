"""Floor-plan / generic image media: authenticated upload, static serving.

Uploaded images are written to disk under ``<config>/homelable/media`` with a
server-generated UUID filename (the client filename is never trusted) and served
via a public static path — the UUID is unguessable, matching how the panel
bundle is served. Upload requires an authenticated HA request (the panel calls
it with the user's access token). Delete rides the authenticated WS channel
(see ``websocket.py``).

Kept deliberately generic so future raw-image uploads reuse the same endpoint.
Adapted from the standalone FastAPI ``/media`` route: on disk, UUID names, magic
-byte validation and a hard size cap are preserved; the transport is an HA
``HomeAssistantView`` + static path instead of FastAPI.
"""
from __future__ import annotations

import logging
import re
import uuid
from http import HTTPStatus
from pathlib import Path

from aiohttp import web
from aiohttp.hdrs import CONTENT_TYPE
from homeassistant.components import frontend
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN, MEDIA_DIR, MEDIA_MAX_BYTES, MEDIA_REGISTERED_KEY, MEDIA_URL

_LOGGER = logging.getLogger(__name__)

# content-type → extension. Also the allowlist of accepted uploads.
_ALLOWED_TYPES: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}

# Magic-byte signatures for defense-in-depth (don't trust content-type alone).
_MAGIC: dict[str, tuple[bytes, ...]] = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".webp": (b"RIFF",),  # RIFF....WEBP; RIFF prefix is enough to reject non-images
}

# Only ever serve/delete files we created: 32 hex chars + known extension.
NAME_RE = re.compile(r"^[0-9a-f]{32}\.(png|jpg|webp)$")


def media_path(hass: HomeAssistant) -> Path:
    """On-disk folder for uploaded media (under the HA config dir)."""
    return Path(hass.config.path(MEDIA_DIR))


async def async_register_media(hass: HomeAssistant) -> None:
    """Create the media dir, register the upload view and its static serving path.

    Idempotent: the view + static path are process-global, so only the first
    config entry registers them.
    """
    if hass.data.get(MEDIA_REGISTERED_KEY):
        return
    media_dir = media_path(hass)
    await hass.async_add_executor_job(
        lambda: media_dir.mkdir(parents=True, exist_ok=True)
    )
    hass.http.register_view(HomelableMediaUploadView(media_dir))
    await hass.http.async_register_static_paths(
        [frontend.StaticPathConfig(MEDIA_URL, str(media_dir), cache_headers=True)]
    )
    hass.data[MEDIA_REGISTERED_KEY] = True


def delete_media(hass: HomeAssistant, filename: str) -> bool:
    """Delete an uploaded file. Returns True when a matching file was removed."""
    if not NAME_RE.match(filename):
        return False
    path = media_path(hass) / filename
    if path.is_file():
        path.unlink()
        return True
    return False


class HomelableMediaUploadView(HomeAssistantView):
    """POST an image (multipart ``file`` field); returns its static URL."""

    url = "/api/homelable/media/upload"
    name = f"api:{DOMAIN}:media:upload"
    requires_auth = True

    def __init__(self, media_dir: Path) -> None:
        self._media_dir = media_dir

    async def post(self, request: web.Request) -> web.Response:
        reader = await request.multipart()
        field = await reader.next()
        if field is None or field.name != "file":
            return self.json_message("Missing file field", HTTPStatus.BAD_REQUEST)

        content_type = (field.headers.get(CONTENT_TYPE) or "").split(";")[0].strip()
        ext = _ALLOWED_TYPES.get(content_type)
        if ext is None:
            return self.json_message(
                "Unsupported media type — PNG, JPEG, or WebP only",
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            )

        # Stream with a hard cap so an oversize upload never loads fully in memory.
        data = bytearray()
        while True:
            chunk = await field.read_chunk(64 * 1024)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > MEDIA_MAX_BYTES:
                return self.json_message(
                    "File too large (max 10 MB)",
                    HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                )
        if not data:
            return self.json_message("Empty file", HTTPStatus.BAD_REQUEST)
        if not any(bytes(data).startswith(sig) for sig in _MAGIC[ext]):
            return self.json_message(
                "File content does not match its type",
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
            )

        hass: HomeAssistant = request.app["hass"]
        await hass.async_add_executor_job(
            lambda: self._media_dir.mkdir(parents=True, exist_ok=True)
        )
        name = f"{uuid.uuid4().hex}{ext}"
        await hass.async_add_executor_job(
            (self._media_dir / name).write_bytes, bytes(data)
        )
        return self.json({"filename": name, "url": f"{MEDIA_URL}/{name}"})
