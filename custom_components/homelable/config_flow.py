"""Config flow for Homelable."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback

from .const import (
    CONF_PROXMOX_HOST,
    CONF_PROXMOX_PORT,
    CONF_PROXMOX_SYNC_ENABLED,
    CONF_PROXMOX_SYNC_INTERVAL,
    CONF_PROXMOX_TOKEN_ID,
    CONF_PROXMOX_TOKEN_SECRET,
    CONF_PROXMOX_VERIFY_TLS,
    CONF_SCAN_AUTO_ENABLED,
    CONF_SCAN_INTERVAL,
    CONF_SCAN_RANGES,
    CONF_SERVICE_CHECK_ENABLED,
    CONF_SERVICE_CHECK_INTERVAL,
    CONF_STATUS_INTERVAL,
    CONF_ZIGBEE_BASE_TOPIC,
    CONF_ZWAVE_GATEWAY,
    CONF_ZWAVE_PREFIX,
    DEFAULT_PROXMOX_PORT,
    DEFAULT_PROXMOX_SYNC_ENABLED,
    DEFAULT_PROXMOX_SYNC_INTERVAL,
    DEFAULT_PROXMOX_VERIFY_TLS,
    DEFAULT_SCAN_AUTO_ENABLED,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SCAN_RANGES,
    DEFAULT_SERVICE_CHECK_ENABLED,
    DEFAULT_SERVICE_CHECK_INTERVAL,
    DEFAULT_STATUS_INTERVAL,
    DEFAULT_ZIGBEE_BASE_TOPIC,
    DEFAULT_ZWAVE_GATEWAY,
    DEFAULT_ZWAVE_PREFIX,
    DOMAIN,
    MIN_PROXMOX_SYNC_INTERVAL,
    MIN_SCAN_INTERVAL,
    MIN_SERVICE_CHECK_INTERVAL,
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
                    CONF_SCAN_AUTO_ENABLED, default=DEFAULT_SCAN_AUTO_ENABLED
                ): bool,
                vol.Required(
                    CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL
                ): vol.All(int, vol.Range(min=MIN_SCAN_INTERVAL)),
                vol.Required(
                    CONF_STATUS_INTERVAL, default=DEFAULT_STATUS_INTERVAL
                ): int,
                vol.Optional(
                    CONF_ZIGBEE_BASE_TOPIC, default=DEFAULT_ZIGBEE_BASE_TOPIC
                ): str,
                vol.Optional(
                    CONF_ZWAVE_PREFIX, default=DEFAULT_ZWAVE_PREFIX
                ): str,
                vol.Optional(
                    CONF_ZWAVE_GATEWAY, default=DEFAULT_ZWAVE_GATEWAY
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
        data = {**self.config_entry.data, **self.config_entry.options}

        if user_input is not None:
            # A blank token secret means "keep the one already stored" — the form
            # never echoes the secret back, so an empty submit must not wipe it.
            if not user_input.get(CONF_PROXMOX_TOKEN_SECRET):
                user_input[CONF_PROXMOX_TOKEN_SECRET] = data.get(
                    CONF_PROXMOX_TOKEN_SECRET, ""
                )
            return self.async_create_entry(title="", data=user_input)
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_RANGES,
                    default=data.get(CONF_SCAN_RANGES, ",".join(DEFAULT_SCAN_RANGES)),
                ): str,
                vol.Required(
                    CONF_SCAN_AUTO_ENABLED,
                    default=data.get(
                        CONF_SCAN_AUTO_ENABLED, DEFAULT_SCAN_AUTO_ENABLED
                    ),
                ): bool,
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(int, vol.Range(min=MIN_SCAN_INTERVAL)),
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
                vol.Optional(
                    CONF_ZWAVE_PREFIX,
                    default=data.get(CONF_ZWAVE_PREFIX, DEFAULT_ZWAVE_PREFIX),
                ): str,
                vol.Optional(
                    CONF_ZWAVE_GATEWAY,
                    default=data.get(CONF_ZWAVE_GATEWAY, DEFAULT_ZWAVE_GATEWAY),
                ): str,
                vol.Required(
                    CONF_SERVICE_CHECK_ENABLED,
                    default=data.get(
                        CONF_SERVICE_CHECK_ENABLED, DEFAULT_SERVICE_CHECK_ENABLED
                    ),
                ): bool,
                vol.Required(
                    CONF_SERVICE_CHECK_INTERVAL,
                    default=data.get(
                        CONF_SERVICE_CHECK_INTERVAL, DEFAULT_SERVICE_CHECK_INTERVAL
                    ),
                ): vol.All(int, vol.Range(min=MIN_SERVICE_CHECK_INTERVAL)),
                # Proxmox VE import. Token is a real credential; leave the secret
                # blank to keep the one already stored. Auto-sync re-imports the
                # inventory into pending on the given interval when enabled.
                vol.Optional(
                    CONF_PROXMOX_HOST,
                    default=data.get(CONF_PROXMOX_HOST, ""),
                ): str,
                vol.Optional(
                    CONF_PROXMOX_PORT,
                    default=data.get(CONF_PROXMOX_PORT, DEFAULT_PROXMOX_PORT),
                ): vol.All(int, vol.Range(min=1, max=65535)),
                vol.Optional(
                    CONF_PROXMOX_TOKEN_ID,
                    default=data.get(CONF_PROXMOX_TOKEN_ID, ""),
                ): str,
                vol.Optional(
                    CONF_PROXMOX_TOKEN_SECRET,
                    default="",
                ): str,
                vol.Required(
                    CONF_PROXMOX_VERIFY_TLS,
                    default=data.get(
                        CONF_PROXMOX_VERIFY_TLS, DEFAULT_PROXMOX_VERIFY_TLS
                    ),
                ): bool,
                vol.Required(
                    CONF_PROXMOX_SYNC_ENABLED,
                    default=data.get(
                        CONF_PROXMOX_SYNC_ENABLED, DEFAULT_PROXMOX_SYNC_ENABLED
                    ),
                ): bool,
                vol.Required(
                    CONF_PROXMOX_SYNC_INTERVAL,
                    default=data.get(
                        CONF_PROXMOX_SYNC_INTERVAL, DEFAULT_PROXMOX_SYNC_INTERVAL
                    ),
                ): vol.All(int, vol.Range(min=MIN_PROXMOX_SYNC_INTERVAL)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
