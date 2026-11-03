"""Config flow: manual form, mDNS auto-discovery and YAML import."""

import logging

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PORT

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
)
from .link import probe_device
from .product_profiles import product_from_serial

LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Optional(CONF_UNIT_ID, default=DEFAULT_UNIT_ID): int,
        vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): int,
    }
)


def _defaults(extra=None):
    data = {
        CONF_PORT: DEFAULT_PORT,
        CONF_UNIT_ID: DEFAULT_UNIT_ID,
        CONF_ADDRESS_OFFSET: DEFAULT_ADDRESS_OFFSET,
        CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
    }
    if extra:
        data.update(extra)
    return data


class EcoFlowModbusFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the configured flow for one EcoFlow Modbus device."""

    VERSION = 1

    async def _async_check_device(self, data, *, discovery=False):
        """Match stored identity and endpoint across all setup sources."""
        serial = (data.get("serial") or "").strip() or None
        host = data[CONF_HOST]
        port = data.get(CONF_PORT, DEFAULT_PORT)
        unit = data.get(CONF_UNIT_ID, DEFAULT_UNIT_ID)
        entries = self._async_current_entries(include_ignore=discovery)
        # Prefer the serial over the endpoint: DHCP may have reassigned an IP.
        match = next(
            (entry for entry in entries if serial and (
                entry.unique_id == serial or entry.data.get("serial") == serial
            )),
            None,
        )
        if match is None:
            match = next(
                (entry for entry in entries if (
                    entry.data.get(CONF_HOST) == host
                    and entry.data.get(CONF_PORT, DEFAULT_PORT) == port
                    and entry.data.get(CONF_UNIT_ID, DEFAULT_UNIT_ID) == unit
                    and not (serial and entry.data.get("serial")
                             and entry.data["serial"] != serial)
                )),
                None,
            )
        if match is not None:
            # Keep the original entry (and its entities, areas and automations).
            if match.unique_id is None:
                self.hass.config_entries.async_update_entry(
                    match, unique_id=serial or f"{host}:{port}"
                )
            await self.async_set_unique_id(match.unique_id)
            updates = None
            if discovery and match.source != config_entries.SOURCE_IGNORE:
                updates = {CONF_HOST: host, CONF_PORT: port}
                if serial:
                    updates["serial"] = serial
            self._abort_if_unique_id_configured(updates=updates)
        await self.async_set_unique_id(serial or f"{host}:{port}")
        self._abort_if_unique_id_configured()

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                reachable, serial = await probe_device(self.hass, user_input)
            except Exception:  # noqa: BLE001
                reachable = False
            if not reachable:
                errors["base"] = "cannot_connect"
            else:
                user_input = {
                    **user_input,
                    "serial": serial,
                    CONF_PRODUCT: product_from_serial(serial),
                }
                await self._async_check_device(user_input)
                return self.async_create_entry(
                    title=f"ha EcoFlow official ({user_input[CONF_HOST]})",
                    data=_defaults(user_input),
                )
        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_zeroconf(self, discovery_info):
        host = discovery_info.host
        port = discovery_info.port
        properties = discovery_info.properties or {}
        model = properties.get("model") or None
        serial = (properties.get("serial") or "").strip() or None
        self._zc_data = {
            CONF_HOST: host,
            CONF_PORT: port,
            CONF_PRODUCT: model,
            "serial": serial,
        }
        await self._async_check_device(self._zc_data, discovery=True)
        self.context["title_placeholders"] = {
            "name": self._zc_display_name(),
        }
        return await self.async_step_zeroconf_confirm()

    def _zc_display_name(self):
        product = self._zc_data[CONF_PRODUCT] or "EcoFlow"
        serial = self._zc_data.get("serial")
        return f"{product}-{serial[-4:]}" if serial else product

    async def async_step_zeroconf_confirm(self, user_input=None):
        if user_input is not None:
            await self._async_check_device(self._zc_data, discovery=True)
            data = _defaults(self._zc_data)
            return self.async_create_entry(
                title=f"ha EcoFlow official ({self._zc_data[CONF_HOST]})",
                data=data,
            )
        return self.async_show_form(
            step_id="zeroconf_confirm",
            description_placeholders={
                "name": self._zc_display_name(),
            },
        )

    async def async_step_import(self, import_info):
        stacked = _defaults(import_info)
        await self._async_check_device(stacked)
        # Connectivity is validated later in async_setup_entry, which raises
        # ConfigEntryNotReady on failure (shown in UI with a retry button).
        return self.async_create_entry(
            title=f"ha EcoFlow official ({stacked[CONF_HOST]})", data=stacked
        )
