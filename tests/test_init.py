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
    assert entry.entry_id in hass.data[DOMAIN]

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.entry_id not in hass.data[DOMAIN]


async def test_unload_entry_fails(hass: HomeAssistant) -> None:
    """Test entry unload failure."""
    real_device_id = await setup_mock_registries(hass)
    config = MOCK_CONFIG.copy()
    config[CONF_DEVICE_ID] = real_device_id
    entry = MockConfigEntry(domain=DOMAIN, data=config, entry_id="test_entry")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    with patch("homeassistant.config_entries.ConfigEntries.async_unload_platforms", return_value=False):
        assert not await hass.config_entries.async_unload(entry.entry_id)
        assert entry.entry_id in hass.data[DOMAIN]


async def test_setup_entry_no_sensor(hass: HomeAssistant) -> None:
    """Test entry setup fails if no sensor found."""
    dev_reg = dr.async_get(hass)
    mobile_app_entry = MockConfigEntry(domain="mobile_app", data={}, entry_id="no_sensor_mobile")
    mobile_app_entry.add_to_hass(hass)

    device = dev_reg.async_get_or_create(
        config_entry_id=mobile_app_entry.entry_id,
        identifiers={("mobile_app", "no_sensor")},
        name="No Sensor Phone",
    )

    config = MOCK_CONFIG.copy()
    config[CONF_DEVICE_ID] = device.id
    entry = MockConfigEntry(domain=DOMAIN, data=config, entry_id="no_sensor_entry")
    entry.add_to_hass(hass)

    assert not await hass.config_entries.async_setup(entry.entry_id)


async def test_setup_entry_device_removed_mid_setup(hass: HomeAssistant) -> None:
    """Test entry setup fails if device disappears."""
    real_device_id = await setup_mock_registries(hass)
    config = MOCK_CONFIG.copy()
    config[CONF_DEVICE_ID] = real_device_id
    entry = MockConfigEntry(domain=DOMAIN, data=config, entry_id="test_entry")
    entry.add_to_hass(hass)

    with patch("homeassistant.helpers.device_registry.DeviceRegistry.async_get", return_value=None):
        assert not await hass.config_entries.async_setup(entry.entry_id)


async def test_setup_entry_no_device(hass: HomeAssistant) -> None:
    """Test entry setup fails if device not found."""
    config = MOCK_CONFIG.copy()
    config[CONF_DEVICE_ID] = "non_existent"
    entry = MockConfigEntry(domain=DOMAIN, data=config, entry_id="no_device_entry")
    entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)


async def test_notification_listener_filtering(hass: HomeAssistant) -> None:
    """Test listener filters correctly."""
    real_device_id = await setup_mock_registries(hass)
    config = MOCK_CONFIG.copy()
    config[CONF_DEVICE_ID] = real_device_id
    entry = MockConfigEntry(domain=DOMAIN, data=config, entry_id="test_entry")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    sensor_id = hass.data[DOMAIN][entry.entry_id]["sensor"]

    with patch("homeassistant.core.ServiceRegistry.async_call") as mock_call:
        hass.states.async_set(sensor_id, "state", {"package": "wrong.package"})
        await hass.async_block_till_done()
        assert not mock_call.called
        hass.states.async_set(sensor_id, "state")
        await hass.async_block_till_done()
        assert not mock_call.called


async def test_notification_listener_and_todo_fallback(hass: HomeAssistant) -> None:
    """Test Google Wallet notifications and fallback to todo."""
    real_device_id = await setup_mock_registries(hass)
    config = MOCK_CONFIG.copy()
    config[CONF_DEVICE_ID] = real_device_id
    entry = MockConfigEntry(domain=DOMAIN, data=config, entry_id="test_entry")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    sensor_id = hass.data[DOMAIN][entry.entry_id]["sensor"]

    with patch("homeassistant.core.ServiceRegistry.async_call") as mock_call:
        hass.states.async_set(sensor_id, "Starbucks", {"package": "com.google.android.apps.walletnfcrel", "android.text": "Paid € 12.34 at Starbucks"})
        await hass.async_block_till_done()
        notify_calls = [c for c in mock_call.call_args_list if c[0][0] == "notify"]
        assert len(notify_calls) > 0
        assert "12.34" in notify_calls[0][0][2]["message"]

    with patch("homeassistant.core.ServiceRegistry.async_call") as mock_todo_call:
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3601))
        await hass.async_block_till_done()
        todo_calls = [c for c in mock_todo_call.call_args_list if c[0][0] == "todo"]
        assert len(todo_calls) > 0
        assert "Pay 12.34€ for Starbucks" in todo_calls[0][0][2]["item"]


async def test_async_add_to_todo_with_registry(hass: HomeAssistant) -> None:
    """Test async_add_to_todo with entity in registry."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test_entry")
    entry.add_to_hass(hass)
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create("todo", DOMAIN, "test_entry_todo", config_entry=entry)

    with patch("homeassistant.core.ServiceRegistry.async_call") as mock_call:
        await async_add_to_todo(hass, entry.entry_id, Decimal("10.00"), "Merchant")
        assert mock_call.called


async def test_async_add_to_todo_no_todo_entity(hass: HomeAssistant) -> None:
    """Test async_add_to_todo when no todo entity is found."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="no_todo_entry")
    entry.add_to_hass(hass)
    
    with patch("homeassistant.core.ServiceRegistry.async_call") as mock_call:
        await async_add_to_todo(hass, entry.entry_id, Decimal("10.00"), "Merchant")
        assert not mock_call.called


async def test_async_add_to_todo_fails(hass: HomeAssistant) -> None:
    """Test async_add_to_todo failure."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test_entry")
    entry.add_to_hass(hass)
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create("todo", DOMAIN, "test_entry_todo", config_entry=entry)
    
    with patch("homeassistant.core.ServiceRegistry.async_call", side_effect=Exception("Todo failed")):
        await async_add_to_todo(hass, entry.entry_id, Decimal("10.00"), "Merchant")


async def test_notification_action_pay_deep_link(hass: HomeAssistant) -> None:
    """Test clicking 'Pay Now' triggers command_activity."""
    real_device_id = await setup_mock_registries(hass)
    config = MOCK_CONFIG.copy()
    config[CONF_DEVICE_ID] = real_device_id
    entry = MockConfigEntry(domain=DOMAIN, data=config, entry_id="test_entry")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    txn_id = "test_txn"
    hass.data[DOMAIN][entry.entry_id]["pending"][txn_id] = {"amount": Decimal("10.00"), "merchant": "Bakery"}

    with patch("homeassistant.core.ServiceRegistry.async_call") as mock_call:
        hass.bus.async_fire("mobile_app_notification_action", {"action": f"WALLET_PAY::{entry.entry_id}::{txn_id}"})
        await hass.async_block_till_done()
        
        activity_calls = [
            c for c in mock_call.call_args_list 
            if c[0][0] == "notify" and c[0][2].get("message") == "command_activity"
        ]
        
        assert len(activity_calls) > 0
        data = activity_calls[0][0][2]["data"]
        assert "giro://" in data["intent_uri"]
        assert txn_id not in hass.data[DOMAIN][entry.entry_id]["pending"]


async def test_notification_action_todo(hass: HomeAssistant) -> None:
    """Test clicking 'Add to To-Do'."""
    real_device_id = await setup_mock_registries(hass)
    config = MOCK_CONFIG.copy()
    config[CONF_DEVICE_ID] = real_device_id
    entry = MockConfigEntry(domain=DOMAIN, data=config, entry_id="test_entry")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create("todo", DOMAIN, "test_entry_todo", config_entry=entry)

    txn_id = "test_txn"
    hass.data[DOMAIN][entry.entry_id]["pending"][txn_id] = {"amount": Decimal("10.00"), "merchant": "Bakery"}

    with patch("homeassistant.core.ServiceRegistry.async_call") as mock_call:
        hass.bus.async_fire("mobile_app_notification_action", {"action": f"WALLET_TODO::{entry.entry_id}::{txn_id}"})
        await hass.async_block_till_done()
        todo_calls = [c for c in mock_call.call_args_list if c[0][0] == "todo"]
        assert len(todo_calls) > 0
        assert txn_id not in hass.data[DOMAIN][entry.entry_id]["pending"]


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
        if domain == "notify":
            return MagicMock()
        return await real_async_call(domain, service, service_data, blocking, context, limit, target)

    with patch("homeassistant.core.ServiceRegistry.async_call", side_effect=mock_async_call) as mock_call:
        await hass.services.async_call(DOMAIN, "pay_transaction", {"amount": 15.50, "merchant": "Supermarket"}, blocking=True)
        activity_calls = [c for c in mock_call.call_args_list if c[0][0] == "notify" and c[0][2].get("message") == "command_activity"]
        assert len(activity_calls) > 0
        data = activity_calls[0][0][2]["data"]
        assert "amount=15.5" in data["intent_uri"]


async def test_trigger_deep_link_device_not_found(hass: HomeAssistant) -> None:
    """Test deep link trigger when device is missing."""
    config = MOCK_CONFIG.copy()
    config[CONF_DEVICE_ID] = "missing_device"
    entry = MockConfigEntry(domain=DOMAIN, data=config, entry_id="test_entry")
    entry.add_to_hass(hass)
    
    from custom_components.wallet_autopay import async_trigger_deep_link
    with patch("homeassistant.helpers.device_registry.DeviceRegistry.async_get", return_value=None):
        await async_trigger_deep_link(hass, entry, Decimal("10.00"), "Merchant")
        # Should log error and return
