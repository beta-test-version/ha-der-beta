"""EcoFlow Modbus TCP integration for Home Assistant.

Setup paths:
- mDNS  auto-discovery: the simulator advertises _ecoflow._tcp.local.
- UI   manual: Settings -> Devices & Services -> Add Integration.
- YAML import: ecoflow_modbus: - host: ... entries become config entries.
"""

import logging

import voluptuous as vol

from homeassistant.config_entries import SOURCE_IMPORT
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_ADDRESS_OFFSET,
    CONF_PRODUCT,
    CONF_SCAN_INTERVAL,
    CONF_UNIT_ID,
    DEFAULT_ADDRESS_OFFSET,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_UNIT_ID,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import EcoFlowModbusCoordinator

LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.All(
            cv.ensure_list,
            [
                vol.Schema(
                    {
                        vol.Required(CONF_HOST): cv.string,
                        vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
                        vol.Optional(CONF_UNIT_ID, default=DEFAULT_UNIT_ID): cv.positive_int,
                        vol.Optional(CONF_PRODUCT): cv.string,
                        vol.Optional(
                            CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL
                        ): cv.positive_int,
                        vol.Optional(
                            CONF_ADDRESS_OFFSET,
                            default=DEFAULT_ADDRESS_OFFSET,
                        ): cv.positive_int,
                    }
                )
            ],
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Load optional frontend styling and migrate YAML config entries."""
    from .frontend import async_setup_frontend

    try:
        await async_setup_frontend(hass)
    except Exception:  # Styling must not prevent device communication.
        LOGGER.warning("Could not load EcoFlow control layout", exc_info=True)
    for conf in config.get(DOMAIN) or []:
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_IMPORT}, data=dict(conf)
            )
        )
    return True


def _migrate_error_code_entity(hass, entry) -> None:
    """Keep the entity ID when error code 10 moves from 0x2020 to 0x201A."""
    registry = er.async_get(hass)
    old_id = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}-2020"
    )
    new_unique_id = f"{entry.entry_id}-201A"
    if old_id and not registry.async_get_entity_id("sensor", DOMAIN, new_unique_id):
        registry.async_update_entity(
            old_id, new_unique_id=new_unique_id, translation_key="reg_201a"
        )


async def async_setup_entry(hass: HomeAssistant, entry) -> bool:
    # Normalize the entry title for entries created by older versions.
    expected_title = "ha EcoFlow official (" + str(entry.data[CONF_HOST]) + ")"
    if entry.title != expected_title:
        hass.config_entries.async_update_entry(entry, title=expected_title)
    coordinator = EcoFlowModbusCoordinator(
        hass, dict(entry.data), entry.entry_id, entry
    )
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception as exc:  # noqa: BLE001
        await coordinator.async_close()
        raise ConfigEntryNotReady(
            "Cannot reach " + str(entry.data[CONF_HOST]) + ": " + str(exc)
        ) from exc

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    _migrate_error_code_entity(hass, entry)
    # Built-in keep-alive: write the heartbeat register every 20s.
    await coordinator.async_start_connection_monitor()
    await coordinator.async_start_heartbeat()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.platforms_loaded = True
    # Devices are created when their first entity lands, so the display
    # refresh (product-SN naming) must run after forwarding.
    await coordinator.async_refresh_device_display()
    # Unsupported points are not shown at all: prune entities this entry
    # created in the past but that no longer match the product filter.
    expected = {
        f"{entry.entry_id}-{reg['offset']:04X}"
        for reg in coordinator.registers
    }
    ent_reg = er.async_get(hass)
    for entity in list(ent_reg.entities.values()):
        if (
            entity.config_entry_id == entry.entry_id
            and entity.unique_id not in expected
        ):
            ent_reg.async_remove(entity.entity_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    coordinator = hass.data[DOMAIN].pop(entry.entry_id)
    await coordinator.async_close()
    return True
