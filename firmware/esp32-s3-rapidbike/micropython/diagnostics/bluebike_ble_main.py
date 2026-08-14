import bluetooth
import struct
import time


DEVICE_NAME = "BlueBike"
EVENT_LOG = "bluebike_events.log"

_IRQ_CENTRAL_CONNECT = 1
_IRQ_CENTRAL_DISCONNECT = 2
_IRQ_GATTS_WRITE = 3

_FLAG_READ = 0x0002
_FLAG_WRITE_NO_RESPONSE = 0x0004
_FLAG_WRITE = 0x0008
_FLAG_NOTIFY = 0x0010

# Nordic UART Service. This is only a discovery probe; the genuine BlueBike
# UUIDs are not known yet.
_NUS_SERVICE = bluetooth.UUID("6e400001-b5a3-f393-e0a9-e50e24dcca9e")
_NUS_RX = (bluetooth.UUID("6e400002-b5a3-f393-e0a9-e50e24dcca9e"),
           _FLAG_WRITE | _FLAG_WRITE_NO_RESPONSE)
_NUS_TX = (bluetooth.UUID("6e400003-b5a3-f393-e0a9-e50e24dcca9e"),
           _FLAG_READ | _FLAG_NOTIFY)

# Also expose the common FFE0/FFE1 serial profile in the GATT database. It is
# placed in scan-response data because a legacy BlueBike may use a 16-bit UUID.
_FFE0_SERVICE = bluetooth.UUID(0xFFE0)
_FFE1 = (bluetooth.UUID(0xFFE1),
         _FLAG_READ | _FLAG_WRITE | _FLAG_WRITE_NO_RESPONSE | _FLAG_NOTIFY)


def _ad_field(ad_type, value):
    return struct.pack("BB", len(value) + 1, ad_type) + value


def _advertising_data():
    # Exactly 31 bytes: flags + complete name + one 128-bit service UUID.
    return (b"\x02\x01\x06" +
            _ad_field(0x09, DEVICE_NAME.encode()) +
            _ad_field(0x07, bytes(_NUS_SERVICE)))


def _scan_response_data():
    return (_ad_field(0x03, struct.pack("<H", 0xFFE0)) +
            _ad_field(0xFF, b"BlueBike\x03"))


def _log(message):
    try:
        with open(EVENT_LOG, "a") as stream:
            stream.write("{} {}\n".format(time.ticks_ms(), message))
    except OSError:
        pass


class BlueBikeProbe:
    def __init__(self):
        self.ble = bluetooth.BLE()
        self.ble.active(True)
        self.ble.config(gap_name=DEVICE_NAME)
        self.ble.irq(self._irq)
        ((self.nus_rx, self.nus_tx), (self.ffe1,)) = self.ble.gatts_register_services((
            (_NUS_SERVICE, (_NUS_RX, _NUS_TX)),
            (_FFE0_SERVICE, (_FFE1,)),
        ))
        self.connections = set()
        _log("BOOT name={}".format(DEVICE_NAME))
        self._advertise()

    def _advertise(self):
        self.ble.gap_advertise(
            100_000,
            adv_data=_advertising_data(),
            resp_data=_scan_response_data(),
            connectable=True,
        )

    def _irq(self, event, data):
        if event == _IRQ_CENTRAL_CONNECT:
            connection, _, _ = data
            self.connections.add(connection)
            _log("CONNECT handle={}".format(connection))
        elif event == _IRQ_CENTRAL_DISCONNECT:
            connection, _, _ = data
            self.connections.discard(connection)
            _log("DISCONNECT handle={}".format(connection))
            self._advertise()
        elif event == _IRQ_GATTS_WRITE:
            connection, value_handle = data
            value = self.ble.gatts_read(value_handle)
            _log("WRITE handle={} value_handle={} hex={}".format(
                connection, value_handle, value.hex()))
            # Echo writes so a scanner can verify that GATT traffic works.
            if value_handle == self.nus_rx:
                try:
                    self.ble.gatts_notify(connection, self.nus_tx, value)
                except OSError:
                    pass
            elif value_handle == self.ffe1:
                try:
                    self.ble.gatts_notify(connection, self.ffe1, value)
                except OSError:
                    pass


probe = BlueBikeProbe()
print("BlueBike BLE discovery probe active")
print("ECU UART pins are not used")

while True:
    time.sleep_ms(500)
