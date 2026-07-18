#!/usr/bin/env python3
"""DJY Baja telemetry app."""

from __future__ import annotations

import ctypes
import json
import math
import queue
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "djy_baja.sqlite3"

RPM_REQUEST = bytes.fromhex("AA DA 55 D9")
STATUS_REQUEST = bytes.fromhex("AA 21 55 20")
TPS_INDEX_TO_PERCENT = {1: 0, 2: 5, 3: 10, 4: 20, 5: 40, 6: 60, 7: 80, 8: 95}

FT_OK = 0
FT_PURGE_RX = 1
FT_PURGE_TX = 2
FT_OPEN_BY_SERIAL_NUMBER = 1
DSDEVICE64_PATH = Path(r"C:\Program Files\DimSport\Driver DimSport\DSDEVICE64.dll")


@dataclass
class GpsState:
    ts: float = 0.0
    speed_kmh: float | None = None
    lat: float | None = None
    lon: float | None = None
    accuracy_m: float | None = None
    heading: float | None = None
    altitude: float | None = None


@dataclass
class MotionState:
    ts: float = 0.0
    accel_x_g: float | None = None
    accel_y_g: float | None = None
    accel_z_g: float | None = None
    total_g: float | None = None


@dataclass
class TelemetryState:
    rpm: int = 0
    throttle_percent: int = 0
    fuel_add: int = 0
    raw_hex: str = ""
    usb_status: str = "starting"
    usb_ts: float = 0.0
    gps: GpsState = field(default_factory=GpsState)
    motion: MotionState = field(default_factory=MotionState)
    recording: bool = False
    session_id: int | None = None
    session_name: str = ""


class SharedState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.state = TelemetryState()

    def latest(self) -> TelemetryState:
        with self.lock:
            gps = GpsState(**self.state.gps.__dict__)
            motion = MotionState(**self.state.motion.__dict__)
            return TelemetryState(
                rpm=self.state.rpm,
                throttle_percent=self.state.throttle_percent,
                fuel_add=self.state.fuel_add,
                raw_hex=self.state.raw_hex,
                usb_status=self.state.usb_status,
                usb_ts=self.state.usb_ts,
                gps=gps,
                motion=motion,
                recording=self.state.recording,
                session_id=self.state.session_id,
                session_name=self.state.session_name,
            )

    def update_usb(self, rpm: int, throttle: int, fuel_add: int, raw_hex: str) -> None:
        with self.lock:
            self.state.rpm = rpm
            self.state.throttle_percent = throttle
            self.state.fuel_add = fuel_add
            self.state.raw_hex = raw_hex
            self.state.usb_status = "USB live"
            self.state.usb_ts = time.time()

    def update_status(self, status: str) -> None:
        with self.lock:
            self.state.usb_status = status

    def update_gps(self, payload: dict[str, Any]) -> None:
        with self.lock:
            self.state.gps = GpsState(
                ts=float(payload.get("ts") or time.time()),
                speed_kmh=_float_or_none(payload.get("speed_kmh")),
                lat=_float_or_none(payload.get("lat")),
                lon=_float_or_none(payload.get("lon")),
                accuracy_m=_float_or_none(payload.get("accuracy_m")),
                heading=_float_or_none(payload.get("heading")),
                altitude=_float_or_none(payload.get("altitude")),
            )

    def update_motion(self, payload: dict[str, Any]) -> None:
        with self.lock:
            self.state.motion = MotionState(
                ts=time.time(),
                accel_x_g=_float_or_none(payload.get("accel_x_g")),
                accel_y_g=_float_or_none(payload.get("accel_y_g")),
                accel_z_g=_float_or_none(payload.get("accel_z_g")),
                total_g=_float_or_none(payload.get("total_g")),
            )

    def set_recording(self, recording: bool, session_id: int | None = None, session_name: str = "") -> None:
        with self.lock:
            self.state.recording = recording
            self.state.session_id = session_id
            self.state.session_name = session_name


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def close_rbmaster() -> None:
    subprocess.run(["taskkill", "/IM", "RBMASTERPRO.EXE", "/F"], capture_output=True, text=True, check=False)


def check_ft(status: int, action: str) -> None:
    if status != FT_OK:
        raise RuntimeError(f"{action} failed: FT_STATUS={status}")


def load_d2xx() -> ctypes.WinDLL:
    if not DSDEVICE64_PATH.exists():
        raise RuntimeError(f"DSDEVICE64.dll not found: {DSDEVICE64_PATH}")
    return ctypes.WinDLL(str(DSDEVICE64_PATH))


def d2xx_devices(dll: ctypes.WinDLL) -> list[dict[str, Any]]:
    num = ctypes.c_ulong()
    check_ft(dll.FT_CreateDeviceInfoList(ctypes.byref(num)), "FT_CreateDeviceInfoList")
    devices = []
    for index in range(num.value):
        flags = ctypes.c_ulong()
        device_type = ctypes.c_ulong()
        device_id = ctypes.c_ulong()
        loc_id = ctypes.c_ulong()
        serial = ctypes.create_string_buffer(16)
        description = ctypes.create_string_buffer(64)
        handle = ctypes.c_void_p()
        check_ft(
            dll.FT_GetDeviceInfoDetail(
                ctypes.c_ulong(index),
                ctypes.byref(flags),
                ctypes.byref(device_type),
                ctypes.byref(device_id),
                ctypes.byref(loc_id),
                serial,
                description,
                ctypes.byref(handle),
            ),
            "FT_GetDeviceInfoDetail",
        )
        devices.append(
            {
                "index": index,
                "flags": flags.value,
                "serial": serial.value.decode("ascii", errors="replace"),
                "description": description.value.decode("ascii", errors="replace"),
            }
        )
    return devices


class D2xxDevice:
    def __init__(self, dll: ctypes.WinDLL, serial_number: str | None, index: int):
        self.dll = dll
        self.handle = ctypes.c_void_p()
        if serial_number:
            serial_buf = ctypes.create_string_buffer(serial_number.encode("ascii"))
            status = dll.FT_OpenEx(serial_buf, ctypes.c_ulong(FT_OPEN_BY_SERIAL_NUMBER), ctypes.byref(self.handle))
        else:
            status = dll.FT_Open(ctypes.c_int(index), ctypes.byref(self.handle))
        check_ft(status, "FT_Open")

    def configure(self) -> None:
        check_ft(self.dll.FT_ResetDevice(self.handle), "FT_ResetDevice")
        check_ft(self.dll.FT_SetBaudRate(self.handle, ctypes.c_ulong(9600)), "FT_SetBaudRate")
        check_ft(self.dll.FT_SetDataCharacteristics(self.handle, ctypes.c_ubyte(8), ctypes.c_ubyte(0), ctypes.c_ubyte(0)), "FT_SetDataCharacteristics")
        check_ft(self.dll.FT_SetFlowControl(self.handle, ctypes.c_ushort(0), ctypes.c_ubyte(0), ctypes.c_ubyte(0)), "FT_SetFlowControl")
        self.set_timeouts(100, 0)
        check_ft(self.dll.FT_Purge(self.handle, ctypes.c_ulong(FT_PURGE_RX | FT_PURGE_TX)), "FT_Purge")

    def set_timeouts(self, read_ms: int, write_ms: int) -> None:
        check_ft(self.dll.FT_SetTimeouts(self.handle, ctypes.c_ulong(read_ms), ctypes.c_ulong(write_ms)), "FT_SetTimeouts")

    def queue_status(self) -> int:
        rx = ctypes.c_ulong()
        check_ft(self.dll.FT_GetQueueStatus(self.handle, ctypes.byref(rx)), "FT_GetQueueStatus")
        return int(rx.value)

    def read(self, size: int) -> bytes:
        buf = ctypes.create_string_buffer(size)
        read = ctypes.c_ulong()
        check_ft(self.dll.FT_Read(self.handle, buf, ctypes.c_ulong(size), ctypes.byref(read)), "FT_Read")
        return bytes(buf.raw[: read.value])

    def write(self, data: bytes) -> None:
        written = ctypes.c_ulong()
        check_ft(self.dll.FT_Write(self.handle, data, ctypes.c_ulong(len(data)), ctypes.byref(written)), "FT_Write")

    def close(self) -> None:
        if self.handle:
            self.dll.FT_Close(self.handle)
            self.handle = ctypes.c_void_p()


def read_for(dev: D2xxDevice, duration: float) -> bytes:
    started = time.monotonic()
    parts: list[bytes] = []
    while time.monotonic() - started < duration:
        waiting = dev.queue_status()
        if waiting:
            parts.append(dev.read(min(waiting, 64)))
        else:
            time.sleep(0.005)
    return b"".join(parts)


def decode_frame(data: bytes) -> tuple[int, int, int] | None:
    if len(data) < 6:
        return None
    rpm = (data[1] << 8) | data[2]
    throttle = TPS_INDEX_TO_PERCENT.get(data[3], 0)
    fuel_add = int(data[5]) - 100
    return rpm, throttle, fuel_add


class UsbWorker(threading.Thread):
    def __init__(self, state: SharedState, db_queue: queue.Queue[tuple[float, TelemetryState]]):
        super().__init__(daemon=True)
        self.state = state
        self.db_queue = db_queue
        self.stop_event = threading.Event()
        self.device: D2xxDevice | None = None

    def run(self) -> None:
        try:
            close_rbmaster()
            dll = load_d2xx()
            devices = d2xx_devices(dll)
            if not devices:
                self.state.update_status("ERROR: USB ECU not found")
                return
            dev_info = devices[0]
            if dev_info["flags"] & 0x1:
                self.state.update_status("ERROR: USB busy")
                return
            self.device = D2xxDevice(dll, dev_info["serial"], dev_info["index"])
            self.device.configure()
            while not self.stop_event.is_set():
                self.device.set_timeouts(100, 0)
                self.device.write(STATUS_REQUEST)
                read_for(self.device, 0.05)
                self.device.set_timeouts(500, 0)
                self.device.write(RPM_REQUEST)
                data = read_for(self.device, 0.12)
                decoded = decode_frame(data)
                if decoded:
                    rpm, throttle, fuel_add = decoded
                    self.state.update_usb(rpm, throttle, fuel_add, data.hex(" ").upper())
                    snapshot = self.state.latest()
                    if snapshot.recording:
                        self.db_queue.put((time.time(), snapshot))
                time.sleep(0.12)
        except Exception as exc:
            self.state.update_status(f"ERROR: {exc}")
        finally:
            if self.device:
                self.device.close()


class Database:
    def __init__(self, path: Path):
        DATA_DIR.mkdir(exist_ok=True)
        self.path = path
        self._init()

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def _init(self) -> None:
        con = self.connect()
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                start_ts REAL NOT NULL,
                end_ts REAL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                ts REAL NOT NULL,
                rpm INTEGER NOT NULL,
                speed_kmh REAL,
                throttle_percent INTEGER NOT NULL,
                fuel_add INTEGER NOT NULL,
                gps_age REAL,
                accel_x_g REAL,
                accel_y_g REAL,
                accel_z_g REAL,
                total_g REAL,
                motion_age REAL,
                raw_hex TEXT NOT NULL
            )
            """
        )
        existing_columns = {row[1] for row in con.execute("PRAGMA table_info(samples)")}
        for column in ("accel_x_g", "accel_y_g", "accel_z_g", "total_g", "motion_age"):
            if column not in existing_columns:
                con.execute(f"ALTER TABLE samples ADD COLUMN {column} REAL")
        con.commit()
        con.close()

    def start_session(self, name: str) -> int:
        con = self.connect()
        cur = con.execute("INSERT INTO sessions (name, start_ts) VALUES (?, ?)", (name, time.time()))
        con.commit()
        session_id = int(cur.lastrowid)
        con.close()
        return session_id

    def stop_session(self, session_id: int) -> None:
        con = self.connect()
        con.execute("UPDATE sessions SET end_ts = ? WHERE id = ?", (time.time(), session_id))
        con.commit()
        con.close()


class DbWriter(threading.Thread):
    def __init__(self, db: Database, db_queue: queue.Queue[tuple[float, TelemetryState]], stop_event: threading.Event):
        super().__init__(daemon=True)
        self.db = db
        self.db_queue = db_queue
        self.stop_event = stop_event

    def run(self) -> None:
        con = self.db.connect()
        buffer: list[tuple[float, TelemetryState]] = []
        last_commit = time.monotonic()
        while not self.stop_event.is_set() or not self.db_queue.empty():
            try:
                buffer.append(self.db_queue.get(timeout=0.2))
            except queue.Empty:
                pass
            now = time.monotonic()
            if buffer and (len(buffer) >= 20 or now - last_commit >= 1.0):
                con.executemany(
                    """
                    INSERT INTO samples (
                        session_id, ts, rpm, speed_kmh, throttle_percent, fuel_add, gps_age,
                        accel_x_g, accel_y_g, accel_z_g, total_g, motion_age, raw_hex
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [self._row(ts, state) for ts, state in buffer if state.session_id is not None],
                )
                con.commit()
                buffer.clear()
                last_commit = now
        con.close()

    def _row(self, ts: float, state: TelemetryState) -> tuple[Any, ...]:
        gps_age = ts - state.gps.ts if state.gps.ts else None
        speed = state.gps.speed_kmh if gps_age is not None and gps_age < 5 else None
        motion_age = ts - state.motion.ts if state.motion.ts else None
        motion_live = motion_age is not None and motion_age < 2
        return (
            state.session_id,
            ts,
            state.rpm,
            speed,
            state.throttle_percent,
            state.fuel_add,
            gps_age,
            state.motion.accel_x_g if motion_live else None,
            state.motion.accel_y_g if motion_live else None,
            state.motion.accel_z_g if motion_live else None,
            state.motion.total_g if motion_live else None,
            motion_age,
            state.raw_hex,
        )


def read_file(name: str) -> bytes:
    path = (WEB_DIR / name).resolve()
    if not str(path).startswith(str(WEB_DIR.resolve())):
        raise FileNotFoundError(name)
    return path.read_bytes()


def make_handler(state: SharedState, db: Database) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, content_type: str = "text/plain; charset=utf-8") -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: Any) -> None:
            self._send(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            routes = {
                "/": "index.html",
                "/vehicle": "vehicle.html",
                "/phone": "phone.html",
                "/dashboard": "dashboard.html",
                "/logs": "logs.html",
            }
            if parsed.path in routes:
                self._send(200, read_file(routes[parsed.path]), "text/html; charset=utf-8")
                return
            if parsed.path == "/djy.css":
                self._send(200, read_file("djy.css"), "text/css; charset=utf-8")
                return
            if parsed.path == "/api/latest":
                self._json(latest_payload(state.latest()))
                return
            if parsed.path == "/api/logs":
                params = parse_qs(parsed.query)
                try:
                    limit = max(1, min(int(params.get("limit", ["1000"])[0]), 10000))
                    session_id = int(params["session_id"][0]) if "session_id" in params else None
                except (TypeError, ValueError):
                    self._send(400, b"invalid query")
                    return
                self._json(logs_payload(db, limit, session_id))
                return
            if parsed.path == "/api/sessions":
                self._json(sessions_payload(db))
                return
            self._send(404, b"not found")

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length) if length else b"{}"
            try:
                payload = json.loads(body.decode("utf-8")) if body else {}
            except json.JSONDecodeError:
                payload = {}
            if parsed.path == "/gps":
                state.update_gps(payload)
                self._json({"ok": True})
                return
            if parsed.path == "/motion":
                state.update_motion(payload)
                self._json({"ok": True})
                return
            if parsed.path == "/api/start":
                current = state.latest()
                if current.recording:
                    self._json({"ok": True, "session_id": current.session_id, "already": True})
                    return
                name = str(payload.get("name") or time.strftime("Run %Y-%m-%d %H:%M:%S"))
                session_id = db.start_session(name)
                state.set_recording(True, session_id, name)
                self._json({"ok": True, "session_id": session_id})
                return
            if parsed.path == "/api/stop":
                current = state.latest()
                if current.session_id is not None:
                    db.stop_session(current.session_id)
                state.set_recording(False, None, "")
                self._json({"ok": True})
                return
            self._send(404, b"not found")

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    return Handler


def latest_payload(s: TelemetryState) -> dict[str, Any]:
    gps_age = time.time() - s.gps.ts if s.gps.ts else None
    speed = s.gps.speed_kmh if gps_age is not None and gps_age < 5 else None
    motion_age = time.time() - s.motion.ts if s.motion.ts else None
    motion_live = motion_age is not None and motion_age < 2
    return {
        "rpm": s.rpm,
        "speed_kmh": speed,
        "throttle_percent": s.throttle_percent,
        "fuel_add": s.fuel_add,
        "accel_x_g": s.motion.accel_x_g if motion_live else None,
        "accel_y_g": s.motion.accel_y_g if motion_live else None,
        "accel_z_g": s.motion.accel_z_g if motion_live else None,
        "total_g": s.motion.total_g if motion_live else None,
        "motion_age": motion_age,
        "recording": s.recording,
        "session_id": s.session_id,
        "session_name": s.session_name,
        "status": (
            f"{s.usb_status} | GPS {'--' if gps_age is None else f'{gps_age:.1f}s'}"
            f" | IMU {'--' if motion_age is None else f'{motion_age:.1f}s'}"
        ),
    }


def sessions_payload(db: Database) -> dict[str, Any]:
    con = db.connect()
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT
            sessions.id,
            sessions.name,
            sessions.start_ts,
            sessions.end_ts,
            COUNT(samples.id) AS samples,
            MAX(samples.rpm) AS max_rpm,
            MAX(samples.speed_kmh) AS max_speed,
            MAX(samples.throttle_percent) AS max_throttle
        FROM sessions
        LEFT JOIN samples ON samples.session_id = sessions.id
        GROUP BY sessions.id
        ORDER BY sessions.id DESC
        LIMIT 100
        """
    ).fetchall()
    con.close()
    return {"sessions": [dict(row) for row in rows]}


def logs_payload(db: Database, limit: int, session_id: int | None = None) -> dict[str, Any]:
    con = db.connect()
    con.row_factory = sqlite3.Row
    where = "WHERE samples.session_id = ?" if session_id is not None else ""
    args: tuple[Any, ...] = (session_id,) if session_id is not None else ()
    summary = con.execute(
        f"""
        SELECT
            COUNT(*) AS samples,
            MAX(rpm) AS max_rpm,
            AVG(rpm) AS avg_rpm,
            MAX(speed_kmh) AS max_speed,
            AVG(speed_kmh) AS avg_speed,
            MAX(throttle_percent) AS max_throttle,
            AVG(fuel_add) AS avg_fuel,
            MIN(ts) AS first_ts,
            MAX(ts) AS last_ts
        FROM samples
        {where}
        """,
        args,
    ).fetchone()
    rows = con.execute(
        f"""
        SELECT samples.ts, samples.session_id, sessions.name AS session_name,
               rpm, speed_kmh, throttle_percent, fuel_add, gps_age,
               accel_x_g, accel_y_g, accel_z_g, total_g, motion_age
        FROM samples
        LEFT JOIN sessions ON sessions.id = samples.session_id
        {where}
        ORDER BY samples.id DESC
        LIMIT ?
        """,
        (*args, limit),
    ).fetchall()
    con.close()
    return {
        "summary": dict(summary),
        "rows": [dict(row) for row in rows],
    }


def main() -> int:
    db = Database(DB_PATH)
    state = SharedState()
    db_queue: queue.Queue[tuple[float, TelemetryState]] = queue.Queue()
    stop_event = threading.Event()
    usb = UsbWorker(state, db_queue)
    writer = DbWriter(db, db_queue, stop_event)
    usb.start()
    writer.start()
    server = ThreadingHTTPServer(("0.0.0.0", 8765), make_handler(state, db))
    print("DJY Baja app: http://127.0.0.1:8765/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        usb.stop_event.set()
        stop_event.set()
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
