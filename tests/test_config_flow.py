"""Test the Homelable config flow."""
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.homelable.const import (
    CONF_SCAN_INTERVAL,
    CONF_SCAN_RANGES,
    CONF_STATUS_INTERVAL,
    DOMAIN,
)


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_single_instance_only(hass: HomeAssistant) -> None:
    """A second config entry is rejected."""
    config_entries.ConfigEntry(
        version=1,
        minor_version=1,
        domain=DOMAIN,
        title="Homelable",
        data={},
        source=config_entries.SOURCE_USER,
        unique_id=None,
        options={},
        discovery_keys={},
    ).add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"
