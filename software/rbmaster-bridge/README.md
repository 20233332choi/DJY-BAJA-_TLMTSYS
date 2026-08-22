# RB Master COM bridge experiment

This experimental 32-bit `DSDEVICE.DLL` presents one D2XX-compatible Rapid Bike
device to RB Master and translates the commonly used FTDI calls to a Windows COM
port. It defaults to `COM8`; set `RB_ESP32_COM` before launching RB Master to use
another port.

For the internet vehicle relay, set `RB_ESP32_RELAY=127.0.0.1:8890`. In that
mode the DLL uses the pit-wall server's loopback TCP bridge instead of a COM
port. Frames are length-prefixed locally and remain raw Rapid Bike bytes across
the HTTPS vehicle tunnel.

The installed Dimsport software and driver files are not modified. The build
script writes the experimental DLL and smoke-test executable to the local
`build/` directory. Copy them manually to a disposable RB Master test directory
only when running this experiment.

All transmitted and received bytes are appended to `rb_esp32_bridge.log` beside
the RB Master test executable.

Hardware test wiring uses the board's CP2102 directly while the ESP32-S3 is held
in reset:

```text
ESP32-S3 RST      -> ESP32-S3 GND (temporary jumper)
GPIO43 / U0TXD    -> Rapid Bike RX
GPIO44 / U0RXD    <- Rapid Bike TX
ESP32-S3 GND      <-> Rapid Bike GND
Rapid Bike VCC    not connected
```

Use only with the engine stopped and key on during initial protocol testing.

The separate
`../../firmware/esp32-s3-rapidbike/micropython/diagnostics/bluebike_ble_main.py`
firmware is a BLE discovery probe only. It advertises the name `BlueBike`, does
not access the Rapid Bike UART pins, and does not yet implement the proprietary
BlueBike protocol.
