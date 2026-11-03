"""Switch platform: 0/1 toggle registers."""

from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SWITCHES


class EcoFlowSwitch(CoordinatorEntity, SwitchEntity):
    """A 0/1 register exposed as a toggle switch."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, reg, values):
        super().__init__(coordinator)
        self._reg = reg
        self._on_value, self._off_value = values
        self._attr_translation_key = f"reg_{reg['offset']:04x}"
        self._attr_unique_id = f"{coordinator.key}-{reg['offset']:04X}"
        self._attr_device_info = coordinator.device_info

    @property
    def is_on(self):
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._reg["offset"]) == self._on_value

    async def async_turn_on(self):
        await self.coordinator.async_write(
            self._reg["offset"], self._reg["dtype"], self._on_value
        )

    async def async_turn_off(self):
        await self.coordinator.async_write(
            self._reg["offset"], self._reg["dtype"], self._off_value
        )


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        EcoFlowSwitch(coordinator, reg, SWITCHES[reg["offset"]])
        for reg in coordinator.registers
        if reg["offset"] in SWITCHES
    ]
    async_add_entities(entities)