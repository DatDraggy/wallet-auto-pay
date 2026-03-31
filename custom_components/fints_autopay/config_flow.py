"""Config flow for FinTS Auto-Pay integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from fints.client import FinTS3PinTanClient
from fints.exceptions import FinTSError

from .const import (
    DOMAIN,
    CONF_DEVICE_ID,
    CONF_BLZ,
    CONF_USERNAME,
    CONF_PIN,
    CONF_ENDPOINT,
    CONF_TARGET_IBAN,
    CONF_RECIPIENT_NAME,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_DEVICE_ID): selector.DeviceSelector(
            selector.DeviceSelectorConfig(integration="mobile_app")
        ),
        vol.Required(CONF_BLZ): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
        ),
        vol.Required(CONF_USERNAME): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
        ),
        vol.Required(CONF_PIN): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        ),
        vol.Required(CONF_ENDPOINT): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.URL)
        ),
        vol.Required(CONF_TARGET_IBAN): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
        ),
        vol.Required(CONF_RECIPIENT_NAME): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
        ),
    }
)


def validate_fints_login(blz: str, username: str, pin: str, endpoint: str) -> None:
    """Validate FinTS credentials."""
    client = FinTS3PinTanClient(blz, username, pin, endpoint)
    with client:
        client.get_sepa_accounts()


class FintsAutoPayConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for FinTS Auto-Pay."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                await self.hass.async_add_executor_job(
                    validate_fints_login,
                    user_input[CONF_BLZ],
                    user_input[CONF_USERNAME],
                    user_input[CONF_PIN],
                    user_input[CONF_ENDPOINT],
                )
            except FinTSError as err:
                _LOGGER.error("FinTS Error: %s", err)
                errors["base"] = "invalid_auth"
            except Exception as err:
                _LOGGER.error("Unexpected error: %s", err)
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=f"FinTS Pay: {user_input[CONF_USERNAME]}", data=user_input
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )
