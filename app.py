#!/usr/bin/env python3
"""DJY Baja telemetry app."""

from __future__ import annotations

import ctypes
import json
import math
import queue
import re
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "djy_baja.sqlite3"
SPEEDHIVE_API_URL = "https://lt-api.speedhive.com/api/events/{event_id}/active"
SPEEDHIVE_ORIGIN = "https://speedhive.mylaps.com"
DEFAULT_SPEEDHIVE_EVENT_ID = "OHJPNRVR-2147485793"
SPEEDHIVE_EVENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{2,127}$")
SPEEDHIVE_FLAG_NAMES = {
    0: "GREEN",
    1: "YELLOW",
    2: "RED",
    3: "FINISH",
    4: "STOP",
    5: "PURPLE",
    6: "UNKNOWN",
}

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
    gps_b: GpsState = field(default_factory=GpsState)
    motion: MotionState = field(default_factory=MotionState)
    recording: bool = False
    session_id: int | None = None
    session_name: str = ""


@dataclass
class LapGate:
    lat: float
    lon: float
    heading: float
    half_width_m: float = 25.0
    min_lap_s: float = 20.0
    updated_ts: float = field(default_factory=time.time)


@dataclass
class LapVehicleState:
    gps: GpsState = field(default_factory=GpsState)
    raw_gps: GpsState = field(default_factory=GpsState)
    previous_gps: GpsState | None = None
    received_at: float = 0.0
    sample_hz: float | None = None
    stable_since: float | None = None
    last_heading: float | None = None
    position_locked: bool = False
    last_sample_accepted: bool = False
    rejected_samples: int = 0
    last_crossing_at: float | None = None
    lap_started_at: float | None = None
    lap_count: int = 0
    last_lap_s: float | None = None
    previous_lap_s: float | None = None
    best_lap_s: float | None = None
    timing_status: str = "WAITING FOR GPS"
    recent_laps: list[dict[str, Any]] = field(default_factory=list)
    run_id: str = field(default_factory=lambda: str(time.time_ns()))


class SharedState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.state = TelemetryState()

    def latest(self) -> TelemetryState:
        with self.lock:
            gps = GpsState(**self.state.gps.__dict__)
            gps_b = GpsState(**self.state.gps_b.__dict__)
            motion = MotionState(**self.state.motion.__dict__)
            return TelemetryState(
                rpm=self.state.rpm,
                throttle_percent=self.state.throttle_percent,
                fuel_add=self.state.fuel_add,
                raw_hex=self.state.raw_hex,
                usb_status=self.state.usb_status,
                usb_ts=self.state.usb_ts,
                gps=gps,
                gps_b=gps_b,
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

    def update_gps(self, payload: dict[str, Any], vehicle_id: str = "A") -> None:
        gps = GpsState(
            ts=float(payload.get("ts") or time.time()),
            speed_kmh=_float_or_none(payload.get("speed_kmh")),
            lat=_float_or_none(payload.get("lat")),
            lon=_float_or_none(payload.get("lon")),
            accuracy_m=_float_or_none(payload.get("accuracy_m")),
            heading=_float_or_none(payload.get("heading")),
            altitude=_float_or_none(payload.get("altitude")),
        )
        with self.lock:
            if vehicle_id == "B":
                self.state.gps_b = gps
            else:
                self.state.gps = gps

    def update_motion(self, payload: dict[str, Any]) -> None:
        with self.lock:
            self.state.motion = MotionState(
                ts=float(payload.get("ts") or time.time()),
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


class SpeedhiveError(RuntimeError):
    """Raised when live timing data cannot be retrieved."""


class SpeedhiveClient:
    def __init__(self, min_refresh_seconds: float = 1.5) -> None:
        self.min_refresh_seconds = min_refresh_seconds
        self.lock = threading.Lock()
        self.cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def active(self, event_id: str) -> dict[str, Any]:
        event_id = validate_speedhive_event_id(event_id)
        now = time.monotonic()
        with self.lock:
            cached = self.cache.get(event_id)
            if cached and now - cached[0] < self.min_refresh_seconds:
                return cached[1]
            try:
                payload = self._fetch(event_id)
                if not isinstance(payload, dict):
                    raise TypeError("unexpected Speedhive response")
                result = normalize_speedhive_payload(event_id, payload)
                self.cache[event_id] = (time.monotonic(), result)
                return result
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError, TypeError) as exc:
                if cached:
                    stale = dict(cached[1])
                    stale["stale"] = True
                    stale["error"] = str(exc)
                    return stale
                raise SpeedhiveError(str(exc)) from exc

    def _fetch(self, event_id: str) -> dict[str, Any]:
        request = Request(
            SPEEDHIVE_API_URL.format(event_id=event_id),
            headers={
                "Accept": "application/json",
                "Origin": SPEEDHIVE_ORIGIN,
                "Referer": f"{SPEEDHIVE_ORIGIN}/",
                "User-Agent": "DJY-Baja-Telemetry/1.0",
            },
        )
        with urlopen(request, timeout=8) as response:
            return json.loads(response.read().decode("utf-8"))


def validate_speedhive_event_id(event_id: str) -> str:
    event_id = event_id.strip()
    if not SPEEDHIVE_EVENT_ID_RE.fullmatch(event_id):
        raise ValueError("invalid Speedhive event id")
    return event_id


def normalize_speedhive_payload(event_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    flag = payload.get("f")
    competitors = []
    for row in payload.get("l") or []:
        competitors.append(
            {
                "competitor_id": row.get("id"),
                "position": row.get("pos", row.get("lbpos")),
                "class_position": row.get("pCl"),
                "number": row.get("dNo") or row.get("no"),
                "name": row.get("nam") or "",
                "class_name": row.get("cln") or row.get("cl") or "",
                "laps": row.get("ls"),
                "last_lap": row.get("lsTm"),
                "best_lap": row.get("btTm"),
                "average_lap": row.get("avTm"),
                "total_time": row.get("tTm"),
                "gap": row.get("gp"),
                "difference": row.get("df"),
                "class_gap": row.get("gpCl"),
                "in_pit": bool(row.get("if")),
                "best_overall": bool(row.get("ibt")),
                "best_in_class": bool(row.get("btCl")),
            }
        )
    return {
        "event_id": event_id,
        "session_id": payload.get("id"),
        "event_name": payload.get("eNam") or "",
        "session_name": payload.get("rnNam") or "",
        "group_name": payload.get("gNam") or "",
        "start_time": payload.get("stod"),
        "race_time": payload.get("rcTm"),
        "time_to_go": payload.get("tmTg"),
        "lap_count": payload.get("ls"),
        "best_lap": payload.get("btLpTim"),
        "flag": flag,
        "flag_name": SPEEDHIVE_FLAG_NAMES.get(flag, "UNKNOWN"),
        "competitors": competitors,
        "fetched_at": time.time(),
        "stale": False,
    }


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
                raw_hex TEXT NOT NULL,
                gps_a_ts REAL,
                gps_a_speed_kmh REAL,
                gps_a_lat REAL,
                gps_a_lon REAL,
                gps_a_accuracy_m REAL,
                gps_a_heading REAL,
                gps_a_altitude REAL,
                gps_a_age REAL,
                gps_b_ts REAL,
                gps_b_speed_kmh REAL,
                gps_b_lat REAL,
                gps_b_lon REAL,
                gps_b_accuracy_m REAL,
                gps_b_heading REAL,
                gps_b_altitude REAL,
                gps_b_age REAL,
                usb_ts REAL,
                motion_ts REAL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS lap_gate (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                heading REAL NOT NULL,
                half_width_m REAL NOT NULL,
                min_lap_s REAL NOT NULL,
                updated_ts REAL NOT NULL
            )
            """
        )
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS lap_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                vehicle_id TEXT NOT NULL,
                lap_number INTEGER NOT NULL,
                start_ts REAL NOT NULL,
                end_ts REAL NOT NULL,
                lap_time_s REAL NOT NULL,
                recorded_ts REAL NOT NULL
            )
            """
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_lap_records_vehicle ON lap_records(vehicle_id, id DESC)")
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS gps_points (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                vehicle_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                lap_number INTEGER NOT NULL,
                ts REAL NOT NULL,
                received_ts REAL NOT NULL,
                lat REAL NOT NULL,
                lon REAL NOT NULL,
                speed_kmh REAL,
                accuracy_m REAL,
                heading REAL,
                altitude REAL,
                filtered_lat REAL,
                filtered_lon REAL,
                accepted INTEGER NOT NULL,
                position_locked INTEGER NOT NULL,
                timing_status TEXT NOT NULL,
                raw_json TEXT NOT NULL
            )
            """
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_gps_points_session ON gps_points(session_id, vehicle_id, id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_gps_points_lap ON gps_points(session_id, vehicle_id, lap_number, id)")
        existing_columns = {row[1] for row in con.execute("PRAGMA table_info(samples)")}
        for column in (
            "accel_x_g", "accel_y_g", "accel_z_g", "total_g", "motion_age",
            "gps_a_ts", "gps_a_speed_kmh", "gps_a_lat", "gps_a_lon", "gps_a_accuracy_m",
            "gps_a_heading", "gps_a_altitude", "gps_a_age",
            "gps_b_ts", "gps_b_speed_kmh", "gps_b_lat", "gps_b_lon", "gps_b_accuracy_m",
            "gps_b_heading", "gps_b_altitude", "gps_b_age", "usb_ts", "motion_ts",
        ):
            if column not in existing_columns:
                con.execute(f"ALTER TABLE samples ADD COLUMN {column} REAL")
        con.commit()
        con.close()

    def record_gps_point(self, point: dict[str, Any]) -> None:
        con = self.connect()
        con.execute(
            """
            INSERT INTO gps_points (
                session_id, vehicle_id, run_id, lap_number, ts, received_ts,
                lat, lon, speed_kmh, accuracy_m, heading, altitude,
                filtered_lat, filtered_lon, accepted, position_locked, timing_status, raw_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                point["session_id"], point["vehicle_id"], point["run_id"], point["lap_number"],
                point["ts"], point["received_ts"], point["lat"], point["lon"], point.get("speed_kmh"),
                point.get("accuracy_m"), point.get("heading"), point.get("altitude"),
                point.get("filtered_lat"), point.get("filtered_lon"), int(bool(point.get("accepted"))),
                int(bool(point.get("position_locked"))), point.get("timing_status") or "", point["raw_json"],
            ),
        )
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

    def load_lap_gate(self) -> LapGate | None:
        con = self.connect()
        row = con.execute(
            "SELECT lat, lon, heading, half_width_m, min_lap_s, updated_ts FROM lap_gate WHERE id = 1"
        ).fetchone()
        con.close()
        return LapGate(*row) if row else None

    def save_lap_gate(self, gate: LapGate) -> None:
        con = self.connect()
        con.execute(
            """
            INSERT INTO lap_gate (id, lat, lon, heading, half_width_m, min_lap_s, updated_ts)
            VALUES (1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                lat=excluded.lat, lon=excluded.lon, heading=excluded.heading,
                half_width_m=excluded.half_width_m, min_lap_s=excluded.min_lap_s,
                updated_ts=excluded.updated_ts
            """,
            (gate.lat, gate.lon, gate.heading, gate.half_width_m, gate.min_lap_s, gate.updated_ts),
        )
        con.commit()
        con.close()

    def record_lap(
        self,
        run_id: str,
        vehicle_id: str,
        lap_number: int,
        start_ts: float,
        end_ts: float,
        lap_time_s: float,
    ) -> None:
        con = self.connect()
        con.execute(
            """
            INSERT INTO lap_records (run_id, vehicle_id, lap_number, start_ts, end_ts, lap_time_s, recorded_ts)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, vehicle_id, lap_number, start_ts, end_ts, lap_time_s, time.time()),
        )
        con.commit()
        con.close()


def validate_lap_vehicle_id(value: Any) -> str:
    vehicle_id = str(value or "A").strip().upper()
    if vehicle_id not in {"A", "B"}:
        raise ValueError("vehicle_id must be A or B")
    return vehicle_id


def gps_from_payload(payload: dict[str, Any]) -> GpsState:
    gps = GpsState(
        ts=float(payload.get("ts") or time.time()),
        speed_kmh=_float_or_none(payload.get("speed_kmh")),
        lat=_float_or_none(payload.get("lat")),
        lon=_float_or_none(payload.get("lon")),
        accuracy_m=_float_or_none(payload.get("accuracy_m")),
        heading=_float_or_none(payload.get("heading")),
        altitude=_float_or_none(payload.get("altitude")),
    )
    if gps.lat is None or gps.lon is None or not (-90 <= gps.lat <= 90) or not (-180 <= gps.lon <= 180):
        raise ValueError("invalid GPS coordinates")
    return gps


def gps_bearing_degrees(start: GpsState, end: GpsState) -> float | None:
    if start.lat is None or start.lon is None or end.lat is None or end.lon is None:
        return None
    lat1 = math.radians(start.lat)
    lat2 = math.radians(end.lat)
    lon_delta = math.radians(end.lon - start.lon)
    y = math.sin(lon_delta) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lon_delta)
    if abs(x) < 1e-12 and abs(y) < 1e-12:
        return None
    return math.degrees(math.atan2(y, x)) % 360


def gps_distance_m(start: GpsState, end: GpsState) -> float:
    if start.lat is None or start.lon is None or end.lat is None or end.lon is None:
        return 0.0
    earth_radius_m = 6_371_000.0
    lat1 = math.radians(start.lat)
    lat2 = math.radians(end.lat)
    delta_lat = lat2 - lat1
    delta_lon = math.radians(end.lon - start.lon)
    haversine = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    return earth_radius_m * 2 * math.atan2(math.sqrt(haversine), math.sqrt(max(0.0, 1.0 - haversine)))


def filter_gps_sample(previous: GpsState | None, raw: GpsState) -> tuple[GpsState | None, bool, str | None]:
    """Reject teleports, lock stationary jitter, and smooth only at low speed."""
    if raw.accuracy_m is not None and raw.accuracy_m > 50.0:
        return None, False, "GPS ACCURACY REJECTED"
    if previous is None or previous.lat is None or previous.lon is None or raw.ts <= previous.ts:
        return raw, False, None

    interval_s = raw.ts - previous.ts
    distance_m = gps_distance_m(previous, raw)
    derived_speed_kmh = distance_m / interval_s * 3.6
    reported_speed_kmh = raw.speed_kmh
    accuracy_m = max(3.0, raw.accuracy_m or 0.0, previous.accuracy_m or 0.0)

    if interval_s <= 5.0 and derived_speed_kmh > 220.0:
        return None, False, "GPS JUMP REJECTED"
    if (
        interval_s <= 5.0
        and reported_speed_kmh is not None
        and reported_speed_kmh < 2.0
        and distance_m > max(15.0, accuracy_m * 2.0)
    ):
        return None, False, "GPS JUMP REJECTED"

    lock_radius_m = max(2.5, min(12.0, accuracy_m * 0.7))
    stationary_hint = reported_speed_kmh is None or reported_speed_kmh < 2.0
    if stationary_hint and distance_m <= lock_radius_m:
        filtered = GpsState(**raw.__dict__)
        filtered.lat = previous.lat
        filtered.lon = previous.lon
        return filtered, True, None

    motion_speed_kmh = reported_speed_kmh if reported_speed_kmh is not None else derived_speed_kmh
    if motion_speed_kmh >= 20.0:
        alpha = 1.0
    elif motion_speed_kmh >= 5.0:
        alpha = 0.65
    else:
        alpha = 0.35
    filtered = GpsState(**raw.__dict__)
    filtered.lat = previous.lat + (float(raw.lat) - previous.lat) * alpha
    filtered.lon = previous.lon + (float(raw.lon) - previous.lon) * alpha
    return filtered, False, None


def gate_coordinates(gate: LapGate, gps: GpsState) -> tuple[float, float]:
    earth_radius_m = 6_371_000.0
    north = math.radians(float(gps.lat) - gate.lat) * earth_radius_m
    east = math.radians(float(gps.lon) - gate.lon) * earth_radius_m * math.cos(math.radians(gate.lat))
    heading = math.radians(gate.heading)
    forward = east * math.sin(heading) + north * math.cos(heading)
    lateral = east * -math.cos(heading) + north * math.sin(heading)
    return forward, lateral


class LapTimingManager:
    def __init__(self, db: Database):
        self.db = db
        self.lock = threading.RLock()
        self.gate = db.load_lap_gate()
        self.vehicles = {"A": LapVehicleState(), "B": LapVehicleState()}

    def update_gps(self, vehicle_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        vehicle_id = validate_lap_vehicle_id(vehicle_id)
        raw_gps = gps_from_payload(payload)
        record: tuple[Any, ...] | None = None
        with self.lock:
            vehicle = self.vehicles[vehicle_id]
            previous_raw = vehicle.raw_gps if vehicle.raw_gps.lat is not None and vehicle.raw_gps.lon is not None else None
            previous = vehicle.gps if vehicle.gps.lat is not None and vehicle.gps.lon is not None else None
            if previous_raw is not None:
                sample_interval = raw_gps.ts - previous_raw.ts
                if 0.02 <= sample_interval <= 10.0:
                    instant_hz = 1.0 / sample_interval
                    vehicle.sample_hz = instant_hz if vehicle.sample_hz is None else (vehicle.sample_hz * 0.8 + instant_hz * 0.2)
            vehicle.raw_gps = raw_gps
            vehicle.received_at = time.time()
            gps, position_locked, rejection_reason = filter_gps_sample(previous, raw_gps)
            if gps is None:
                vehicle.last_sample_accepted = False
                vehicle.rejected_samples += 1
                vehicle.stable_since = None
                vehicle.position_locked = False
                vehicle.timing_status = rejection_reason or "GPS SAMPLE REJECTED"
                return self.vehicle_payload(vehicle_id)
            if gps.accuracy_m is not None and gps.accuracy_m <= 15.0:
                if vehicle.stable_since is None:
                    vehicle.stable_since = vehicle.received_at
            else:
                vehicle.stable_since = None
            heading = gps.heading
            if heading is not None and math.isfinite(heading):
                vehicle.last_heading = heading % 360
            elif previous is not None:
                distance_m = gps_distance_m(previous, gps)
                interval_s = gps.ts - previous.ts
                derived_speed_kmh = distance_m / interval_s * 3.6 if interval_s > 0 else 0.0
                motion_speed_kmh = gps.speed_kmh if gps.speed_kmh is not None else derived_speed_kmh
                if distance_m >= 0.75 and motion_speed_kmh >= 3.0:
                    derived_heading = gps_bearing_degrees(previous, gps)
                    if derived_heading is not None:
                        vehicle.last_heading = derived_heading
            vehicle.previous_gps = previous
            vehicle.gps = gps
            vehicle.position_locked = position_locked
            vehicle.last_sample_accepted = True
            if self.gate is None:
                vehicle.timing_status = "SET START LINE"
            elif previous is None or gps.ts <= previous.ts:
                vehicle.timing_status = "WAITING FOR MOVEMENT"
            elif (gps.accuracy_m or 999) > 50 or (previous.accuracy_m or 999) > 50:
                vehicle.timing_status = "LOW GPS ACCURACY"
            else:
                record = self._detect_crossing(vehicle_id, vehicle, previous, gps)
        if record is not None:
            self.db.record_lap(*record)
        return self.vehicle_payload(vehicle_id)

    def _detect_crossing(
        self,
        vehicle_id: str,
        vehicle: LapVehicleState,
        previous: GpsState,
        current: GpsState,
    ) -> tuple[Any, ...] | None:
        gate = self.gate
        if gate is None:
            return None
        previous_forward, previous_lateral = gate_coordinates(gate, previous)
        current_forward, current_lateral = gate_coordinates(gate, current)
        if not (previous_forward < 0 <= current_forward):
            if vehicle.lap_started_at is not None:
                vehicle.timing_status = "TIMING"
            return None
        distance = current_forward - previous_forward
        if distance <= 0:
            return None
        fraction = max(0.0, min(1.0, -previous_forward / distance))
        lateral = previous_lateral + (current_lateral - previous_lateral) * fraction
        if abs(lateral) > gate.half_width_m:
            return None
        crossing_ts = previous.ts + (current.ts - previous.ts) * fraction
        if vehicle.last_crossing_at is not None and crossing_ts - vehicle.last_crossing_at < 4.0:
            vehicle.timing_status = "CROSSING COOLDOWN"
            return None
        vehicle.last_crossing_at = crossing_ts
        if vehicle.lap_started_at is None:
            vehicle.lap_started_at = crossing_ts
            vehicle.timing_status = "LAP ARMED"
            return None
        lap_time_s = crossing_ts - vehicle.lap_started_at
        if lap_time_s < gate.min_lap_s:
            vehicle.timing_status = "CROSSING IGNORED"
            return None
        vehicle.lap_count += 1
        vehicle.previous_lap_s = vehicle.last_lap_s
        vehicle.last_lap_s = lap_time_s
        vehicle.best_lap_s = lap_time_s if vehicle.best_lap_s is None else min(vehicle.best_lap_s, lap_time_s)
        lap = {
            "lap_number": vehicle.lap_count,
            "lap_time_s": lap_time_s,
            "end_ts": crossing_ts,
        }
        vehicle.recent_laps.insert(0, lap)
        del vehicle.recent_laps[10:]
        start_ts = vehicle.lap_started_at
        vehicle.lap_started_at = crossing_ts
        vehicle.timing_status = "LAP COMPLETE"
        return (vehicle.run_id, vehicle_id, vehicle.lap_count, start_ts, crossing_ts, lap_time_s)

    def set_gate_from_vehicle(
        self,
        vehicle_id: str,
        half_width_m: float | None = None,
        min_lap_s: float | None = None,
    ) -> dict[str, Any]:
        vehicle_id = validate_lap_vehicle_id(vehicle_id)
        with self.lock:
            vehicle = self.vehicles[vehicle_id]
            if not vehicle.received_at or time.time() - vehicle.received_at > 10:
                raise ValueError(f"vehicle {vehicle_id} GPS is not live")
            heading = vehicle.gps.heading
            if heading is None and vehicle.previous_gps is not None:
                heading = gps_bearing_degrees(vehicle.previous_gps, vehicle.gps)
            if heading is None:
                raise ValueError("vehicle must be moving to determine the start-line direction")
            width = half_width_m if half_width_m is not None else (self.gate.half_width_m if self.gate else 25.0)
            minimum = min_lap_s if min_lap_s is not None else (self.gate.min_lap_s if self.gate else 20.0)
            if not 5 <= width <= 100:
                raise ValueError("half_width_m must be between 5 and 100")
            if not 5 <= minimum <= 3600:
                raise ValueError("min_lap_s must be between 5 and 3600")
            self.gate = LapGate(
                lat=float(vehicle.gps.lat),
                lon=float(vehicle.gps.lon),
                heading=float(heading) % 360,
                half_width_m=width,
                min_lap_s=minimum,
            )
            self._reset_locked()
            selected = self.vehicles[vehicle_id]
            selected.lap_started_at = selected.gps.ts
            selected.timing_status = "TIMING"
            self.db.save_lap_gate(self.gate)
            return self.payload()

    def reset(self, vehicle_id: str | None = None) -> dict[str, Any]:
        with self.lock:
            self._reset_locked(validate_lap_vehicle_id(vehicle_id) if vehicle_id else None)
            return self.payload()

    def _reset_locked(self, vehicle_id: str | None = None) -> None:
        targets = (vehicle_id,) if vehicle_id else ("A", "B")
        for target in targets:
            old = self.vehicles[target]
            self.vehicles[target] = LapVehicleState(
                gps=old.gps,
                raw_gps=old.raw_gps,
                previous_gps=old.previous_gps,
                received_at=old.received_at,
                sample_hz=old.sample_hz,
                stable_since=old.stable_since,
                last_heading=old.last_heading,
                position_locked=old.position_locked,
                last_sample_accepted=old.last_sample_accepted,
                rejected_samples=old.rejected_samples,
                timing_status="WAITING FOR FIRST CROSSING" if self.gate else "SET START LINE",
            )

    def vehicle_payload(self, vehicle_id: str, now: float | None = None) -> dict[str, Any]:
        vehicle_id = validate_lap_vehicle_id(vehicle_id)
        now = time.time() if now is None else now
        with self.lock:
            vehicle = self.vehicles[vehicle_id]
            gps_age = now - vehicle.received_at if vehicle.received_at else None
            gps_stable_s = max(0.0, now - vehicle.stable_since) if vehicle.stable_since is not None else 0.0
            gps_live = gps_age is not None and gps_age < 5
            current_lap_s = None
            if vehicle.lap_started_at is not None and vehicle.gps.ts >= vehicle.lap_started_at:
                current_lap_s = vehicle.gps.ts - vehicle.lap_started_at
            delta_s = None
            if vehicle.last_lap_s is not None and vehicle.previous_lap_s is not None:
                delta_s = vehicle.last_lap_s - vehicle.previous_lap_s
            return {
                "vehicle_id": vehicle_id,
                "gps_live": gps_live,
                "gps_age": gps_age,
                "sample_hz": vehicle.sample_hz,
                "gps_stable_s": gps_stable_s,
                "gps_ready": gps_live and gps_stable_s >= 15.0,
                "position_locked": vehicle.position_locked,
                "last_sample_accepted": vehicle.last_sample_accepted,
                "rejected_samples": vehicle.rejected_samples,
                "run_id": vehicle.run_id,
                "raw_lat": vehicle.raw_gps.lat,
                "raw_lon": vehicle.raw_gps.lon,
                "filtered_lat": vehicle.gps.lat,
                "filtered_lon": vehicle.gps.lon,
                "lat": vehicle.gps.lat,
                "lon": vehicle.gps.lon,
                "accuracy_m": vehicle.gps.accuracy_m,
                "speed_kmh": vehicle.gps.speed_kmh,
                "heading": vehicle.last_heading,
                "timing_status": vehicle.timing_status,
                "lap_started_at": vehicle.lap_started_at,
                "current_lap_s": current_lap_s,
                "lap_count": vehicle.lap_count,
                "lap_number": vehicle.lap_count + (1 if vehicle.lap_started_at is not None else 0),
                "last_lap_s": vehicle.last_lap_s,
                "previous_lap_s": vehicle.previous_lap_s,
                "delta_s": delta_s,
                "best_lap_s": vehicle.best_lap_s,
                "recent_laps": list(vehicle.recent_laps),
            }

    def payload(self) -> dict[str, Any]:
        now = time.time()
        with self.lock:
            gate = None
            if self.gate is not None:
                gate = {
                    "lat": self.gate.lat,
                    "lon": self.gate.lon,
                    "heading": self.gate.heading,
                    "half_width_m": self.gate.half_width_m,
                    "min_lap_s": self.gate.min_lap_s,
                    "updated_ts": self.gate.updated_ts,
                }
            return {
                "server_time": now,
                "gate": gate,
                "vehicles": {key: self.vehicle_payload(key, now) for key in ("A", "B")},
            }


class PitCommandManager:
    VALID_COMMANDS = {"GREEN", "YELLOW", "RED", "STOP", "CHECKERED", "BOX"}
    VALID_TARGETS = {"ALL", "A", "B"}

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.version = 0
        self.commands: dict[str, dict[str, Any]] = {}

    def set(self, command: Any, target: Any) -> dict[str, Any]:
        command_name = str(command or "").strip().upper()
        target_name = str(target or "ALL").strip().upper()
        if target_name not in self.VALID_TARGETS:
            raise ValueError("target must be ALL, A or B")
        with self.lock:
            self.version += 1
            if command_name == "CLEAR":
                if target_name == "ALL":
                    self.commands.clear()
                else:
                    self.commands.pop(target_name, None)
            elif command_name in self.VALID_COMMANDS:
                self.commands[target_name] = {
                    "version": self.version,
                    "command": command_name,
                    "target": target_name,
                    "created_ts": time.time(),
                }
            else:
                raise ValueError("command must be GREEN, YELLOW, RED, STOP, CHECKERED, BOX or CLEAR")
        return self.payload()

    def payload(self, audience: str | None = None) -> dict[str, Any]:
        if audience is not None:
            audience = audience.strip().upper()
            if audience not in self.VALID_TARGETS:
                raise ValueError("vehicle_id must be ALL, A or B")
        with self.lock:
            if audience is None:
                commands = list(self.commands.values())
            elif audience == "ALL":
                commands = [self.commands["ALL"]] if "ALL" in self.commands else []
            else:
                commands = [self.commands[key] for key in ("ALL", audience) if key in self.commands]
            return {
                "server_time": time.time(),
                "version": self.version,
                "commands": sorted((dict(item) for item in commands), key=lambda item: item["version"]),
            }


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
                        accel_x_g, accel_y_g, accel_z_g, total_g, motion_age, raw_hex,
                        gps_a_ts, gps_a_speed_kmh, gps_a_lat, gps_a_lon, gps_a_accuracy_m,
                        gps_a_heading, gps_a_altitude, gps_a_age,
                        gps_b_ts, gps_b_speed_kmh, gps_b_lat, gps_b_lon, gps_b_accuracy_m,
                        gps_b_heading, gps_b_altitude, gps_b_age, usb_ts, motion_ts
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        gps_b_age = ts - state.gps_b.ts if state.gps_b.ts else None
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
            state.gps.ts or None,
            state.gps.speed_kmh,
            state.gps.lat,
            state.gps.lon,
            state.gps.accuracy_m,
            state.gps.heading,
            state.gps.altitude,
            gps_age,
            state.gps_b.ts or None,
            state.gps_b.speed_kmh,
            state.gps_b.lat,
            state.gps_b.lon,
            state.gps_b.accuracy_m,
            state.gps_b.heading,
            state.gps_b.altitude,
            gps_b_age,
            state.usb_ts or None,
            state.motion.ts or None,
        )


def read_file(name: str) -> bytes:
    path = (WEB_DIR / name).resolve()
    if not str(path).startswith(str(WEB_DIR.resolve())):
        raise FileNotFoundError(name)
    return path.read_bytes()


def make_handler(
    state: SharedState,
    db: Database,
    speedhive: SpeedhiveClient,
    lap_timing: LapTimingManager,
    pit_commands: PitCommandManager,
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, content_type: str = "text/plain; charset=utf-8") -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, payload: Any, code: int = 200) -> None:
            self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            routes = {
                "/": "index.html",
                "/pit": "pit.html",
                "/vehicle": "vehicle.html",
                "/phone": "phone.html",
                "/dashboard": "dashboard.html",
                "/logs": "logs.html",
                "/timing": "timing.html",
                "/driver": "driver.html",
                "/lap-timing": "lap_timing.html",
                "/map": "map.html",
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
            if parsed.path == "/api/lap/status":
                self._json(lap_timing.payload())
                return
            if parsed.path == "/api/pit-command":
                params = parse_qs(parsed.query)
                audience = params.get("vehicle_id", [None])[0]
                try:
                    self._json(pit_commands.payload(audience))
                except ValueError as exc:
                    self._json({"error": str(exc)}, 400)
                return
            if parsed.path == "/api/speedhive/active":
                params = parse_qs(parsed.query)
                event_id = params.get("event_id", [DEFAULT_SPEEDHIVE_EVENT_ID])[0]
                try:
                    self._json(speedhive.active(event_id))
                except ValueError as exc:
                    self._json({"error": str(exc)}, 400)
                except SpeedhiveError as exc:
                    self._json({"error": f"Speedhive unavailable: {exc}"}, 502)
                return
            if parsed.path == "/api/logs":
                params = parse_qs(parsed.query)
                try:
                    limit = max(1, min(int(params.get("limit", ["1000"])[0]), 10000))
                    session_id = int(params["session_id"][0]) if "session_id" in params else None
                    vehicle_id = validate_lap_vehicle_id(params.get("vehicle_id", ["A"])[0])
                except (TypeError, ValueError):
                    self._send(400, b"invalid query")
                    return
                self._json(logs_payload(db, limit, session_id, vehicle_id))
                return
            if parsed.path == "/api/sessions":
                self._json(sessions_payload(db))
                return
            if parsed.path == "/api/laps":
                params = parse_qs(parsed.query)
                try:
                    session_id = int(params.get("session_id", [""])[0])
                    vehicle_id = validate_lap_vehicle_id(params.get("vehicle_id", ["A"])[0])
                except (TypeError, ValueError):
                    self._send(400, b"invalid lap query")
                    return
                self._json(laps_payload(db, session_id, vehicle_id))
                return
            if parsed.path == "/api/track":
                params = parse_qs(parsed.query)
                try:
                    session_id = int(params.get("session_id", [""])[0])
                    vehicle_id = params.get("vehicle_id", [None])[0]
                    if vehicle_id is not None:
                        vehicle_id = validate_lap_vehicle_id(vehicle_id)
                    lap_text = params.get("lap_number", [None])[0]
                    lap_number = int(lap_text) if lap_text not in (None, "") else None
                    if lap_number is not None and lap_number < 0:
                        raise ValueError("lap_number must be zero or greater")
                except (TypeError, ValueError):
                    self._send(400, b"invalid track query")
                    return
                self._json(track_payload(db, session_id, vehicle_id, lap_number))
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
            if parsed.path == "/api/pit-command":
                try:
                    self._json(pit_commands.set(payload.get("command"), payload.get("target")))
                except ValueError as exc:
                    self._json({"error": str(exc)}, 400)
                return
            if parsed.path == "/gps":
                try:
                    vehicle_id = validate_lap_vehicle_id(payload.get("vehicle_id"))
                    vehicle = lap_timing.update_gps(vehicle_id, payload)
                    state.update_gps(payload, vehicle_id)
                    snapshot = state.latest()
                    if snapshot.recording and snapshot.session_id is not None:
                        received_ts = time.time()
                        gps_ts = float(payload.get("ts") or received_ts)
                        db.record_gps_point(
                            {
                                "session_id": snapshot.session_id,
                                "vehicle_id": vehicle_id,
                                "run_id": vehicle["run_id"],
                                "lap_number": vehicle["lap_number"],
                                "ts": gps_ts,
                                "received_ts": received_ts,
                                "lat": float(payload["lat"]),
                                "lon": float(payload["lon"]),
                                "speed_kmh": _float_or_none(payload.get("speed_kmh")),
                                "accuracy_m": _float_or_none(payload.get("accuracy_m")),
                                "heading": _float_or_none(payload.get("heading")),
                                "altitude": _float_or_none(payload.get("altitude")),
                                "filtered_lat": vehicle.get("filtered_lat") if vehicle.get("last_sample_accepted") else None,
                                "filtered_lon": vehicle.get("filtered_lon") if vehicle.get("last_sample_accepted") else None,
                                "accepted": vehicle.get("last_sample_accepted"),
                                "position_locked": vehicle.get("position_locked"),
                                "timing_status": vehicle.get("timing_status"),
                                "raw_json": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                            }
                        )
                    self._json({"ok": True, "vehicle": vehicle})
                except ValueError as exc:
                    self._json({"error": str(exc)}, 400)
                return
            if parsed.path == "/motion":
                try:
                    if validate_lap_vehicle_id(payload.get("vehicle_id")) == "A":
                        state.update_motion(payload)
                    self._json({"ok": True})
                except ValueError as exc:
                    self._json({"error": str(exc)}, 400)
                return
            if parsed.path == "/api/lap/gate":
                try:
                    vehicle_id = validate_lap_vehicle_id(payload.get("vehicle_id"))
                    half_width = _float_or_none(payload.get("half_width_m"))
                    min_lap = _float_or_none(payload.get("min_lap_s"))
                    self._json(lap_timing.set_gate_from_vehicle(vehicle_id, half_width, min_lap))
                except ValueError as exc:
                    self._json({"error": str(exc)}, 400)
                return
            if parsed.path == "/api/lap/reset":
                try:
                    requested = payload.get("vehicle_id")
                    self._json(lap_timing.reset(str(requested) if requested else None))
                except ValueError as exc:
                    self._json({"error": str(exc)}, 400)
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
        "gps_lat": s.gps.lat if gps_age is not None and gps_age < 5 else None,
        "gps_lon": s.gps.lon if gps_age is not None and gps_age < 5 else None,
        "gps_accuracy_m": s.gps.accuracy_m if gps_age is not None and gps_age < 5 else None,
        "gps_heading": s.gps.heading if gps_age is not None and gps_age < 5 else None,
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


def track_payload(
    db: Database,
    session_id: int,
    vehicle_id: str | None = None,
    lap_number: int | None = None,
) -> dict[str, Any]:
    where = ["session_id = ?"]
    args: list[Any] = [session_id]
    if vehicle_id is not None:
        where.append("vehicle_id = ?")
        args.append(vehicle_id)
    if lap_number is not None:
        where.append("lap_number = ?")
        args.append(lap_number)
    con = db.connect()
    con.row_factory = sqlite3.Row
    session = con.execute(
        "SELECT id, name, start_ts, end_ts FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    rows = con.execute(
        f"""
        SELECT vehicle_id, run_id, lap_number, ts, received_ts,
               lat, lon, speed_kmh, accuracy_m, heading, altitude,
               filtered_lat, filtered_lon, accepted, position_locked, timing_status
        FROM gps_points
        WHERE {' AND '.join(where)}
        ORDER BY vehicle_id, id
        LIMIT 200000
        """,
        args,
    ).fetchall()
    con.close()
    tracks = {"A": [], "B": []}
    for row in rows:
        tracks[row["vehicle_id"]].append(dict(row))
    return {"session": dict(session) if session else None, "tracks": tracks}


def laps_payload(db: Database, session_id: int, vehicle_id: str) -> dict[str, Any]:
    vehicle_id = validate_lap_vehicle_id(vehicle_id)
    con = db.connect()
    con.row_factory = sqlite3.Row
    points = con.execute(
        """
        SELECT run_id, lap_number, ts, lat, lon, speed_kmh,
               filtered_lat, filtered_lon, accepted
        FROM gps_points
        WHERE session_id = ? AND vehicle_id = ?
        ORDER BY lap_number, id
        """,
        (session_id, vehicle_id),
    ).fetchall()
    lap_records = con.execute(
        """
        SELECT run_id, lap_number, start_ts, end_ts, lap_time_s
        FROM lap_records
        WHERE vehicle_id = ?
        ORDER BY id
        """,
        (vehicle_id,),
    ).fetchall()
    session = con.execute(
        "SELECT id, name, start_ts, end_ts FROM sessions WHERE id = ?", (session_id,)
    ).fetchone()
    con.close()

    recorded = {(row["run_id"], row["lap_number"]): dict(row) for row in lap_records}
    grouped: dict[int, dict[str, Any]] = {}
    previous_by_lap: dict[int, GpsState] = {}
    for point in points:
        lap_number = int(point["lap_number"])
        lap = grouped.setdefault(
            lap_number,
            {
                "lap_number": lap_number,
                "run_id": point["run_id"],
                "first_ts": point["ts"],
                "last_ts": point["ts"],
                "points": 0,
                "distance_m": 0.0,
                "max_speed_kmh": None,
                "avg_speed_kmh": None,
                "_speed_sum": 0.0,
                "_speed_count": 0,
            },
        )
        lap["points"] += 1
        lap["first_ts"] = min(lap["first_ts"], point["ts"])
        lap["last_ts"] = max(lap["last_ts"], point["ts"])
        speed = point["speed_kmh"]
        if speed is not None:
            lap["max_speed_kmh"] = speed if lap["max_speed_kmh"] is None else max(lap["max_speed_kmh"], speed)
            lap["_speed_sum"] += speed
            lap["_speed_count"] += 1
        if not point["accepted"]:
            continue
        current = GpsState(
            lat=point["filtered_lat"] if point["filtered_lat"] is not None else point["lat"],
            lon=point["filtered_lon"] if point["filtered_lon"] is not None else point["lon"],
        )
        previous = previous_by_lap.get(lap_number)
        if previous is not None:
            lap["distance_m"] += gps_distance_m(previous, current)
        previous_by_lap[lap_number] = current

    result = []
    for lap_number in sorted(grouped):
        lap = grouped[lap_number]
        lap["avg_speed_kmh"] = (
            lap["_speed_sum"] / lap["_speed_count"] if lap["_speed_count"] else None
        )
        record = recorded.get((lap["run_id"], lap_number))
        lap["lap_time_s"] = record["lap_time_s"] if record else None
        lap["start_ts"] = record["start_ts"] if record else lap["first_ts"]
        lap["end_ts"] = record["end_ts"] if record else lap["last_ts"]
        lap.pop("_speed_sum")
        lap.pop("_speed_count")
        result.append(lap)
    return {
        "session": dict(session) if session else None,
        "vehicle_id": vehicle_id,
        "laps": result,
    }


def logs_payload(
    db: Database,
    limit: int,
    session_id: int | None = None,
    vehicle_id: str = "A",
) -> dict[str, Any]:
    vehicle_id = validate_lap_vehicle_id(vehicle_id)
    gps_prefix = "gps_a" if vehicle_id == "A" else "gps_b"
    con = db.connect()
    con.row_factory = sqlite3.Row
    where = "WHERE samples.session_id = ?" if session_id is not None else ""
    args: tuple[Any, ...] = (session_id,) if session_id is not None else ()
    summary = con.execute(
        f"""
        SELECT
            COUNT(*) AS samples,
            MAX(CASE WHEN ? = 'A' THEN rpm END) AS max_rpm,
            AVG(CASE WHEN ? = 'A' THEN rpm END) AS avg_rpm,
            MAX(CASE WHEN ? = 'A' THEN speed_kmh ELSE {gps_prefix}_speed_kmh END) AS max_speed,
            AVG(CASE WHEN ? = 'A' THEN speed_kmh ELSE {gps_prefix}_speed_kmh END) AS avg_speed,
            MAX(CASE WHEN ? = 'A' THEN throttle_percent END) AS max_throttle,
            AVG(CASE WHEN ? = 'A' THEN fuel_add END) AS avg_fuel,
            MIN(ts) AS first_ts,
            MAX(ts) AS last_ts
        FROM samples
        {where}
        """,
        (vehicle_id, vehicle_id, vehicle_id, vehicle_id, vehicle_id, vehicle_id, *args),
    ).fetchone()
    rows = con.execute(
        f"""
        SELECT samples.ts, samples.session_id, sessions.name AS session_name,
               rpm, speed_kmh, throttle_percent, fuel_add, gps_age,
               CASE WHEN ? = 'A' THEN speed_kmh ELSE {gps_prefix}_speed_kmh END AS vehicle_speed_kmh,
               CASE WHEN ? = 'A' THEN gps_age ELSE {gps_prefix}_age END AS vehicle_gps_age,
               CASE WHEN ? = 'A' THEN gps_a_ts ELSE gps_b_ts END AS vehicle_gps_ts,
               CASE WHEN ? = 'A' THEN gps_a_lat ELSE gps_b_lat END AS vehicle_gps_lat,
               CASE WHEN ? = 'A' THEN gps_a_lon ELSE gps_b_lon END AS vehicle_gps_lon,
               CASE WHEN ? = 'A' THEN gps_a_accuracy_m ELSE gps_b_accuracy_m END AS vehicle_gps_accuracy_m,
               CASE WHEN ? = 'A' THEN gps_a_heading ELSE gps_b_heading END AS vehicle_gps_heading,
               CASE WHEN ? = 'A' THEN gps_a_altitude ELSE gps_b_altitude END AS vehicle_gps_altitude,
               accel_x_g, accel_y_g, accel_z_g, total_g, motion_age,
               gps_a_ts, gps_a_speed_kmh, gps_a_lat, gps_a_lon, gps_a_accuracy_m,
               gps_a_heading, gps_a_altitude, gps_a_age,
               gps_b_ts, gps_b_speed_kmh, gps_b_lat, gps_b_lon, gps_b_accuracy_m,
               gps_b_heading, gps_b_altitude, gps_b_age, usb_ts, motion_ts, raw_hex
        FROM samples
        LEFT JOIN sessions ON sessions.id = samples.session_id
        {where}
        ORDER BY samples.id DESC
        LIMIT ?
        """,
        (vehicle_id, vehicle_id, vehicle_id, vehicle_id, vehicle_id, vehicle_id, vehicle_id, vehicle_id, *args, limit),
    ).fetchall()
    con.close()
    return {
        "vehicle_id": vehicle_id,
        "summary": dict(summary),
        "rows": [dict(row) for row in rows],
    }


class SingleInstanceHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False


def main() -> int:
    db = Database(DB_PATH)
    state = SharedState()
    speedhive = SpeedhiveClient()
    lap_timing = LapTimingManager(db)
    pit_commands = PitCommandManager()
    db_queue: queue.Queue[tuple[float, TelemetryState]] = queue.Queue()
    stop_event = threading.Event()
    usb = UsbWorker(state, db_queue)
    writer = DbWriter(db, db_queue, stop_event)
    server = SingleInstanceHTTPServer(("0.0.0.0", 8765), make_handler(state, db, speedhive, lap_timing, pit_commands))
    usb.start()
    writer.start()
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
