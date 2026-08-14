from machine import UART
from time import sleep_ms, ticks_diff, ticks_ms


RX_PIN = 18
TX_PIN = 17
STATUS_REQUEST = b"\xAA\x21\x55\x20"
RPM_REQUEST = b"\xAA\xDA\x55\xD9"


def read_response(uart, timeout_ms):
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


def request(uart, command, timeout_ms):
    while uart.any():
        uart.read()
    uart.write(command)
    return read_response(uart, timeout_ms)


modes = (
    ("NORMAL", 0),
    ("RX_INVERTED", UART.INV_RX),
    ("TX_INVERTED", UART.INV_TX),
    ("BOTH_INVERTED", UART.INV_RX | UART.INV_TX),
)

for name, inversion in modes:
    uart = UART(
        1,
        baudrate=9600,
        bits=8,
        parity=None,
        stop=1,
        rx=RX_PIN,
        tx=TX_PIN,
        invert=inversion,
        rxbuf=256,
    )
    sleep_ms(100)
    status = request(uart, STATUS_REQUEST, 100)
    rpm = request(uart, RPM_REQUEST, 220)
    print("{} STATUS={} RPM={}".format(name, status.hex(" ").upper(), rpm.hex(" ").upper()))
    uart.deinit()
    sleep_ms(100)
