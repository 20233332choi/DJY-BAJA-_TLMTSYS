# ESP32-S3 Rapid Bike 펌웨어

## 운영 버전

현재 실제 보드에서 사용하는 애플리케이션은 [`micropython/main.py`](micropython/main.py)입니다. PC USB-UART와 Rapid Bike TTL UART 사이의 유선 브리지, Wi-Fi TCP 브리지, UDP 자동 검색, 상태 LED 및 오류 기록을 제공합니다.

```text
esp32-s3-rapidbike/
├─ micropython/
│  ├─ main.py                 # 보드에 main.py로 설치되는 운영 코드
│  ├─ base-firmware/          # MicroPython ESP32-S3 기본 이미지
│  ├─ config/                 # Wi-Fi 예제와 Windows WLAN 프로필
│  ├─ diagnostics/            # 연결 과정에서 사용한 점검 프로그램
│  └─ tools/                  # 설치 스크립트
├─ arduino-uart-bridge/       # Wi-Fi 없는 최소 9600-baud 유선 대안
└─ docs/                      # FTDI 에뮬레이션 조사 기록
```

## 요구사항

```powershell
python -m pip install --upgrade mpremote esptool
```

기본 MicroPython 이미지 전체를 다시 설치해야 할 때만 `esptool`이 필요합니다. 평상시 `main.py` 업데이트는 `mpremote`만 사용합니다.

## 애플리케이션 업데이트

보드를 REPL/다운로드 가능한 상태로 만든 뒤 다음을 실행합니다.

```powershell
cd .\firmware\esp32-s3-rapidbike\micropython\tools
.\install_wired_bridge.ps1 -Port COM8
```

로컬 `config/wifi_config.py`까지 설치하려면:

```powershell
.\install_wired_bridge.ps1 -Port COM8 -IncludeWifiConfig
```

## MicroPython 기본 이미지 복구

`BOOT`을 누른 채 `RST/EN`을 눌렀다 놓고, `BOOT`을 놓아 다운로드 모드로 진입합니다.

```powershell
python -m esptool --chip esp32s3 --port COM8 erase_flash
python -m esptool --chip esp32s3 --port COM8 --baud 460800 write_flash -z 0x0 .\micropython\base-firmware\ESP32_GENERIC_S3-SPIRAM_OCT-20260406-v1.28.0.bin
```

플래시가 끝나면 리셋하고 운영 `main.py`를 다시 설치합니다.

동봉된 `ESP32_GENERIC_S3-SPIRAM_OCT-20260406-v1.28.0.bin`은 Octal
SPIRAM용 공식 MicroPython v1.28.0 이미지입니다.

- 다운로드: <https://micropython.org/download/ESP32_GENERIC_S3/>
- 소스 코드: <https://github.com/micropython/micropython>
- 라이선스: [MIT](https://github.com/micropython/micropython/blob/master/LICENSE)

## Wi-Fi 설정

[`micropython/config/wifi_config.example.py`](micropython/config/wifi_config.example.py)를 `wifi_config.py`로 복사한 뒤 실제 SSID와 비밀번호를 입력합니다. `wifi_config.py`는 Git에서 제외됩니다.

## Arduino 대안

`arduino-uart-bridge`는 PlatformIO 기반의 단순 유선 브리지입니다. Wi-Fi, LED 상태, 오류 기록, 자동 검색은 포함하지 않으며 UART 양방향 전달만 수행합니다.

```powershell
cd .\firmware\esp32-s3-rapidbike\arduino-uart-bridge
pio run
pio run --target upload
```

하드웨어 배선과 안전 주의사항은 [하드웨어 매뉴얼](../../docs/hardware/README.md)을 먼저 확인하세요.
