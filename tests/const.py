"""Constants for fints_autopay tests."""
from custom_components.fints_autopay.const import (
    CONF_BLZ,
    CONF_DEVICE_ID,
    CONF_ENDPOINT,
    CONF_PIN,
    CONF_RECIPIENT_NAME,
    CONF_TARGET_IBAN,
    CONF_USERNAME,
)

MOCK_CONFIG = {
    CONF_DEVICE_ID: "test_device_id",
    CONF_BLZ: "12345678",
    CONF_USERNAME: "testuser",
    CONF_PIN: "testpin",
    CONF_ENDPOINT: "https://fints.example.com",
    CONF_TARGET_IBAN: "DE1234567890",
    CONF_RECIPIENT_NAME: "John Doe",
}
