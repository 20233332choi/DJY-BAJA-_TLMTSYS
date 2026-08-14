from machine import Pin
from time import sleep


# Safe idle firmware: both Rapid Bike signal pins are input-only.
# This file intentionally creates no UART transmitter.
Pin(17, Pin.IN)
Pin(18, Pin.IN)

print("Rapid Bike passive mode")
print("GPIO17 and GPIO18 are input-only; no commands are transmitted.")

while True:
    sleep(1)
