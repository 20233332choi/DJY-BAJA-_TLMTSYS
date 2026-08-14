from machine import Pin, UART
import time


BAUD = 9600

# Both observed wires are RX-only. TX pins are assigned to unused, unconnected
# GPIOs so this firmware cannot drive either Rapid Bike communication wire.
RXD_LABELED_WIRE_PIN = 17
TXD_LABELED_WIRE_PIN = 18
UNUSED_TX_1_PIN = 15
UNUSED_TX_2_PIN = 16

Pin(RXD_LABELED_WIRE_PIN, Pin.IN)
Pin(TXD_LABELED_WIRE_PIN, Pin.IN)

host_to_rb = UART(
    1,
    baudrate=BAUD,
    bits=8,
    parity=None,
    stop=1,
    rx=RXD_LABELED_WIRE_PIN,
    tx=UNUSED_TX_1_PIN,
    timeout=0,
    timeout_char=2,
)

rb_to_host = UART(
    2,
    baudrate=BAUD,
    bits=8,
    parity=None,
    stop=1,
    rx=TXD_LABELED_WIRE_PIN,
    tx=UNUSED_TX_2_PIN,
    timeout=0,
    timeout_char=2,
)


def emit(direction, payload):
    if not payload:
        return
    print("{:010d} {} {:03d} {}".format(
        time.ticks_ms(),
        direction,
        len(payload),
        payload.hex(" ").upper(),
    ))


print("FTDI/Rapid Bike passive two-wire sniffer")
print("GPIO17 observes the RXD-labeled wire")
print("GPIO18 observes the TXD-labeled wire")
print("No observed communication wire is configured as an output")

while True:
    count = host_to_rb.any()
    if count:
        emit("WIRE-RXD", host_to_rb.read(count))

    count = rb_to_host.any()
    if count:
        emit("WIRE-TXD", rb_to_host.read(count))

    time.sleep_ms(1)
