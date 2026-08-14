from machine import UART
from time import sleep_ms, ticks_diff, ticks_ms


RX_PIN = 18
TX_PIN = 17
STATUS_REQUEST = b"\xAA\x21\x55\x20"
RPM_REQUEST = b"\xAA\xDA\x55\xD9"

rapid_bike = UART(1, baudrate=9600, bits=8, parity=None, stop=1, rx=RX_PIN, tx=TX_PIN)


def discard_input():
    while rapid_bike.any():
        rapid_bike.read()


def request(command, timeout_ms):
    discard_input()
    rapid_bike.write(command)
    started = ticks_ms()
    response = bytearray()
    while ticks_diff(ticks_ms(), started) < timeout_ms:
        waiting = rapid_bike.any()
        if waiting:
            chunk = rapid_bike.read(waiting)
            if chunk:
                response.extend(chunk)
        sleep_ms(1)
    return bytes(response)


def show(label, data):
    print("{} {} byte(s): {}".format(label, len(data), data.hex(" ").upper()))


print("Rapid Bike read-only probe")
print("UART1 RX=GPIO{} TX=GPIO{}, 9600 8N1".format(RX_PIN, TX_PIN))
print("Only STATUS and RPM requests are transmitted.")

while True:
    status = request(STATUS_REQUEST, 80)
    show("STATUS", status)

    rpm_data = request(RPM_REQUEST, 180)
    show("RPM", rpm_data)
    if len(rpm_data) >= 6:
        rpm = (rpm_data[1] << 8) | rpm_data[2]
        fuel_add = rpm_data[5] - 100
        print("DECODE rpm={} tps_index={} fuel_add={}".format(rpm, rpm_data[3], fuel_add))

    sleep_ms(1000)
