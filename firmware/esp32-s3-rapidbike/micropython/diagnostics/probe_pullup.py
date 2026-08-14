from machine import Pin, UART
from time import sleep_ms, ticks_diff, ticks_ms


RX_PIN = 18
TX_PIN = 17
STATUS_REQUEST = b"\xAA\x21\x55\x20"
RPM_REQUEST = b"\xAA\xDA\x55\xD9"

# The Rapid Bike connector exposes a 3.3 V reference and its data pins float
# without the original adapter. Enable only the ESP32's weak internal pull-ups;
# do not connect the two 3.3 V supply rails together.
Pin(RX_PIN, Pin.IN, Pin.PULL_UP)
Pin(TX_PIN, Pin.IN, Pin.PULL_UP)
sleep_ms(50)

uart = UART(
    1,
    baudrate=9600,
    bits=8,
    parity=None,
    stop=1,
    rx=RX_PIN,
    tx=TX_PIN,
    rxbuf=256,
)


def read_for(timeout_ms):
    started = ticks_ms()
    response = bytearray()
    while ticks_diff(ticks_ms(), started) < timeout_ms:
        waiting = uart.any()
        if waiting:
            chunk = uart.read(waiting)
            if chunk:
                response.extend(chunk)
        sleep_ms(1)
    return bytes(response)


def request(command, timeout_ms):
    while uart.any():
        uart.read()
    uart.write(command)
    uart.flush()
    return read_for(timeout_ms)


for attempt in range(5):
    status = request(STATUS_REQUEST, 80)
    rpm = request(RPM_REQUEST, 180)
    print(
        "TRY {} STATUS={} RPM={}".format(
            attempt + 1, status.hex(" ").upper(), rpm.hex(" ").upper()
        )
    )
    sleep_ms(250)

uart.deinit()
