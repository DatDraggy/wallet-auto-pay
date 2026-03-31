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
    CONF_PACKAGE,
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

SERVICE_PAY_TODO_ITEM_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_id,
        vol.Required("item_name"): cv.string,
    }
)

SERVICE_PAY_NEXT_ITEM_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_id,
    }
)


def get_giro_intent_url(recipient: str, iban: str, amount: Decimal, merchant: str, package: str = None) -> str:
    """Generate a pre-filled banking Intent URI for Android."""
    encoded_recipient = urllib.parse.quote(recipient)
    encoded_reason = urllib.parse.quote(f"Auto-Pay {merchant}")
    
    intent = f"intent://payment?name={encoded_recipient}&iban={iban}&amount={amount}&reason={encoded_reason}#Intent;scheme=giro;"
    if package:
        intent += f"package={package};"
    intent += "end"
    return intent


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


async def async_send_payment_notification(
    hass: HomeAssistant, 
    notify_service: str, 
    recipient: str, 
    iban: str, 
    amount: Decimal, 
    merchant: str,
    entry_id: str,
    txn_id: str = None,
    package: str = None
) -> None:
    """Send a notification with a direct Intent button."""
    intent_url = get_giro_intent_url(recipient, iban, amount, merchant, package)
    
    actions = [{"action": "URI", "title": "Jetzt bezahlen", "uri": intent_url}]
    if txn_id:
        actions.append({"action": f"{ACTION_ADD_TODO}::{entry_id}::{txn_id}", "title": "Später (To-Do)"})

    await hass.services.async_call(
        NOTIFY_DOMAIN,
        notify_service,
        {
            "message": f"Wallet: {amount}€ bei {merchant}. Banking App öffnen?",
            "title": "Wallet Auto-Pay",
            "data": {
                "actions": actions,
                "importance": "high",
                "priority": "high",
                "ttl": 0
            },
        },
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Wallet Auto-Pay from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    
    ent_reg = er.async_get(hass)
    device_id = entry.data[CONF_DEVICE_ID]
    
    notification_sensor = None
    entities = er.async_entries_for_device(ent_reg, device_id)
    for ent in entities:
        if ent.domain == "sensor" and (
            "last_notification" in ent.entity_id or 
            "last_notification" in (ent.unique_id or "").lower() or
            ent.original_name == "Last Notification"
        ):
            notification_sensor = ent.entity_id
            break
            
    if not notification_sensor:
        _LOGGER.error("Could not find 'Last Notification' sensor for device %s.", device_id)
        return False

    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get(device_id)
    if not device:
        _LOGGER.error("Selected device no longer exists.")
        return False
        
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

            await async_send_payment_notification(
                hass, notify_service, entry.data[CONF_RECIPIENT_NAME],
                entry.data[CONF_TARGET_IBAN], amount, merchant, entry.entry_id, txn_id,
                entry.data.get(CONF_PACKAGE)
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
        if act_type == ACTION_ADD_TODO:
            await async_add_to_todo(hass, entry.entry_id, pending["amount"], pending["merchant"])

    action_listener = hass.bus.async_listen(EVENT_MOBILE_APP_NOTIFICATION_ACTION, _handle_action)
    hass.data[DOMAIN][entry.entry_id]["listeners"].append(action_listener)

    # Internal helper to handle payment logic for services
    async def process_payment(amount: Decimal, merchant: str, config_entry_id: str):
        target_entry = hass.config_entries.async_get_entry(config_entry_id)
        if target_entry and target_entry.entry_id in hass.data[DOMAIN]:
            await async_send_payment_notification(
                hass, hass.data[DOMAIN][target_entry.entry_id]["notify"],
                target_entry.data[CONF_RECIPIENT_NAME], target_entry.data[CONF_TARGET_IBAN],
                amount, merchant, target_entry.entry_id,
                package=target_entry.data.get(CONF_PACKAGE)
            )

    # Service Handlers
    async def handle_pay_transaction(call: ServiceCall) -> None:
        await process_payment(
            call.data["amount"], 
            call.data["merchant"], 
            call.data.get("entry_id", entry.entry_id)
        )

    async def handle_pay_todo_item(call: ServiceCall) -> None:
        entity_id = call.data["entity_id"]
        item_name = call.data["item_name"]
        match = re.search(r"Pay\s+(\d+(?:[.,]\d{1,2})?)€\s+for\s+(.+)", item_name, re.I)
        if match:
            amount, merchant = Decimal(match.group(1).replace(",", ".")), match.group(2).strip()
            entity_entry = er.async_get(hass).async_get(entity_id)
            if entity_entry and entity_entry.config_entry_id:
                await process_payment(amount, merchant, entity_entry.config_entry_id)

    async def handle_pay_next_item(call: ServiceCall) -> None:
        entity_id = call.data["entity_id"]
        component = hass.data.get("todo")
        if not component: return
        todo_list = component.get_entity(entity_id)
        if not todo_list or not todo_list.todo_items:
            _LOGGER.warning("No items found in To-Do list %s", entity_id)
            return
        
        # Parse the first item
        item = todo_list.todo_items[0]
        match = re.search(r"Pay\s+(\d+(?:[.,]\d{1,2})?)€\s+for\s+(.+)", item.summary, re.I)
        if match:
            amount, merchant = Decimal(match.group(1).replace(",", ".")), match.group(2).strip()
            entity_entry = er.async_get(hass).async_get(entity_id)
            if entity_entry and entity_entry.config_entry_id:
                await process_payment(amount, merchant, entity_entry.config_entry_id)

    hass.services.async_register(DOMAIN, "pay_transaction", handle_pay_transaction, schema=SERVICE_PAY_TRANSACTION_SCHEMA)
    hass.services.async_register(DOMAIN, "pay_todo_item", handle_pay_todo_item, schema=SERVICE_PAY_TODO_ITEM_SCHEMA)
    hass.services.async_register(DOMAIN, "pay_next_item", handle_pay_next_item, schema=SERVICE_PAY_NEXT_ITEM_SCHEMA)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        for listener in hass.data[DOMAIN][entry.entry_id]["listeners"]:
            listener()
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, "pay_transaction")
            hass.services.async_remove(DOMAIN, "pay_todo_item")
            hass.services.async_remove(DOMAIN, "pay_next_item")
    return unload_ok
