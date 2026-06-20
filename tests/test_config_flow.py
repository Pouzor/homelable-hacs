"""Test the Homelable config flow."""
from unittest.mock import patch

import pytest
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.homelable.const import (
    CONF_SCAN_INTERVAL,
    CONF_SCAN_RANGES,
    CONF_SERVICE_CHECK_ENABLED,
    CONF_SERVICE_CHECK_INTERVAL,
    CONF_STATUS_INTERVAL,
    DOMAIN,
)


async def test_user_flow_creates_entry(hass: HomeAssistant) -> None:
    """A user-initiated flow with valid input creates a config entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"

    with patch(
        "custom_components.homelable.async_setup_entry", return_value=True
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_SCAN_RANGES: "192.168.1.0/24",
                CONF_SCAN_INTERVAL: 3600,
                CONF_STATUS_INTERVAL: 60,
            },
        )
        await hass.async_block_till_done()

    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["title"] == "Homelable"


async def test_single_instance_only(hass: HomeAssistant) -> None:
    """A second config entry is rejected."""
    MockConfigEntry(domain=DOMAIN, title="Homelable", data={}).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_options_flow_saves_service_check_settings(hass: HomeAssistant) -> None:
    """The options flow exposes and persists per-service check settings."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Homelable",
        data={
            CONF_SCAN_RANGES: "192.168.1.0/24",
            CONF_SCAN_INTERVAL: 3600,
            CONF_STATUS_INTERVAL: 60,
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"

    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_SCAN_RANGES: "192.168.1.0/24",
            CONF_SCAN_INTERVAL: 3600,
            CONF_STATUS_INTERVAL: 60,
            CONF_SERVICE_CHECK_ENABLED: True,
            CONF_SERVICE_CHECK_INTERVAL: 120,
        },
    )
    assert result2["type"] == FlowResultType.CREATE_ENTRY
    assert result2["data"][CONF_SERVICE_CHECK_ENABLED] is True
    assert result2["data"][CONF_SERVICE_CHECK_INTERVAL] == 120


async def test_options_flow_rejects_sub_minimum_interval(hass: HomeAssistant) -> None:
    """Service-check interval below the minimum is rejected."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Homelable",
        data={CONF_SCAN_RANGES: "192.168.1.0/24"},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    with pytest.raises(vol.Invalid):
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_SCAN_RANGES: "192.168.1.0/24",
                CONF_SCAN_INTERVAL: 3600,
                CONF_STATUS_INTERVAL: 60,
                CONF_SERVICE_CHECK_ENABLED: True,
                CONF_SERVICE_CHECK_INTERVAL: 5,
            },
        )
