from machine import Pin, UART
import os
import time


STATUS_REQUEST = bytes((0xAA, 0x21, 0x55, 0x20))


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
os.dupterm(None, 0)
try:
    UART(0).deinit()
except Exception:
    pass
time.sleep_ms(100)

host = UART(2, baudrate=9600, bits=8, parity=None, stop=1,
            rx=44, tx=43, timeout=0, timeout_char=2,
            rxbuf=1024, txbuf=1024)
bike = UART(1, baudrate=9600, bits=8, parity=None, stop=1,
            rx=18, tx=17, timeout=0, timeout_char=2,
            rxbuf=1024, txbuf=1024)

# Give the PC time to open COM8 after a reset.
led.set(18, 0, 18)
time.sleep_ms(2000)

while bike.any():
    bike.read()

led.set(0, 0, 30)
bike.write(STATUS_REQUEST)
bike.flush()

response = bytearray()
deadline = time.ticks_add(time.ticks_ms(), 700)
while time.ticks_diff(deadline, time.ticks_ms()) > 0:
    count = bike.any()
    if count:
        chunk = bike.read(count)
        if chunk:
            response.extend(chunk)
    time.sleep_ms(1)

if response:
    host.write(response)
    host.flush()
    led.set(0, 28, 24)  # Cyan held: GPIO17/18 query succeeded.
else:
    led.set(30, 0, 0)   # Red held: no Rapid Bike reply.

while True:
    time.sleep_ms(1000)
