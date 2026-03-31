"""Test the Wallet Auto-Pay config flow."""
from homeassistant import config_entries, data_entry_flow
from homeassistant.core import HomeAssistant

from custom_components.wallet_autopay.const import DOMAIN
from tests.const import MOCK_CONFIG


async def test_form(hass: HomeAssistant) -> None:
    """Test we get the form and create entry."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM

    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        MOCK_CONFIG,
    )

    assert result2["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result2["title"] == f"Wallet Pay: {MOCK_CONFIG['recipient_name']}"
    assert result2["data"] == MOCK_CONFIG
