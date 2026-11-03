"""Response deadlines and recovery without real network/time delays."""
import asyncio
import socket
import struct
import threading
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import test_device_identity as identity
import test_number_controls as numbers

sys.path.insert(0, str(identity.COMPONENT / "vendor"))
from pymodbus.client import ModbusTcpClient
from pymodbus.exceptions import ConnectionException, ModbusIOException
from pymodbus.pdu import ExceptionResponse


class DeviceExceptionError(Exception):
    """Stand in for modbus_connection's typed device-exception response."""


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    coordinator = identity.ProductTests.coordinator
    def setUp(self):
        identity.ProductTests.setUp(self)
        self.coord = self.coordinator(serial="SN0019")
        self.clock = 100.0
        self.ns["monotonic"] = lambda: self.clock
        self.coord._last_response = self.clock
        self.coord.link.last_response = self.clock
        self.coord.link.reconnect = AsyncMock(side_effect=OSError("offline"))
        self.coord.link.close = AsyncMock()
        self.coord.async_set_update_error = Mock()
        self.coord.async_request_refresh = AsyncMock()

    def test_deadline_uses_configured_scan_not_partition_tick(self):
        self.coord.conf["scan_interval"] = 30
        cls = type(self.coord)
        custom = cls(self.coord.hass, self.coord.conf, "test", self.coord.entry)
        self.assertEqual(custom._response_timeout, 90)
        self.assertEqual(len(custom._partitions), 30)

    async def test_exact_deadline_is_three_full_scan_intervals(self):
        self.assertEqual(self.coord._response_timeout, 15)
        self.clock = 114.9
        await self.coord._check_connection()
        self.coord.link.reconnect.assert_not_awaited()
        self.clock = 115
        self.assertEqual(await self.coord._check_connection(), 3)
        self.coord.link.reconnect.assert_awaited_once()
        self.assertTrue(self.coord._reconnecting)
        self.coord.async_set_update_error.assert_called_once()

    async def test_empty_none_and_exceptions_do_not_refresh_deadline(self):
        for result in ({}, {0: None}, OSError("no reply")):
            with self.subTest(result=result):
                self.clock = 114
                self.coord.link.read_all = AsyncMock()
                if isinstance(result, Exception):
                    self.coord.link.read_all.side_effect = result
                else:
                    self.coord.link.read_all.return_value = result
                await self.coord._async_update_data()
                self.assertEqual(self.coord._last_response, 100)
        self.clock = 115
        with self.assertRaisesRegex(RuntimeError, "timeout"):
            await self.coord._async_update_data()

    async def test_fresh_zero_value_resets_deadline(self):
        self.clock = 114
        self.coord.link.read_all.return_value = {0: 0}
        self.coord.link.last_response = self.clock
        self.assertEqual((await self.coord._async_update_data())[0], 0)
        self.clock = 115
        await self.coord._check_connection()
        self.coord.link.reconnect.assert_not_awaited()
        self.assertEqual(self.coord._last_response, 114)

    async def test_inflight_response_postpones_reconnect(self):
        self.coord.link.last_response = 114
        self.clock = 115
        await self.coord._check_connection()
        self.coord.link.reconnect.assert_not_awaited()

    async def test_retries_and_confirmed_recovery_refresh_entities(self):
        self.clock = 115
        for _ in range(3):
            self.assertEqual(await self.coord._check_connection(), 3)
            self.clock += 3
        self.assertEqual(self.coord.link.reconnect.await_count, 3)
        self.coord.async_set_update_error.assert_called_once()
        self.coord.link.reconnect.side_effect = None
        await self.coord._check_connection()
        self.assertFalse(self.coord._reconnecting)
        self.coord.async_request_refresh.assert_awaited_once()
        self.assertEqual(self.coord._last_response, self.clock)

    async def test_device_exception_probe_resumes_real_polling_for_both_links(self):
        shared_harness = LinkRecoveryTests()
        shared_harness.setUp()
        shared = shared_harness.shared_link()
        shared._unit.read_holding_registers.side_effect = DeviceExceptionError("refused")
        own_harness = OwnClientRecoveryTests()
        own_harness.setUp()
        own_harness.client.last_response = 50
        own_harness.transport.read_holding_registers.return_value = ExceptionResponse(3, 2)
        async def executor(func, *args):
            return func(*args)
        own = object.__new__(shared_harness.ns["OwnClientLink"])
        own._client = own_harness.client
        own._hass = SimpleNamespace(async_add_executor_job=executor)
        for link in (shared, own):
            with self.subTest(link=type(link).__name__):
                self.clock = 115
                self.coord._last_response = 100
                self.coord._reconnecting = False
                self.coord.link = link
                self.coord.async_request_refresh.reset_mock()
                await self.coord._check_connection()
                self.assertFalse(self.coord._reconnecting)
                self.coord.async_request_refresh.assert_awaited_once()
                self.coord._partitions = [[{"offset": 0x020E, "dtype": "UINT16"}]]
                self.coord._cursor = 0
                if link is shared:
                    shared._unit.read_holding_registers.side_effect = None
                    shared._unit.read_holding_registers.return_value = [47]
                else:
                    own_harness.transport.read_holding_registers.return_value = SimpleNamespace(
                        isError=lambda: False, registers=[47]
                    )
                result = await self.coord._async_update_data()
                self.assertEqual(result[0x020E], 47)

    async def test_polls_and_writes_pause_during_recovery(self):
        self.coord._reconnecting = True
        with self.assertRaisesRegex(RuntimeError, "Reconnecting"):
            await self.coord._async_update_data()
        with self.assertRaisesRegex(RuntimeError, "Reconnecting"):
            await self.coord.async_write(0, "UINT16", 1)
        self.coord.link.read_all.assert_not_awaited()

    async def test_monitor_retries_using_three_second_sleep(self):
        self.clock = 115
        delays = []
        async def sleep(delay):
            delays.append(delay)
            self.clock += delay
            if len(delays) == 3:
                raise asyncio.CancelledError
        self.ns["asyncio"] = SimpleNamespace(sleep=sleep)
        with self.assertRaises(asyncio.CancelledError):
            await self.coord._connection_loop()
        self.assertEqual(delays, [3, 3, 3])
        self.assertEqual(self.coord.link.reconnect.await_count, 3)

    async def test_unload_cancels_and_awaits_monitor_and_heartbeat(self):
        async def wait():
            await asyncio.Future()
        await self.coord.async_start_connection_monitor()
        first = self.coord._connection_task
        await self.coord.async_start_connection_monitor()
        self.assertIs(self.coord._connection_task, first)
        self.coord._heartbeat_task = asyncio.create_task(wait())
        heartbeat = self.coord._heartbeat_task
        await self.coord.async_close()
        self.assertTrue(first.cancelled())
        self.assertTrue(heartbeat.cancelled())
        self.coord.link.close.assert_awaited_once()


class LinkRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        identity.ProbeTests.setUp(self)
        self.ns.update(
            ModbusExceptionError=DeviceExceptionError,
            monotonic=lambda: 42,
            word_count=lambda dtype, length=8: 2 if dtype == "UINT32" else 1,
            decode_value=lambda dtype, words, length=8: words[0],
            encode_value=lambda dtype, value, length=8: (
                [value] if dtype == "UINT16" else [0, value]
            ),
        )

    def shared_link(self):
        link = object.__new__(self.ns["SharedModbusLink"])
        link._lock = asyncio.Lock()
        link._address_offset = 40001
        link.last_response = 0
        link._unit = SimpleNamespace(
            disconnect=AsyncMock(),
            read_holding_registers=AsyncMock(return_value=[0, 0]),
            write_register=AsyncMock(),
            write_registers=AsyncMock(),
        )
        return link

    async def test_own_reconnect_drains_executor_before_unload(self):
        job = asyncio.get_running_loop().create_future()
        link = object.__new__(self.ns["OwnClientLink"])
        link._client = Mock()
        link._hass = SimpleNamespace(async_add_executor_job=Mock(return_value=job))
        task = asyncio.create_task(link.reconnect())
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        self.assertFalse(job.cancelled())
        job.set_result(None)
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_shared_reconnect_recycles_transport_and_probes_utc(self):
        link = self.shared_link()
        await link.reconnect()
        link._unit.disconnect.assert_awaited_once()
        link._unit.read_holding_registers.assert_awaited_once_with(40001 + 0x0201, 2)
        self.assertEqual(link.last_response, 42)

    async def test_shared_device_exception_counts_as_recovery(self):
        link = self.shared_link()
        link._unit.read_holding_registers.side_effect = DeviceExceptionError("illegal address")
        await link.reconnect()
        self.assertEqual(link.last_response, 42)

    async def test_shared_transport_failures_do_not_count_as_recovery(self):
        for error in (TimeoutError("no reply"), OSError("offline"), RuntimeError("bad frame")):
            with self.subTest(error=type(error).__name__):
                link = self.shared_link()
                link._unit.read_holding_registers.side_effect = error
                with self.assertRaises(type(error)):
                    await link.reconnect()
                self.assertEqual(link.last_response, 0)

    async def test_shared_reconnect_bounds_a_silent_request(self):
        link = self.shared_link()
        self.ns["LOCAL_MODBUS_TIMEOUT_S"] = 0.01
        async def silent_read(*args):
            await asyncio.Future()
        link._unit.read_holding_registers.side_effect = silent_read
        with self.assertRaises(TimeoutError):
            await link.reconnect()
        self.assertEqual(link.last_response, 0)
        self.assertFalse(link._lock.locked())

    async def test_shared_short_reply_does_not_count_as_recovery(self):
        link = self.shared_link()
        link._unit.read_holding_registers.return_value = [0]
        with self.assertRaisesRegex(RuntimeError, "registers answered"):
            await link.reconnect()
        self.assertEqual(link.last_response, 0)

    async def test_shared_reconnect_waits_for_inflight_io_before_disconnect(self):
        link = self.shared_link()
        await link._lock.acquire()
        task = asyncio.create_task(link.reconnect())
        try:
            await asyncio.sleep(0)
            link._unit.disconnect.assert_not_awaited()
        finally:
            link._lock.release()
            await task
        link._unit.disconnect.assert_awaited_once()

    async def test_shared_write_waits_until_reconnect_probe_finishes(self):
        link = self.shared_link()
        started, release = asyncio.Event(), asyncio.Event()
        async def read(*args):
            started.set()
            await release.wait()
            return [0, 0]
        link._unit.read_holding_registers.side_effect = read
        reconnect = asyncio.create_task(link.reconnect())
        await asyncio.wait_for(started.wait(), 1)
        write = asyncio.create_task(link.write_value(0x025F, "UINT16", 1))
        try:
            await asyncio.sleep(0)
            link._unit.write_register.assert_not_awaited()
        finally:
            release.set()
            await asyncio.gather(reconnect, write)
        link._unit.write_register.assert_awaited_once()

    async def test_shared_poll_continues_after_device_exception_without_fake_value(self):
        link = self.shared_link()
        link._unit.read_holding_registers.side_effect = [DeviceExceptionError(), [7]]
        regs = [{"offset": i, "dtype": "UINT16"} for i in range(2)]
        self.assertEqual(await link.read_all(regs), {0: None, 1: 7})
        self.assertEqual(link.last_response, 42)

    async def test_shared_reads_update_timestamp_and_stop_on_timeout(self):
        link = self.shared_link()
        link._unit.read_holding_registers.return_value = [0]
        self.assertEqual(await link._read_words({"offset": 0, "dtype": "UINT16"}), [0])
        self.assertEqual(link.last_response, 42)
        link._unit.read_holding_registers.side_effect = TimeoutError
        regs = [{"offset": i, "dtype": "UINT16"} for i in range(3)]
        self.assertEqual(await link.read_all(regs), {0: None})
        self.assertEqual(link._unit.read_holding_registers.await_count, 2)
        self.assertEqual(link.last_response, 42)

    async def test_shared_write_acks_refresh_deadline(self):
        for dtype in ("UINT16", "UINT32"):
            with self.subTest(dtype=dtype):
                link = self.shared_link()
                await link.write_value(0x025F, dtype, 1)
                self.assertEqual(link.last_response, 42)

    async def test_shared_write_device_exception_refreshes_deadline_but_still_fails(self):
        for dtype, method in (("UINT16", "write_register"), ("UINT32", "write_registers")):
            with self.subTest(dtype=dtype):
                link = self.shared_link()
                getattr(link._unit, method).side_effect = DeviceExceptionError("refused")
                with self.assertRaises(DeviceExceptionError):
                    await link.write_value(0x025F, dtype, 1)
                self.assertEqual(link.last_response, 42)

    async def test_shared_write_timeout_does_not_refresh_deadline(self):
        link = self.shared_link()
        link._unit.write_register.side_effect = TimeoutError
        with self.assertRaises(TimeoutError):
            await link.write_value(0x025F, "UINT16", 1)
        self.assertEqual(link.last_response, 0)


class OwnClientRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        harness = numbers.NumberControlTests()
        harness.setUp()
        self.ns = harness.codec
        self.ns.update(threading=__import__("threading"), monotonic=lambda: 100,
                       ModbusTcpClient=Mock(), ConnectionException=OSError,
                       ExceptionResponse=ExceptionResponse,
                       LOGGER=__import__("logging").getLogger(__name__))
        self.client = self.ns["ModbusClient"]({"host": "192.0.2.1"})
        self.transport = self.client._client
        self.transport.read_holding_registers.return_value = SimpleNamespace(
            isError=lambda: False, registers=[0, 0])

    def test_reconnect_closes_stale_socket_and_confirms_zero_reply(self):
        self.client.reconnect()
        self.transport.close.assert_called_once()
        self.transport.read_holding_registers.assert_called_once_with(40001 + 0x0201, 2, slave=1)
        self.assertEqual(self.client.last_response, 100)
        self.assertFalse(self.client._interrupt_poll.is_set())
        self.ns["ModbusTcpClient"].assert_called_once_with(
            host="192.0.2.1", port=502, timeout=3, retries=0)

    def test_socket_connect_without_data_does_not_count_as_recovery(self):
        self.client.last_response = 50
        self.transport.read_holding_registers.side_effect = OSError("no data")
        with self.assertRaisesRegex(OSError, "No response"):
            self.client.reconnect()
        self.assertEqual(self.client.last_response, 50)
        self.assertFalse(self.client._interrupt_poll.is_set())

    def test_offline_poll_stops_after_first_failed_request(self):
        self.transport.connected = False
        self.transport.connect.return_value = False
        regs = [{"offset": i, "dtype": "UINT16"} for i in range(50)]
        self.assertEqual(self.client.read_all(regs), {})
        self.transport.connect.assert_called_once()


    def test_device_exception_counts_as_recovery(self):
        for code in (1, 2, 3, 4, 5, 6, 8, 10, 11):
            with self.subTest(exception_code=code):
                self.client.last_response = 50
                self.transport.read_holding_registers.return_value = ExceptionResponse(3, code)
                self.client.reconnect()
                self.assertEqual(self.client.last_response, 100)
                self.assertFalse(self.client._interrupt_poll.is_set())

    def test_local_io_error_missing_reply_and_short_reply_do_not_recover(self):
        for result in (ModbusIOException("no response"), None, ExceptionResponse(3),
                       SimpleNamespace(isError=lambda: False, registers=[0])):
            with self.subTest(result=result):
                self.client.last_response = 50
                self.transport.read_holding_registers.return_value = result
                with self.assertRaisesRegex(OSError, "No response"):
                    self.client.reconnect()
                self.assertEqual(self.client.last_response, 50)
                self.assertFalse(self.client._interrupt_poll.is_set())

    def test_device_read_exception_updates_deadline_without_a_sensor_value(self):
        self.client.last_response = 50
        self.transport.read_holding_registers.return_value = ExceptionResponse(3, 2)
        self.assertIsNone(self.client.read_one({"offset": 0, "dtype": "UINT16"}))
        self.assertEqual(self.client.last_response, 100)

    def test_local_read_error_does_not_refresh_deadline(self):
        self.client.last_response = 50
        self.transport.read_holding_registers.return_value = ModbusIOException("no response")
        self.assertIsNone(self.client.read_one({"offset": 0, "dtype": "UINT16"}))
        self.assertEqual(self.client.last_response, 50)

    def test_poll_continues_after_device_exception(self):
        self.transport.read_holding_registers.side_effect = [
            ExceptionResponse(3, 2), SimpleNamespace(isError=lambda: False, registers=[7])
        ]
        regs = [{"offset": i, "dtype": "UINT16"} for i in range(2)]
        self.assertEqual(self.client.read_all(regs), {0: None, 1: 7})

    def test_close_connect_and_probe_are_all_under_client_lock(self):
        def close():
            self.assertTrue(self.client._lock.locked())
            self.transport.connected = False
        def connect():
            self.assertTrue(self.client._lock.locked())
            self.transport.connected = True
            return True
        def read(*args, **kwargs):
            self.assertTrue(self.client._lock.locked())
            return SimpleNamespace(isError=lambda: False, registers=[0, 0])
        self.transport.close.side_effect = close
        self.transport.connect.side_effect = connect
        self.transport.read_holding_registers.side_effect = read
        self.client.reconnect()
        self.transport.connect.assert_called_once()

    def test_reconnect_respects_unit_and_address_configuration(self):
        self.client.unit_id = 5
        self.client.address_offset = 0
        self.client.reconnect()
        self.transport.read_holding_registers.assert_called_once_with(0x0201, 2, slave=5)

    def test_write_acks_and_refusals_refresh_deadline_without_masking_write_errors(self):
        for dtype, method in (("UINT16", "write_register"), ("UINT32", "write_registers")):
            for result in (SimpleNamespace(isError=lambda: False), ExceptionResponse(6, 2),
                           ModbusIOException("no response"), None):
                with self.subTest(dtype=dtype, result=result):
                    self.client.last_response = 50
                    getattr(self.transport, method).return_value = result
                    if result is not None and not result.isError():
                        self.client.write_value(0x025F, dtype, 1)
                    else:
                        with self.assertRaisesRegex(Exception, "Write failed"):
                            self.client.write_value(0x025F, dtype, 1)
                    received = result is not None and not isinstance(result, ModbusIOException)
                    self.assertEqual(self.client.last_response, 100 if received else 50)


class TcpRecoveryTests(unittest.TestCase):
    """Exercise the actual bundled transport, including complete TCP frames."""

    def run_probe(self, probe_response):
        harness = OwnClientRecoveryTests()
        harness.setUp()
        harness.ns.update(
            ModbusTcpClient=ModbusTcpClient,
            ConnectionException=ConnectionException,
            RECONNECT_INTERVAL_S=0.2,
        )
        requests, failures = [], []
        release = threading.Event()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(2)

            def serve():
                try:
                    peer, _ = listener.accept()
                    with peer:
                        peer.settimeout(2)
                        responses = [probe_response]
                        if probe_response != "silent":
                            responses.append("soc")
                        for response in responses:
                            request = b""
                            while len(request) < 12:
                                block = peer.recv(12 - len(request))
                                if not block:
                                    raise RuntimeError("Client closed before sending its request")
                                request += block
                            transaction, _, _, unit, function, address, count = struct.unpack(
                                ">HHHBBHH", request
                            )
                            requests.append((address, count, unit))
                            if response == "silent":
                                release.wait(2)
                                return
                            if response == "exception":
                                pdu = bytes([function | 0x80, 2])
                            else:
                                words = [0, 0] if response == "zero" else [47]
                                pdu = bytes([function, len(words) * 2]) + struct.pack(
                                    ">" + "H" * len(words), *words
                                )
                            peer.sendall(struct.pack(">HHHB", transaction, 0, len(pdu) + 1, unit) + pdu)
                        release.wait(2)
                except Exception as error:
                    failures.append(error)

            worker = threading.Thread(target=serve)
            worker.start()
            client = harness.ns["ModbusClient"]({
                "host": "127.0.0.1", "port": listener.getsockname()[1]
            })
            client.last_response = 50
            try:
                if probe_response == "silent":
                    with self.assertRaisesRegex(ConnectionException, "No response"):
                        client.reconnect()
                    self.assertEqual(client.last_response, 50)
                else:
                    client.reconnect()
                    self.assertEqual(client.last_response, 100)
                    self.assertEqual(client.read_one({"offset": 0x020E, "dtype": "UINT16"}), 47)
            finally:
                release.set()
                client.close()
                worker.join(3)
            self.assertFalse(worker.is_alive())
            self.assertEqual(failures, [])
            expected = [(40001 + 0x0201, 2, 1)]
            if probe_response != "silent":
                expected.append((40001 + 0x020E, 1, 1))
            self.assertEqual(requests, expected)

    def test_device_exception_frame_recovers_and_next_point_is_read(self):
        self.run_probe("exception")

    def test_zero_utc_frame_recovers_and_next_point_is_read(self):
        self.run_probe("zero")

    def test_connected_tcp_without_modbus_response_does_not_recover(self):
        self.run_probe("silent")


if __name__ == "__main__":
    unittest.main()
