#include <Arduino.h>

namespace {

// ESP32-S3 UART0 is connected to the board's CP2102 UART USB-C port.
constexpr int HOST_RX_PIN = 44;
constexpr int HOST_TX_PIN = 43;

// Separate UART used for the Rapid Bike 3.3 V TTL connection.
constexpr int RAPIDBIKE_RX_PIN = 18;
constexpr int RAPIDBIKE_TX_PIN = 17;
constexpr uint32_t BRIDGE_BAUD = 9600;

HardwareSerial host(0);
HardwareSerial rapidBike(1);

void forwardAvailable(HardwareSerial &source, HardwareSerial &destination) {
  uint8_t buffer[128];
  size_t length = 0;

  while (source.available() > 0 && length < sizeof(buffer)) {
    const int value = source.read();
    if (value < 0) {
      break;
    }
    buffer[length++] = static_cast<uint8_t>(value);
  }

  if (length > 0) {
    destination.write(buffer, length);
  }
}

}  // namespace

void setup() {
  // No text is printed: every byte on UART0 belongs to RB Master.
  host.begin(BRIDGE_BAUD, SERIAL_8N1, HOST_RX_PIN, HOST_TX_PIN);
  rapidBike.begin(BRIDGE_BAUD, SERIAL_8N1,
                  RAPIDBIKE_RX_PIN, RAPIDBIKE_TX_PIN);
}

void loop() {
  forwardAvailable(host, rapidBike);
  forwardAvailable(rapidBike, host);
  delayMicroseconds(50);
}
