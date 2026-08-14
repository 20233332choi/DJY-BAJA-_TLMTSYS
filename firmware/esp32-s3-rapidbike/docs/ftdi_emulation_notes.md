# ESP32-S3 FTDI/D2XX emulation target

The board's native `USB` connector, not its CP2102 `UART` connector, is the
candidate device port. The initial target is the subset used by RB Master:

- USB VID/PID `0403:E6F8`
- one vendor-specific interface
- FTDI-style bulk IN/OUT endpoints
- FTDI control requests for reset, baud, data format, flow control, latency,
  modem control and purge
- two FTDI status bytes prefixed to each bulk-IN packet
- UART bridge at 9600 8N1 to the Rapid Bike TTL connector

Before implementing it, capture a known-good session with the real FTDI adapter
and `ftdi_passive_sniffer.py`. This determines signal direction, idle polarity,
the actual ECU response and whether the connector VCC is only a reference or is
required by the original adapter.
