import importlib.util
import sys
import tempfile
import threading
import time
import unittest
import queue
import socket
import struct
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
SPEC = importlib.util.spec_from_file_location("djy_pit_wall_app", APP_PATH)
app = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = app
SPEC.loader.exec_module(app)


class VehicleRelayTests(unittest.TestCase):
    def test_request_response_round_trip(self):
        relay = app.VehicleRelay()
        received = []

        def vehicle():
            command = relay.exchange("A", b"", "test-vehicle", timeout=1.0)
            received.append(command)
            relay.exchange("A", b"\x10\x20\x30", "test-vehicle", timeout=0.05)

        vehicle_thread = threading.Thread(target=vehicle)
        vehicle_thread.start()
        deadline = time.monotonic() + 1.0
        while not relay.status()["vehicles"]["A"]["online"]:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.005)

        response = relay.request("A", b"\xAA\x21\x55\x20", expected=3, timeout=1.0)
        vehicle_thread.join(1.0)

        self.assertEqual(received, [b"\xAA\x21\x55\x20"])
        self.assertEqual(response, b"\x10\x20\x30")
        self.assertFalse(vehicle_thread.is_alive())

    def test_offline_vehicle_is_rejected(self):
        relay = app.VehicleRelay()
        with self.assertRaises(ConnectionError):
            relay.request("A", b"request", expected=1, timeout=0.05)

    def test_packet_limits_are_enforced(self):
        relay = app.VehicleRelay()
        with self.assertRaises(ValueError):
            relay.request("A", b"", expected=1, timeout=0.05)
        with self.assertRaises(ValueError):
            relay.request("A", b"request", expected=app.RELAY_MAX_PACKET_BYTES + 1, timeout=0.05)

    def test_raw_client_takeover_aborts_telemetry_request(self):
        relay = app.VehicleRelay()
        relay.exchange("A", b"", "test-vehicle", timeout=0.01)
        failure = []

        def request_telemetry():
            try:
                relay.request("A", b"telemetry", expected=1, timeout=1.0)
            except Exception as error:
                failure.append(error)

        request_thread = threading.Thread(target=request_telemetry)
        request_thread.start()
        deadline = time.monotonic() + 1.0
        while not relay.channels["A"].downlink:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.005)

        relay.set_raw_client_active("A", True)
        request_thread.join(1.0)

        self.assertFalse(request_thread.is_alive())
        self.assertEqual(len(failure), 1)
        self.assertIsInstance(failure[0], ConnectionError)
        self.assertEqual(relay.channels["A"].downlink, bytearray())

    def test_http_exchange_requires_token_and_returns_downlink(self):
        old_token = app.DJY_RELAY_TOKEN
        app.DJY_RELAY_TOKEN = "test-relay-token"
        with tempfile.TemporaryDirectory() as temporary_directory:
            db = app.Database(Path(temporary_directory) / "test.sqlite3")
            state = app.SharedState()
            speedhive = app.SpeedhiveClient()
            lap_timing = app.LapTimingManager(db)
            pit_commands = app.PitCommandManager()
            relay = app.VehicleRelay()
            handler = app.make_handler(state, db, speedhive, lap_timing, pit_commands, relay)
            server = app.ThreadingHTTPServer(("127.0.0.1", 0), handler)
            server_thread = threading.Thread(target=server.serve_forever)
            server_thread.start()
            url = f"http://127.0.0.1:{server.server_address[1]}/api/vehicle/exchange"
            try:
                with self.assertRaises(HTTPError) as error:
                    urlopen(Request(url, data=b"", method="POST"), timeout=1.0)
                self.assertEqual(error.exception.code, 401)

                response_body = []

                def vehicle_http_request():
                    request = Request(
                        url,
                        data=b"",
                        method="POST",
                        headers={
                            "Authorization": "Bearer test-relay-token",
                            "X-DJY-Vehicle-ID": "A",
                            "Content-Type": "application/octet-stream",
                        },
                    )
                    with urlopen(request, timeout=2.0) as response:
                        response_body.append(response.read())

                http_thread = threading.Thread(target=vehicle_http_request)
                http_thread.start()
                deadline = time.monotonic() + 1.0
                while not relay.status()["vehicles"]["A"]["online"]:
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(0.005)

                def queue_command():
                    try:
                        relay.request("A", b"raw-command", expected=1, timeout=0.1)
                    except TimeoutError:
                        pass

                command_thread = threading.Thread(target=queue_command)
                command_thread.start()
                http_thread.join(1.0)
                command_thread.join(1.0)
                self.assertEqual(response_body, [b"raw-command"])
            finally:
                server.shutdown()
                server.server_close()
                server_thread.join(1.0)
                app.DJY_RELAY_TOKEN = old_token

    def test_relay_worker_updates_ecu_telemetry(self):
        old_token = app.DJY_RELAY_TOKEN
        app.DJY_RELAY_TOKEN = "test-relay-token"
        relay = app.VehicleRelay()
        state = app.SharedState()
        db_queue = queue.Queue()
        commands = []
        init_responses = (b"\x02", b"\x00\x1f", bytes(23))
        rpm_response = bytes((0xAA, 0x0B, 0xB8, 4, 0, 105)) + bytes(18)
        status_response = bytes((0xAA, 0, 0, 0, 0x13, 0x88))

        def vehicle():
            upstream = b""
            for response in (*init_responses, rpm_response, status_response):
                command = relay.exchange("A", upstream, "test-vehicle", timeout=1.0)
                commands.append(command)
                upstream = response
            relay.exchange("A", upstream, "test-vehicle", timeout=0.1)

        vehicle_thread = threading.Thread(target=vehicle)
        worker = app.RelayWorker(state, db_queue, relay)
        try:
            vehicle_thread.start()
            worker.start()
            deadline = time.monotonic() + 2.0
            while state.latest().battery_voltage != 12.5:
                self.assertLess(time.monotonic(), deadline)
                time.sleep(0.01)
            snapshot = state.latest()
            self.assertEqual(
                commands[:5],
                [request for request, _ in app.ECU_INIT_SEQUENCE]
                + [app.RPM_REQUEST, app.STATUS_REQUEST],
            )
            self.assertEqual(snapshot.rpm, 3000)
            self.assertEqual(snapshot.throttle_percent, 20)
            self.assertEqual(snapshot.fuel_add, 5)
            self.assertEqual(snapshot.battery_voltage, 12.5)
        finally:
            worker.stop_event.set()
            worker.join(2.0)
            vehicle_thread.join(2.0)
            app.DJY_RELAY_TOKEN = old_token

    def test_local_tcp_bridge_preserves_packet_boundaries(self):
        old_settle = app.RELAY_RAW_TAKEOVER_SETTLE_SECONDS
        app.RELAY_RAW_TAKEOVER_SETTLE_SECONDS = 0.02
        relay = app.VehicleRelay()
        bridge = app.RelayTcpBridge(relay, port=0)
        observed = []

        def vehicle():
            command = relay.exchange("A", b"", "test-vehicle", timeout=1.0)
            observed.append(command)
            relay.exchange("A", b"ecu-response", "test-vehicle", timeout=0.1)

        vehicle_thread = threading.Thread(target=vehicle)
        vehicle_thread.start()
        deadline = time.monotonic() + 1.0
        while not relay.status()["vehicles"]["A"]["online"]:
            self.assertLess(time.monotonic(), deadline)
            time.sleep(0.005)

        bridge.start()
        self.assertTrue(bridge.ready_event.wait(1.0))
        client = socket.create_connection(("127.0.0.1", bridge.port), timeout=1.0)
        try:
            payload = b"raw-map-packet"
            client.sendall(struct.pack("<I", len(payload)) + payload)
            response_length = struct.unpack("<I", client.recv(4))[0]
            response = bytearray()
            while len(response) < response_length:
                response.extend(client.recv(response_length - len(response)))
            self.assertEqual(observed, [payload])
            self.assertEqual(bytes(response), b"ecu-response")
        finally:
            client.close()
            bridge.stop()
            bridge.join(2.0)
            vehicle_thread.join(2.0)
            app.RELAY_RAW_TAKEOVER_SETTLE_SECONDS = old_settle


if __name__ == "__main__":
    unittest.main()
