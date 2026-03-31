"""Support for FinTS Auto-Pay To-Do list."""
from __future__ import annotations

import uuid
from typing import Any
from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CONF_USERNAME

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the FinTS To-Do list."""
    async_add_entities([FintsAutopayTodoList(entry)], update_before_add=True)


class FintsAutopayTodoList(TodoListEntity):
    """A FinTS Auto-Pay To-Do list."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
    )

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the to-do list."""
        self._attr_unique_id = f"{entry.entry_id}_todo"
        self._attr_name = f"FinTS Auto-Pay: {entry.data[CONF_USERNAME]}"
        self._items: list[TodoItem] = []

    @property
    def todo_items(self) -> list[TodoItem]:
        """Return the items in the to-do list."""
        return self._items

    async def async_create_todo_item(self, item: TodoItem) -> None:
        """Create a new to-do item."""
        # Home Assistant might not provide a UID during creation via service call
        if not item.uid:
            item.uid = uuid.uuid4().hex
        self._items.append(item)
        self.async_write_ha_state()

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Update a to-do item."""
        for idx, existing in enumerate(self._items):
            if existing.uid == item.uid:
                self._items[idx] = item
                break
        self.async_write_ha_state()

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        """Delete to-do items."""
        self._items = [item for item in self._items if item.uid not in uids]
        self.async_write_ha_state()
