"""Pure-pymodbus Modbus TCP client with EcoFlow word-order encoding.

This module has no Home Assistant imports so it can be tested standalone
against the simulator. Word order matches the EcoFlow protocol:
multi-word values store the low word first (0x12345678 -> [0x5678, 0x1234]).
"""

import logging
import os
import struct
import sys
import threading
from time import monotonic

# Bundled pymodbus (see vendor/) so the integration works without pip
# access to PyPI. The vendored copy shadows any system-installed pymodbus.
_VENDOR_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
if os.path.isdir(_VENDOR_DIR) and _VENDOR_DIR not in sys.path:
    sys.path.insert(0, _VENDOR_DIR)

from pymodbus.client import ModbusTcpClient  # noqa: E402
from pymodbus.exceptions import ConnectionException  # noqa: E402
from pymodbus.pdu import ExceptionResponse  # noqa: E402

from .const import (
    CONF_ADDRESS_OFFSET,
    CONF_UNIT_ID,
    DEFAULT_ADDRESS_OFFSET,
    DEFAULT_UNIT_ID,
    RECONNECT_INTERVAL_S,
    RECONNECT_PROBE_OFFSET,
)

LOGGER = logging.getLogger(__name__)

WORD_COUNT = {
    "UINT16": 1,
    "INT16": 1,
    "UINT32": 2,
    "INT32": 2,
    "FLOAT": 2,
}


def word_count(dtype, length=8):
    if dtype == "STRING":
        return length or 8
    return WORD_COUNT.get(dtype, 1)


def encode_value(dtype, value, length=8):
    """Encode a python value into Modbus words (EcoFlow word order)."""
    if dtype == "UINT16":
        return [int(value) & 0xFFFF]
    if dtype == "INT16":
        return [struct.unpack(">H", struct.pack(">h", int(value)))[0]]
    if dtype == "UINT32":
        v = int(value) & 0xFFFFFFFF
        return [(v >> 16) & 0xFFFF, v & 0xFFFF]
    if dtype == "INT32":
        hi, lo = struct.unpack(">HH", struct.pack(">i", int(value)))
        return [hi, lo]
    if dtype == "FLOAT":
        hi, lo = struct.unpack(">HH", struct.pack(">f", float(value)))
        return [hi, lo]
    if dtype == "STRING":
        n = length or 8
        raw = str(value).encode("ascii", errors="replace")[: n * 2]
        raw = raw.ljust(n * 2, b"\x00")
        words = []
        for i in range(0, n * 2, 2):
            words.append((raw[i] << 8) | raw[i + 1])
        return words[:n]
    return [int(value) & 0xFFFF]


def decode_value(dtype, words, length=8):
    """Decode Modbus words into a python value (EcoFlow word order)."""
    if not words:
        return None
    if dtype == "UINT16":
        return words[0]
    if dtype == "INT16":
        return struct.unpack(">h", struct.pack(">H", words[0]))[0]
    if dtype == "UINT32":
        if len(words) < 2:
            return None
        return (words[0] << 16) | words[1]
    if dtype == "INT32":
        if len(words) < 2:
            return None
        return struct.unpack(">i", struct.pack(">HH", words[0], words[1]))[0]
    if dtype == "FLOAT":
        if len(words) < 2:
            return None
        return round(struct.unpack(">f", struct.pack(">HH", words[0], words[1]))[0], 4)
    if dtype == "STRING":
        n = length or 8
        chars = []
        for w in words[:n]:
            chars.append(chr((w >> 8) & 0xFF))
            chars.append(chr(w & 0xFF))
        return "".join(chars).rstrip("\x00")
    return words[0]


class ModbusClient:
    """Synchronous Modbus TCP client guarded by a lock.

    Sync pymodbus calls run inside Home Assistant's executor, but the
    lock keeps concurrent reads/writes from interleaving on the socket.
    """

    def __init__(self, conf):
        host = conf["host"]
        port = conf.get("port", 502)
        self.unit_id = conf.get(CONF_UNIT_ID, DEFAULT_UNIT_ID)
        self.address_offset = conf.get(CONF_ADDRESS_OFFSET, DEFAULT_ADDRESS_OFFSET)
        self._lock = threading.Lock()
        self._interrupt_poll = threading.Event()
        self.last_response = monotonic()
        self._client = ModbusTcpClient(
            host=host, port=port, timeout=RECONNECT_INTERVAL_S, retries=0
        )

    @property
    def host(self):
        return self._client.comm_params.host

    @property
    def port(self):
        return self._client.comm_params.port

    def _ensure_connected(self):
        if not self._client.connected:
            if not self._client.connect():
                raise ConnectionException(
                    f"Cannot connect to {self.host}:{self.port}"
                )

    def _pdu_address(self, offset):
        return offset + self.address_offset

    def _record_response(self, result):
        """Record a device reply, excluding locally generated I/O errors."""
        if result is None:
            return False
        if result.isError():
            if not isinstance(result, ExceptionResponse) or not result.exception_code:
                return False
        self.last_response = monotonic()
        return True

    def _read_response(self, offset, count):
        """Read a raw reply with the client lock already held."""
        self._ensure_connected()
        address = self._pdu_address(offset)
        try:
            result = self._client.read_holding_registers(
                address, count, slave=self.unit_id
            )
        except ConnectionException:
            self._client.close()
            return None
        if result is not None and not result.isError():
            if len(result.registers) != count:
                LOGGER.debug("Unexpected register count at %s: %s", hex(address), result)
                return None
        if not self._record_response(result):
            LOGGER.debug("No valid read response at %s: %s", hex(address), result)
            return None
        return result

    def read_one(self, reg):
        """Read a single register definition, return decoded value or None."""
        count = word_count(reg["dtype"], reg.get("length", 8))
        with self._lock:
            result = self._read_response(reg["offset"], count)
        if result is None:
            return None
        if result.isError():
            LOGGER.debug("Read error at offset %s: %s", hex(reg["offset"]), result)
            return None
        return decode_value(reg["dtype"], result.registers, reg.get("length", 8))

    def read_all(self, regs):
        """Read all register definitions, return {offset: value} dict."""
        data = {}
        started = monotonic()
        for reg in regs:
            if self._interrupt_poll.is_set():
                break
            try:
                value = self.read_one(reg)
            except ConnectionException:
                break
            data[reg["offset"]] = value
            if value is None and (not self._client.connected
                                  or monotonic() - started >= RECONNECT_INTERVAL_S):
                break
        return data

    def write_value(self, offset, dtype, value, length=8):
        """Write a python value to a register, low word first."""
        address = self._pdu_address(offset)
        words = encode_value(dtype, value, length)
        with self._lock:
            self._ensure_connected()
            try:
                if len(words) == 1:
                    result = self._client.write_register(
                        address, words[0], slave=self.unit_id
                    )
                else:
                    result = self._client.write_registers(
                        address, words, slave=self.unit_id
                    )
            except ConnectionException:
                self._client.close()
                raise
            received = self._record_response(result)
        if not received or result.isError():
            raise Exception(f"Write failed at {hex(address)}: {result}")

    def reconnect(self):
        """Discard a stale socket and require a real Modbus reply to recover."""
        self._interrupt_poll.set()
        try:
            with self._lock:
                self._client.close()
                result = self._read_response(RECONNECT_PROBE_OFFSET, word_count("UINT32"))
                if result is None:
                    raise ConnectionException("No response to reconnect probe")
                if result.isError():
                    # Refusing a register is distinct from not answering at all.
                    LOGGER.debug("Reconnect probe received a device exception: %s", result)
        finally:
            self._interrupt_poll.clear()

    def close(self):
        self._interrupt_poll.set()
        with self._lock:
            self._client.close()