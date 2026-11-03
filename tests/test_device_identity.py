"""Identity regression tests with a small Home Assistant protocol fake.

Run with ``python -m unittest discover -s tests -v``. These tests execute the
integration methods without requiring Home Assistant (unsupported on Windows).
They do not replace a live Home Assistant integration test.
"""

import ast
import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol
import unittest
from time import monotonic
from unittest.mock import AsyncMock, Mock


COMPONENT = Path(__file__).resolve().parents[1] / "custom_components/ecoflow_modbus"


class AbortFlow(Exception):
    """Represent Home Assistant's flow abort."""


class ConfigEntries:
    def __init__(self, entries):
        self.entries = entries
        self.updates = []

    def async_update_entry(self, entry, **changes):
        self.updates.append(changes)
        for key, value in changes.items():
            setattr(entry, key, value)


class FlowBase:
    def __init_subclass__(cls, **kwargs):
        pass

    def _async_current_entries(self, include_ignore=False):
        return [entry for entry in self.hass.config_entries.entries
                if include_ignore or entry.source != "ignore"]

    async def async_set_unique_id(self, unique_id):
        self.unique_id = unique_id

    def _abort_if_unique_id_configured(self, updates=None):
        for entry in self.hass.config_entries.entries:
            if entry.unique_id != self.unique_id:
                continue
            if entry.source == "ignore" and self.source == "user":
                return
            if updates:
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, **updates}
                )
            raise AbortFlow("already_configured")

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", "unique_id": self.unique_id, **kwargs}

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}


def load_methods(filename, namespace):
    """Load real definitions, replacing only unavailable framework imports."""
    tree = ast.parse((COMPONENT / filename).read_text(encoding="utf-8"))
    tree.body = [node for node in tree.body if not isinstance(
        node, (ast.Import, ast.ImportFrom)
    )]
    namespace.setdefault("monotonic", monotonic)
    namespace.setdefault("UpdateFailed", RuntimeError)
    exec(compile(tree, str(COMPONENT / filename), "exec"), namespace)
    return namespace


def entry(unique_id="legacy", host="192.0.2.1", serial=None, source="user"):
    data = {"host": host, "port": 502}
    if serial:
        data["serial"] = serial
    return SimpleNamespace(unique_id=unique_id, data=data, source=source,
                           entry_id="keep-this-entry", title="My inverter")


class IdentityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        namespace = load_methods("const.py", {})
        load_methods("product_profiles.py", namespace)
        namespace.update(
            logging=__import__("logging"), CONF_HOST="host", CONF_PORT="port",
            vol=SimpleNamespace(Schema=lambda value: value,
                                Required=lambda key, **kw: key,
                                Optional=lambda key, **kw: key),
            config_entries=SimpleNamespace(ConfigFlow=FlowBase, SOURCE_IGNORE="ignore"),
            probe_device=AsyncMock(return_value=(True, "SN1234")),
        )
        self.ns = load_methods("config_flow.py", namespace)

    def flow(self, entries=(), source="zeroconf"):
        flow = self.ns["EcoFlowModbusFlowHandler"]()
        flow.hass = SimpleNamespace(config_entries=ConfigEntries(list(entries)))
        flow.source = source
        flow.context = {}
        return flow

    async def discover(self, flow, host="192.0.2.1", serial="SN1234"):
        return await flow.async_step_zeroconf(SimpleNamespace(
            host=host, port=502, properties={"serial": serial, "model": "PowerOcean"}
        ))

    async def test_legacy_arbitrary_id_matches_endpoint_and_keeps_entry(self):
        existing = entry()
        flow = self.flow([existing])
        with self.assertRaisesRegex(AbortFlow, "already_configured"):
            await self.discover(flow)
        self.assertEqual(existing.unique_id, "legacy")
        self.assertEqual(existing.entry_id, "keep-this-entry")
        self.assertEqual(existing.data["serial"], "SN1234")

    async def test_saved_serial_matches_after_ip_change(self):
        existing = entry(serial="SN1234")
        with self.assertRaises(AbortFlow):
            await self.discover(self.flow([existing]), host="192.0.2.9")
        self.assertEqual(existing.data["host"], "192.0.2.9")

    async def test_serial_matches_before_reused_ip(self):
        other = entry(unique_id="other", serial="OTHER")
        existing = entry(host="192.0.2.9", serial="SN1234")
        with self.assertRaises(AbortFlow):
            await self.discover(self.flow([other, existing]))
        self.assertEqual(other.data["serial"], "OTHER")
        self.assertEqual(existing.data["host"], "192.0.2.1")

    async def test_manual_add_matches_discovered_entry_by_sn(self):
        existing = entry(unique_id="SN1234", host="inverter.local", serial="SN1234")
        with self.assertRaises(AbortFlow):
            await self.flow([existing], "user").async_step_user(
                {"host": "192.0.2.1", "port": 502}
            )

    async def test_new_manual_entry_stores_serial_and_uses_serial_id(self):
        result = await self.flow(source="user").async_step_user(
            {"host": "192.0.2.1", "port": 502}
        )
        self.assertEqual(result["data"]["serial"], "SN1234")
        self.assertEqual(result["unique_id"], "SN1234")

    async def test_yaml_import_matches_serial_based_entry_by_endpoint(self):
        with self.assertRaises(AbortFlow):
            await self.flow([entry(unique_id="SN1234", serial="SN1234")],
                            "import").async_step_import({"host": "192.0.2.1"})

    async def test_confirm_rechecks_entries_created_while_form_was_open(self):
        flow = self.flow()
        self.assertEqual((await self.discover(flow))["type"], "form")
        flow.hass.config_entries.entries.append(entry(serial="SN1234"))
        with self.assertRaises(AbortFlow):
            await flow.async_step_zeroconf_confirm({})

    async def test_no_serial_discovery_matches_existing_endpoint(self):
        with self.assertRaises(AbortFlow):
            await self.discover(self.flow([entry(unique_id="SN1234")]), serial="")

    async def test_entry_without_unique_id_is_reused(self):
        existing = entry(unique_id=None)
        with self.assertRaises(AbortFlow):
            await self.discover(self.flow([existing]))
        self.assertEqual(existing.unique_id, "SN1234")

    async def test_ignored_discovery_remains_ignored(self):
        existing = entry(unique_id="SN1234", source="ignore")
        flow = self.flow([existing])
        with self.assertRaises(AbortFlow):
            await self.discover(flow)
        self.assertEqual(flow.hass.config_entries.updates, [])

    async def test_poll_persists_sn_before_device_registry_exists(self):
        namespace = dict(self.ns)
        namespace.update(DataUpdateCoordinator=object, asyncio=asyncio,
                         create_link=Mock(), DeviceInfo=dict, REGISTERS=[])
        coordinator_cls = load_methods("coordinator.py", namespace)[
            "EcoFlowModbusCoordinator"
        ]
        coordinator = object.__new__(coordinator_cls)
        coordinator.entry = entry()
        coordinator.hass = SimpleNamespace(config_entries=ConfigEntries([coordinator.entry]))
        coordinator._partitions = [[{"offset": 0}]]
        coordinator._cursor = 0
        coordinator._reconnecting = False
        coordinator._last_response = monotonic()
        coordinator._response_timeout = 15
        coordinator._data_cache = {}
        coordinator.serial = None
        coordinator._serial_loaded = False
        coordinator._display_version = None
        coordinator._applied_serial = None
        coordinator._infer_product = True
        coordinator.product = "PowerOcean"
        coordinator.platforms_loaded = False
        coordinator._product_reload_scheduled = False
        coordinator.link = SimpleNamespace(
            last_response=0,
            read_all=AsyncMock(return_value={0: 1}),
            read_one=AsyncMock(return_value="SN1234"),
        )
        coordinator.async_refresh_device_display = AsyncMock()
        await coordinator._async_update_data()
        self.assertEqual(coordinator.entry.data["serial"], "SN1234")
        await coordinator._async_update_data()
        self.assertEqual(len(coordinator.hass.config_entries.updates), 1)
        self.assertEqual(coordinator.entry.unique_id, "legacy")

    async def test_manual_product_is_selected_from_sn(self):
        for serial, product in (
            ("ES21ZE1B2J7E0019", "STREAM 5000"),
            ("ES22ZE1B2J7E0019", "STREAM AC 5000"),
            ("OTHER0019", "PowerOcean"),
            (None, "PowerOcean"),
        ):
            with self.subTest(serial=serial):
                self.ns["probe_device"].return_value = (True, serial)
                result = await self.flow(source="user").async_step_user(
                    {"host": "192.0.2.1", "port": 502}
                )
                self.assertEqual(result["data"]["product"], product)

    async def test_discovery_reads_model_txt_field(self):
        flow = self.flow()
        result = await flow.async_step_zeroconf(SimpleNamespace(
            host="192.0.2.1", port=502,
            properties={"model": "STREAM AC 5000", "serial": "SN0019"},
        ))
        self.assertEqual(result["description_placeholders"]["name"],
                         "STREAM AC 5000-0019")
        result = await flow.async_step_zeroconf_confirm({})
        self.assertEqual(result["data"]["product"], "STREAM AC 5000")
        self.assertEqual(result["data"]["serial"], "SN0019")


class ProductTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ns = load_methods("const.py", {})
        load_methods("product_profiles.py", self.ns)
        load_methods("registers_schema.py", self.ns)

    def test_profiles_normalize_sn_and_use_fallback(self):
        for serial, expected in (
            (" ES21abcd ", "STREAM 5000"),
            ("es22abcd", "STREAM AC 5000"),
            ("ES23abcd", "PowerOcean"), (None, "PowerOcean"),
        ):
            with self.subTest(serial=serial):
                self.assertEqual(self.ns["product_from_serial"](serial), expected)

    def test_new_model_can_be_added_using_only_profile_table(self):
        self.ns["PRODUCT_SN_PREFIXES"]["Future model"] = ("ES30", "ES31")
        self.ns["PRODUCT_SN_PREFIXES"]["Special model"] = ("ES301",)
        resolve = self.ns["product_from_serial"]
        self.assertEqual(resolve("ES300019"), "Future model")
        self.assertEqual(resolve("ES310019"), "Future model")
        self.assertEqual(resolve("ES301019"), "Special model")

    def coordinator(self, serial=None, source="user", product="PowerOcean"):
        class CoordinatorBase:
            def __init__(self, hass, *args, **kwargs):
                self.hass = hass

        link = SimpleNamespace(last_response=0, read_one=AsyncMock(return_value=serial),
                               read_all=AsyncMock(return_value={}))
        self.ns.update(DataUpdateCoordinator=CoordinatorBase, asyncio=asyncio,
                       logging=__import__("logging"), timedelta=timedelta,
                       create_link=Mock(return_value=link), DeviceInfo=dict)
        cls = load_methods("coordinator.py", self.ns)["EcoFlowModbusCoordinator"]
        existing = entry(serial=serial, source=source)
        existing.data["product"] = product
        manager = ConfigEntries([existing])
        manager.async_schedule_reload = Mock()
        coordinator = cls(SimpleNamespace(config_entries=manager),
                          dict(existing.data), existing.entry_id, existing)
        coordinator.async_refresh_device_display = AsyncMock()
        return coordinator

    async def test_legacy_wrong_model_is_corrected_before_first_poll(self):
        for prefix, expected in (("ES21", "STREAM 5000"),
                                 ("ES22", "STREAM AC 5000")):
            with self.subTest(prefix=prefix):
                coordinator = self.coordinator(serial=prefix + "0019")
                await coordinator._async_update_data()
                self.assertEqual(coordinator.product, expected)
                self.assertEqual(coordinator.device_info["model"], expected)
                self.assertEqual(coordinator.entry.data["product"], expected)
                self.assertTrue(coordinator.registers)
                self.assertTrue(all(expected in reg["products"]
                                    for reg in coordinator.registers))
                offsets = {reg["offset"] for reg in coordinator.registers}
                self.assertNotIn(0x08D2, offsets)  # PowerOcean-only daily energy.
                coordinator.hass.config_entries.async_schedule_reload.assert_not_called()

    async def test_sn_is_read_before_product_specific_registers(self):
        coordinator = self.coordinator()
        coordinator.link.read_one.return_value = "ES220019"
        await coordinator._async_update_data()
        self.assertEqual(coordinator.product, "STREAM AC 5000")
        polled = coordinator.link.read_all.await_args.args[0]
        self.assertTrue(all("STREAM AC 5000" in reg["products"] for reg in polled))
        self.assertEqual(coordinator.entry.data["serial"], "ES220019")

    async def test_delayed_sn_reloads_entities_once(self):
        coordinator = self.coordinator()
        await coordinator._async_update_data()  # SN not yet available.
        self.assertEqual(coordinator.product, "PowerOcean")
        coordinator.platforms_loaded = True
        coordinator.link.read_one.return_value = "ES210019"
        await coordinator._async_update_data()
        await coordinator._async_update_data()
        self.assertEqual(coordinator.product, "STREAM 5000")
        coordinator.hass.config_entries.async_schedule_reload.assert_called_once_with(
            "keep-this-entry"
        )

    async def test_explicit_mdns_product_is_preserved(self):
        coordinator = self.coordinator(serial="ES220019", source="zeroconf",
                                       product="STREAM 5000")
        await coordinator._async_update_data()
        self.assertEqual(coordinator.product, "STREAM 5000")


class ProbeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        namespace = load_methods("const.py", {})
        namespace.update(
            asyncio=asyncio, logging=__import__("logging"), Any=Any, Protocol=Protocol,
            ModbusClient=Mock(), decode_value=lambda *args: " SN1234 ",
            encode_value=Mock(), word_count=Mock(), HAS_SHARED_CONNECTION=False,
        )
        self.ns = load_methods("link.py", namespace)
        # Imports are replaced in this harness; exercise both link paths.
        self.ns["HAS_SHARED_CONNECTION"] = False

    async def test_own_client_reads_identity_and_closes(self):
        client = Mock()
        client.read_one.side_effect = [1, " SN1234 "]
        self.ns["ModbusClient"].return_value = client

        async def executor(func, *args):
            return func(*args)

        result = await self.ns["probe_device"](
            SimpleNamespace(async_add_executor_job=executor), {"host": "192.0.2.1"}
        )
        self.assertEqual(result, (True, "SN1234"))
        client.close.assert_called_once()

    async def test_own_client_optional_sn_failure_allows_setup(self):
        client = Mock()
        client.read_one.side_effect = [1, TimeoutError()]
        self.ns["ModbusClient"].return_value = client

        async def executor(func, *args):
            return func(*args)

        result = await self.ns["probe_device"](
            SimpleNamespace(async_add_executor_job=executor), {"host": "192.0.2.1"}
        )
        self.assertEqual(result, (True, None))
        client.close.assert_called_once()

    async def shared_probe(self, read):
        @asynccontextmanager
        async def temporary_unit(*args):
            yield SimpleNamespace(read_holding_registers=read)

        self.ns.update(HAS_SHARED_CONNECTION=True,
                       async_get_temporary_unit=temporary_unit,
                       ModbusTcpParams=lambda **kwargs: kwargs,
                       LOCAL_MODBUS_TIMEOUT_S=0.01)
        return await self.ns["probe_device"](None, {"host": "192.0.2.1"})

    async def test_shared_optional_sn_timeout_allows_setup(self):
        async def read(address, count):
            if count == 8:
                await asyncio.Future()
            return [1]

        self.assertEqual(await self.shared_probe(read), (True, None))

    async def test_shared_connection_timeout_fails_setup(self):
        async def read(address, count):
            await asyncio.Future()

        self.assertEqual(await self.shared_probe(read), (False, None))

    async def test_shared_client_reads_serial(self):
        read = AsyncMock(side_effect=[[1], [0] * 8])
        self.assertEqual(await self.shared_probe(read), (True, "SN1234"))
        self.assertEqual(read.await_args_list[1].args, (40004, 8))


if __name__ == "__main__":
    unittest.main()
