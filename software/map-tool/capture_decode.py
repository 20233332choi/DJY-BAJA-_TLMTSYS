"""Decode UART traffic from a PulseView/sigrok .sr capture.

The nanoDLA stores one byte per sample; D0..D7 are bits 0..7. This tool
keeps map development read-only: it only inspects saved captures and never
opens the vehicle relay or writes to the ECU.
"""

from __future__ import annotations

import argparse
import configparser
import io
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np


SAMPLE_RATE_RE = re.compile(r"^\s*([0-9.]+)\s*([kKmMgG]?)Hz\s*$")
LOGIC_MEMBER_RE = re.compile(r"^logic-\d+-(\d+)$")


@dataclass(frozen=True)
class UartByte:
    time_s: float
    value: int


@dataclass(frozen=True)
class UartBurst:
    channel: int
    baud: int
    start_s: float
    end_s: float
    data: bytes

    def as_json(self) -> dict[str, object]:
        return {
            "channel": self.channel,
            "baud": self.baud,
            "start_s": round(self.start_s, 6),
            "end_s": round(self.end_s, 6),
            "length": len(self.data),
            "hex": self.data.hex(" ").upper(),
            "rapidbike_command": is_rapidbike_command(self.data),
        }


def parse_sample_rate(text: str) -> float:
    match = SAMPLE_RATE_RE.match(text)
    if not match:
        raise ValueError(f"unsupported samplerate: {text!r}")
    value = float(match.group(1))
    multiplier = {"": 1.0, "k": 1e3, "m": 1e6, "g": 1e9}[match.group(2).lower()]
    return value * multiplier


def load_sigrok_capture(path: Path) -> tuple[np.ndarray, float, list[str]]:
    with zipfile.ZipFile(path) as archive:
        metadata_text = archive.read("metadata").decode("utf-8", "replace")
        parser = configparser.ConfigParser()
        parser.read_file(io.StringIO(metadata_text))
        device = parser["device 1"]
        sample_rate = parse_sample_rate(device["samplerate"])
        unitsize = int(device.get("unitsize", "1"))
        if unitsize != 1:
            raise ValueError(f"only one-byte logic samples are supported, got {unitsize}")
        probes = [
            device.get(f"probe{index}", f"D{index - 1}")
            for index in range(1, int(device["total probes"]) + 1)
        ]
        members: list[tuple[int, str]] = []
        for name in archive.namelist():
            match = LOGIC_MEMBER_RE.match(name)
            if match:
                members.append((int(match.group(1)), name))
        if not members:
            raise ValueError("capture contains no digital logic samples")
        raw = b"".join(archive.read(name) for _, name in sorted(members))
    return np.frombuffer(raw, dtype=np.uint8), sample_rate, probes


def decode_uart(samples: np.ndarray, sample_rate: float, channel: int, baud: int) -> list[UartByte]:
    if channel < 0 or channel > 7:
        raise ValueError("channel must be between 0 and 7")
    ticks_per_bit = sample_rate / baud
    if ticks_per_bit < 8:
        raise ValueError("sample rate must provide at least eight samples per UART bit")
    levels = ((samples >> channel) & 1).astype(np.uint8, copy=False)
    falling_edges = np.flatnonzero((levels[:-1] == 1) & (levels[1:] == 0)) + 1
    result: list[UartByte] = []
    next_start = 0
    stop_offset = int(round(9.5 * ticks_per_bit))
    data_offsets = np.rint((1.5 + np.arange(8)) * ticks_per_bit).astype(np.int64)
    frame_span = int(round(10.0 * ticks_per_bit))
    for candidate_value in falling_edges:
        candidate = int(candidate_value)
        if candidate < next_start or candidate + stop_offset >= len(levels):
            continue
        if levels[candidate + int(round(0.5 * ticks_per_bit))] != 0:
            continue
        if levels[candidate + stop_offset] != 1:
            continue
        bit_values = levels[candidate + data_offsets]
        value = int(np.dot(bit_values, 1 << np.arange(8)))
        result.append(UartByte(candidate / sample_rate, value))
        next_start = candidate + frame_span
    return result


def group_bursts(events: list[UartByte], channel: int, baud: int) -> list[UartBurst]:
    if not events:
        return []
    gap_limit = 20.0 / baud
    groups: list[list[UartByte]] = [[events[0]]]
    for event in events[1:]:
        if event.time_s - groups[-1][-1].time_s > gap_limit:
            groups.append([event])
        else:
            groups[-1].append(event)
    return [
        UartBurst(
            channel=channel,
            baud=baud,
            start_s=group[0].time_s,
            end_s=group[-1].time_s + (10.0 / baud),
            data=bytes(event.value for event in group),
        )
        for group in groups
    ]


def rapidbike_checksum(frame_without_checksum: bytes) -> int:
    if len(frame_without_checksum) < 3 or frame_without_checksum[0] != 0xAA or frame_without_checksum[2] != 0x55:
        raise ValueError("not a RapidBike request prefix")
    return (frame_without_checksum[1] + sum(frame_without_checksum[3:]) - 1) & 0xFF


def is_rapidbike_command(data: bytes) -> bool:
    return (
        4 <= len(data) <= 64
        and data[0] == 0xAA
        and data[2] == 0x55
        and rapidbike_checksum(data[:-1]) == data[-1]
    )


def analyze(path: Path, channels: list[int], bauds: list[int]) -> dict[str, object]:
    samples, sample_rate, probes = load_sigrok_capture(path)
    signal_stats: list[dict[str, object]] = []
    for channel in range(min(len(probes), 8)):
        levels = (samples >> channel) & 1
        signal_stats.append(
            {
                "channel": channel,
                "probe": probes[channel],
                "high_fraction": float(np.count_nonzero(levels)) / len(levels),
                "transitions": int(np.count_nonzero(levels[1:] != levels[:-1])),
            }
        )
    analyses: list[dict[str, object]] = []
    for channel in channels:
        for baud in bauds:
            events = decode_uart(samples, sample_rate, channel, baud)
            bursts = group_bursts(events, channel, baud)
            analyses.append(
                {
                    "channel": channel,
                    "probe": probes[channel] if channel < len(probes) else f"D{channel}",
                    "baud": baud,
                    "byte_count": len(events),
                    "bursts": [burst.as_json() for burst in bursts],
                }
            )
    return {
        "capture": str(path.resolve()),
        "sample_rate_hz": sample_rate,
        "sample_count": len(samples),
        "duration_s": len(samples) / sample_rate,
        "probes": probes,
        "signal_stats": signal_stats,
        "analyses": analyses,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path, help="PulseView .sr capture")
    parser.add_argument("--channels", default="0,1", help="comma-separated digital channels")
    parser.add_argument("--bauds", default="9600,38400", help="comma-separated UART baud rates")
    parser.add_argument("--json", type=Path, help="write complete analysis JSON")
    args = parser.parse_args()
    channels = [int(item) for item in args.channels.split(",") if item.strip()]
    bauds = [int(item) for item in args.bauds.split(",") if item.strip()]
    report = analyze(args.capture, channels, bauds)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        f"capture={report['duration_s']:.3f}s "
        f"samples={report['sample_count']} rate={report['sample_rate_hz']:.0f}Hz"
    )
    for stats in report["signal_stats"]:
        print(
            f"{stats['probe']}: transitions={stats['transitions']} "
            f"high={stats['high_fraction'] * 100:.3f}%"
        )
    for analysis in report["analyses"]:
        commands = [burst for burst in analysis["bursts"] if burst["rapidbike_command"]]
        print(
            f"{analysis['probe']} baud={analysis['baud']}: "
            f"bytes={analysis['byte_count']} bursts={len(analysis['bursts'])} "
            f"rapidbike_commands={len(commands)}"
        )
        for burst in commands:
            print(f"  {burst['start_s']:10.6f}s  {burst['hex']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
