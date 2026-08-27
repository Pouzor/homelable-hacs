"""Frontend registration: the Lovelace panel and the canvas card.

The bundles are build output (gitignored), so every test points `_FRONTEND_DIR`
at a tmp_path holding whatever files the case is about.
"""
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component

from custom_components.homelable import panel
from custom_components.homelable.const import (
    CARD_REGISTERED_KEY,
    FRONTEND_STATIC_KEY,
    PANEL_URL,
)


@pytest.fixture(autouse=True)
async def http_component(hass: HomeAssistant) -> None:
    """`hass.http` only exists once the http component is set up."""
    assert await async_setup_component(hass, "http", {})


@pytest.fixture
def frontend_dir(tmp_path: Path):
    """Point the integration at an empty frontend directory."""
    with patch.object(panel, "_FRONTEND_DIR", tmp_path):
        yield tmp_path


def _bundle(directory: Path, name: str) -> str:
    (directory / name).write_text("export default 1")
    return name


async def test_card_bundle_is_added_to_the_frontend_modules(
    hass: HomeAssistant, frontend_dir: Path
) -> None:
    """Registering the card is what makes custom:homelable-canvas-card resolve."""
    name = _bundle(frontend_dir, "homelable-card-abc123.js")

    with patch.object(panel.frontend, "add_extra_js_url") as add_url:
        await panel.async_register_card(hass)

    add_url.assert_called_once_with(hass, f"{PANEL_URL}/{name}")
    assert hass.data[CARD_REGISTERED_KEY] == f"{PANEL_URL}/{name}"


async def test_card_registration_is_idempotent(
    hass: HomeAssistant, frontend_dir: Path
) -> None:
    """A second config entry must not register the module twice."""
    _bundle(frontend_dir, "homelable-card-abc123.js")

    with patch.object(panel.frontend, "add_extra_js_url") as add_url:
        await panel.async_register_card(hass)
        await panel.async_register_card(hass)

    assert add_url.call_count == 1


async def test_missing_card_bundle_warns_and_registers_nothing(
    hass: HomeAssistant, frontend_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A source checkout without a build must not break setup."""
    with patch.object(panel.frontend, "add_extra_js_url") as add_url:
        await panel.async_register_card(hass)

    add_url.assert_not_called()
    assert CARD_REGISTERED_KEY not in hass.data
    assert "npm run build:ha" in caplog.text


async def test_unregister_card_removes_the_module(
    hass: HomeAssistant, frontend_dir: Path
) -> None:
    """Unloading the last entry stops serving the card."""
    name = _bundle(frontend_dir, "homelable-card-abc123.js")

    with patch.object(panel.frontend, "add_extra_js_url"):
        await panel.async_register_card(hass)
    with patch.object(panel.frontend, "remove_extra_js_url") as remove_url:
        panel.async_unregister_card(hass)

    remove_url.assert_called_once_with(hass, f"{PANEL_URL}/{name}")
    assert CARD_REGISTERED_KEY not in hass.data


async def test_unregister_card_without_registration_is_a_no_op(
    hass: HomeAssistant,
) -> None:
    """Setup can fail before the card registers; unload still runs."""
    with patch.object(panel.frontend, "remove_extra_js_url") as remove_url:
        panel.async_unregister_card(hass)

    remove_url.assert_not_called()


async def test_the_static_path_is_registered_once_for_both_bundles(
    hass: HomeAssistant, frontend_dir: Path
) -> None:
    """Panel and card share one directory; two routes on it would collide."""
    _bundle(frontend_dir, "homelable-panel-abc123.js")
    _bundle(frontend_dir, "homelable-card-def456.js")

    with (
        patch.object(
            hass.http, "async_register_static_paths", return_value=None
        ) as register_static,
        patch.object(panel.frontend, "add_extra_js_url"),
        patch.object(panel.frontend, "async_register_built_in_panel"),
    ):
        await panel.async_register_panel(hass)
        await panel.async_register_card(hass)

    assert register_static.call_count == 1
    assert hass.data[FRONTEND_STATIC_KEY] is True


async def test_the_card_is_served_even_without_a_panel_bundle(
    hass: HomeAssistant, frontend_dir: Path
) -> None:
    """The panel bails out before the static path; the card must still get one."""
    _bundle(frontend_dir, "homelable-card-def456.js")

    with (
        patch.object(
            hass.http, "async_register_static_paths", return_value=None
        ) as register_static,
        patch.object(panel.frontend, "add_extra_js_url"),
        patch.object(panel.frontend, "async_register_built_in_panel") as register_panel,
    ):
        await panel.async_register_panel(hass)
        await panel.async_register_card(hass)

    register_panel.assert_not_called()
    assert register_static.call_count == 1


async def test_panel_registration_uses_the_hashed_bundle(
    hass: HomeAssistant, frontend_dir: Path
) -> None:
    """The panel's module_url must point at the built bundle, not a fixed name."""
    name = _bundle(frontend_dir, "homelable-panel-abc123.js")
    _bundle(frontend_dir, "homelable-card-def456.js")

    with (
        patch.object(hass.http, "async_register_static_paths", return_value=None),
        patch.object(panel.frontend, "async_register_built_in_panel") as register_panel,
    ):
        await panel.async_register_panel(hass)

    config = register_panel.call_args.kwargs["config"]
    assert config["_panel_custom"]["module_url"] == f"{PANEL_URL}/{name}"
