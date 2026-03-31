"""Test the FinTS Auto-Pay To-Do list."""
from homeassistant.core import HomeAssistant
from homeassistant.components.todo import TodoItem, TodoItemStatus
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fints_autopay.const import DOMAIN
from tests.const import MOCK_CONFIG
from tests.test_init import setup_mock_registries


async def test_todo_list_operations(hass: HomeAssistant) -> None:
    """Test standard to-do list operations."""
    real_device_id = await setup_mock_registries(hass)
    config = MOCK_CONFIG.copy()
    config["device_id"] = real_device_id

    entry = MockConfigEntry(domain=DOMAIN, data=config, entry_id="test_entry")
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("todo.fints_auto_pay_testuser")
    assert state is not None

    # Test adding an item
    await hass.services.async_call(
        "todo",
        "add_item",
        {"entity_id": "todo.fints_auto_pay_testuser", "item": "Test Item"},
        blocking=True,
    )
    
    # Verify item exists
    # We need to access the entity through the component
    todo_list = hass.data["todo"].get_entity("todo.fints_auto_pay_testuser")
    assert len(todo_list.todo_items) == 1
    assert todo_list.todo_items[0].summary == "Test Item"
    
    # Test updating an item
    item_id = todo_list.todo_items[0].uid
    await hass.services.async_call(
        "todo",
        "update_item",
        {
            "entity_id": "todo.fints_auto_pay_testuser",
            "item": item_id,
            "rename": "Updated Item",
            "status": "completed",
        },
        blocking=True,
    )
    assert todo_list.todo_items[0].summary == "Updated Item"
    assert todo_list.todo_items[0].status == TodoItemStatus.COMPLETED

    # Test deleting an item
    await hass.services.async_call(
        "todo",
        "remove_item",
        {"entity_id": "todo.fints_auto_pay_testuser", "item": [item_id]},
        blocking=True,
    )
    assert len(todo_list.todo_items) == 0
