"""Test the FinTS Auto-Pay config flow."""
from unittest.mock import patch

from homeassistant import config_entries, data_entry_flow
from homeassistant.core import HomeAssistant

from custom_components.fints_autopay.const import DOMAIN
from const import MOCK_CONFIG


async def test_form(hass: HomeAssistant) -> None:
    """Test we get the form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == data_entry_flow.FlowResultType.FORM
    assert result["errors"] == {}

    with patch(
        "custom_components.fints_autopay.config_flow.FinTS3PinTanClient"
    ) as mock_client:
        mock_client.return_value.__enter__.return_value.get_sepa_accounts.return_value = []
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            MOCK_CONFIG,
        )

    assert result2["type"] == data_entry_flow.FlowResultType.CREATE_ENTRY
    assert result2["title"] == MOCK_CONFIG["username"]
    assert result2["data"] == MOCK_CONFIG
