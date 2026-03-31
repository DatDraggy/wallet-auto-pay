"""Test the FinTS Auto-Pay integration."""
from unittest.mock import patch, MagicMock
from decimal import Decimal
from datetime import timedelta

from homeassistant.core import HomeAssistant, State
from homeassistant.const import STATE_ON
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
import homeassistant.util.dt as dt_util

from custom_components.fints_autopay.const import DOMAIN, CONF_SENSOR
from tests.const import MOCK_CONFIG


async def test_setup_unload_entry(hass: HomeAssistant) -> None:
    """Test entry setup and unload."""
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id="test")
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert DOMAIN in hass.data
    assert "test" in hass.data[DOMAIN]

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert "test" not in hass.data[DOMAIN]


async def test_notification_listener(hass: HomeAssistant) -> None:
    """Test the listener picks up Google Wallet notifications."""
    entry_id = "test"
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id=entry_id)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Simulate Google Wallet notification state change
    with patch("homeassistant.core.ServiceRegistry.async_call") as mock_call:
        hass.states.async_set(
            MOCK_CONFIG[CONF_SENSOR],
            "Starbucks",
            {
                "package": "com.google.android.apps.walletnfcrel",
                "android.text": "Paid € 12.34 at Starbucks",
            },
        )
        await hass.async_block_till_done()

        # Check if notify service was called
        assert mock_call.called
        args = mock_call.call_args_list[0]
        assert args[0][0] == "notify"
        assert "12.34" in args[0][2]["message"]
        assert "Starbucks" in args[0][2]["message"]

        # Check pending transaction storage
        pending = hass.data[DOMAIN][entry_id]["pending"]
        assert len(pending) == 1
        txn_id = list(pending.keys())[0]
        assert pending[txn_id]["amount"] == Decimal("12.34")

    # Fast forward 1 hour to trigger fallback and clear the timer
    with patch("homeassistant.core.ServiceRegistry.async_call") as mock_todo_call:
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3601))
        await hass.async_block_till_done()
        
        # Verify it was added to todo
        assert mock_todo_call.called
        assert mock_todo_call.call_args[0][0] == "todo"
        
        # Verify removed from pending
        assert len(hass.data[DOMAIN][entry_id]["pending"]) == 0


async def test_notification_action_pay(hass: HomeAssistant) -> None:
    """Test clicking 'Pay Now' triggers FinTS transfer."""
    entry_id = "test_entry"
    entry = MockConfigEntry(domain=DOMAIN, data=MOCK_CONFIG, entry_id=entry_id)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Manually add a pending transaction
    txn_id = "test_txn"
    hass.data[DOMAIN][entry_id]["pending"][txn_id] = {
        "amount": Decimal("10.00"),
        "merchant": "Bakery",
    }

    # Simulate notification action event
    with patch("custom_components.fints_autopay.execute_fints_transfer") as mock_transfer:
        hass.bus.async_fire(
            "mobile_app_notification_action",
            {"action": f"FINTS_PAY::{entry_id}::{txn_id}"},
        )
        await hass.async_block_till_done()

        # Verify transfer was executed with correct data
        mock_transfer.assert_called_once()
        assert mock_transfer.call_args[0][1] == Decimal("10.00")
        assert mock_transfer.call_args[0][2] == "Bakery"
        
        # Verify transaction was removed from pending
        assert txn_id not in hass.data[DOMAIN][entry_id]["pending"]
