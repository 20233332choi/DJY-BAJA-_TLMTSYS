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
