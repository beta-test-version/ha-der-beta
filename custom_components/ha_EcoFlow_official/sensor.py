"""Sensor platform: read-only registers."""

import math

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.components.sensor.const import SensorDeviceClass
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, EXCLUDED_OFFSETS

# UINT32 registers shown as dotted versions (high -> low byte segments)
_VERSION_OFFSETS = {0x0000, 0x000B}

# HA selects a BATTERY sensor for the device header. Only the system SOC
# represents the whole device; pack SOC/SOH and SOC limits must not compete.
_SYSTEM_SOC_OFFSET = 0x020E

# Classify the point table by meaning, not by unit: rated values and control
# limits can use the same units as actual telemetry.
_MEASUREMENT_OFFSETS = frozenset({
    0x0016, 0x0017,  # Inverter active/reactive power.
    0x0206, 0x0208, 0x020A, 0x020C, 0x020E, 0x0264,  # System flow and SOC.
    0x022E, 0x0230, 0x0232, 0x0234, 0x0236, 0x0238,  # Grid current/voltage.
    0x1300, 0x1302, 0x1304,  # Per-phase active power.
    0x0253, 0x0255, 0x0257, 0x0259, 0x025B, 0x025D,  # PV voltage/current.
    0x2100, 0x2102, 0x2104, 0x2106, 0x2108, 0x210A,  # PV4 V/A and PV power.
    0x0227, 0x023D, 0x023F, 0x0241,  # Battery capacity, voltage/current/temp.
    0x0229, 0x022B, 0x0272, 0x0273, 0x1400, 0x1401,  # Available battery power.
    0x2202, 0x2204, 0x2206, 0x2207,  # Measured cell voltage/temp extremes.
    0x0225, 0x0243, 0x0245, 0x0247, 0x0249, 0x024B, 0x024D, 0x024F, 0x0251,
    0x2401, 0x2404, 0x2407,  # Bypass outlet power.
    0x0800, 0x0820, 0x1000, 0x1002, 0x2010,  # Current fault/device counts.
    *range(0x0821, 0x082D),  # Individual battery SOC.
    *range(0x2300, 0x230C),  # Individual battery SOH.
})
# Directional energy counters: lifetime and today's readings. Daily counters
# reset at midnight; lifetime counters can reset when the device is reset.
_ENERGY_COUNTER_OFFSETS = frozenset({
    0x0870, 0x0872, 0x0880, 0x0882,  # Grid import/export.
    0x0890, 0x0892, 0x08A0, 0x08A2,  # Inverter charge/discharge.
    0x08B0, 0x08B2, 0x08C0, 0x08C2,  # Battery charge/discharge.
    0x08D0, 0x08D2, 0x1200, 0x1202,  # Solar production/home consumption.
})
# 0x0840/0x0850/0x0860 say only "total energy (today)" in the point table.
# Keep ordinary history until net-vs-throughput and reset semantics are known.
_NUMERIC_TYPES = frozenset({"INT16", "UINT16", "INT32", "UINT32", "FLOAT"})
_BATTERY_CAPACITY_OFFSET = 0x0227

# Enum sensors: offset -> {value: label}
_ENUM_MAPS = {
    0x0001: {1: "Three-phase system", 2: "Single-phase system"},
    0x2400: {0: "Off grid", 1: "On grid"},
}

UNIT_DEVICE_CLASS = {
    "W": (SensorDeviceClass.POWER, UnitOfPower.WATT),
    "V": (SensorDeviceClass.VOLTAGE, UnitOfElectricPotential.VOLT),
    "mV": (SensorDeviceClass.VOLTAGE, UnitOfElectricPotential.MILLIVOLT),
    "A": (SensorDeviceClass.CURRENT, UnitOfElectricCurrent.AMPERE),
    "Hz": (SensorDeviceClass.FREQUENCY, UnitOfFrequency.HERTZ),
    "KWH": (SensorDeviceClass.ENERGY, UnitOfEnergy.KILO_WATT_HOUR),
    "WH": (SensorDeviceClass.ENERGY, UnitOfEnergy.WATT_HOUR),
    "VA": (SensorDeviceClass.APPARENT_POWER, "VA"),
    "Var": (SensorDeviceClass.REACTIVE_POWER, "var"),
    "℃": (SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS),
}


def unit_device_class(unit, name):
    if unit == "%":
        return (None, PERCENTAGE)
    return UNIT_DEVICE_CLASS.get(unit, (None, unit if unit != "/" else None))


def _register_state_class(reg):
    """Apply reviewed statistics semantics to existing read-only sensors."""
    if reg["permission"] != "R" or reg["dtype"] not in _NUMERIC_TYPES:
        return None
    if reg["offset"] in _ENERGY_COUNTER_OFFSETS:
        return SensorStateClass.TOTAL_INCREASING
    if reg["offset"] in _MEASUREMENT_OFFSETS:
        return SensorStateClass.MEASUREMENT
    return None


class EcoFlowSensor(CoordinatorEntity, SensorEntity):
    """A read-only register exposed as a sensor."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, reg):
        super().__init__(coordinator)
        self._reg = reg
        self._attr_translation_key = f"reg_{reg['offset']:04x}"
        self._attr_unique_id = f"{coordinator.key}-{reg['offset']:04X}"
        self._attr_device_info = coordinator.device_info
        device_class, unit = unit_device_class(reg["unit"], reg["name"])
        if reg["offset"] == _BATTERY_CAPACITY_OFFSET:
            device_class = SensorDeviceClass.ENERGY_STORAGE
        if reg["offset"] == _SYSTEM_SOC_OFFSET:
            device_class = SensorDeviceClass.BATTERY
        elif reg["unit"] == "%" and ("SOC" in reg["name"] or "SOH" in reg["name"]):
            self._attr_icon = "mdi:battery"
        if reg["offset"] in _ENUM_MAPS:
            device_class = SensorDeviceClass.ENUM
            unit = None
            self._attr_options = [
                str(v) for v in _ENUM_MAPS[reg["offset"]]
            ]
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        self._attr_state_class = _register_state_class(reg)
        if reg["dtype"] == "FLOAT":
            self._attr_suggested_display_precision = 2

    @property
    def native_value(self):
        if self.coordinator.data is None:
            return None
        value = self.coordinator.data.get(self._reg["offset"])
        if value is None:
            return None
        if self._attr_state_class is not None:
            # Invalid readings must not poison statistics or become false resets.
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
            ):
                return None
            if self._attr_state_class == SensorStateClass.TOTAL_INCREASING and value < 0:
                return None
        options = _ENUM_MAPS.get(self._reg["offset"])
        if options:
            return str(value) if value in options else None
        if (
            self._reg["offset"] in _VERSION_OFFSETS
            and isinstance(value, int)
            and not isinstance(value, bool)
            and self._reg["dtype"] == "UINT32"
        ):
            return ".".join(str((value >> shift) & 0xFF) for shift in (24, 16, 8, 0))
        return value


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        EcoFlowSensor(coordinator, reg)
        for reg in coordinator.registers
        if reg["permission"] == "R" and reg["offset"] not in EXCLUDED_OFFSETS
    ]
    async_add_entities(entities)
