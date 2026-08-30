"""Tests for integration setup / unload wiring."""
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.homelable import async_unload_entry, scanner
from custom_components.homelable.const import DOMAIN


@pytest.fixture(autouse=True)
def drain_scan_executor():
    """Join the scanner's own thread pool so HA's leak check stays meaningful."""
    yield
    executor = scanner._executor
    scanner.shutdown_executor()
    if executor is not None:
        executor.shutdown(wait=True)


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    """A config entry already registered in `hass.data[DOMAIN]`."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = MagicMock()
    return entry


async def test_unload_keeps_the_scan_pool_while_another_entry_lives(
    hass: HomeAssistant,
) -> None:
    """Two entries, one unloaded: the other is still scanning (issue #88)."""
    first, second = _entry(hass), _entry(hass)
    await scanner._run_offloop(lambda: None)
    pool = scanner._executor
    assert pool is not None

    with patch("custom_components.homelable.async_unregister_panel") as unregister:
        assert await async_unload_entry(hass, first) is True

    assert scanner._executor is pool
    unregister.assert_not_called()
    assert set(hass.data[DOMAIN]) == {second.entry_id}


async def test_unload_drops_the_scan_pool_with_the_last_entry(
    hass: HomeAssistant,
) -> None:
    """Nothing left to scan for: the pool goes with the last entry."""
    first, second = _entry(hass), _entry(hass)
    await scanner._run_offloop(lambda: None)
    assert scanner._executor is not None

    with patch("custom_components.homelable.async_unregister_panel") as unregister:
        assert await async_unload_entry(hass, first) is True
        assert await async_unload_entry(hass, second) is True

    assert scanner._executor is None
    unregister.assert_called_once()
    assert hass.data[DOMAIN] == {}


async def test_unload_stops_serving_the_card_with_the_last_entry(
    hass: HomeAssistant,
) -> None:
    """The card module is process-global: it goes when the last entry does."""
    entry = _entry(hass)

    with (
        patch("custom_components.homelable.async_unregister_panel"),
        patch("custom_components.homelable.async_unregister_card") as unregister_card,
    ):
        assert await async_unload_entry(hass, entry) is True

    unregister_card.assert_called_once_with(hass)
