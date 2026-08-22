# DJY Map Tool

This directory starts with a read-only capture workflow. It never transmits
ECU commands and cannot write a map.

Decode a nanoDLA PulseView capture:

```powershell
python .\capture_decode.py C:\Users\user\Documents\rapidbike-handshake-20260819.sr `
  --json C:\Users\user\Documents\rapidbike-handshake-20260819.json
```

The analyzer expects the project wiring used by the vehicle bridge:

- D0 / CH0: ESP32 GPIO17, ESP32 to RapidBike
- D1 / CH1: ESP32 GPIO18, RapidBike to ESP32
- GND: shared ESP32/RapidBike ground

Only GND and analyzer inputs are connected. Never connect the analyzer VCC pin
or a vehicle 12 V line.
