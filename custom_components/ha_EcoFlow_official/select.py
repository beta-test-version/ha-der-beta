"""Select platform: enumerated control registers."""

from homeassistant.components.select import SelectEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, SELECTS


class EcoFlowSelect(CoordinatorEntity, SelectEntity):
    """An enumerated register exposed as a dropdown."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, reg, options):
        super().__init__(coordinator)
        self._reg = reg
        self._options = options
        self._attr_translation_key = f"reg_{reg['offset']:04x}"
        self._attr_unique_id = f"{coordinator.key}-{reg['offset']:04X}"
        self._attr_device_info = coordinator.device_info
        self._attr_options = [str(v) for v in options]

    @property
    def current_option(self):
        if self.coordinator.data is None:
            return None
        value = self.coordinator.data.get(self._reg["offset"])
        return str(value) if value is not None and value in self._options else None

    async def async_select_option(self, option):
        await self.coordinator.async_write(
            self._reg["offset"], self._reg["dtype"], int(option)
        )


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        EcoFlowSelect(coordinator, reg, SELECTS[reg["offset"]])
        for reg in coordinator.registers
        if reg["offset"] in SELECTS
    ]
    async_add_entities(entities)