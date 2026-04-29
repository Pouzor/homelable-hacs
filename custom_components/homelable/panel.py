"""Lovelace panel registration for Homelable."""
from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components import frontend
from homeassistant.core import HomeAssistant

from .const import DOMAIN, PANEL_ICON, PANEL_NAME, PANEL_TITLE, PANEL_URL

_LOGGER = logging.getLogger(__name__)

_FRONTEND_DIR = Path(__file__).parent / "frontend"


async def async_register_panel(hass: HomeAssistant) -> None:
    """Serve frontend bundle and register the Lovelace panel."""
    if DOMAIN in hass.data.get("frontend_panels", {}):
        return

    bundle_files = list(_FRONTEND_DIR.glob("homelable-panel-*.js"))
    if not bundle_files:
        _LOGGER.warning(
            "No frontend bundle found in %s. Run `npm run build:ha` in frontend-src/.",
            _FRONTEND_DIR,
        )
        return

    bundle_path = bundle_files[0]
    bundle_name = bundle_path.name

    await hass.http.async_register_static_paths(
        [
            frontend.StaticPathConfig(
                PANEL_URL, str(_FRONTEND_DIR), cache_headers=True
            )
        ]
    )

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
                "module_url": f"{PANEL_URL}/{bundle_name}",
            }
        },
        require_admin=False,
    )


def async_unregister_panel(hass: HomeAssistant) -> None:
    """Remove the Lovelace panel."""
    frontend.async_remove_panel(hass, DOMAIN)
