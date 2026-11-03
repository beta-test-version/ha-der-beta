"""Polling coordinator for one EcoFlow Modbus device.

Polling is partitioned round-robin: the registers are split into N groups
(N = scan interval in seconds, so one group is read each second) and the full
set refreshes once per scan interval. This spreads the burst of Modbus
requests evenly instead of one big burst every poll tick — friendly to
devices that serve one client at a time.
"""

import asyncio
import logging
from datetime import timedelta
from time import monotonic

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_PRODUCT,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    HEARTBEAT_INTERVAL_S,
    HEARTBEAT_OFFSET,
    RECONNECT_INTERVAL_S,
    RESPONSE_TIMEOUT_MULTIPLIER,
)
from .link import create_link
from .product_profiles import product_from_serial
from .registers_schema import REGISTERS

LOGGER = logging.getLogger(__name__)



def filter_registers(product):
    """Filter register definitions by supported product.

    Product comes from mDNS or the serial prefix for manual configuration.
    Unknown products default to the PowerOcean register set.
    """
    product = product or "PowerOcean"
    return [r for r in REGISTERS if product in r.get("products", [])]


class EcoFlowModbusCoordinator(DataUpdateCoordinator):
    """Fetches register values in rotating partitions and exposes writes."""

    def __init__(self, hass, conf, key, entry):
        self.key = key
        self.conf = conf
        self.entry = entry
        self.host = conf["host"]
        self.link = create_link(hass, entry, conf)
        self.serial = conf.get("serial") or None
        self._serial_loaded = bool(self.serial)
        self._infer_product = entry.source in {"user", "import"} or not conf.get(CONF_PRODUCT)
        self.product = (
            product_from_serial(self.serial) if self._infer_product
            else conf[CONF_PRODUCT]
        )
        self.platforms_loaded = False
        self._product_reload_scheduled = False

        self._data_cache: dict[int, object] = {}
        self._cursor = 0
        self._heartbeat_task: asyncio.Task | None = None
        self._connection_task: asyncio.Task | None = None
        self._reconnecting = False
        self._last_response = monotonic()
        self._display_version: str | None = None
        self._applied_serial: str | None = None
        scan_seconds = conf.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        self._response_timeout = scan_seconds * RESPONSE_TIMEOUT_MULTIPLIER
        self._partition_count = max(1, min(scan_seconds, 60))
        self._configure_registers()

        self.device_info = DeviceInfo(
            identifiers={(DOMAIN, key)},
            name=f"{self.product} Modbus {self.host}",
            manufacturer="EcoFlow",
            model=self.product,
        )
        if self.serial:
            self.device_info["serial_number"] = self.serial

        super().__init__(
            hass,
            LOGGER,
            name=f"EcoFlow Modbus {key}",
            update_interval=timedelta(
                seconds=scan_seconds / len(self._partitions)
            ),
        )

    def _configure_registers(self) -> None:
        """Rebuild the polling groups for the identified product."""
        self.registers = filter_registers(self.product)
        partitions = [[] for _ in range(self._partition_count)]
        for index, reg in enumerate(self.registers):
            partitions[index % self._partition_count].append(reg)
        self._partitions = [group for group in partitions if group]
        self._cursor = 0

    def serial_number(self, data=None) -> str | None:
        """Serial from mDNS config, else from the SN register value."""
        if self.serial:
            return self.serial
        if data is None:
            data = self._data_cache
        if data:
            value = data.get(0x0003)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    async def _read_serial_from_device(self) -> str or None:
        """Read the SN register directly, independent of the product filter."""
        probe = {"offset": 0x0003, "dtype": "STRING", "length": 8}
        try:
            value = await self.link.read_one(probe)
        except Exception:
            value = None
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    def _firmware_version(self, data) -> str | None:
        """Format the UINT32 firmware register as a.b.c.d, if loaded."""
        if not data:
            return None
        value = data.get(0x000B)
        if isinstance(value, int) and not isinstance(value, bool):
            return ".".join(
                str((value >> shift) & 0xFF) for shift in (24, 16, 8, 0)
            )
        return None

    async def async_refresh_device_display(self) -> None:
        """Fill device info: product name-SNlast four digits name, model and firmware.

        Devices exist only after their first entity has been created, so this
        runs after the platforms have been forwarded.
        """
        dev_reg = dr.async_get(self.hass)
        devices = dr.async_entries_for_config_entry(dev_reg, self.entry.entry_id)
        if not devices:
            return
        device = devices[0]
        serial = self.serial_number(self._data_cache)
        updates = {"model": self.product}
        firmware = self._firmware_version(self._data_cache)
        if firmware:
            updates["sw_version"] = firmware
        if serial:
            display_name = f"{self.product}-{serial[-4:]}"
            updates["name"] = display_name
            updates["serial_number"] = serial
            # Entry title shows product + SN tail as well, with the host
            # appended so entry and device names stay distinct.
            entry_title = f"{self.product}-{serial[-4:]} ({self.host})"
            if self.entry is not None and self.entry.title != entry_title:
                self.hass.config_entries.async_update_entry(
                    self.entry, title=entry_title
                )
        dev_reg.async_update_device(device.id, **updates)

    async def async_start_connection_monitor(self) -> None:
        """Watch actual replies independently of HA polling/backoff."""
        if self._connection_task is None:
            self._connection_task = asyncio.create_task(self._connection_loop())

    async def _check_connection(self) -> float:
        """Check the deadline and perform at most one recovery attempt."""
        last_response = max(self._last_response, self.link.last_response)
        remaining = self._response_timeout - (monotonic() - last_response)
        if not self._reconnecting and remaining > 0:
            return min(1, remaining)
        if not self._reconnecting:
            self._reconnecting = True
            LOGGER.warning("No Modbus response from %s for %ss; reconnecting",
                           self.host, self._response_timeout)
            self.async_set_update_error(UpdateFailed("Device response timeout"))
        try:
            await self.link.reconnect()
        except Exception:
            LOGGER.debug("Modbus reconnect failed for %s", self.host, exc_info=True)
            return RECONNECT_INTERVAL_S
        self._last_response = monotonic()
        self._reconnecting = False
        LOGGER.info("Modbus connection restored for %s", self.host)
        await self.async_request_refresh()
        return 1

    async def _connection_loop(self) -> None:
        while True:
            await asyncio.sleep(await self._check_connection())

    async def async_start_heartbeat(self) -> None:
        """Write the keep-alive register every HEARTBEAT_INTERVAL_S seconds."""
        if self._heartbeat_task is not None:
            return

        async def _loop() -> None:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL_S)
                if self._reconnecting:
                    continue
                try:
                    await self.link.write_value(HEARTBEAT_OFFSET, "UINT16", 1)
                except Exception:  # noqa: BLE001 - a missed beat must not kill the loop
                    LOGGER.debug("heartbeat write failed", exc_info=True)

        self._heartbeat_task = asyncio.create_task(_loop())

    async def _async_update_data(self):
        if self._reconnecting:
            raise UpdateFailed("Reconnecting to device")
        # Resolve identity before reading product-specific registers or
        # creating entities, including legacy manual entries without an SN.
        if not self._serial_loaded:
            fetched = await self._read_serial_from_device()
            if fetched:
                self._last_response = monotonic()
                self.serial = fetched
                self._serial_loaded = True
        serial = self.serial_number(self._data_cache)
        product_changed = False
        if self._infer_product:
            product = product_from_serial(serial)
            if product != self.product:
                self.product = product
                self._configure_registers()
                self._data_cache.clear()
                self.device_info["model"] = product
                self.device_info["name"] = f"{product} Modbus {self.host}"
                product_changed = True
        identity = {}
        if serial and self.entry.data.get("serial") != serial:
            identity["serial"] = serial
        if self._infer_product and self.entry.data.get(CONF_PRODUCT) != self.product:
            identity[CONF_PRODUCT] = self.product
        if identity:
            self.hass.config_entries.async_update_entry(
                self.entry, data={**self.entry.data, **identity}
            )
        if (product_changed and self.platforms_loaded
                and not self._product_reload_scheduled):
            # If SN becomes readable only after setup, reload the platforms
            # once so the new register set is created and old points pruned.
            self._product_reload_scheduled = True
            self.hass.config_entries.async_schedule_reload(self.entry.entry_id)
        partition = self._partitions[self._cursor]
        self._cursor = (self._cursor + 1) % len(self._partitions)
        try:
            result = await self.link.read_all(partition)
        except Exception:
            LOGGER.debug("Modbus poll failed", exc_info=True)
            result = {}
        if any(value is not None for value in result.values()):
            self._last_response = self.link.last_response
        elif (monotonic() - max(self._last_response, self.link.last_response)
              >= self._response_timeout):
            raise UpdateFailed("Device response timeout")
        if self._reconnecting:
            raise UpdateFailed("Reconnecting to device")
        for offset, value in result.items():
            if value is not None:
                self._data_cache[offset] = value
        firmware = self._firmware_version(self._data_cache)
        serial = self.serial_number(self._data_cache)
        if (firmware and firmware != self._display_version) or (
            serial and serial != self._applied_serial
        ):
            self._display_version = firmware or self._display_version
            self._applied_serial = serial or self._applied_serial
            try:
                await self.async_refresh_device_display()
            except Exception:  # noqa: BLE001 - registry hiccups must not fail a poll
                LOGGER.debug("device display refresh failed", exc_info=True)
        return dict(self._data_cache)

    async def async_write(self, offset, dtype, value, length=8):
        if self._reconnecting:
            raise UpdateFailed("Reconnecting to device")
        await self.link.write_value(offset, dtype, value, length)
        # Reflect the new value right away instead of waiting for the
        # partition this register belongs to.
        reg = next((r for r in self.registers if r["offset"] == offset), None)
        if reg is not None:
            new_value = await self.link.read_one(reg)
            if new_value is not None:
                self._last_response = monotonic()
                self._data_cache[offset] = new_value
                self.data = dict(self._data_cache)
                self.async_update_listeners()

    async def async_close(self):
        tasks = [task for task in (self._heartbeat_task, self._connection_task)
                 if task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._heartbeat_task = None
        self._connection_task = None
        if self.link is not None:
            await self.link.close()
