"""Test the FinTS Auto-Pay integration."""
from unittest.mock import patch, MagicMock
from decimal import Decimal
from datetime import timedelta

from homeassistant.core import HomeAssistant, Event, State
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.const import Platform
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
import homeassistant.util.dt as dt_util

from custom_components.fints_autopay.const import DOMAIN, CONF_DEVICE_ID
from custom_components.fints_autopay import async_add_to_todo, execute_fints_transfer
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

    with patch("homeassistant.config_entries.ConfigEntries.async_forward_entry_setups", return_value=True), \
         patch("homeassistant.config_entries.ConfigEntries.async_unload_platforms", return_value=True):
        
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        
        assert len(hass.data[DOMAIN][entry.entry_id]["listeners"]) > 0

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
    
    with patch("homeassistant.config_entries.ConfigEntries.async_forward_entry_setups", return_value=True):
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
    
    with patch("homeassistant.config_entries.ConfigEntries.async_forward_entry_setups", return_value=True):
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
    
    with patch("homeassistant.config_entries.ConfigEntries.async_forward_entry_setups", return_value=True):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    sensor_id = hass.data[DOMAIN][entry.entry_id]["sensor"]

    with patch("homeassistant.core.ServiceRegistry.async_call") as mock_call:
        hass.states.async_set(sensor_id, "Starbucks", {"package": "com.google.android.apps.walletnfcrel", "android.text": "Paid € 12.34 at Starbucks"})
        await hass.async_block_till_done()
        notify_calls = [call for call in mock_call.call_args_list if call[0][0] == "notify"]
        assert len(notify_calls) > 0
        assert "12.34" in notify_calls[0][0][2]["message"]

    with patch("homeassistant.core.ServiceRegistry.async_call") as mock_todo_call:
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3601))
        await hass.async_block_till_done()
        todo_calls = [call for call in mock_todo_call.call_args_list if call[0][0] == "todo"]
        assert len(todo_calls) > 0
        assert "Pay 12.34€ for Starbucks" in todo_calls[0][0][2]["item"]


async def test_async_add_to_todo_with_registry(hass: HomeAssistant) -> None:
    """Test async_add_to_todo with entity in registry."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test_entry")
    entry.add_to_hass(hass)
    
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create(
        "todo",
        DOMAIN,
        "test_entry_todo",
        config_entry=entry,
    )

    with patch("homeassistant.core.ServiceRegistry.async_call") as mock_call:
        await async_add_to_todo(hass, entry.entry_id, Decimal("10.00"), "Merchant")
        assert mock_call.called


async def test_async_add_to_todo_no_registry(hass: HomeAssistant) -> None:
    """Test async_add_to_todo with no entity in registry."""
    with patch("homeassistant.core.ServiceRegistry.async_call") as mock_call:
        await async_add_to_todo(hass, "test_entry", Decimal("10.00"), "Merchant")
        assert mock_call.called


async def test_async_add_to_todo_fails(hass: HomeAssistant) -> None:
    """Test async_add_to_todo failure."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test_entry")
    entry.add_to_hass(hass)
    
    with patch("homeassistant.core.ServiceRegistry.async_call", side_effect=Exception("Todo failed")):
        await async_add_to_todo(hass, entry.entry_id, Decimal("10.00"), "Merchant")
        # Should just log error


async def test_execute_fints_transfer(hass: HomeAssistant) -> None:
    """Test the blocking FinTS transfer function."""
    with patch("custom_components.fints_autopay.FinTS3PinTanClient") as mock_client_class:
        mock_client = mock_client_class.return_value
        mock_client.__enter__.return_value = mock_client
        mock_acc = MagicMock()
        mock_client.get_sepa_accounts.return_value = [mock_acc]
        execute_fints_transfer(MOCK_CONFIG, Decimal("10.00"), "Merchant")
        mock_client.simple_sepa_transfer.assert_called_once()


async def test_execute_fints_transfer_no_accounts(hass: HomeAssistant) -> None:
    """Test the blocking FinTS transfer function with no accounts."""
    with patch("custom_components.fints_autopay.FinTS3PinTanClient") as mock_client_class:
        mock_client = mock_client_class.return_value
        mock_client.__enter__.return_value = mock_client
        mock_client.get_sepa_accounts.return_value = []
        try:
            execute_fints_transfer(MOCK_CONFIG, Decimal("10.00"), "Merchant")
        except ValueError as e:
            assert str(e) == "No SEPA accounts found."


async def test_notification_action_pay(hass: HomeAssistant) -> None:
    """Test clicking 'Pay Now' triggers FinTS transfer."""
    real_device_id = await setup_mock_registries(hass)
    config = MOCK_CONFIG.copy()
    config[CONF_DEVICE_ID] = real_device_id
    entry = MockConfigEntry(domain=DOMAIN, data=config, entry_id="test_entry")
    entry.add_to_hass(hass)
    
    with patch("homeassistant.config_entries.ConfigEntries.async_forward_entry_setups", return_value=True):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    txn_id = "test_txn"
    hass.data[DOMAIN][entry.entry_id]["pending"][txn_id] = {"amount": Decimal("10.00"), "merchant": "Bakery"}

    with patch("custom_components.fints_autopay.execute_fints_transfer") as mock_transfer:
        hass.bus.async_fire("mobile_app_notification_action", {"action": f"FINTS_PAY::{entry.entry_id}::{txn_id}"})
        await hass.async_block_till_done()
        mock_transfer.assert_called_once()
        assert txn_id not in hass.data[DOMAIN][entry.entry_id]["pending"]


async def test_notification_action_pay_failed(hass: HomeAssistant) -> None:
    """Test clicking 'Pay Now' handle failure."""
    real_device_id = await setup_mock_registries(hass)
    config = MOCK_CONFIG.copy()
    config[CONF_DEVICE_ID] = real_device_id
    entry = MockConfigEntry(domain=DOMAIN, data=config, entry_id="test_entry")
    entry.add_to_hass(hass)
    
    with patch("homeassistant.config_entries.ConfigEntries.async_forward_entry_setups", return_value=True):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    txn_id = "test_txn"
    hass.data[DOMAIN][entry.entry_id]["pending"][txn_id] = {"amount": Decimal("10.00"), "merchant": "Bakery"}

    with patch("custom_components.fints_autopay.execute_fints_transfer", side_effect=Exception("Transfer failed")):
        hass.bus.async_fire("mobile_app_notification_action", {"action": f"FINTS_PAY::{entry.entry_id}::{txn_id}"})
        await hass.async_block_till_done()
        # Should catch exception and log


async def test_notification_action_todo(hass: HomeAssistant) -> None:
    """Test clicking 'Add to To-Do'."""
    real_device_id = await setup_mock_registries(hass)
    config = MOCK_CONFIG.copy()
    config[CONF_DEVICE_ID] = real_device_id
    entry = MockConfigEntry(domain=DOMAIN, data=config, entry_id="test_entry")
    entry.add_to_hass(hass)
    
    with patch("homeassistant.config_entries.ConfigEntries.async_forward_entry_setups", return_value=True):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    txn_id = "test_txn"
    hass.data[DOMAIN][entry.entry_id]["pending"][txn_id] = {"amount": Decimal("10.00"), "merchant": "Bakery"}

    with patch("homeassistant.core.ServiceRegistry.async_call") as mock_call:
        hass.bus.async_fire("mobile_app_notification_action", {"action": f"FINTS_TODO::{entry.entry_id}::{txn_id}"})
        await hass.async_block_till_done()
        todo_calls = [call for call in mock_call.call_args_list if call[0][0] == "todo"]
        assert len(todo_calls) > 0
        assert txn_id not in hass.data[DOMAIN][entry.entry_id]["pending"]
