"""Constants for the Wallet Auto-Pay integration."""
DOMAIN = "wallet_autopay"

CONF_DEVICE_ID = "device_id"
CONF_TARGET_IBAN = "target_iban"
CONF_RECIPIENT_NAME = "recipient_name"

# We keep the old action names for internal consistency but the logic changes
ACTION_PAY_NOW = "WALLET_PAY"
ACTION_ADD_TODO = "WALLET_TODO"
DEFAULT_FALLBACK_TIMEOUT = 3600
