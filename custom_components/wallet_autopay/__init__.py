"""The Wallet Auto-Pay integration."""
from __future__ import annotations

import logging
import re
import uuid
from decimal import Decimal
import urllib.parse
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, State, ServiceCall
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event, async_call_later
from homeassistant.components.notify import DOMAIN as NOTIFY_DOMAIN
from homeassistant.const import Platform
import homeassistant.helpers.config_validation as cv
import voluptuous as vol

from .const import (
    DOMAIN,
    CONF_DEVICE_ID,
    CONF_TARGET_IBAN,
    CONF_RECIPIENT_NAME,
    ACTION_PAY_NOW,
    ACTION_ADD_TODO,
    DEFAULT_FALLBACK_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.TODO]
EVENT_MOBILE_APP_NOTIFICATION_ACTION = "mobile_app_notification_action"

# Service schemas
SERVICE_PAY_TRANSACTION_SCHEMA = vol.Schema(
    {
        vol.Required("amount"): vol.Coerce(Decimal),
        vol.Required("merchant"): cv.string,
        vol.Optional("entry_id"): cv.string,
    }
)


async def async_add_to_todo(hass: HomeAssistant, entry_id: str, amount: Decimal, merchant: str) -> None:
    """Add a transaction to the auto-created To-Do list."""
    ent_reg = er.async_get(hass)
    entities = er.async_entries_for_config_entry(ent_reg, entry_id)
    todo_entity = None
    for entity in entities:
        if entity.domain == "todo":
            todo_entity = entity.entity_id
            break

    if not todo_entity:
        _LOGGER.error("Could not find To-Do entity for entry %s", entry_id)
        return

    try:
        await hass.services.async_call(
            "todo",
            "add_item",
            {"entity_id": todo_entity, "item": f"Pay {amount}€ for {merchant}"},
        )
        _LOGGER.info("Transaction for %s added to to-do list.", merchant)
    except Exception as e:
        _LOGGER.error("Failed to add to to-do list: %s", e)


async def async_trigger_deep_link(hass: HomeAssistant, entry: ConfigEntry, amount: Decimal, merchant: str) -> None:
    """Send the command_activity intent to the phone."""
    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get(entry.data[CONF_DEVICE_ID])
    if not device:
        _LOGGER.error("Device not found for deep link.")
        return

    notify_service = f"mobile_app_{device.name.lower().replace(' ', '_').replace('-', '_')}"
    
    recipient = urllib.parse.quote(entry.data[CONF_RECIPIENT_NAME])
    iban = entry.data[CONF_TARGET_IBAN]
    reason = urllib.parse.quote(f"Auto-Pay {merchant}")
    
    # GIRO URL Scheme
    giro_url = f"giro://x-callback-url/payment?name={recipient}&iban={iban}&amount={amount}&reason={reason}"
    
    await hass.services.async_call(
        NOTIFY_DOMAIN,
        notify_service,
        {
            "message": "command_activity",
            "data": {
                "intent_action": "android.intent.action.VIEW",
                "intent_uri": giro_url,
                "intent_package": "de.fiducia.it.gic.android.direkt1822",
                "priority": "high",
                "ttl": 0
            }
        }
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Wallet Auto-Pay from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    
    ent_reg = er.async_get(hass)
    device_id = entry.data[CONF_DEVICE_ID]
    
    notification_sensor = None
    entities = er.async_entries_for_device(ent_reg, device_id)
    for ent in entities:
        # Very aggressive check: domain sensor + 'last_notification' anywhere in ID or name
        if ent.domain == "sensor" and (
            "last_notification" in ent.entity_id or 
            "last_notification" in (ent.unique_id or "").lower() or
            ent.original_name == "Last Notification"
        ):
            notification_sensor = ent.entity_id
            break
            
    if not notification_sensor:
        _LOGGER.error(
            "Could not find 'Last Notification' sensor for device %s. "
            "Available entities on device: %s",
            device_id,
            [e.entity_id for e in entities]
        )
        return False

    # Derive notify service from the sensor name
    # e.g. sensor.pixel_9_pro_xl_last_notification -> mobile_app_pixel_9_pro_xl
    device_slug = notification_sensor.replace("sensor.", "").replace("_last_notification", "")
    notify_service = f"mobile_app_{device_slug}"

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
                    "message": f"Google Wallet: {amount}€ at {merchant}. Open banking app?",
                    "title": "Wallet Auto-Pay",
                    "data": {
                        "actions": [
                            {"action": action_pay, "title": "Pay Now (Open App)"},
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
            await async_trigger_deep_link(hass, entry, pending["amount"], pending["merchant"])
        elif act_type == ACTION_ADD_TODO:
            await async_add_to_todo(hass, entry.entry_id, pending["amount"], pending["merchant"])

    action_listener = hass.bus.async_listen(EVENT_MOBILE_APP_NOTIFICATION_ACTION, _handle_action)
    hass.data[DOMAIN][entry.entry_id]["listeners"].append(action_listener)

    # Register services
    async def handle_pay_transaction(call: ServiceCall) -> None:
        """Service to trigger a payment deep link manually."""
        amount = call.data["amount"]
        merchant = call.data["merchant"]
        call_entry_id = call.data.get("entry_id", entry.entry_id)
        
        target_entry = hass.config_entries.async_get_entry(call_entry_id)
        if target_entry:
            await async_trigger_deep_link(hass, target_entry, amount, merchant)

    hass.services.async_register(
        DOMAIN, "pay_transaction", handle_pay_transaction, schema=SERVICE_PAY_TRANSACTION_SCHEMA
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        for listener in hass.data[DOMAIN][entry.entry_id]["listeners"]:
            listener()
        hass.data[DOMAIN].pop(entry.entry_id)
        
        # Unregister service if no entries left
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, "pay_transaction")
            
    return unload_ok
