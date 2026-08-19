# DJY Baja Telemetry System

DJY Baja 차량의 Rapid Bike ECU 데이터, iPhone GPS/IMU, 랩타이밍을 한곳에서 수집하고 표시하는 텔레메트리 프로젝트입니다. ESP32-S3 펌웨어와 Windows 피트월 소프트웨어를 서로 독립적으로 관리할 수 있도록 분리했습니다.

## 프로젝트 구성

```text
DJY-BAJA-_TLMTSYS/
├─ firmware/
│  └─ esp32-s3-rapidbike/
│     ├─ micropython/             # 현재 보드에서 사용하는 운영 펌웨어
│     ├─ arduino-uart-bridge/     # 최소 유선 브리지 대안
│     └─ docs/                    # 펌웨어 실험 기록
├─ software/
│  ├─ pit-wall/                   # Python 서버와 웹 대시보드
│  ├─ rbmaster-bridge/            # RB Master 연동 실험용 C 소스
│  └─ rbmaster-tools/             # 진단 스크립트
├─ docs/
│  ├─ hardware/                   # 배선 매뉴얼과 Espressif PDF
│  └─ images/                     # 핀맵 및 진단 화면
└─ .local/                        # 제조사 프로그램·DLL 등 로컬 전용 파일(Git 제외)
```

## 빠른 시작

### 1. 피트월 소프트웨어

Windows PowerShell 또는 명령 프롬프트에서 다음을 실행합니다.

```powershell
cd .\software\pit-wall
.\run_app.bat
```

브라우저에서 `http://127.0.0.1:8765/`을 엽니다. 자세한 설치, GPS/IMU, 랩타이밍, 데이터 저장 방법은 [피트월 소프트웨어 매뉴얼](software/pit-wall/README.md)을 참고하세요.

### 2. ESP32-S3 펌웨어

현재 보드에서 사용하는 운영 파일은 [MicroPython `main.py`](firmware/esp32-s3-rapidbike/micropython/main.py)입니다. 기본 MicroPython 이미지와 설치 도구도 함께 보관합니다.

```powershell
cd .\firmware\esp32-s3-rapidbike\micropython\tools
.\install_wired_bridge.ps1 -Port COM8
```

전체 설치 및 복구 방법은 [ESP32-S3 펌웨어 매뉴얼](firmware/esp32-s3-rapidbike/README.md)을 참고하세요.

## 하드웨어 매뉴얼

사용 보드는 `ESP32-S3-DevKitC-1` 계열이며 Rapid Bike 통신은 3.3V TTL UART 기준입니다.

| ESP32-S3 | 역할 | 연결 대상 |
|---|---|---|
| GPIO17 (`U1TXD`) | ECU 방향 송신 | Rapid Bike RX |
| GPIO18 (`U1RXD`) | ECU 응답 수신 | Rapid Bike TX/Data |
| GND | 신호 기준 | Rapid Bike GND |
| USB-to-UART | PC 연결 및 전원 | Windows PC USB |

> **전기적 주의:** ESP32 GPIO에는 5V 또는 차량의 12V를 직접 연결하면 안 됩니다. Rapid Bike 측 신호가 3.3V TTL인지 확인하고, 보드는 USB 또는 검증된 레귤레이터로 전원 공급하세요. 차량과 ESP32 사이에는 반드시 공통 GND가 필요합니다.

보드의 GPIO43/44는 PC USB-to-UART 통신에 사용하고 GPIO17/18은 Rapid Bike 통신에 사용합니다. Octal Flash/PSRAM 모델에서는 GPIO35~37이 내부 메모리에 사용될 수 있으므로 외부 배선에 사용하지 않습니다.

- [상세 배선·LED·복구 매뉴얼](docs/hardware/README.md)
- [차량 원격 무선 릴레이 매뉴얼](docs/wireless-relay.md)
- [ESP32-S3 DevKit 전체 PDF](docs/hardware/esp32-s3-devkitc-1-user-guide.pdf)
- [ESP32-S3 핀 배치 이미지](docs/images/hardware/esp32_s3_pin_layout.png)

## 기본 통신 설정

| 항목 | 값 |
|---|---|
| UART 시작 속도 | 9600 baud, 8-N-1 |
| UART 고속 전환 | 38400 baud |
| ESP32 AP SSID | `RapidBike-ESP32` |
| ESP32 AP 주소 | `192.168.4.1` |
| TCP 데이터 포트 | `8888` |
| UDP 검색 포트 | `8889` |
| PC 대시보드 | `http://127.0.0.1:8765/` |
| 차량 HTTPS 릴레이 | `/api/vehicle/exchange` |
| RB Master 로컬 릴레이 | `127.0.0.1:8890` |

AP 비밀번호는 펌웨어 소스에 정의되어 있습니다. 실제 운용 전에는 팀 전용 비밀번호로 변경하세요. 일반 공유기나 휴대폰 핫스팟 접속 정보는 `wifi_config.py`에 저장하며 Git에 포함하지 않습니다.

## GitHub에 포함하지 않는 파일

- 실제 주행 SQLite DB와 로그
- Wi-Fi SSID/비밀번호가 들어 있는 `wifi_config.py`
- PlatformIO 빌드 캐시와 Python 캐시
- Rapid Bike Master 설치 파일, DLL, 패치 실행 파일
- 패킷 캡처 및 임시 진단 결과

제조사 프로그램은 `.local/rbmaster/`에 로컬 보관되며 저장소에는 올리지 않습니다. 이미 Git 기록에 들어간 제조사 바이너리가 있다면 단순 삭제 커밋만으로 과거 기록에서 사라지지는 않으므로, 공개 저장소 전환 전 별도의 이력 정리가 필요합니다.

## 외부 문서 출처

동봉한 ESP32-S3 PDF와 핀 이미지는 Espressif의 `esp-dev-kits` 문서에서 가져왔습니다.

- 원문: <https://docs.espressif.com/projects/esp-dev-kits/en/latest/esp32s3/>
- 원본 저장소: <https://github.com/espressif/esp-dev-kits>
- 문서 라이선스: [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)

PDF 파일은 내용 변경 없이 이름만 프로젝트 용도에 맞게 정리했습니다. Espressif 및 제품명은 각 권리자의 상표입니다.

동봉한 ESP32-S3 MicroPython v1.28.0 Octal-SPIRAM 바이너리는
[MicroPython 공식 다운로드](https://micropython.org/download/ESP32_GENERIC_S3/)에서
제공되며 [MIT 라이선스](https://github.com/micropython/micropython/blob/master/LICENSE)를 따릅니다.
