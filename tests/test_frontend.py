"""Optional frontend registration without an installed HA runtime."""
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from test_device_identity import COMPONENT, load_methods


class FrontendTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ns = load_methods("const.py", {})
        self.ns.update(Path=Path, __file__=str(COMPONENT / "frontend.py"),
                       StaticPathConfig=lambda *args: args, add_extra_js_url=Mock(),
                       async_when_setup=Mock())
        load_methods("frontend.py", self.ns)
        self.hass = SimpleNamespace(
            config=SimpleNamespace(components={"frontend"}), data={},
            http=SimpleNamespace(async_register_static_paths=AsyncMock()))

    async def test_multiple_entries_share_one_module_and_static_path(self):
        for _ in range(2):
            await self.ns["async_setup_frontend"](self.hass)
        self.hass.http.async_register_static_paths.assert_awaited_once()
        paths = self.hass.http.async_register_static_paths.await_args.args[0]
        url, path, cache = paths[0]
        self.assertTrue(Path(path).is_file())
        self.assertFalse(cache)
        self.ns["add_extra_js_url"].assert_called_once_with(self.hass, url)

    async def test_headless_ha_does_not_load_frontend(self):
        self.hass.config.components.clear()
        await self.ns["async_setup_frontend"](self.hass)
        self.hass.http.async_register_static_paths.assert_not_awaited()
        self.ns["add_extra_js_url"].assert_not_called()
        self.ns["async_when_setup"].assert_called_once()

    async def test_frontend_loaded_after_integration_registers_module(self):
        self.hass.config.components.clear()
        for _ in range(2):
            await self.ns["async_setup_frontend"](self.hass)
        self.ns["async_when_setup"].assert_called_once()
        callback = self.ns["async_when_setup"].call_args.args[2]
        self.hass.config.components.add("frontend")
        await callback(self.hass, "frontend")
        self.hass.http.async_register_static_paths.assert_awaited_once()
        self.ns["add_extra_js_url"].assert_called_once_with(
            self.hass, "/ecoflow_modbus/compact-controls-v2.js")

    async def test_failed_registration_does_not_mark_module_loaded(self):
        self.hass.http.async_register_static_paths.side_effect = OSError("failed")
        with self.assertRaises(OSError):
            await self.ns["async_setup_frontend"](self.hass)
        self.assertFalse(self.hass.data)
        self.ns["add_extra_js_url"].assert_not_called()
