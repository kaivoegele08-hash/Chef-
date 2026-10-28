"""Baut F1-UDP-Pakete byteweise nach der Spezifikation (docs/protocols/f1.md).

Bewusst unabhängig vom Parser formuliert (eigene Format-Strings aus den
Offset-Tabellen), damit Tests echte Layoutfehler finden.
"""

from __future__ import annotations

import struct

HEADER = "<HBBBBBQfIIBB"


def header(fmt: int, pid: int, uid: int = 42, t: float = 1.0, frame: int = 1, player: int = 3) -> bytes:
    return struct.pack(HEADER, fmt, {2024: 24, 2025: 25, 2026: 26}[fmt], 1, 0, 1, pid, uid, t, frame, frame, player, 255)


def ncars(fmt: int) -> int:
    return 24 if fmt == 2026 else 22


def session(fmt: int, *, weather=3, track_temp=31, air_temp=22, total_laps=20, track_len=5793, stype=15,
            track_id=11, sc=0, time_left=3600, duration=5400, zones=((0.0, 0), (0.5, 3)), forecast=(),
            s2=1950.0, s3=4000.0, **kw) -> bytes:
    b = bytearray(header(fmt, 1, **kw))
    b += struct.pack("<BbbBHBbBHHBBBBBB", weather, track_temp, air_temp, total_laps, track_len, stype, track_id, 0,
                     time_left, duration, 80, 0, 0, 255, 0, len(zones))
    for i in range(21):
        start, flag = zones[i] if i < len(zones) else (0.0, 0)
        b += struct.pack("<fb", start, flag)
    b += struct.pack("<BBB", sc, 0, len(forecast))
    for i in range(64):
        f = forecast[i] if i < len(forecast) else (0, 0, 0, 0, 2, 0, 2, 0)
        b += struct.pack("<BBBbbbbB", *f)
    b += struct.pack("<BB3I14BI", 0, 90, 1, 2, 3, 12, 16, 8, *([0] * 11), 600)
    b += bytes(33) + bytes(12)
    b += struct.pack("<ff", s2, s3)
    if fmt == 2026:
        b += bytes(926 - 753)
    return bytes(b)


def lap_data(fmt: int, cars: list[dict], **kw) -> bytes:
    b = bytearray(header(fmt, 2, **kw))
    for i in range(ncars(fmt)):
        c = cars[i] if i < len(cars) else {"result": 0}
        b += struct.pack("<IIHBHBHBHBfffBBBBBBBBBBBBBBBHHBfB",
                         c.get("last_ms", 0), c.get("cur_ms", 0), c.get("s1_ms", 0), 0, c.get("s2_ms", 0), 0,
                         c.get("front_ms", 0), 0, c.get("leader_ms", 0), 0, c.get("dist", 0.0),
                         c.get("total", 0.0), 0.0, c.get("pos", i + 1), c.get("lap", 1), c.get("pit", 0),
                         c.get("stops", 0), c.get("sector", 0), c.get("invalid", 0), 0, c.get("warnings", 0), 0, 0, 0,
                         i + 1, c.get("driver_status", 4), c.get("result", 2), 0, 0, 0, 0, 0.0, 255)
    b += bytes([255, 255])
    return bytes(b)


def participants(fmt: int, names: list[str], teams: list[int], **kw) -> bytes:
    b = bytearray(header(fmt, 4, **kw))
    b.append(len(names))
    for i in range(ncars(fmt)):
        name = (names[i] if i < len(names) else "").encode()
        team = teams[i] if i < len(teams) else 0
        if fmt == 2024:
            b += struct.pack("<BBBBBBB48sBBHB", 1, i, i, team, 0, i + 1, 0, name, 1, 1, 0, 1)
        elif fmt == 2025:
            b += struct.pack("<BBBBBBB32sBBHBB12B", 1, i, i, team, 0, i + 1, 0, name, 1, 1, 0, 1, 0, *([0] * 12))
        else:
            b += struct.pack("<BHHHBBB32sBBHBB12B", 1, i, i, team, 0, i + 1, 0, name, 1, 1, 0, 1, 0, *([0] * 12))
    return bytes(b)


def telemetry(fmt: int, player: int, *, speed=287, throttle=1.0, brake=0.0, steer=0.05, gear=7, rpm=11500,
              surface=(95, 96, 101, 102), inner=(100, 100, 105, 105), press=(22.0, 22.0, 23.5, 23.5),
              brakes=(400, 410, 600, 610), **kw) -> bytes:
    b = bytearray(header(fmt, 6, player=player, **kw))
    for i in range(ncars(fmt)):
        sp = speed if i == player else 250
        if fmt == 2026:
            b += struct.pack("<HfffBbHBBH4H4B4BB4f4B", sp, throttle, steer, brake, 0, gear, rpm, 0, 50, 0, *brakes,
                             *surface, *inner, 105, *press, 0, 0, 0, 0)
        else:
            b += struct.pack("<HfffBbHBBH4H4B4BH4f4B", sp, throttle, steer, brake, 0, gear, rpm, 0, 50, 0, *brakes,
                             *surface, *inner, 105, *press, 0, 0, 0, 0)
    b += bytes([255, 255, 0])
    return bytes(b)


def status(fmt: int, player: int, *, fuel=42.5, cap=110.0, rem=1.3, visual=17, age=7, ers=2_000_000.0, ers_mode=1,
           fia=0, **kw) -> bytes:
    b = bytearray(header(fmt, 7, player=player, **kw))
    for i in range(ncars(fmt)):
        vis = visual if i == player else (16 if i % 2 else 18)
        a = age if i == player else i
        if fmt == 2026:
            b += struct.pack("<BBBBBfffHHBBHBBBbfffBffffB", 1, 0, 1, 56, 0, fuel, cap, rem, 13000, 4000, 8, 1, 0,
                             18, vis, a, fia, 500000.0, 120000.0, ers, ers_mode, 0.0, 0.0, 0.0, 1_000_000.0, 0)
        else:
            b += struct.pack("<BBBBBfffHHBBHBBBbfffBfffB", 1, 0, 1, 56, 0, fuel, cap, rem, 13000, 4000, 8, 1, 0,
                             18, vis, a, fia, 500000.0, 120000.0, ers, ers_mode, 0.0, 0.0, 1_000_000.0, 0)
    return bytes(b)


def damage(fmt: int, *, wear=(10.0, 11.0, 14.0, 15.0), fl_wing=0, rear_wing=0, puncture_idx=None, **kw) -> bytes:
    b = bytearray(header(fmt, 10, **kw))
    for _i in range(ncars(fmt)):
        tyre_dmg = [0, 0, 0, 0]
        if puncture_idx is not None:
            tyre_dmg[puncture_idx] = 100
        if fmt == 2024:
            b += struct.pack("<4f4B4B18B", *wear, *tyre_dmg, 0, 0, 0, 0, fl_wing, 0, rear_wing, 0, 0, 0, *([0] * 12))
        else:
            b += struct.pack("<4f4B4B4B18B", *wear, *tyre_dmg, 0, 0, 0, 0, 0, 0, 0, 0, fl_wing, 0, rear_wing, 0, 0,
                             0, *([0] * 12))
    return bytes(b)


def motion(fmt: int, positions: list[tuple[float, float, float]], **kw) -> bytes:
    b = bytearray(header(fmt, 0, **kw))
    for i in range(ncars(fmt)):
        x, y, z = positions[i] if i < len(positions) else (0.0, 0.0, 0.0)
        if fmt == 2026:
            b += struct.pack("<ffffffhhhhhhhhhfff", x, y, z, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1500, -800, 1000, 0, 0, 0)
        else:
            b += struct.pack("<ffffffhhhhhhffffff", x, y, z, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1.5, -0.8, 1.0, 0, 0, 0)
    return bytes(b)


def event(fmt: int, code: str, details: bytes = b"", **kw) -> bytes:
    return header(fmt, 3, **kw) + code.encode() + details.ljust(12, b"\0")
