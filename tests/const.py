"""Constants for fints_autopay tests."""
from custom_components.fints_autopay.const import (
    CONF_BLZ,
    CONF_ENDPOINT,
    CONF_NOTIFY,
    CONF_PIN,
    CONF_SENSOR,
    CONF_TARGET_IBAN,
    CONF_RECIPIENT_NAME,
    CONF_TODO,
    CONF_USERNAME,
)

MOCK_CONFIG = {
    CONF_SENSOR: "sensor.test_notification_sensor",
    CONF_NOTIFY: "notify.test_notify_service",
    CONF_TODO: "todo.test_todo_list",
    CONF_BLZ: "12345678",
    CONF_USERNAME: "testuser",
    CONF_PIN: "123456",
    CONF_ENDPOINT: "https://fints.example.com/api",
    CONF_TARGET_IBAN: "DE89370400440532013000",
    CONF_RECIPIENT_NAME: "John Doe",
}
