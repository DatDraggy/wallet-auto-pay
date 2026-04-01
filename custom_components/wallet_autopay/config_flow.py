"""Config flow for Wallet Auto-Pay integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    DOMAIN,
    CONF_DEVICE_ID,
    CONF_TARGET_IBAN,
    CONF_RECIPIENT_NAME,
    CONF_PACKAGE,
    CONF_TODO_ONLY,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DEVICE_ID): selector.DeviceSelector(
            selector.DeviceSelectorConfig(integration="mobile_app")
        ),
        vol.Required(CONF_TARGET_IBAN): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
        ),
        vol.Required(CONF_RECIPIENT_NAME): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
        ),
        vol.Optional(CONF_PACKAGE): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
        ),
        vol.Optional(CONF_TODO_ONLY, default=False): selector.BooleanSelector(),
    }
)


class WalletAutoPayConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Wallet Auto-Pay."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        if user_input is not None:
            title = f"Wallet Pay: {user_input[CONF_RECIPIENT_NAME]}"
            return self.async_create_entry(
                title=title, 
                data=user_input
            )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> WalletAutoPayOptionsFlowHandler:
        """Get the options flow for this handler."""
        return WalletAutoPayOptionsFlowHandler(config_entry)


class WalletAutoPayOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle Wallet Auto-Pay options."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        data = self.config_entry.options if self.config_entry.options else self.config_entry.data
        
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_TARGET_IBAN,
                        default=data.get(CONF_TARGET_IBAN, ""),
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                    ),
                    vol.Required(
                        CONF_RECIPIENT_NAME,
                        default=data.get(CONF_RECIPIENT_NAME, ""),
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                    ),
                    vol.Optional(
                        CONF_PACKAGE,
                        default=data.get(CONF_PACKAGE, "de.fiduciagad.direkt1822.banking"),
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                    ),
                    vol.Optional(
                        CONF_TODO_ONLY,
                        default=data.get(CONF_TODO_ONLY, False),
                    ): selector.BooleanSelector(),
                }
            )
        )
