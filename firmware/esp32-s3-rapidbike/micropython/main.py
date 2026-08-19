from machine import Pin, UART
import os
import socket
import time


START_BAUD = 9600
HIGH_BAUD = 38400
HOST_RX_PIN = 44
HOST_TX_PIN = 43
RAPIDBIKE_RX_PIN = 18
RAPIDBIKE_TX_PIN = 17
HOST_SELFTEST_REQUEST = bytes((0xF0, 0x0D, 0xCA, 0xFE))
HOST_SELFTEST_RESPONSE = bytes((0xFE, 0xCA, 0x0D, 0xF0))
HOST_WIFI_STATUS_REQUEST = bytes((0xF0, 0x0D, 0x57, 0x49))
HOST_ERROR_STATUS_REQUEST = bytes((0xF0, 0x0D, 0x45, 0x52))
# Rapid Bike Master asks the ECU to change speed with these two frames.  The
# FTDI driver changes the PC serial speed immediately after it receives the
# ECU's one-byte acknowledgement, so both UARTs must follow at that point.
SET_HIGH_BAUD_REQUEST = bytes((0xAA, 0x5B, 0x55, 0x26, 0x80))
SET_START_BAUD_REQUEST = bytes((0xAA, 0x5B, 0x55, 0x09, 0x63))
MAP_DOWNLOAD_COMMAND = 0x65
RELAY_MAX_PACKET_BYTES = 65536
WIFI_SSID = "RapidBike-ESP32"
WIFI_PASSWORD = "RapidBike125"
WIFI_PORT = 8888
WIFI_DISCOVERY_PORT = 8889
WIFI_DISCOVERY_REQUEST = b"RAPIDBIKE_DISCOVER_V1"
ERROR_LOG_PATH = "bridge_error.txt"


def load_last_error():
    try:
        with open(ERROR_LOG_PATH, "r") as error_file:
            return error_file.read(240).encode()
    except Exception:
        return b"NO_ERROR_RECORDED"


def save_fatal_error(stage, error):
    try:
        with open(ERROR_LOG_PATH, "w") as error_file:
            error_file.write("%s: %r" % (stage, error))
    except Exception:
        pass


def reinit_uart(uart, baudrate, rx_pin, tx_pin):
    last_error = None
    for _ in range(3):
        try:
            uart.init(
                baudrate=baudrate,
                bits=8,
                parity=None,
                stop=1,
                rx=rx_pin,
                tx=tx_pin,
                timeout=0,
                timeout_char=2,
                rxbuf=4096,
                txbuf=4096,
            )
            return
        except OSError as error:
            last_error = error
            time.sleep_ms(5)
    raise last_error


def flush_uart(uart):
    last_error = None
    for _ in range(3):
        try:
            uart.flush()
            return
        except OSError as error:
            last_error = error
            time.sleep_ms(3)
    raise last_error


def start_wifi_bridge():
    """Join the configured WLAN, falling back to a private access point."""
    import network

    mode = "AP"
    interface = None
    station = None
    try:
        try:
            from wifi_config import WIFI_STA_NETWORKS
        except ImportError:
            from wifi_config import WIFI_STA_PASSWORD, WIFI_STA_SSID
            WIFI_STA_NETWORKS = ((WIFI_STA_SSID, WIFI_STA_PASSWORD),)

        station = network.WLAN(network.WLAN.IF_STA)
        station.active(True)
        try:
            station.config(pm=station.PM_NONE)
        except Exception:
            pass
        for station_ssid, station_password in WIFI_STA_NETWORKS:
            if station.isconnected():
                break
            station.disconnect()
            station.connect(station_ssid, station_password)
            deadline = time.ticks_add(time.ticks_ms(), 7000)
            while not station.isconnected() and time.ticks_diff(deadline, time.ticks_ms()) > 0:
                time.sleep_ms(100)
        if station.isconnected():
            interface = station
            mode = "STA"
    except Exception:
        interface = None

    ap = network.WLAN(network.WLAN.IF_AP)
    if interface is not None:
        ap.active(False)
    else:
        # ESP32-S3 MicroPython can reject AP setup while an unsuccessful STA
        # connection remains active ("Wifi Invalid Mode"). Fully stop it first.
        try:
            station.disconnect()
            station.active(False)
        except Exception:
            pass
        time.sleep_ms(100)
        # Current ESP32-S3 MicroPython requires the AP interface to be active
        # before applying its configuration; otherwise config() raises
        # OSError("Wifi Invalid Mode").
        ap.active(True)
        time.sleep_ms(100)
        try:
            security = getattr(network.WLAN, "SEC_WPA2", None)
            if security is None:
                security = getattr(network, "SEC_WPA2", None)
            if security is None:
                raise ValueError("new WLAN security API unavailable")
            ap.config(ssid=WIFI_SSID, security=security, key=WIFI_PASSWORD, channel=6)
        except Exception:
            authmode = getattr(network, "AUTH_WPA_WPA2_PSK", 4)
            ap.config(essid=WIFI_SSID, password=WIFI_PASSWORD, authmode=authmode, channel=6)
        ap.ifconfig(("192.168.4.1", "255.255.255.0", "192.168.4.1", "192.168.4.1"))
        interface = ap

    ip_address = interface.ifconfig()[0]

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("0.0.0.0", WIFI_PORT))
    listener.listen(1)
    listener.setblocking(False)

    discovery = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    discovery.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    discovery.bind(("0.0.0.0", WIFI_DISCOVERY_PORT))
    discovery.setblocking(False)
    return interface, listener, discovery, ip_address, mode


def load_relay_config():
    try:
        import relay_config
    except ImportError:
        return None
    url = str(getattr(relay_config, "RELAY_URL", "")).strip()
    token = str(getattr(relay_config, "RELAY_TOKEN", "")).strip()
    vehicle_id = str(getattr(relay_config, "VEHICLE_ID", "A")).strip().upper()
    if not url or not token:
        return None
    if vehicle_id not in ("A", "B"):
        raise ValueError("VEHICLE_ID must be A or B")
    return url, token, vehicle_id


def parse_relay_url(url):
    marker = url.find("://")
    if marker <= 0:
        raise ValueError("RELAY_URL must start with http:// or https://")
    scheme = url[:marker].lower()
    if scheme not in ("http", "https"):
        raise ValueError("unsupported relay URL scheme")
    remainder = url[marker + 3 :]
    slash = remainder.find("/")
    if slash < 0:
        authority = remainder
        path = "/api/vehicle/exchange"
    else:
        authority = remainder[:slash]
        path = remainder[slash:] or "/api/vehicle/exchange"
    if not authority or "@" in authority:
        raise ValueError("invalid relay URL")
    if ":" in authority:
        host, port_text = authority.rsplit(":", 1)
        port = int(port_text)
    else:
        host = authority
        port = 443 if scheme == "https" else 80
    return scheme, host, port, path


class RelayHttpClient:
    def __init__(self, url, token, vehicle_id):
        self.scheme, self.host, self.port, self.path = parse_relay_url(url)
        self.token = token
        self.vehicle_id = vehicle_id
        if "\r" in token or "\n" in token:
            raise ValueError("invalid relay token")
        self.sock = None
        self.buffer = bytearray()

    def close(self):
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                pass
        self.sock = None
        self.buffer = bytearray()

    def connect(self):
        self.close()
        address = socket.getaddrinfo(self.host, self.port, 0, socket.SOCK_STREAM)[0][-1]
        raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw.settimeout(25)
        raw.connect(address)
        if self.scheme == "https":
            import ssl

            try:
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                self.sock = context.wrap_socket(raw, server_hostname=self.host)
            except (AttributeError, TypeError):
                self.sock = ssl.wrap_socket(raw, server_hostname=self.host)
        else:
            self.sock = raw

    def _write_all(self, data):
        view = memoryview(data)
        written = 0
        while written < len(view):
            if hasattr(self.sock, "write"):
                count = self.sock.write(view[written:])
            else:
                count = self.sock.send(view[written:])
            if not count:
                raise OSError("relay socket write failed")
            written += count

    def _read_chunk(self, length=1024):
        if hasattr(self.sock, "read"):
            return self.sock.read(length)
        return self.sock.recv(length)

    def _read_response(self):
        header_end = -1
        while header_end < 0:
            chunk = self._read_chunk()
            if not chunk:
                raise OSError("relay connection closed")
            self.buffer.extend(chunk)
            if len(self.buffer) > 16384:
                raise OSError("relay response headers too large")
            header_end = self.buffer.find(b"\r\n\r\n")
        header_bytes = bytes(self.buffer[:header_end])
        self.buffer = self.buffer[header_end + 4 :]
        lines = header_bytes.split(b"\r\n")
        status_fields = lines[0].split(b" ", 2)
        if len(status_fields) < 2:
            raise OSError("invalid relay HTTP status")
        status_code = int(status_fields[1])
        headers = {}
        for line in lines[1:]:
            if b":" in line:
                name, value = line.split(b":", 1)
                headers[name.strip().lower()] = value.strip()
        if b"content-length" not in headers:
            raise OSError("relay response missing content length")
        content_length = int(headers[b"content-length"])
        if content_length < 0 or content_length > RELAY_MAX_PACKET_BYTES:
            raise OSError("invalid relay response length")
        while len(self.buffer) < content_length:
            chunk = self._read_chunk(min(1024, content_length - len(self.buffer)))
            if not chunk:
                raise OSError("relay response body closed")
            self.buffer.extend(chunk)
        body = bytes(self.buffer[:content_length])
        self.buffer = self.buffer[content_length:]
        if headers.get(b"connection", b"").lower() == b"close":
            self.close()
        if status_code != 200:
            raise OSError("relay HTTP %d: %s" % (status_code, body[:120]))
        return body

    def exchange(self, upstream):
        if self.sock is None:
            self.connect()
        request = (
            "POST %s HTTP/1.1\r\n"
            "Host: %s\r\n"
            "User-Agent: DJY-ESP32-S3/1\r\n"
            "Authorization: Bearer %s\r\n"
            "X-DJY-Vehicle-ID: %s\r\n"
            "Content-Type: application/octet-stream\r\n"
            "Content-Length: %d\r\n"
            "Connection: close\r\n"
            "ngrok-skip-browser-warning: 1\r\n\r\n"
            % (self.path, self.host, self.token, self.vehicle_id, len(upstream))
        ).encode()
        self._write_all(request)
        if upstream:
            self._write_all(upstream)
        response = self._read_response()
        # ngrok may acknowledge keep-alive while delaying the next request on
        # this MicroPython TLS socket. One HTTP exchange per connection keeps
        # ECU replies ordered and inside the server response deadline.
        self.close()
        return response


def discard_uart_input(uart):
    while uart.any():
        uart.read(min(uart.any(), 256))


def read_uart_response(uart, first_timeout_ms=2500, idle_timeout_ms=20):
    response = bytearray()
    first_deadline = time.ticks_add(time.ticks_ms(), first_timeout_ms)
    idle_deadline = None
    while len(response) < RELAY_MAX_PACKET_BYTES:
        waiting = uart.any()
        if waiting:
            chunk = uart.read(min(waiting, 256))
            if chunk:
                response.extend(chunk)
                idle_deadline = time.ticks_add(time.ticks_ms(), idle_timeout_ms)
            continue
        now = time.ticks_ms()
        if response:
            if time.ticks_diff(now, idle_deadline) >= 0:
                break
        elif time.ticks_diff(now, first_deadline) >= 0:
            break
        time.sleep_ms(1)
    return bytes(response)


def run_internet_relay(rapid_bike, relay_url, relay_token, vehicle_id):
    client = RelayHttpClient(relay_url, relay_token, vehicle_id)
    upstream = b""
    current_baud = START_BAUD
    retry_ms = 1000
    last_saved_error = None
    while True:
        try:
            led.set(18, 0, 22)  # Purple: server relay connected/waiting.
            downstream = client.exchange(upstream)
            upstream = b""
            retry_ms = 1000
            if not downstream:
                continue
            led.set(0, 0, 30)  # Blue: server command to ECU.
            discard_uart_input(rapid_bike)
            rapid_bike.write(downstream)
            flush_uart(rapid_bike)
            requested_baud = None
            if SET_HIGH_BAUD_REQUEST in downstream:
                requested_baud = HIGH_BAUD
            elif SET_START_BAUD_REQUEST in downstream:
                requested_baud = START_BAUD
            # Map blocks are emitted by the ECU in bursts. A 20 ms gap is a
            # valid inter-burst pause, not the end of the response. Keep the
            # low-latency timeout for normal telemetry and allow map downloads
            # enough quiet time to collect the complete block.
            is_map_download = len(downstream) > 1 and downstream[1] == MAP_DOWNLOAD_COMMAND
            upstream = read_uart_response(
                rapid_bike,
                first_timeout_ms=4000 if is_map_download else 2500,
                idle_timeout_ms=350 if is_map_download else 20,
            )
            if requested_baud is not None and upstream and requested_baud != current_baud:
                reinit_uart(rapid_bike, requested_baud, RAPIDBIKE_RX_PIN, RAPIDBIKE_TX_PIN)
                current_baud = requested_baud
                led.set(28, 18, 0)  # Amber: baud changed.
            elif upstream:
                led.set(0, 26, 22)  # Cyan: ECU response ready for upload.
        except Exception as relay_error:
            client.close()
            error_text = repr(relay_error)
            if error_text != last_saved_error:
                save_fatal_error("internet_relay", relay_error)
                last_saved_error = error_text
            led.set(24, 0, 0)  # Red: internet retry; local firmware stays alive.
            time.sleep_ms(retry_ms)
            retry_ms = min(retry_ms * 2, 5000)


class StatusLed:
    def __init__(self):
        self.devices = []
        import neopixel
        for pin_number in (48, 38):
            try:
                self.devices.append(neopixel.NeoPixel(Pin(pin_number), 1))
            except Exception:
                pass

    def set(self, red, green, blue):
        for device in self.devices:
            try:
                device[0] = (red, green, blue)
                device.write()
            except Exception:
                pass


led = StatusLed()
fatal_stage = "startup"

try:
    # Keep a short UART recovery window after reset. Sending Ctrl-C during
    # this interval stops main.py before the production bridge detaches REPL.
    led.set(18, 12, 0)
    time.sleep_ms(1500)
    # Release the UART0 console before assigning its physical pins to UART2.
    # MicroPython may emit a harmless ESP-IDF warning here when UART0's driver
    # has already been released, so keep the operation guarded.
    os.dupterm(None, 0)
    try:
        UART(0).deinit()
    except Exception:
        pass
    time.sleep_ms(100)

    host = UART(
        2,
        baudrate=START_BAUD,
        bits=8,
        parity=None,
        stop=1,
        rx=HOST_RX_PIN,
        tx=HOST_TX_PIN,
        timeout=0,
        timeout_char=2,
        rxbuf=4096,
        txbuf=4096,
    )
    # The Rapid Bike TX/data line floats when the bike is powered off. Keep
    # UART idle high with the ESP32's weak pull-up so electrical noise is not
    # forwarded or displayed as false cyan ECU activity.
    Pin(RAPIDBIKE_RX_PIN, Pin.IN, Pin.PULL_UP)
    time.sleep_ms(10)
    rapid_bike = UART(
        1,
        baudrate=START_BAUD,
        bits=8,
        parity=None,
        stop=1,
        rx=RAPIDBIKE_RX_PIN,
        tx=RAPIDBIKE_TX_PIN,
        timeout=0,
        timeout_char=2,
        rxbuf=4096,
        txbuf=4096,
    )

    host_buffer = bytearray(256)
    bike_buffer = bytearray(256)
    command_window = bytearray()
    current_baud = START_BAUD
    pending_baud = None
    wifi_ap = None
    wifi_listener = None
    wifi_discovery = None
    wifi_client = None
    wifi_mode = None
    wifi_status = b"WIFI:STARTING"
    last_error_status = load_last_error()
    try:
        wifi_ap, wifi_listener, wifi_discovery, wifi_ip, wifi_mode = start_wifi_bridge()
        wifi_status = ("WIFI:%s:%s:%d" % (wifi_mode, wifi_ip, WIFI_PORT)).encode()
    except Exception as wifi_error:
        # A Wi-Fi problem must never disable the proven wired bridge.
        wifi_status = ("WIFI:ERROR:" + repr(wifi_error)).encode()
        wifi_ap = None
        wifi_listener = None
    relay_config = load_relay_config()
    if relay_config is not None and wifi_mode == "STA":
        # Internet relay mode is intentionally exclusive. The ESP32 keeps one
        # outbound HTTP/TLS connection and carries raw Rapid Bike request and
        # response bytes through it, so no inbound port forwarding is needed.
        for local_socket in (wifi_listener, wifi_discovery):
            if local_socket is not None:
                try:
                    local_socket.close()
                except Exception:
                    pass
        fatal_stage = "internet_relay"
        run_internet_relay(rapid_bike, *relay_config)
    led.set(0, 18, 0)  # Ready: green.
    activity_until = time.ticks_ms()

    while True:
        activity = 0

        if wifi_discovery is not None:
            try:
                discovery_data, discovery_address = wifi_discovery.recvfrom(64)
                if discovery_data == WIFI_DISCOVERY_REQUEST:
                    wifi_discovery.sendto(wifi_status, discovery_address)
            except OSError:
                pass

        if wifi_listener is not None and wifi_client is None:
            try:
                wifi_client, _ = wifi_listener.accept()
                wifi_client.setblocking(False)
            except OSError:
                pass

        if wifi_client is not None:
            try:
                incoming = wifi_client.recv(256)
                if incoming:
                    fatal_stage = "wifi_to_bike_write"
                    rapid_bike.write(incoming)
                    flush_uart(rapid_bike)
                    command_window.extend(incoming)
                    if len(command_window) > 32:
                        command_window = bytearray(command_window[-32:])
                    if SET_HIGH_BAUD_REQUEST in command_window:
                        pending_baud = HIGH_BAUD
                        command_window = bytearray()
                    elif SET_START_BAUD_REQUEST in command_window:
                        pending_baud = START_BAUD
                        command_window = bytearray()
                    activity = 1
                else:
                    wifi_client.close()
                    wifi_client = None
            except OSError:
                pass

        count = host.any()
        if count:
            fatal_stage = "host_read"
            count = min(count, len(host_buffer))
            received = host.readinto(host_buffer, count)
            if received:
                incoming = bytes(memoryview(host_buffer)[:received])
                if incoming == HOST_SELFTEST_REQUEST:
                    # Local PC<->ESP32 check. This frame is never sent to the ECU.
                    host.write(HOST_SELFTEST_RESPONSE)
                    flush_uart(host)
                elif incoming == HOST_WIFI_STATUS_REQUEST:
                    host.write(wifi_status)
                    flush_uart(host)
                elif incoming == HOST_ERROR_STATUS_REQUEST:
                    host.write(last_error_status)
                    flush_uart(host)
                else:
                    fatal_stage = "host_to_bike_write"
                    rapid_bike.write(incoming)
                    flush_uart(rapid_bike)
                    command_window.extend(incoming)
                    if len(command_window) > 32:
                        command_window = bytearray(command_window[-32:])
                    if SET_HIGH_BAUD_REQUEST in command_window:
                        pending_baud = HIGH_BAUD
                        command_window = bytearray()
                    elif SET_START_BAUD_REQUEST in command_window:
                        pending_baud = START_BAUD
                        command_window = bytearray()
                activity = 1

        count = rapid_bike.any()
        if count:
            fatal_stage = "bike_read"
            count = min(count, len(bike_buffer))
            received = rapid_bike.readinto(bike_buffer, count)
            if received:
                fatal_stage = "bike_to_host_write"
                host.write(memoryview(bike_buffer)[:received])
                flush_uart(host)
                if wifi_client is not None:
                    try:
                        wifi_client.write(memoryview(bike_buffer)[:received])
                    except OSError:
                        try:
                            wifi_client.close()
                        except Exception:
                            pass
                        wifi_client = None
                if pending_baud is not None and pending_baud != current_baud:
                    # The acknowledgement has completely left the old-speed
                    # host UART. Switch immediately; RB Master sends its next
                    # frame only a few milliseconds after changing the FTDI.
                    fatal_stage = "bike_baud_%d" % pending_baud
                    reinit_uart(rapid_bike, pending_baud, RAPIDBIKE_RX_PIN, RAPIDBIKE_TX_PIN)
                    fatal_stage = "host_baud_%d" % pending_baud
                    reinit_uart(host, pending_baud, HOST_RX_PIN, HOST_TX_PIN)
                    current_baud = pending_baud
                    led.set(28, 18, 0)  # Baud changed: amber.
                pending_baud = None
                activity = 2

        if activity == 1:
            led.set(0, 0, 30)  # PC command: blue.
            activity_until = time.ticks_add(time.ticks_ms(), 500)
        elif activity == 2:
            led.set(0, 26, 22)  # ECU response: cyan.
            activity_until = time.ticks_add(time.ticks_ms(), 500)
        elif time.ticks_diff(time.ticks_ms(), activity_until) >= 0:
            led.set(0, 10, 0)

        time.sleep_ms(1)
except Exception as fatal_error:
    save_fatal_error(fatal_stage, fatal_error)
    led.set(30, 0, 0)
    fatal_status = ("%s: %r" % (fatal_stage, fatal_error)).encode()
    # Keep a minimal 9600-baud diagnostic channel alive even after a fatal
    # bridge error so the next failure can be read without dumping flash.
    try:
        reinit_uart(host, START_BAUD, HOST_RX_PIN, HOST_TX_PIN)
    except Exception:
        host = None
    while True:
        if host is not None:
            try:
                count = host.any()
                if count:
                    incoming = host.read(count)
                    if incoming == HOST_ERROR_STATUS_REQUEST:
                        host.write(fatal_status)
                        flush_uart(host)
            except Exception:
                pass
        time.sleep_ms(10)
