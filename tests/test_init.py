"""Test the Wallet Auto-Pay integration."""
from unittest.mock import patch, MagicMock
from decimal import Decimal
from datetime import timedelta

from homeassistant.core import HomeAssistant, Event, State
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.const import Platform
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
import homeassistant.util.dt as dt_util

from custom_components.wallet_autopay.const import DOMAIN, CONF_DEVICE_ID
from custom_components.wallet_autopay import async_add_to_todo
from tests.const import MOCK_CONFIG


async def setup_mock_registries(hass: HomeAssistant):
    """Set up mock device and entity registries."""
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    mobile_app_entry = MockConfigEntry(domain="mobile_app", data={}, entry_id="mobile_app_entry")
    mobile_app_entry.add_to_hass(hass)

    device = dev_reg.async_get_or_create(
        config_entry_id=mobile_app_entry.entry_id,
        identifiers={("mobile_app", "test_unique_id")},
        name="Test Phone",
    )
    
    ent_reg.async_get_or_create(
        "sensor",
        "mobile_app",
        "test_unique_id_last_notification",
        suggested_object_id="test_phone_last_notification",
        device_id=device.id,
        original_name="Last Notification",
    )
    
    return device.id


async def test_setup_unload_entry(hass: HomeAssistant) -> None:
    """Test entry setup and unload."""
    real_device_id = await setup_mock_registries(hass)
    config = MOCK_CONFIG.copy()
    config[CONF_DEVICE_ID] = real_device_id

    entry = MockConfigEntry(domain=DOMAIN, data=config, entry_id="test_entry")
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert DOMAIN in hass.data
    assert entry.entry_id in hass.data[DOMAIN]["entries"]

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.entry_id not in hass.data[DOMAIN]["entries"]


async def test_notification_listener_and_todo_fallback(hass: HomeAssistant) -> None:
    """Test Google Wallet notifications and fallback to todo."""
    real_device_id = await setup_mock_registries(hass)
    config = MOCK_CONFIG.copy()
    config[CONF_DEVICE_ID] = real_device_id
    entry = MockConfigEntry(domain=DOMAIN, data=config, entry_id="test_entry")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    sensor_id = hass.data[DOMAIN]["entries"][entry.entry_id]["sensor"]

    with patch("homeassistant.core.ServiceRegistry.async_call") as mock_call:
        hass.states.async_set(sensor_id, "Starbucks", {"package": "com.google.android.apps.walletnfcrel", "android.text": "Paid € 12.34 at Starbucks"})
        await hass.async_block_till_done()
        assert any(c[0][0] == "notify" for c in mock_call.call_args_list)

    with patch("homeassistant.core.ServiceRegistry.async_call") as mock_todo_call:
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3601))
        await hass.async_block_till_done()
        assert any(c[0][0] == "todo" for c in mock_todo_call.call_args_list)


async def test_notification_action_pay_pdf(hass: HomeAssistant) -> None:
    """Test clicking 'Pay Now' triggers command_activity with PDF."""
    real_device_id = await setup_mock_registries(hass)
    config = MOCK_CONFIG.copy()
    config[CONF_DEVICE_ID] = real_device_id
    entry = MockConfigEntry(domain=DOMAIN, data=config, entry_id="test_entry")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    txn_id = "test_txn"
    hass.data[DOMAIN]["entries"][entry.entry_id]["pending"][txn_id] = {"amount": Decimal("10.00"), "merchant": "Bakery", "recipient": "John", "iban": "DE123"}

    with patch("homeassistant.core.ServiceRegistry.async_call") as mock_call:
        hass.bus.async_fire("mobile_app_notification_action", {"action": f"WALLET_PAY::{entry.entry_id}::{txn_id}"})
        await hass.async_block_till_done()
        
        activity_calls = [c for c in mock_call.call_args_list if c[0][0] == "notify" and c[0][2].get("message") == "command_activity"]
        assert len(activity_calls) > 0
        data = activity_calls[0][0][2]["data"]
        assert "application/pdf" in data["intent_type"]
        assert ".pdf" in data["intent_extras"]


async def test_pay_transaction_service(hass: HomeAssistant) -> None:
    """Test the pay_transaction service."""
    real_device_id = await setup_mock_registries(hass)
    config = MOCK_CONFIG.copy()
    config[CONF_DEVICE_ID] = real_device_id
    entry = MockConfigEntry(domain=DOMAIN, data=config, entry_id="test_entry")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    real_async_call = hass.services.async_call
    async def mock_async_call(domain, service, service_data=None, blocking=False, context=None, limit=None, target=None):
        if domain == "notify": return MagicMock()
        return await real_async_call(domain, service, service_data, blocking, context, limit, target)

    with patch("homeassistant.core.ServiceRegistry.async_call", side_effect=mock_async_call) as mock_call:
        await hass.services.async_call(DOMAIN, "pay_transaction", {"amount": 15.50, "merchant": "Supermarket"}, blocking=True)
        assert any(c[0][0] == "notify" for c in mock_call.call_args_list)
