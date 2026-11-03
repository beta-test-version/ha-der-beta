"""The HA device battery indicator must represent system SOC."""
from types import SimpleNamespace
import math
import unittest

from test_device_identity import load_methods


class CoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator


class SensorEntity:
    pass


class SystemSocTests(unittest.TestCase):
    def setUp(self):
        self.ns = load_methods("const.py", {})
        load_methods("registers_schema.py", self.ns)
        class Attributes:
            def __getattr__(self, name):
                return name.lower()
        self.ns.update(
            math=math, SensorEntity=SensorEntity, CoordinatorEntity=CoordinatorEntity,
            SensorDeviceClass=Attributes(), SensorStateClass=Attributes(), PERCENTAGE="%",
            **{name: Attributes() for name in (
                "UnitOfElectricCurrent", "UnitOfElectricPotential", "UnitOfEnergy",
                "UnitOfFrequency", "UnitOfPower", "UnitOfTemperature")},
        )
        load_methods("sensor.py", self.ns)
        self.coordinator = SimpleNamespace(key="device", device_info={},
                                          data={0x020E: 73})

    def sensor(self, reg):
        return self.ns["EcoFlowSensor"](self.coordinator, reg)

    def test_system_soc_is_only_device_battery_source_for_each_product(self):
        for product in ("PowerOcean", "STREAM 5000", "STREAM AC 5000"):
            with self.subTest(product=product):
                batteries = [reg["offset"] for reg in self.ns["REGISTERS"]
                             if product in reg["products"]
                             and reg["permission"] == "R"
                             and self.sensor(reg)._attr_device_class == "battery"]
                self.assertEqual(batteries, [0x020E])

    def test_indicator_reads_system_soc_and_keeps_existing_entity_identity(self):
        reg = next(r for r in self.ns["REGISTERS"] if r["offset"] == 0x020E)
        sensor = self.sensor(reg)
        self.assertEqual(sensor.native_value, 73)
        self.assertEqual(sensor._attr_native_unit_of_measurement, "%")
        self.assertEqual(sensor._attr_unique_id, "device-020E")
        self.coordinator.data[0x020E] = 0
        self.assertEqual(sensor.native_value, 0)

    def test_pack_soc_and_soh_keep_their_independent_values(self):
        for reg in self.ns["REGISTERS"]:
            if reg["unit"] != "%" or reg["offset"] == 0x020E:
                continue
            with self.subTest(offset=reg["offset"]):
                self.coordinator.data[reg["offset"]] = 90
                sensor = self.sensor(reg)
                self.assertEqual(sensor.native_value, 90)
                self.assertIsNone(sensor._attr_device_class)
                self.assertEqual(sensor._attr_native_unit_of_measurement, "%")


if __name__ == "__main__":
    unittest.main()
