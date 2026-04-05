"""The Wallet Auto-Pay integration."""
from __future__ import annotations

import logging
import re
import uuid
import io
from decimal import Decimal
import urllib.parse
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, State, ServiceCall
from homeassistant.helpers import device_registry as dr, entity_registry as er, translation
from homeassistant.helpers.event import async_track_state_change_event, async_call_later
from homeassistant.helpers.network import get_url
from homeassistant.components.notify import DOMAIN as NOTIFY_DOMAIN
from homeassistant.components.http import HomeAssistantView
from homeassistant.const import Platform
import homeassistant.helpers.config_validation as cv
import voluptuous as vol

import qrcode

from .const import (
    DOMAIN,
    CONF_DEVICE_ID,
    CONF_TARGET_IBAN,
    CONF_RECIPIENT_NAME,
    CONF_PACKAGE,
    CONF_TODO_ONLY,
    ACTION_PAY_NOW,
    ACTION_ADD_TODO,
    DEFAULT_FALLBACK_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.TODO]
EVENT_MOBILE_APP_NOTIFICATION_ACTION = "mobile_app_notification_action"

SERVICE_PAY_TRANSACTION_SCHEMA = vol.Schema({
    vol.Required("amount"): vol.Coerce(Decimal),
    vol.Required("merchant"): cv.string,
    vol.Optional("entry_id"): cv.string,
})

SERVICE_PAY_TODO_ITEM_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_id,
    vol.Required("item_name"): cv.string,
})

SERVICE_PAY_NEXT_ITEM_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_id,
})

async def async_get_localized_string(hass: HomeAssistant, key: str, language: str = None) -> str:
    """Get a localized string for the notification."""
    if language is None:
        language = hass.config.language
    
    translations = await translation.async_get_translations(hass, language, "notify", [DOMAIN])
    return translations.get(f"component.{DOMAIN}.notify.{key}", key)


def generate_epc_qr(recipient: str, iban: str, amount: Decimal, merchant: str) -> bytes:
    """Generate a standard EPC QR Code (GiroCode) as PNG bytes."""
    epc_lines = ["BCD", "002", "1", "SCT", "", recipient, iban, f"EUR{amount:.2f}", "", f"Auto-Pay {merchant}"[:140], ""]
    epc_string = "\n".join(epc_lines)
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(epc_string)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='PNG')
    return img_byte_arr.getvalue()


class GiroCodeView(HomeAssistantView):
    """View to serve generated GiroCode images."""
    url = "/api/wallet_autopay/qr/{txn_id}.png"
    name = "api:wallet_autopay:qr"
    requires_auth = False

    def __init__(self, hass: HomeAssistant):
        self.hass = hass

    async def get(self, request, txn_id):
        for entry_id, entry_data in self.hass.data[DOMAIN].get("entries", {}).items():
            pending = entry_data.get("pending", {})
            if txn_id in pending:
                data = pending[txn_id]
                image_bytes = await self.hass.async_add_executor_job(
                    generate_epc_qr, data.get("recipient", ""), data.get("iban", ""), data["amount"], data["merchant"]
                )
                from aiohttp import web
                return web.Response(body=image_bytes, content_type="image/png")
        from aiohttp import web
        return web.Response(status=404)


async def async_add_to_todo(hass: HomeAssistant, entry_id: str, amount: Decimal, merchant: str) -> None:
    ent_reg = er.async_get(hass)
    entities = er.async_entries_for_config_entry(ent_reg, entry_id)
    todo_entity = next((e.entity_id for e in entities if e.domain == "todo"), None)
    if not todo_entity: return
    try:
        await hass.services.async_call("todo", "add_item", {"entity_id": todo_entity, "item": f"Pay {amount}€ for {merchant}"})
    except Exception as e: _LOGGER.error("Failed to add to todo: %s", e)


async def async_trigger_photo_transfer(hass: HomeAssistant, entry_id: str, txn_id: str) -> None:
    """Send the command_activity to trigger the banking app's photo transfer scanner."""
    entry_data = hass.data[DOMAIN]["entries"].get(entry_id)
    if not entry_data: return

    config_entry = hass.config_entries.async_get_entry(entry_id)
    opts = config_entry.options if config_entry.options else config_entry.data
    package = opts.get(CONF_PACKAGE, "de.fiduciagad.direkt1822.banking").strip()
    
    # Get the absolute URL for the QR code
    base_url = get_url(hass, allow_internal=False, prefer_external=True)
    image_url = f"{base_url}/api/wallet_autopay/qr/{txn_id}.png"

    await hass.services.async_call(
        NOTIFY_DOMAIN,
        entry_data["notify"],
        {
            "message": "command_activity",
            "data": {
                "intent_action": "android.intent.action.SEND",
                "intent_type": "image/png",
                "intent_package": package,
                "intent_extras": f"android.intent.extra.STREAM:{image_url}",
                "priority": "high",
                "ttl": 0
            }
        }
    )


async def async_send_payment_notification(hass: HomeAssistant, notify_service: str, amount: Decimal, merchant: str, entry_id: str, txn_id: str, force_full: bool = False) -> None:
    """Send a notification with custom action buttons or simple notice for todo_only."""
    config_entry = hass.config_entries.async_get_entry(entry_id)
    opts = config_entry.options if config_entry.options else config_entry.data
    todo_only = opts.get(CONF_TODO_ONLY, False)

    if todo_only and not force_full:
        msg_template = await async_get_localized_string(hass, "todo_only_message")
        message = msg_template.format(amount=amount, merchant=merchant)
        data = {"importance": "high", "priority": "high", "ttl": 0}
    else:
        msg_template = await async_get_localized_string(hass, "payment_notification_message")
        message = msg_template.format(amount=amount, merchant=merchant)
        image_url = f"/api/wallet_autopay/qr/{txn_id}.png"
        
        pay_now_title = await async_get_localized_string(hass, "pay_now")
        pay_later_title = await async_get_localized_string(hass, "pay_later")
        
        actions = [
            {"action": f"{ACTION_PAY_NOW}::{entry_id}::{txn_id}", "title": pay_now_title},
            {"action": f"{ACTION_ADD_TODO}::{entry_id}::{txn_id}", "title": pay_later_title}
        ]
        data = {"image": image_url, "actions": actions, "importance": "high", "priority": "high", "ttl": 0}

    await hass.services.async_call(NOTIFY_DOMAIN, notify_service, {
        "message": message,
        "title": "Wallet Auto-Pay",
        "data": data,
    })


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {"view_registered": False, "entries": {}})
    if not hass.data[DOMAIN]["view_registered"]:
        hass.http.register_view(GiroCodeView(hass))
        hass.data[DOMAIN]["view_registered"] = True
    
    ent_reg = er.async_get(hass)
    notification_sensor = next((e.entity_id for e in er.async_entries_for_device(ent_reg, entry.data[CONF_DEVICE_ID]) if e.domain == "sensor" and ("last_notification" in e.entity_id or "last_notification" in (e.unique_id or "").lower())), None)
    if not notification_sensor: return False

    dev_reg = dr.async_get(hass)
    device = dev_reg.async_get(entry.data[CONF_DEVICE_ID])
    if not device: return False
    notify_service = f"mobile_app_{device.name.lower().replace(' ', '_').replace('-', '_')}"

    hass.data[DOMAIN]["entries"][entry.entry_id] = {"pending": {}, "listeners": [], "sensor": notification_sensor, "notify": notify_service}
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _handle_state_change(event: Event) -> None:
        new_state = event.data.get("new_state")
        if not new_state or new_state.attributes.get("package") != "com.google.android.apps.walletnfcrel": return
        text = new_state.attributes.get("android.text", "")
        title = new_state.attributes.get("android.title", "")
        
        # In Google Wallet, for standard payment text is "Paid € 12.34 at Starbucks"
        # For actual payments the title might be the vendor and text "1,00 € mit {payment method}"
        
        amount_match = re.search(r"(?:€|EUR)?\s*(\d+(?:[.,]\d{1,2})?)\s*(?:€|EUR)?", text, re.I)
        merchant_match = re.search(r"(?:at|bei)\s+(.+)", text, re.I)
        
        if amount_match:
            amount_str = amount_match.group(1).replace(",", ".")
            amount = Decimal(amount_str)
            
            merchant = ""
            if merchant_match:
                merchant = merchant_match.group(1).strip()
            elif title:
                merchant = title.strip()
                
            if merchant:
                txn_id = uuid.uuid4().hex
                
                opts = entry.options if entry.options else entry.data
                todo_only = opts.get(CONF_TODO_ONLY, False)
                
                hass.data[DOMAIN]["entries"][entry.entry_id]["pending"][txn_id] = {
                    "amount": amount, 
                    "merchant": merchant, 
                    "recipient": opts.get(CONF_RECIPIENT_NAME, ""), 
                    "iban": opts.get(CONF_TARGET_IBAN, "")
                }

                if todo_only:
                    # Add to todo immediately and clear pending
                    await async_add_to_todo(hass, entry.entry_id, amount, merchant)
                    hass.data[DOMAIN]["entries"][entry.entry_id]["pending"].pop(txn_id, None)
                else:
                    async def _fallback(_now):
                        pending = hass.data[DOMAIN]["entries"][entry.entry_id]["pending"].pop(txn_id, None)
                        if pending: await async_add_to_todo(hass, entry.entry_id, pending["amount"], pending["merchant"])
                    async_call_later(hass, DEFAULT_FALLBACK_TIMEOUT, _fallback)
                
                await async_send_payment_notification(hass, notify_service, amount, merchant, entry.entry_id, txn_id)

    hass.data[DOMAIN]["entries"][entry.entry_id]["listeners"].append(async_track_state_change_event(hass, notification_sensor, _handle_state_change))

    async def _handle_action(event: Event) -> None:
        action = event.data.get("action", "")
        if "::" not in action: return
        act_type, ent_id, txn_id = action.split("::")
        if ent_id != entry.entry_id: return
        
        # Note: We don't pop yet for PAY_NOW so the image view can still serve the QR code
        pending = hass.data[DOMAIN]["entries"][entry.entry_id]["pending"].get(txn_id)
        if not pending: return

        if act_type == ACTION_PAY_NOW:
            await async_trigger_photo_transfer(hass, entry.entry_id, txn_id)
        elif act_type == ACTION_ADD_TODO:
            hass.data[DOMAIN]["entries"][entry.entry_id]["pending"].pop(txn_id, None)
            await async_add_to_todo(hass, entry.entry_id, pending["amount"], pending["merchant"])

    hass.data[DOMAIN]["entries"][entry.entry_id]["listeners"].append(hass.bus.async_listen(EVENT_MOBILE_APP_NOTIFICATION_ACTION, _handle_action))

    async def handle_pay_transaction(call: ServiceCall) -> None:
        target_id = call.data.get("entry_id", entry.entry_id)
        if target_id in hass.data[DOMAIN]["entries"]:
            txn_id = uuid.uuid4().hex
            t_entry = hass.config_entries.async_get_entry(target_id)
            t_opts = t_entry.options if t_entry.options else t_entry.data
            
            hass.data[DOMAIN]["entries"][target_id]["pending"][txn_id] = {
                "amount": call.data["amount"], 
                "merchant": call.data["merchant"], 
                "recipient": t_opts.get(CONF_RECIPIENT_NAME, ""), 
                "iban": t_opts.get(CONF_TARGET_IBAN, "")
            }
            
            if t_opts.get(CONF_TODO_ONLY):
                await async_add_to_todo(hass, target_id, call.data["amount"], call.data["merchant"])
                hass.data[DOMAIN]["entries"][target_id]["pending"].pop(txn_id, None)

            await async_send_payment_notification(hass, hass.data[DOMAIN]["entries"][target_id]["notify"], call.data["amount"], call.data["merchant"], target_id, txn_id)

    async def handle_pay_todo_item(call: ServiceCall) -> None:
        match = re.search(r"Pay\s+(\d+(?:[.,]\d{1,2})?)€\s+for\s+(.+)", call.data["item_name"], re.I)
        if match:
            amount, merchant = Decimal(match.group(1).replace(",", ".")), match.group(2).strip()
            entity_entry = er.async_get(hass).async_get(call.data["entity_id"])
            if entity_entry and entity_entry.config_entry_id:
                txn_id = uuid.uuid4().hex
                t_entry = hass.config_entries.async_get_entry(entity_entry.config_entry_id)
                t_opts = t_entry.options if t_entry.options else t_entry.data
                
                hass.data[DOMAIN]["entries"][t_entry.entry_id]["pending"][txn_id] = {
                    "amount": amount, 
                    "merchant": merchant, 
                    "recipient": t_opts.get(CONF_RECIPIENT_NAME, ""), 
                    "iban": t_opts.get(CONF_TARGET_IBAN, "")
                }
                
                # Manual trigger from TODO item should probably always show notification even if todo_only is set, 
                # but let's follow the setting for consistency.
                if t_opts.get(CONF_TODO_ONLY):
                    # It's already in a todo list (ostensibly), but adding it again if that's what's requested
                    await async_add_to_todo(hass, t_entry.entry_id, amount, merchant)
                    hass.data[DOMAIN]["entries"][t_entry.entry_id]["pending"].pop(txn_id, None)

                await async_send_payment_notification(hass, hass.data[DOMAIN]["entries"][t_entry.entry_id]["notify"], amount, merchant, t_entry.entry_id, txn_id)

    async def handle_pay_next_item(call: ServiceCall) -> None:
        component = hass.data.get("todo")
        if not component:
            return
            
        todo_list = component.get_entity(call.data["entity_id"])
        if not todo_list or not todo_list.todo_items:
            # Send notification that nothing is left to pay
            entity_entry = er.async_get(hass).async_get(call.data["entity_id"])
            if entity_entry and entity_entry.config_entry_id:
                notify_service = hass.data[DOMAIN]["entries"][entity_entry.config_entry_id]["notify"]
                msg = await async_get_localized_string(hass, "no_items_to_pay")
                await hass.services.async_call(NOTIFY_DOMAIN, notify_service, {
                    "message": msg,
                    "title": "Wallet Auto-Pay",
                })
            return

        item = todo_list.todo_items[0]
        match = re.search(r"Pay\s+(\d+(?:[.,]\d{1,2})?)€\s+for\s+(.+)", item.summary, re.I)
        if match:
            amount, merchant = Decimal(match.group(1).replace(",", ".")), match.group(2).strip()
            entity_entry = er.async_get(hass).async_get(call.data["entity_id"])
            if entity_entry and entity_entry.config_entry_id:
                txn_id = uuid.uuid4().hex
                t_entry = hass.config_entries.async_get_entry(entity_entry.config_entry_id)
                t_opts = t_entry.options if t_entry.options else t_entry.data
                
                # Store in pending so the image view can serve it
                hass.data[DOMAIN]["entries"][t_entry.entry_id]["pending"][txn_id] = {
                    "amount": amount, 
                    "merchant": merchant, 
                    "recipient": t_opts.get(CONF_RECIPIENT_NAME, ""), 
                    "iban": t_opts.get(CONF_TARGET_IBAN, "")
                }
                
                # Remove from todo list immediately
                await todo_list.async_delete_todo_items([item.uid])
                
                # Show QR code in Persistent Notification (HA UI)
                image_url = f"/api/wallet_autopay/qr/{txn_id}.png"
                msg_template = await async_get_localized_string(hass, "payment_notification_message")
                message = msg_template.format(amount=amount, merchant=merchant)
                
                # Using Markdown for image display in persistent notification
                markdown_msg = f"![GiroCode]({image_url})\n\n{message}\n\n**Empfänger:** {t_opts.get(CONF_RECIPIENT_NAME)}\n**IBAN:** {t_opts.get(CONF_TARGET_IBAN)}"
                
                await hass.services.async_call("persistent_notification", "create", {
                    "title": "Wallet Auto-Pay: QR-Code",
                    "message": markdown_msg,
                    "notification_id": f"{DOMAIN}_pay_next_{t_entry.entry_id}"
                })

    async def handle_trigger_payment_notification(call: ServiceCall) -> None:
        target_id = call.data.get("entry_id", entry.entry_id)
        if target_id in hass.data[DOMAIN]["entries"]:
            txn_id = uuid.uuid4().hex
            t_entry = hass.config_entries.async_get_entry(target_id)
            t_opts = t_entry.options if t_entry.options else t_entry.data
            
            hass.data[DOMAIN]["entries"][target_id]["pending"][txn_id] = {
                "amount": call.data["amount"], 
                "merchant": call.data["merchant"], 
                "recipient": t_opts.get(CONF_RECIPIENT_NAME, ""), 
                "iban": t_opts.get(CONF_TARGET_IBAN, "")
            }
            
            # Force full notification regardless of todo_only setting
            await async_send_payment_notification(
                hass, 
                hass.data[DOMAIN]["entries"][target_id]["notify"], 
                call.data["amount"], 
                call.data["merchant"], 
                target_id, 
                txn_id,
                force_full=True
            )

    hass.services.async_register(DOMAIN, "pay_transaction", handle_pay_transaction, schema=SERVICE_PAY_TRANSACTION_SCHEMA)
    hass.services.async_register(DOMAIN, "pay_todo_item", handle_pay_todo_item, schema=SERVICE_PAY_TODO_ITEM_SCHEMA)
    hass.services.async_register(DOMAIN, "pay_next_item", handle_pay_next_item, schema=SERVICE_PAY_NEXT_ITEM_SCHEMA)
    hass.services.async_register(DOMAIN, "trigger_payment_notification", handle_trigger_payment_notification, schema=SERVICE_PAY_TRANSACTION_SCHEMA)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        for listener in hass.data[DOMAIN]["entries"][entry.entry_id]["listeners"]: listener()
        hass.data[DOMAIN]["entries"].pop(entry.entry_id)
        if not hass.data[DOMAIN]["entries"]:
            for svc in ["pay_transaction", "pay_todo_item", "pay_next_item"]: hass.services.async_remove(DOMAIN, svc)
    return unload_ok