"""Button platform: one-shot write registers (heartbeat etc.)."""

from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import BUTTON_WRITES, DOMAIN


class EcoFlowButton(CoordinatorEntity, ButtonEntity):
    """A register written once on press."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, reg, write_value):
        super().__init__(coordinator)
        self._reg = reg
        self._write_value = write_value
        self._attr_translation_key = f"reg_{reg['offset']:04x}"
        self._attr_unique_id = f"{coordinator.key}-{reg['offset']:04X}"
        self._attr_device_info = coordinator.device_info

    async def async_press(self):
        await self.coordinator.async_write(
            self._reg["offset"], self._reg["dtype"], self._write_value
        )


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        EcoFlowButton(coordinator, reg, BUTTON_WRITES[reg["name"]])
        for reg in coordinator.registers
        if reg["name"] in BUTTON_WRITES
    ]
    async_add_entities(entities)