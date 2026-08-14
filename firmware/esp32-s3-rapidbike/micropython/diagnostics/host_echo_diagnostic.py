from machine import Pin, UART
import os
import time


HOST_RX_PIN = 44
HOST_TX_PIN = 43
BAUD = 9600
SAFE_FLAG = "safe_mode.flag"


class StatusLed:
    def __init__(self):
        self.devices = []
        try:
            import neopixel
            for pin_number in (48, 38):
                try:
                    self.devices.append(neopixel.NeoPixel(Pin(pin_number), 1))
                except Exception:
                    pass
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
boot = Pin(0, Pin.IN, Pin.PULL_UP)

try:
    os.stat(SAFE_FLAG)
    safe_mode = True
except OSError:
    safe_mode = False

if safe_mode:
    led.set(0, 0, 24)
    print("HOST ECHO SAFE MODE")
    print("Delete safe_mode.flag and reset to resume diagnostic mode")
else:
    # Release the UART0 console route before assigning GPIO44/43 to UART2.
    os.dupterm(None, 0)
    try:
        UART(0).deinit()
    except Exception:
        pass
    time.sleep_ms(100)

    host = UART(
        2,
        baudrate=BAUD,
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
    buffer = bytearray(256)
    led.set(18, 0, 18)  # Diagnostic ready (purple/magenta).
    last_activity = time.ticks_ms()
    boot_pressed_at = None

    while True:
        if boot.value() == 0:
            if boot_pressed_at is None:
                boot_pressed_at = time.ticks_ms()
            elif time.ticks_diff(time.ticks_ms(), boot_pressed_at) >= 800:
                with open(SAFE_FLAG, "w") as stream:
                    stream.write("1")
                led.set(0, 0, 24)
                time.sleep_ms(200)
                import machine
                machine.reset()
        else:
            boot_pressed_at = None

        count = host.any()
        if count:
            if count > len(buffer):
                count = len(buffer)
            received = host.readinto(buffer, count)
            if received:
                host.write(memoryview(buffer)[:received])
                led.set(0, 0, 30)
                last_activity = time.ticks_ms()
        elif time.ticks_diff(time.ticks_ms(), last_activity) > 800:
            led.set(18, 0, 18)

        time.sleep_ms(1)
