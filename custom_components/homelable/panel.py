"""Frontend registration for Homelable: the Lovelace panel and the card.

Both bundles are built by `npm run build:ha` into ``frontend/`` and served from
the same public static path — hashed filenames, like HA serves its own frontend.
The panel is registered as a `custom` panel; the card is added to the frontend's
extra module URLs so Lovelace can resolve `custom:homelable-canvas-card`.
"""
from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components import frontend
from homeassistant.core import HomeAssistant

from .const import (
    CARD_REGISTERED_KEY,
    DOMAIN,
    FRONTEND_STATIC_KEY,
    PANEL_ICON,
    PANEL_NAME,
    PANEL_TITLE,
    PANEL_URL,
)

_LOGGER = logging.getLogger(__name__)

_FRONTEND_DIR = Path(__file__).parent / "frontend"

_BUILD_HINT = "Run `npm run build:ha` in frontend-src/."


def _find_bundle(prefix: str) -> Path | None:
    """Newest-first is meaningless here — a clean build leaves exactly one."""
    files = list(_FRONTEND_DIR.glob(f"{prefix}-*.js"))
    return files[0] if files else None


async def _async_register_static_path(hass: HomeAssistant) -> None:
    """Serve the frontend directory. Process-global, so only ever done once."""
    if hass.data.get(FRONTEND_STATIC_KEY):
        return
    await hass.http.async_register_static_paths(
        [frontend.StaticPathConfig(PANEL_URL, str(_FRONTEND_DIR), cache_headers=True)]
    )
    hass.data[FRONTEND_STATIC_KEY] = True


async def async_register_panel(hass: HomeAssistant) -> None:
    """Serve the frontend bundle and register the Lovelace panel."""
    if DOMAIN in hass.data.get("frontend_panels", {}):
        return

    bundle_path = await hass.async_add_executor_job(_find_bundle, "homelable-panel")
    if bundle_path is None:
        _LOGGER.warning(
            "No frontend bundle found in %s. %s", _FRONTEND_DIR, _BUILD_HINT
        )
        return

    await _async_register_static_path(hass)

    frontend.async_register_built_in_panel(
        hass,
        component_name="custom",
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        frontend_url_path=DOMAIN,
        config={
            "_panel_custom": {
                "name": PANEL_NAME,
                "embed_iframe": False,
                "trust_external": False,
                "module_url": f"{PANEL_URL}/{bundle_path.name}",
            }
        },
        require_admin=False,
    )


def async_unregister_panel(hass: HomeAssistant) -> None:
    """Remove the Lovelace panel."""
    frontend.async_remove_panel(hass, DOMAIN)


async def async_register_card(hass: HomeAssistant) -> None:
    """Load the card bundle on every HA frontend page.

    That is what `add_extra_js_url` does, which is why the card entry is built
    to stay small and to pull the canvas in only once a card is on screen.
    Registering it is what makes `custom:homelable-canvas-card` resolvable in a
    dashboard; without it Lovelace reports a missing custom element.
    """
    if hass.data.get(CARD_REGISTERED_KEY):
        return

    bundle_path = await hass.async_add_executor_job(_find_bundle, "homelable-card")
    if bundle_path is None:
        _LOGGER.warning(
            "No Lovelace card bundle found in %s — the Homelable card will not be "
            "available. %s",
            _FRONTEND_DIR,
            _BUILD_HINT,
        )
        return

    await _async_register_static_path(hass)

    url = f"{PANEL_URL}/{bundle_path.name}"
    frontend.add_extra_js_url(hass, url)
    hass.data[CARD_REGISTERED_KEY] = url


def async_unregister_card(hass: HomeAssistant) -> None:
    """Stop serving the card module. No-op when it was never registered."""
    url = hass.data.pop(CARD_REGISTERED_KEY, None)
    if url:
        frontend.remove_extra_js_url(hass, url)
