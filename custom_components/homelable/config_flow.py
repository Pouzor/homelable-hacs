"""Config flow for Homelable."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback

from .const import (
    CONF_SCAN_INTERVAL,
    CONF_SCAN_RANGES,
    CONF_STATUS_INTERVAL,
    CONF_ZIGBEE_BASE_TOPIC,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SCAN_RANGES,
    DEFAULT_STATUS_INTERVAL,
    DEFAULT_ZIGBEE_BASE_TOPIC,
    DOMAIN,
)


class HomelableConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Homelable."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            return self.async_create_entry(title="Homelable", data=user_input)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_RANGES, default=",".join(DEFAULT_SCAN_RANGES)
                ): str,
                vol.Required(
                    CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL
                ): int,
                vol.Required(
                    CONF_STATUS_INTERVAL, default=DEFAULT_STATUS_INTERVAL
                ): int,
                vol.Optional(
                    CONF_ZIGBEE_BASE_TOPIC, default=DEFAULT_ZIGBEE_BASE_TOPIC
                ): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        """Get the options flow."""
        return HomelableOptionsFlow(config_entry)


class HomelableOptionsFlow(OptionsFlow):
    """Options flow for Homelable."""

    # `self.config_entry` is auto-populated by the base class on modern HA.
    # Don't assign it in __init__ — it's a read-only property in HA ≥ 2024.12.

    def __init__(self, config_entry) -> None:  # noqa: ARG002
        pass

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        data = {**self.config_entry.data, **self.config_entry.options}
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_RANGES,
                    default=data.get(CONF_SCAN_RANGES, ",".join(DEFAULT_SCAN_RANGES)),
                ): str,
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): int,
                vol.Required(
                    CONF_STATUS_INTERVAL,
                    default=data.get(CONF_STATUS_INTERVAL, DEFAULT_STATUS_INTERVAL),
                ): int,
                vol.Optional(
                    CONF_ZIGBEE_BASE_TOPIC,
                    default=data.get(
                        CONF_ZIGBEE_BASE_TOPIC, DEFAULT_ZIGBEE_BASE_TOPIC
                    ),
                ): str,
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
