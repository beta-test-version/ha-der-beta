"""Modbus link selection.

Follows the pattern of HA's built-in shared Modbus connection introduced in
Home Assistant 2026.9 (one connection per device endpoint, shared by every
integration that asks for the same endpoint):

- ``SharedModbusLink``   uses a unit on the shared connection when available.
- ``OwnClientLink``      falls back to our bundled pymodbus client.

Whether the shared connection exists is decided once, when this module is
imported (same as the reference implementation): there is no runtime switch
and no fallback from one link to the other. A device that serves one Modbus
client at a time would treat a second socket as a second client, so sharing
the connection matters when several integrations talk to one inverter.
"""

from __future__ import annotations

import asyncio
import logging
from time import monotonic
from typing import Any, Protocol

from .const import (
    CONF_ADDRESS_OFFSET,
    CONF_UNIT_ID,
    DEFAULT_ADDRESS_OFFSET,
    DEFAULT_UNIT_ID,
    LOCAL_MODBUS_TIMEOUT_S,
    RECONNECT_INTERVAL_S,
    RECONNECT_PROBE_OFFSET,
)
from .modbus_client import ModbusClient, decode_value, encode_value, word_count

LOGGER = logging.getLogger(__name__)

try:
    from homeassistant.components.modbus import (  # noqa: F401
        async_get_temporary_unit,
        async_get_unit,
    )
    from modbus_connection import (  # noqa: F401
        ModbusExceptionError,
        ModbusTcpParams,
    )

    HAS_SHARED_CONNECTION = True
except ImportError:
    HAS_SHARED_CONNECTION = False

# One budget for a whole poll: a few single timeouts, well short of the sum of
# one per request (same idea as the reference implementation).
_POLL_BUDGET_S = LOCAL_MODBUS_TIMEOUT_S * 4


class ModbusLink(Protocol):
    """What the coordinator needs from a Modbus link."""

    async def read_all(self, regs: list[dict]) -> dict[int, Any]: ...

    async def read_one(self, reg: dict) -> Any: ...

    async def write_value(
        self, offset: int, dtype: str, value: Any, length: int = 8
    ) -> None: ...

    @property
    def last_response(self) -> float: ...

    async def reconnect(self) -> None: ...

    async def close(self) -> None: ...


class OwnClientLink:
    """Our bundled synchronous pymodbus client, run via the executor."""

    def __init__(self, hass, conf: dict) -> None:
        self._hass = hass
        self._client = ModbusClient(conf)

    @property
    def last_response(self) -> float:
        return self._client.last_response

    async def reconnect(self) -> None:
        # Cancellation cannot stop a running executor thread. Drain it before
        # unload closes the client, so it cannot reopen a socket afterwards.
        job = asyncio.ensure_future(
            self._hass.async_add_executor_job(self._client.reconnect)
        )
        try:
            await asyncio.shield(job)
        except asyncio.CancelledError:
            try:
                await job
            except Exception:
                pass
            raise

    async def read_all(self, regs: list[dict]) -> dict[int, Any]:
        return await self._hass.async_add_executor_job(self._client.read_all, regs)

    async def read_one(self, reg: dict) -> Any:
        return await self._hass.async_add_executor_job(self._client.read_one, reg)

    async def write_value(
        self, offset: int, dtype: str, value: Any, length: int = 8
    ) -> None:
        await self._hass.async_add_executor_job(
            self._client.write_value, offset, dtype, value, length
        )

    async def close(self) -> None:
        await self._hass.async_add_executor_job(self._client.close)


class SharedModbusLink:
    """A unit on Home Assistant's shared Modbus connection.

    The connection belongs to the ``modbus`` integration: this link never
    opens or closes a socket of its own.
    """

    def __init__(self, hass, entry, conf: dict) -> None:
        self._unit = async_get_unit(
            hass,
            entry,
            ModbusTcpParams(
                host=conf["host"], port=conf.get("port", 502)
            ),
            conf.get(CONF_UNIT_ID, DEFAULT_UNIT_ID),
        )
        self._address_offset = conf.get(
            CONF_ADDRESS_OFFSET, DEFAULT_ADDRESS_OFFSET
        )
        # One call sequence at a time so a poll, a heartbeat and a write
        # never interleave their requests on the shared connection.
        self._lock = asyncio.Lock()
        self.last_response = monotonic()

    async def reconnect(self) -> None:
        # Let in-flight I/O finish before recycling the shared transport.
        async with self._lock:
            async with asyncio.timeout(LOCAL_MODBUS_TIMEOUT_S):
                await self._unit.disconnect()
                try:
                    await self._read_words(
                        {"offset": RECONNECT_PROBE_OFFSET, "dtype": "UINT32"}
                    )
                except ModbusExceptionError as err:
                    # A device refusal still proves that the link is answering.
                    LOGGER.debug("Reconnect probe received a device exception: %s", err)

    def _address(self, offset: int) -> int:
        return offset + self._address_offset

    async def _read_words(self, reg: dict) -> list[int]:
        count = word_count(reg["dtype"], reg.get("length", 8))
        try:
            words = await self._unit.read_holding_registers(
                self._address(reg["offset"]), count
            )
        except ModbusExceptionError:
            self.last_response = monotonic()
            raise
        if len(words) != count:
            raise RuntimeError(
                f"{len(words)} registers answered for {count} "
                f"at offset {reg['offset']:#06x}"
            )
        self.last_response = monotonic()
        return words

    async def read_one(self, reg: dict) -> Any:
        async with self._lock:
            async with asyncio.timeout(LOCAL_MODBUS_TIMEOUT_S):
                words = await self._read_words(reg)
        return decode_value(reg["dtype"], words, reg.get("length", 8))

    async def read_all(self, regs: list[dict]) -> dict[int, Any]:
        data: dict[int, Any] = {}
        async with self._lock:
            try:
                async with asyncio.timeout(_POLL_BUDGET_S):
                    for reg in regs:
                        try:
                            async with asyncio.timeout(RECONNECT_INTERVAL_S):
                                words = await self._read_words(reg)
                            data[reg["offset"]] = decode_value(
                                reg["dtype"], words, reg.get("length", 8)
                            )
                        except TimeoutError:
                            data[reg["offset"]] = None
                            break
                        except Exception:  # noqa: BLE001
                            data[reg["offset"]] = None
            except TimeoutError:
                LOGGER.warning(
                    "Modbus poll exceeded its %.1fs budget", _POLL_BUDGET_S
                )
        return data

    async def write_value(
        self, offset: int, dtype: str, value: Any, length: int = 8
    ) -> None:
        words = encode_value(dtype, value, length)
        address = self._address(offset)
        async with self._lock:
            async with asyncio.timeout(LOCAL_MODBUS_TIMEOUT_S):
                try:
                    if len(words) == 1:
                        await self._unit.write_register(address, words[0])
                    else:
                        await self._unit.write_registers(address, words)
                except ModbusExceptionError:
                    self.last_response = monotonic()
                    raise
                self.last_response = monotonic()

    async def close(self) -> None:
        # The shared connection is owned by the modbus integration.
        pass


def create_link(hass, entry, conf: dict) -> ModbusLink:
    """Return the link this process uses for one config entry.

    A device already held on the shared connection with different link
    settings raises from ``async_get_unit`` and setup fails with it, exactly
    like the reference implementation.
    """
    if HAS_SHARED_CONNECTION:
        return SharedModbusLink(hass, entry, conf)
    return OwnClientLink(hass, conf)


async def probe_device(hass, conf: dict) -> tuple[bool, str | None]:
    """Check reachability and read identity during config flow.

    Uses a temporary unit on the shared connection when possible (so a device
    already held by another integration is shared rather than refused), else
    the bundled client.
    """
    probe = {"offset": 0x0000, "dtype": "UINT16", "length": 8}
    sn_probe = {"offset": 0x0003, "dtype": "STRING", "length": 8}
    serial = None
    reachable = False
    if HAS_SHARED_CONNECTION:
        try:
            async with asyncio.timeout(LOCAL_MODBUS_TIMEOUT_S):
                async with async_get_temporary_unit(
                    hass,
                    ModbusTcpParams(
                        host=conf["host"], port=conf.get("port", 502)
                    ),
                    conf.get(CONF_UNIT_ID, DEFAULT_UNIT_ID),
                ) as unit:
                    await unit.read_holding_registers(
                        conf.get(CONF_ADDRESS_OFFSET, DEFAULT_ADDRESS_OFFSET)
                        + probe["offset"],
                        1,
                    )
                    reachable = True
                    try:
                        words = await unit.read_holding_registers(
                            conf.get(CONF_ADDRESS_OFFSET, DEFAULT_ADDRESS_OFFSET)
                            + sn_probe["offset"], 8
                        )
                        serial = decode_value("STRING", words, 8)
                    except Exception:  # A missing SN must not prevent setup.
                        pass
                    return True, (serial.strip() or None) if serial else None
        except Exception:  # noqa: BLE001
            # The optional SN read may exhaust the discovery timeout after
            # the mandatory connection probe has already succeeded.
            return reachable, None
    client = ModbusClient(conf)
    try:
        reachable = await hass.async_add_executor_job(client.read_one, probe) is not None
        if reachable:
            try:
                serial = await hass.async_add_executor_job(client.read_one, sn_probe)
            except Exception:  # A missing SN must not prevent setup.
                pass
        return reachable, (serial.strip() or None) if serial else None
    finally:
        await hass.async_add_executor_job(client.close)
