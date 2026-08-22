# 차량 원격 무선 릴레이

이 모드는 차량의 ESP32-S3가 휴대폰 핫스팟을 통해 피트월 서버로 **밖으로 접속**합니다. 포트포워딩은 필요하지 않으며 Rapid Bike 요청과 응답을 원본 바이트로 보존합니다.

```text
Rapid Bike ↔ ESP32-S3 ↔ 휴대폰 핫스팟 ↔ LTE/5G ↔ HTTPS 서버(피트 노트북)
                                                    ├─ 피트월 RPM/TPS/전압
                                                    ├─ 휴대폰 GPS/IMU/드라이버 화면
                                                    └─ RB Master 로컬 DLL 브리지
```

## 통신 방식

ESP32는 `/api/vehicle/exchange`에 인증된 장시간 HTTP 요청을 유지합니다. 서버에 보낼 ECU 응답이 있으면 요청 본문에 넣고, 서버는 차량으로 보낼 다음 명령을 응답 본문으로 돌려줍니다.

Rapid Bike가 요청-응답 방식이므로 이 half-duplex 교환은 다음 순서로 동작합니다.

1. ESP32가 서버 명령을 기다립니다.
2. 피트월이 상태 또는 RPM 요청을 큐에 넣습니다.
3. ESP32가 요청을 UART로 Rapid Bike에 전달합니다.
4. ECU 응답을 다음 HTTPS 요청에 실어 서버로 보냅니다.
5. 피트월 화면과 DB가 갱신됩니다.

연결이 끊기면 ESP32는 1초부터 최대 30초까지 지수 백오프로 자동 재접속합니다. 차량에서 서버로 시작하는 연결이므로 이동통신사의 NAT 환경에서도 동작합니다.

## 1. 공통 토큰 생성

Windows PowerShell에서 32바이트 임의 토큰을 만듭니다.

```powershell
$relayBytes = New-Object byte[] 32
$relayRng = [Security.Cryptography.RandomNumberGenerator]::Create()
$relayRng.GetBytes($relayBytes)
$relayToken = ([BitConverter]::ToString($relayBytes)).Replace('-', '')
$relayToken
```

출력된 값은 서버 환경변수와 ESP32 `relay_config.py`에 동일하게 입력합니다. 저장소, 채팅, 캡처 화면에 공개하지 마세요.

## 2. 피트 노트북 서버

고정 HTTPS 주소가 있는 ngrok 도메인 또는 별도 HTTPS 리버스 프록시를 `127.0.0.1:8765`로 연결합니다. 주소가 매번 바뀌면 ESP32 설정도 다시 설치해야 하므로 경기용은 고정 주소가 필요합니다.

```powershell
cd .\software\pit-wall
$env:RAPIDBIKE_LINK = 'relay'
$env:DJY_RELAY_TOKEN = $relayToken
python .\app.py
```

정상 시작 로그:

```text
DJY Baja app: http://127.0.0.1:8765/
Vehicle relay: /api/vehicle/exchange
RB Master local relay: 127.0.0.1:8890
```

연결 상태 확인:

```text
http://127.0.0.1:8765/api/vehicle/relay-status
```

차량 A의 `online`이 `true`이고 `age_s`가 계속 작게 유지되면 ESP32 인터넷 릴레이가 살아 있습니다.

## 3. ESP32 설정

예제 파일 두 개를 복사합니다.

```powershell
cd .\firmware\esp32-s3-rapidbike\micropython\config
Copy-Item .\wifi_config.example.py .\wifi_config.py
Copy-Item .\relay_config.example.py .\relay_config.py
```

`wifi_config.py`에는 차량 휴대폰 핫스팟을 입력합니다.

```python
WIFI_STA_NETWORKS = (
    ("CAR_PHONE_HOTSPOT", "HOTSPOT_PASSWORD"),
)
```

`relay_config.py`에는 공개 HTTPS 주소와 공통 토큰을 입력합니다.

```python
RELAY_URL = "https://YOUR-STABLE-DOMAIN.example/api/vehicle/exchange"
RELAY_TOKEN = "SERVER와_같은_임의_토큰"
VEHICLE_ID = "A"
```

설치:

```powershell
cd ..\tools
.\install_wired_bridge.ps1 -Port COM8 -IncludeWifiConfig -IncludeRelayConfig
```

`relay_config.py`가 있더라도 핫스팟 STA 연결에 실패하면 인터넷 릴레이는 시작하지 않고 기존 로컬 AP/유선 브리지 모드로 남습니다.

## 4. RPM 수신 시험

1. 피트 노트북 서버와 HTTPS 터널을 먼저 실행합니다.
2. 차량 휴대폰 핫스팟을 켭니다.
3. ESP32와 Rapid Bike 전원을 켭니다.
4. `/api/vehicle/relay-status`에서 차량 A가 온라인인지 확인합니다.
5. 대시보드에서 `Relay live`와 RPM/TPS/전압이 갱신되는지 확인합니다.
6. 휴대폰은 공개 주소의 `/driver?source=local&vehicle=A`를 열어 GPS와 피트 명령을 함께 사용합니다.

## 5. RB Master 원본 통신과 맵 전송

피트월 서버는 `127.0.0.1:8890`에 로컬 전용 프레임 브리지를 엽니다. 수정된 32비트 `DSDEVICE.DLL`은 `RB_ESP32_RELAY` 환경변수가 있을 때 COM8 대신 이 포트를 사용합니다.

```powershell
cd .\software\rbmaster-bridge
.\build_bridge.ps1
$env:RB_ESP32_RELAY = '127.0.0.1:8890'
```

`build/DSDEVICE.DLL`은 설치된 원본 RB Master를 덮어쓰지 말고 별도의 테스트용 복사본 옆에 둡니다. 그 PowerShell 창에서 테스트용 `RBMASTERPRO.EXE`를 실행합니다.

RB Master가 연결되면 피트월의 자동 RPM 폴링은 일시 중지되고 RB Master 원본 패킷이 독점적으로 터널을 사용합니다. RB Master가 종료되면 자동 RPM 폴링이 재개됩니다.

> **맵 쓰기 안전 경고:** 실제 ECU 맵 쓰기는 아직 차량 실물과 이동통신 구간에서 검증되지 않았습니다. 통신 단절 중 쓰기는 ECU 데이터를 손상시킬 수 있습니다. 첫 시험은 엔진 정지, 안정화 전원, 유선 복구 어댑터 및 원본 맵 백업이 있는 벤치에서 수행하세요. 읽기와 RPM 모니터링을 먼저 장시간 검증한 뒤 쓰기를 허용해야 합니다.

## LED

| 색상 | 릴레이 상태 |
|---|---|
| 보라색 | 서버 연결/명령 대기 |
| 파란색 | 서버 명령을 ECU로 전송 |
| 청록색 | ECU 응답을 서버로 업로드 준비 |
| 주황색 | UART baud 전환 |
| 빨간색 | 인터넷 연결 실패, 자동 재시도 |

## 현재 검증 범위

- **대회장 실전 운용 및 실전 검증 완료**: 실제 차량 주행 중 텔레메트리 수신, 드라이버 HUD 및 피트월 랩타이밍 운용 검증 완료
- **보조 ECU 데이터 수신 전용 (Read-Only)**: 현재는 Rapid Bike 보조 ECU로부터 실시간 정보(RPM, TPS, 연료 보정값, 배터리 전압)를 안정적으로 수신 및 모니터링하는 용도로 안전하게 운용 중
- 서버 원본 바이트 request/response 터널 자동 테스트
- 인증 없는 차량 요청 거부
- RPM/TPS/전압 디코딩까지의 모의 및 실차 왕복
- 로컬 TCP 프레임 경계 보존
- 32비트 D2XX DLL의 TCP 열기, Write, Read 스모크 테스트

> **참고:** 현재 안정적인 모니터링을 위해 수신(Read-Only) 기능 위주로 실전 운용되었으며, 무선 환경에서의 맵 쓰기(Map Write)는 통신 단절 시 ECU 보호를 위해 추후 벤치 테스트 단계로 유지하고 있습니다.
