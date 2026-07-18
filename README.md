# 대자연 DJY Baja Pit Wall

대자연 DJY Baja 차량용 피트월 대시보드입니다. Rapid Bike EVO USB와 iPhone GPS를 함께 받아 표시하고 저장합니다.

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
1. 차량 to 서버
2. 속도전송
3. 실시간대쉬보드
4. 주행기록 열람
```

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

이전 실험·캡처 자료와 실제 주행 DB는 로컬에만 보존되며 GitHub 업로드에서는 제외됩니다.
