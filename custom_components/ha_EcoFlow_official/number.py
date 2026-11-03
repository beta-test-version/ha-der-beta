"""Number platform: writable numeric registers."""


from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    BUTTON_WRITES,
    DOMAIN,
    EXCLUDED_OFFSETS,
    SELECTS,
    SWITCHES,
)
from .sensor import unit_device_class

_BOUNDS_BY_OFFSET = {
    0x021C: (0, 100),  # LED brightness
    0x0217: (0, 100),  # Backup reserve ratio
    0x023C: (1, 100),  # Feed-in limit percentage
    0x2200: (0, 100),  # SOC lower limit
    0x2201: (0, 100),  # SOC upper limit
    0x021B: (0, 120),  # Breaker capacity
    0x000D: (1, 254),  # Device communication address
}

# Storage limits are the fallback; documented point limits take precedence.
_BOUNDS_BY_TYPE = {
    "INT16": (-32768, 32767),
    "UINT16": (0, 65535),
    "INT32": (-2147483648, 2147483647),
    "UINT32": (0, 4294967295),
}


def _is_special(reg):
    return (
        reg["offset"] in SWITCHES
        or reg["name"] in BUTTON_WRITES
        or reg["offset"] in SELECTS
        or reg["offset"] in EXCLUDED_OFFSETS
        or reg["dtype"] == "STRING"
    )


def _writable_numbers(registers):
    for reg in registers:
        if reg["permission"] in ("RW", "W") and not _is_special(reg):
            yield reg


def _bounds(reg):
    return _BOUNDS_BY_OFFSET.get(
        reg["offset"], _BOUNDS_BY_TYPE.get(reg["dtype"], (0, 100))
    )


class EcoFlowNumber(CoordinatorEntity, NumberEntity):
    """A writable numeric register exposed as a number."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, reg):
        super().__init__(coordinator)
        self._reg = reg
        self._attr_translation_key = f"reg_{reg['offset']:04x}"
        self._attr_unique_id = f"{coordinator.key}-{reg['offset']:04X}"
        self._attr_device_info = coordinator.device_info
        device_class, unit = unit_device_class(reg["unit"], reg["name"])
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        min_value, max_value = _bounds(reg)
        self._attr_native_min_value = min_value
        self._attr_native_max_value = max_value
        self._attr_native_step = 0.1 if reg["dtype"] == "FLOAT" else 1
        # A slider cannot select precise watt values across an integer range.
        self._attr_mode = (
            NumberMode.BOX if max_value - min_value > 1000 else NumberMode.AUTO
        )

    @property
    def native_value(self):
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._reg["offset"])

    async def async_set_native_value(self, value):
        if self._reg["dtype"] == "FLOAT":
            value = float(value)
        else:
            value = int(value)
        await self.coordinator.async_write(
            self._reg["offset"], self._reg["dtype"], value, self._reg.get("length", 8)
        )


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        EcoFlowNumber(coordinator, reg)
        for reg in _writable_numbers(coordinator.registers)
    ]
    async_add_entities(entities)
