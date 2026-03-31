"""Constants for Wallet Auto-Pay tests."""
from custom_components.wallet_autopay.const import (
    CONF_DEVICE_ID,
    CONF_RECIPIENT_NAME,
    CONF_TARGET_IBAN,
)

MOCK_CONFIG = {
    CONF_DEVICE_ID: "test_device_id",
    CONF_TARGET_IBAN: "DE1234567890",
    CONF_RECIPIENT_NAME: "John Doe",
}
