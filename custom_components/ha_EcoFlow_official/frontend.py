"""Load the optional device-page layout module when HA has a frontend."""
from pathlib import Path

from homeassistant.setup import async_when_setup

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig

from .const import DOMAIN

_MODULE_URL = "/ecoflow_modbus/compact-controls-v2.js"
_REGISTERED = f"{DOMAIN}_frontend_registered"
_WAITING = f"{DOMAIN}_frontend_waiting"


async def async_setup_frontend(hass) -> None:
    """Register once across multiple devices; headless HA needs no module."""
    if hass.data.get(_REGISTERED):
        return
    if "frontend" not in hass.config.components:
        if not hass.data.get(_WAITING):
            async def loaded(loaded_hass, component):
                await async_setup_frontend(loaded_hass)
            async_when_setup(hass, "frontend", loaded)
            hass.data[_WAITING] = True
        return
    await hass.http.async_register_static_paths([
        StaticPathConfig(
            _MODULE_URL,
            str(Path(__file__).parent / "frontend/compact-controls.js"),
            False,
        )
    ])
    add_extra_js_url(hass, _MODULE_URL)
    hass.data[_REGISTERED] = True
