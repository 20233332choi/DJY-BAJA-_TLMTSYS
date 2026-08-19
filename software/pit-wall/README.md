# 대자연 DJY Baja Pit Wall

대자연 DJY Baja 차량용 피트월 대시보드입니다. Rapid Bike EVO USB와 iPhone GPS를 함께 받아 표시하고 저장합니다.

저장소 루트에서 이 프로그램 폴더로 이동한 뒤 실행합니다.

```powershell
cd .\software\pit-wall
.\run_app.bat
```

## 새 컴퓨터 설치 요구사항

### 필수 환경

- Windows 10/11 64비트
- Rapid Bike EVO와 `USB Bike Adaptor`
- Python 3.10 이상 64비트
- Edge, Chrome 등 최신 웹 브라우저

Python 설치 시 `Add python.exe to PATH`를 선택해야 `run_app.bat`으로 실행할 수 있습니다. 설치 후 PowerShell에서 다음 명령으로 확인합니다.

```powershell
python --version
python -c "import struct; print(struct.calcsize('P') * 8)"
```

두 번째 명령의 결과가 `64`여야 합니다. 외부 Python 패키지는 사용하지 않으므로 `pip install`은 필요하지 않습니다.

### Rapid Bike USB 드라이버

Rapid Bike Master 또는 Dimsport USB 드라이버를 먼저 설치해야 합니다. 현재 프로그램은 다음 64비트 D2XX DLL을 사용합니다.

```text
C:\Program Files\DimSport\Driver DimSport\DSDEVICE64.dll
```

설치 여부는 PowerShell에서 확인할 수 있습니다.

```powershell
Test-Path "C:\Program Files\DimSport\Driver DimSport\DSDEVICE64.dll"
```

결과가 `True`여야 합니다. Rapid Bike Master는 USB를 동시에 사용할 수 없으며, 이 프로그램을 실행하면 `RBMASTERPRO.EXE`를 자동으로 종료합니다.

### iPhone GPS와 IMU 사용 시

휴대폰 GPS와 가속도 센서는 HTTPS에서만 사용할 수 있으므로 다음 항목이 추가로 필요합니다.

- 인터넷 연결
- ngrok 계정과 인증 토큰
- Windows용 ngrok Agent가 PATH에 등록된 상태
- iPhone Safari

ngrok 설치 후 처음 한 번 인증 토큰을 등록합니다.

```powershell
ngrok config add-authtoken "발급받은_토큰"
ngrok help
```

토큰은 저장소나 설정 파일에 커밋하지 마세요.

## 처음 실행

1. 프로젝트 폴더 전체를 새 컴퓨터로 옮기거나 GitHub에서 복제합니다.
2. Rapid Bike USB를 연결합니다.
3. `run_app.bat`을 실행합니다.
4. Windows 방화벽 창이 뜨면 개인 네트워크 접근을 허용합니다.
5. PC 브라우저에서 `http://127.0.0.1:8765/`을 엽니다.
6. 휴대폰을 사용한다면 `start_gps_https.bat`을 실행하고 표시된 `https://.../phone` 주소를 iPhone Safari에서 엽니다.
7. `GPS + IMU 전송 시작`을 누르고 위치 및 동작·방향 권한을 허용합니다.

정상 연결 시 대시보드 상태에 `USB live`가 표시됩니다. 프로그램을 두 번 실행하면 USB 또는 `8765` 포트가 충돌할 수 있으므로 한 번만 실행하세요.

## ESP32 Wi-Fi Rapid Bike 연결

ESP32 브리지 펌웨어는 차량 근처에서 자체 Wi-Fi를 만듭니다.

```text
SSID: RapidBike-ESP32
Password: RapidBike125
ESP32 IP: 192.168.4.1
TCP port: 8888
```

PC를 이 Wi-Fi에 연결한 뒤 평소처럼 `run_app.bat`을 실행하면 TCP 포트를 자동 감지하고 상태에 `WiFi live`가 표시됩니다. 인터넷 없음 경고는 정상입니다. RPM, TPS, 연료 보정값과 ECU가 보고한 배터리 전압을 수신하고 기존 주행 기록에 저장합니다.

연결 방식을 강제로 선택하려면 실행 전에 환경변수를 설정합니다.

```powershell
$env:RAPIDBIKE_LINK="wifi"  # ESP32 TCP
$env:RAPIDBIKE_LINK="usb"   # 기존 D2XX USB
$env:RAPIDBIKE_LINK="auto"  # 기본값: Wi-Fi 우선, 없으면 USB
python .\app.py
```

Rapid Bike Master와 이 앱이 동시에 ECU 명령을 보내면 응답이 섞일 수 있으므로 둘 중 하나만 실행합니다. 앱은 시작할 때 Rapid Bike Master를 종료합니다.

ESP32에 `wifi_config.py`가 설치되어 있으면 먼저 그 파일의 일반 WPA2 공유기 또는 휴대폰 핫스팟에 접속합니다. 앱은 UDP 포트 `8889`로 ESP32를 자동 발견하므로 DHCP 주소를 직접 입력할 필요가 없습니다. 저장된 Wi-Fi에 12초 이내 접속하지 못하면 위의 `RapidBike-ESP32` 자체 AP로 자동 복귀합니다. `wifi_config.py`는 비밀번호 보호를 위해 Git에서 제외됩니다.

### 트랙 원격 릴레이

차량이 피트 Wi-Fi 범위를 벗어나는 경우 ESP32가 차량 휴대폰 핫스팟과 LTE/5G를 통해 이 서버의 공개 HTTPS 주소로 접속할 수 있습니다.

```powershell
$env:RAPIDBIKE_LINK = "relay"
$env:DJY_RELAY_TOKEN = "ESP32 relay_config.py와 같은 임의 토큰"
python .\app.py
```

이 모드에서는 `/api/vehicle/exchange`가 Rapid Bike 원본 바이트를 차량과 교환하고 `127.0.0.1:8890`이 RB Master 테스트 DLL용 로컬 브리지를 제공합니다. ESP32가 연결되기 전에는 `Relay waiting/retry`, 정상 RPM 수신 중에는 `Relay live`가 표시됩니다.

전체 핫스팟·토큰·ESP32 설치·RB Master 절차는 [차량 원격 무선 릴레이 매뉴얼](../../docs/wireless-relay.md)을 참고하세요.

### 실행 오류 확인

| 상태 | 확인할 내용 |
|---|---|
| `DSDEVICE64.dll not found` | Rapid Bike Master 또는 Dimsport 드라이버 설치 |
| `USB ECU not found` | USB 케이블, 어댑터 전원, Windows 장치 인식 확인 |
| `USB busy` | Rapid Bike Master와 중복 실행 여부 확인 |
| 웹 페이지가 열리지 않음 | `app.py` 실행 여부와 8765 포트, 방화벽 확인 |
| iPhone GPS/IMU 권한이 없음 | ngrok의 HTTPS `/phone` 주소로 접속했는지 확인 |

## 표시 값

```text
RPM        Rapid Bike USB에서 읽은 엔진 회전수
BATTERY    Rapid Bike 상태 응답에서 환산한 ECU 전압(멀티미터 교차 확인 권장)
SPEED      iPhone GPS 속도, km/h
THROTTLE   Rapid Bike 응답의 TPS 열 인덱스 기반 개도량
FUEL ADD   INJ raw - 100, 현재 추가 연료 분사 보정값
PHONE IMU  휴대폰 기준 X/Y/Z 선형 가속도와 중력 포함 TOTAL G
```

## 실행

Rapid Bike Master는 자동으로 종료하고 USB를 직접 읽습니다.

```powershell
python .\app.py
```

또는:

```powershell
.\run_app.bat
```

PC 브라우저에서:

```text
http://127.0.0.1:8765/
```

첫 화면 메뉴:

```text
1. 피트 A/B 차량 (통합 화면)
2. 드라이버 A/B (새 창)
3. 휴대폰 GPS 전송
4. 주행기록 열람
```

피트 A/B 통합 화면에서는 `A/B GPS LAP`, `SPEEDHIVE`, `ECU DASHBOARD`, `RUN CONTROL` 탭으로 기존 기능에 접근할 수 있습니다. 기존 `/timing`, `/lap-timing`, `/map`, `/vehicle`, `/dashboard` 주소도 직접 사용할 수 있습니다.

## 2대 GPS 자체 랩타이밍

Speedhive와 별개로 차량 A/B의 휴대폰 GPS를 동시에 받아 자체 랩타임을 측정할 수 있습니다.

```text
http://127.0.0.1:8765/lap-timing
```

각 차량에서 `/phone?vehicle=A`, `/phone?vehicle=B`를 열어 전송을 시작한 뒤, 한 차량이 출발선을 지날 때 해당 차량으로 출발선을 설정합니다. 진행 방향에 직각인 가상선을 만들며, 같은 방향으로 다시 통과할 때 랩을 완료합니다. 피트 화면에는 두 차량의 현재/최근/베스트 랩, 이전 랩 대비 차이, GPS 정확도와 수신 주기, 현재 위치 궤적을 함께 표시합니다. 출발선 부근 GPS 흔들림에 의한 중복 인식을 막기 위해 교차 후 4초 쿨다운을 적용합니다.

휴대폰 브라우저 GPS의 실제 갱신률과 정확도는 기기 및 OS에 따라 달라집니다. 더 정밀한 측정이 필요하면 Raspberry Pi에 연결된 10Hz 이상의 외장 GNSS가 `/gps` API로 같은 형식의 데이터를 보내도록 연결하는 구성이 적합합니다.

정지 상태에서는 GPS 정확도에 비례한 반경 안의 위치 흔들림을 `STILL LOCK`으로 고정합니다. 주행을 시작하면 자동으로 잠금이 풀리고, 저속에서만 좌표를 부드럽게 보정하며 20km/h 이상에서는 랩 교차 정밀도를 위해 원래 좌표를 사용합니다. 정확도 50m 초과 또는 순간이동으로 판단되는 좌표는 버리고 화면의 `DROP` 수에 누적합니다.

출발선은 `GPS ACC`가 15m 이하인 상태가 최소 15초 연속 유지되어 `GPS READY`가 초록색으로 바뀐 뒤 설정하세요. 실제 경기 전에는 야외의 하늘이 열린 장소에서 GPS 전송을 시작하고 30~60초 정도 기다리는 것을 권장합니다. 오랫동안 위치 서비스를 사용하지 않았거나 주변에 건물·금속 구조물이 많으면 1~2분이 걸릴 수 있으므로 시간보다 `GPS READY` 표시를 우선합니다.

위치 지도는 항상 북쪽이 위쪽인 `NORTH UP` 방식입니다. 화면 가장자리와 우측 상단 나침반에 N/E/S/W를 표시하며, 시작선의 노란 화살표는 차량이 통과해야 하는 진행 방향입니다. 화살표 끝에는 `NE 45°`와 같은 8방위 및 방위각을 함께 표시합니다.

지도의 `START SIZE` 슬라이더는 시작선 마커의 화면상 길이만 40~200px 범위에서 조절합니다. 실제 랩 판정 범위는 상단의 `HALF WIDTH (m)` 값이며, 표시 크기를 바꿔도 판정 범위는 변하지 않습니다.

지도는 GPS가 15초간 안정되어 `READY`가 되기 전까지 흔들리는 좌표를 궤적에 넣지 않습니다. 출발선을 새로 설정하면 기존 궤적을 비우고 `START LOCK`으로 전환하여 출발선을 화면 중앙에 고정합니다. 코스 전체를 맞춰 보려면 `AUTO FIT`, 잘못 쌓인 표시만 지우려면 `CLEAR TRAIL`을 사용합니다. 지도 왼쪽에는 차량별 현재 방위각과 8방위를 표시하고 차량 위치의 화살표도 같은 방향을 가리킵니다. GPS 방위각은 주행 방향이므로 정지 상태의 최초 수신 때는 값이 없을 수 있으며, 주행이 시작되면 GPS 값 또는 연속 좌표로 계산한 마지막 유효 방향을 유지합니다.

실제 도로 지도를 별도 창으로 보려면 `/lap-timing` 또는 `/timing` 상단의 `REAL MAP ↗`를 누르거나 아래 주소를 직접 엽니다.

```text
http://127.0.0.1:8765/map
```

실제 지도에서는 OpenStreetMap 배경 위에 차량 A/B의 현재 위치, GPS 정확도 반경, READY 이후의 이동 궤적, 방위각 화살표와 출발선을 표시합니다. `ALL/A/B`로 표시 차량을 고르고 `START`, `FOLLOW A`, `FOLLOW B`, `FREE`로 화면 중심의 추적 대상을 선택합니다. 지도 타일을 받으려면 인터넷 연결이 필요하지만 타일 연결이 끊겨도 로컬 GPS 수신과 랩 측정은 계속 동작합니다.

## Speedhive 라이브 타이밍

첫 화면에서 `Speedhive 라이브 타이밍`을 선택하거나 브라우저에서 아래 주소를 엽니다.

```text
http://127.0.0.1:8765/timing
```

기본값은 `OHJPNRVR-2147485793` 이벤트입니다. 화면 상단 입력란에 다른 Speedhive 라이브 타이밍 URL 또는 이벤트 ID를 넣고 `연결`을 누르면 해당 이벤트로 전환됩니다. 마지막으로 연결한 이벤트 ID는 브라우저에 저장됩니다.

라이브 화면은 인터넷에서 현재 활성 세션을 약 2초 간격으로 확인해 다음 값을 표시합니다.

```text
경기 및 세션 이름, 그룹
전체/클래스 순위, 차량 번호, 참가자, 클래스
랩 수, 최근 랩, 베스트 랩, 선두와의 차이, 총 주행 시간
```

Speedhive 서버 연결이 잠시 끊어지면 마지막으로 받은 데이터가 `STALE` 상태로 표시됩니다. 아직 활성 세션이 없거나 MYLAPS에서 이벤트 공개가 종료되면 라이브 데이터가 표시되지 않을 수 있습니다.

라이브 타이밍 화면 상단에는 Speedhive에서 수신한 현재 트랙 플래그가 항상 표시됩니다.

### 차량용 드라이버 화면

홈 화면의 `차량용 드라이버 화면` 또는 라이브 타이밍 화면의 `DRIVER ↗`를 누르면 별도 창으로 열립니다.

```text
http://127.0.0.1:8765/driver
```

출발 전에 Speedhive 이벤트를 불러와 자신의 차량을 선택합니다. 주행 화면에는 최근 랩타임과 바로 앞 차량과의 GAP을 크게 표시하고, 전체 참가자 중 현재 순위·클래스 순위·랩 수·베스트랩을 함께 표시합니다. 새 랩이 감지되면 직전 랩보다 빠를 때 원색 초록 `▲`, 느릴 때 원색 빨강 `▼`와 차이 초를 3.5초 동안 전체화면으로 보여줍니다. 차량 선택은 브라우저에 저장되며 `차량 변경` 버튼으로 다시 선택할 수 있습니다.

드라이버 화면의 시작 메뉴에서 `LOCAL GPS`를 선택하면 Speedhive 없이 차량 A/B의 자체 GPS 랩타임을 사용할 수 있습니다. 이 모드에서는 드라이버 화면이 휴대폰 GPS를 직접 `/gps`로 전송하므로 `/phone` 페이지를 별도로 열 필요가 없습니다. 현재 랩, 최근 랩, 베스트 랩, 이전 랩 대비 ▲/▼, 속도, GPS 정확도와 준비 상태를 표시합니다. GPS 권한이 자동으로 시작되지 않으면 상단 `GPS START`를 누르세요. Speedhive 연결이 가능할 때는 LOCAL GPS 모드에서도 트랙 플래그 경고를 계속 받습니다.

차량별 LOCAL GPS 화면을 바로 열려면 `/driver?source=local&vehicle=A` 또는 `/driver?source=local&vehicle=B`를 사용할 수 있습니다.

LOCAL GPS 드라이버 화면은 F1 스티어링 휠처럼 주행 중 즉시 읽을 값의 우선순위를 높여 중앙에 GPS 속도, 상단에 12칸 RPM 시프트 라이트와 숫자 RPM, 좌우에 축소된 LAST/CURRENT LAP을 배치합니다. RapidBike USB RPM과 스로틀은 현재 차량 A에만 연결되므로 차량 B에서는 `RPM ----`, `THROTTLE --`로 표시합니다. 차량 B에 별도 ECU 입력을 추가하기 전까지 차량 A의 엔진 값을 B에 대입하지 않습니다.

피트 월의 `/timing`과 `/lap-timing` 화면에서는 `ALL / A / B`로 열람 차량과 명령 송신 차량을 한 번에 선택합니다. `ALL`은 A/B 정보를 함께 표시하고 두 드라이버에게 보내며, `A` 또는 `B`는 선택 차량 정보만 표시하고 해당 드라이버에게만 보냅니다. `GREEN`, `YELLOW`, `RED`, `STOP`, `BOX THIS LAP`을 수동 전송할 수 있고 `CLEAR`로 선택 대상의 경고를 해제합니다. `RED`, `YELLOW`, `STOP`은 드라이버 화면에 지속되는 전체화면 경고이고, `BOX THIS LAP`은 5초간 점멸한 뒤 상단 배지에 남습니다. 새 랩의 델타 전체화면에는 해당 `LAP` 번호도 같이 표시됩니다.

드라이버 화면은 플래그 변경을 가장 높은 우선순위로 표시합니다. 옐로·레드 플래그는 해제될 때까지 원색 전체화면 경고로 유지되고, 그린은 `TRACK CLEAR`, 피니시와 스톱은 세션 상태를 전체화면으로 안내합니다. 피트 화면의 `CHEQUERED FLAG` 버튼으로 선택한 차량에 수동 체커드 플래그를 보낼 수 있으며 `CLEAR`로 해제합니다. Speedhive의 `Purple` 값은 공식 의미가 정의되지 않은 사용자 지정 상태이므로 피트 화면에 `CUSTOM`으로만 표시하며 드라이버 경고로 사용하지 않습니다.

## iPhone GPS

GUI가 실행되면 GPS 수신 서버가 `8765` 포트로 열립니다.

아이폰 Safari에서 HTTPS 주소로 접속해야 위치 권한이 정상 동작합니다. ngrok를 직접 실행하거나 아래 파일을 사용하세요.

```powershell
.\start_gps_https.bat
```

아이폰에서 표시된 `https://.../phone` 주소를 열고 `GPS + IMU 전송 시작`을 누르면 대시보드의 `SPEED`와 가속도 값이 갱신됩니다.

아이폰은 버튼을 누를 때 GPS와 동작 및 방향 접근 권한을 모두 허용해야 합니다. 휴대폰 기준 X/Y/Z 축이므로 차량에 단단히 고정한 방향을 주행 중 바꾸지 마세요.

## 저장

`차량 to 서버` 화면에서 `주행 시작`을 누른 뒤부터 저장되고, `주행 종료`를 누르면 해당 세션이 닫힙니다.
`주행기록 열람`에서는 왼쪽 세션을 선택해 해당 주행의 요약, RPM/속도 추이, 상세 샘플을 확인할 수 있습니다.

```text
data/djy_baja.sqlite3
sessions
samples
```

새 주행 샘플에는 `accel_x_g`, `accel_y_g`, `accel_z_g`, `total_g`, `motion_age`도 함께 저장됩니다.
또한 ECU 원본 응답(`raw_hex`), GPS 차량 A/B의 수신 시각·속도·위도·경도·정확도·방향·고도·수신 지연, USB/IMU 수신 시각도 샘플별로 저장됩니다. 기존 `data/djy_baja.sqlite3`는 프로그램 재시작 시 필요한 컬럼이 자동으로 추가됩니다.

주행기록에서 세션을 선택한 뒤 `TRACK MAP`을 누르면 저장된 차량 A/B 주행라인을 지도에서 다시 볼 수 있습니다. `ALL LAPS` 또는 랩 번호를 선택할 수 있으며, GPS 원본점과 필터링된 표시점이 함께 보존됩니다.
기록 열람 화면의 `VEHICLE A/B` 탭에서는 차량별 정보와 랩 목록을 따로 확인할 수 있고, 각 랩의 랩타임·주행거리·최고/평균속도·GPS 포인트 및 해당 랩 지도도 확인할 수 있습니다.

이전 실험·캡처 자료와 실제 주행 DB는 로컬에만 보존되며 GitHub 업로드에서는 제외됩니다.
