"""The FinTS Auto-Pay integration."""
from __future__ import annotations

import logging
import re
import uuid
from decimal import Decimal

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, State
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.components.notify import DOMAIN as NOTIFY_DOMAIN

from fints.client import FinTS3PinTanClient
from fints.exceptions import FinTSError

from .const import (
    DOMAIN,
    CONF_SENSOR,
    CONF_NOTIFY,
    CONF_TODO,
    CONF_BLZ,
    CONF_USERNAME,
    CONF_PIN,
    CONF_ENDPOINT,
    CONF_TARGET_IBAN,
    CONF_RECIPIENT_NAME,
)

import asyncio
from datetime import timedelta
from homeassistant.helpers.event import async_call_later

_LOGGER = logging.getLogger(__name__)

EVENT_MOBILE_APP_NOTIFICATION_ACTION = "mobile_app_notification_action"
ACTION_PAY_NOW = "FINTS_PAY"
ACTION_ADD_TODO = "FINTS_TODO"
DEFAULT_FALLBACK_TIMEOUT = 3600  # 1 hour


async def async_add_to_todo(hass: HomeAssistant, entry_id: str, amount: Decimal, merchant: str) -> None:
    """Add a transaction to the configured To-Do list."""
    # We need to find the entry to get the todo_entity
    entry = hass.config_entries.async_get_entry(entry_id)
    if not entry:
        return
    
    todo_entity = entry.data[CONF_TODO]
    try:
        await hass.services.async_call(
            "todo",
            "add_item",
            {"entity_id": todo_entity, "item": f"Pay {amount}€ for {merchant}"},
        )
        _LOGGER.info("Automatically added %s€ for %s to To-Do list (fallback)", amount, merchant)
    except Exception as e:
        _LOGGER.error("Fallback: Adding to todo list failed: %s", e)


def execute_fints_transfer(config_data: dict, amount: Decimal, merchant: str) -> None:
    """Execute a synchronous SEPA transfer via FinTS."""
    client = FinTS3PinTanClient(
        config_data[CONF_BLZ],
        config_data[CONF_USERNAME],
        config_data[CONF_PIN],
        config_data[CONF_ENDPOINT],
    )

    with client:
        accounts = client.get_sepa_accounts()
        if not accounts:
            raise ValueError("No SEPA accounts found to send money from.")

        # Pick the first account as the source
        account = accounts[0]

        client.simple_sepa_transfer(
            account=account,
            iban=config_data[CONF_TARGET_IBAN],
            bic="",
            recipient_name=config_data[CONF_RECIPIENT_NAME],
            amount=amount,
            account_name="Checking Account",
            reason=f"Auto-Pay {merchant}",
        )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up FinTS Auto-Pay from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "pending": {},
        "listeners": [],
    }

    notification_sensor = entry.data[CONF_SENSOR]

    async def _handle_state_change(event: Event) -> None:
        """Handle state changes of the notification sensor."""
        new_state = event.data.get("new_state")
        if not new_state:
            return

        attributes = new_state.attributes
        package = attributes.get("package")
        if package != "com.google.android.apps.walletnfcrel":
            return

        android_text = attributes.get("android.text", "")
        # Common Google Wallet text: "Paid € 12.34 at Starbucks" or "12,34 € bei Rewe"
        match = re.search(
            r"(?:€|EUR)?\s*(\d+(?:[.,]\d{1,2})?)\s*(?:€|EUR)?\s*(?:at|bei)\s+(.+)",
            android_text,
            re.IGNORECASE,
        )
        if match:
            amount_str = match.group(1).replace(",", ".")
            merchant = match.group(2).strip()
            amount = Decimal(amount_str)

            txn_id = uuid.uuid4().hex
            hass.data[DOMAIN][entry.entry_id]["pending"][txn_id] = {
                "amount": amount,
                "merchant": merchant,
            }

            async def _fallback_to_todo(_now):
                """Fallback if no action taken."""
                pending = hass.data[DOMAIN][entry.entry_id]["pending"].pop(txn_id, None)
                if pending:
                    await async_add_to_todo(hass, entry.entry_id, pending["amount"], pending["merchant"])

            async_call_later(hass, DEFAULT_FALLBACK_TIMEOUT, _fallback_to_todo)

            notify_service = entry.data[CONF_NOTIFY]
            domain_service = notify_service.split(".", 1)

            if len(domain_service) == 2:
                svc_domain, svc_name = domain_service
            else:
                svc_domain, svc_name = NOTIFY_DOMAIN, notify_service

            action_pay = f"{ACTION_PAY_NOW}::{entry.entry_id}::{txn_id}"
            action_todo = f"{ACTION_ADD_TODO}::{entry.entry_id}::{txn_id}"

            await hass.services.async_call(
                svc_domain,
                svc_name,
                {
                    "message": f"Google Wallet: {amount}€ at {merchant}. Pay now from checking?",
                    "title": "FinTS Auto-Pay",
                    "data": {
                        "actions": [
                            {"action": action_pay, "title": "Pay Now"},
                            {"action": action_todo, "title": "Add to To-Do List"},
                        ]
                    },
                },
            )

    state_listener = async_track_state_change_event(
        hass, notification_sensor, _handle_state_change
    )
    hass.data[DOMAIN][entry.entry_id]["listeners"].append(state_listener)

    async def _handle_notification_action(event: Event) -> None:
        """Handle actionable notification button presses."""
        action = event.data.get("action", "")

        if action.startswith(ACTION_PAY_NOW) or action.startswith(ACTION_ADD_TODO):
            parts = action.split("::")
            if len(parts) < 3:
                return

            txn_id = parts[-1]
            entry_id = parts[-2]

            if entry_id != entry.entry_id:
                return

            pending = hass.data[DOMAIN][entry.entry_id]["pending"].pop(txn_id, None)
            if not pending:
                _LOGGER.warning("Transaction %s not found or already processed", txn_id)
                return

            amount = pending["amount"]
            merchant = pending["merchant"]

            if action.startswith(ACTION_PAY_NOW):
                try:
                    _LOGGER.info(
                        "Executing FinTS transfer for %s€ to %s", amount, merchant
                    )
                    await hass.async_add_executor_job(
                        execute_fints_transfer, entry.data, amount, merchant
                    )
                    _LOGGER.info("Transfer successful!")
                except Exception as e:
                    _LOGGER.error("Transfer failed: %s", e)

            elif action.startswith(ACTION_ADD_TODO):
                todo_entity = entry.data[CONF_TODO]
                try:
                    await hass.services.async_call(
                        "todo",
                        "add_item",
                        {"entity_id": todo_entity, "item": f"Pay {amount}€ for {merchant}"},
                    )
                except Exception as e:
                    _LOGGER.error("Adding to todo list failed: %s", e)

    action_listener = hass.bus.async_listen(
        EVENT_MOBILE_APP_NOTIFICATION_ACTION, _handle_notification_action
    )
    hass.data[DOMAIN][entry.entry_id]["listeners"].append(action_listener)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    for listener in hass.data[DOMAIN][entry.entry_id]["listeners"]:
        # Unsubscribe listener
        listener()

    hass.data[DOMAIN].pop(entry.entry_id)
    return True
