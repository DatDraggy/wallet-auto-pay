"""The FinTS Auto-Pay integration."""
from __future__ import annotations

import logging
import re
import uuid
from decimal import Decimal
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, State
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event, async_call_later
from homeassistant.components.notify import DOMAIN as NOTIFY_DOMAIN
from homeassistant.const import Platform

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

PLATFORMS: list[Platform] = [Platform.TODO]
EVENT_MOBILE_APP_NOTIFICATION_ACTION = "mobile_app_notification_action"
ACTION_PAY_NOW = "FINTS_PAY"
ACTION_ADD_TODO = "FINTS_TODO"
DEFAULT_FALLBACK_TIMEOUT = 3600


async def async_add_to_todo(hass: HomeAssistant, entry_id: str, amount: Decimal, merchant: str) -> None:
    """Add a transaction to the auto-created To-Do list."""
    todo_entity = f"todo.fints_auto_pay_{entry_id.replace('-', '_')}" # Fallback guess if registry lookup fails
    
    # Try to find the actual entity_id from the registry
    ent_reg = er.async_get(hass)
    entities = er.async_entries_for_config_entry(ent_reg, entry_id)
    for entity in entities:
        if entity.domain == "todo":
            todo_entity = entity.entity_id
            break

    try:
        await hass.services.async_call(
            "todo",
            "add_item",
            {"entity_id": todo_entity, "item": f"Pay {amount}€ for {merchant}"},
        )
        _LOGGER.info("Fallback: Transaction for %s added to to-do list.", merchant)
    except Exception as e:
        _LOGGER.error("Failed to add to to-do list: %s", e)


def execute_fints_transfer(config_data: dict, amount: Decimal, merchant: str) -> None:
    """Execute SEPA transfer via FinTS."""
    client = FinTS3PinTanClient(
        config_data[CONF_BLZ],
        config_data[CONF_USERNAME],
        config_data[CONF_PIN],
        config_data[CONF_ENDPOINT],
    )

    with client:
        accounts = client.get_sepa_accounts()
        if not accounts:
            raise ValueError("No SEPA accounts found.")

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
    
    # Find the Last Notification sensor for this device
    ent_reg = er.async_get(hass)
    device_id = entry.data[CONF_DEVICE_ID]
    
    notification_sensor = None
    entities = er.async_entries_for_device(ent_reg, device_id)
    for ent in entities:
        if ent.domain == "sensor" and ent.original_name == "Last Notification":
            notification_sensor = ent.entity_id
            break
            
    if not notification_sensor:
        _LOGGER.error("Could not find 'Last Notification' sensor for the selected device.")
        return False

    # Derive notify service from device registry
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get(device_id)
    if not device:
        _LOGGER.error("Selected device no longer exists.")
        return False
        
    # The mobile_app integration usually names the notify service: notify.mobile_app_<slugified_device_name>
    # We can try to find it via the companion app's naming convention
    device_name_slug = dr.format_mac(device.name).replace(":", "_").lower() if not device.name else "unknown"
    # A more reliable way is to look at the 'mobile_app' notify services directly
    notify_service = f"mobile_app_{device.name.lower().replace(' ', '_').replace('-', '_')}"

    hass.data[DOMAIN][entry.entry_id] = {
        "pending": {},
        "listeners": [],
        "sensor": notification_sensor,
        "notify": notify_service,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _handle_state_change(event: Event) -> None:
        new_state = event.data.get("new_state")
        if not new_state or new_state.attributes.get("package") != "com.google.android.apps.walletnfcrel":
            return

        text = new_state.attributes.get("android.text", "")
        match = re.search(r"(?:€|EUR)?\s*(\d+(?:[.,]\d{1,2})?)\s*(?:€|EUR)?\s*(?:at|bei)\s+(.+)", text, re.I)
        if match:
            amount = Decimal(match.group(1).replace(",", "."))
            merchant = match.group(2).strip()
            txn_id = uuid.uuid4().hex
            
            hass.data[DOMAIN][entry.entry_id]["pending"][txn_id] = {"amount": amount, "merchant": merchant}

            async def _fallback(_now):
                pending = hass.data[DOMAIN][entry.entry_id]["pending"].pop(txn_id, None)
                if pending:
                    await async_add_to_todo(hass, entry.entry_id, pending["amount"], pending["merchant"])

            async_call_later(hass, DEFAULT_FALLBACK_TIMEOUT, _fallback)

            action_pay = f"{ACTION_PAY_NOW}::{entry.entry_id}::{txn_id}"
            action_todo = f"{ACTION_ADD_TODO}::{entry.entry_id}::{txn_id}"

            await hass.services.async_call(
                NOTIFY_DOMAIN,
                notify_service,
                {
                    "message": f"Google Wallet: {amount}€ at {merchant}. Pay now?",
                    "title": "FinTS Auto-Pay",
                    "data": {
                        "actions": [
                            {"action": action_pay, "title": "Pay Now"},
                            {"action": action_todo, "title": "Add to To-Do"}
                        ]
                    },
                },
            )

    state_listener = async_track_state_change_event(hass, notification_sensor, _handle_state_change)
    hass.data[DOMAIN][entry.entry_id]["listeners"].append(state_listener)

    async def _handle_action(event: Event) -> None:
        action = event.data.get("action", "")
        if "::" not in action: return
        
        act_type, ent_id, txn_id = action.split("::")
        if ent_id != entry.entry_id: return

        pending = hass.data[DOMAIN][entry.entry_id]["pending"].pop(txn_id, None)
        if not pending: return

        if act_type == ACTION_PAY_NOW:
            try:
                await hass.async_add_executor_job(execute_fints_transfer, entry.data, pending["amount"], pending["merchant"])
                _LOGGER.info("Payment successful.")
            except Exception as e:
                _LOGGER.error("Payment failed: %s", e)
        elif act_type == ACTION_ADD_TODO:
            await async_add_to_todo(hass, entry.entry_id, pending["amount"], pending["merchant"])

    action_listener = hass.bus.async_listen(EVENT_MOBILE_APP_NOTIFICATION_ACTION, _handle_action)
    hass.data[DOMAIN][entry.entry_id]["listeners"].append(action_listener)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        for listener in hass.data[DOMAIN][entry.entry_id]["listeners"]:
            listener()
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
