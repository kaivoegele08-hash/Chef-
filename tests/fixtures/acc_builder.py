"""ACC-Testdaten: Broadcasting-Nachrichten und Shared-Memory-Seiten."""

from __future__ import annotations

import struct
from typing import Any

from pitwall.adapters.acc import ACC_GRAPHICS, ACC_PHYSICS, ACC_STATIC_33
from pitwall.adapters.layout import Layout

MAXINT = 2147483647


def s(text: str) -> bytes:
    d = text.encode("utf-8")
    return struct.pack("<H", len(d)) + d


def lap(ms: int | None, car: int, splits: list[int | None] | None = None, invalid: bool = False) -> bytes:
    sp = splits or []
    out = struct.pack("<iHHB", MAXINT if ms is None else ms, car, 0, len(sp))
    out += b"".join(struct.pack("<i", MAXINT if x is None else x) for x in sp)
    return out + bytes([int(invalid), 1, 0, 0])


def registration(conn: int = 7, ok: bool = True) -> bytes:
    return bytes([1]) + struct.pack("<iBB", conn, int(ok), 1) + s("" if ok else "wrong password")


def realtime(session_type: int = 10, phase: int = 5, session_time_ms: float = 600_000.0,
             end_ms: float = 1_800_000.0, focused: int = 1, rain: int = 0) -> bytes:
    out = bytes([2]) + struct.pack("<HHBBffi", 1, 0, session_type, phase, session_time_ms, end_ms, focused)
    out += s("set1") + s("cam") + s("Basic HUD") + bytes([0])
    out += struct.pack("<f", 50_000.0) + bytes([22, 31, 3, rain, 0])
    return out + lap(120_500, 2, [40_000, 41_000, 39_500])


def car_update(idx: int, position: int, laps: int, spline: float, location: int = 1, kmh: int = 200,
               last_ms: int | None = 121_000) -> bytes:
    out = bytes([3]) + struct.pack("<HHBBfffBHHHHfHi", idx, 0, 1, 6, 0.0, 0.0, 0.0, location, kmh, position,
                                   position, 0, spline, laps, -150)
    return out + lap(120_800, idx) + lap(last_ms, idx, [40_100, 41_200, 39_700]) + lap(60_000, idx)


def entry_list(conn: int, ids: list[int]) -> bytes:
    return bytes([4]) + struct.pack("<iH", conn, len(ids)) + b"".join(struct.pack("<H", i) for i in ids)


def entry_car(idx: int, team: str, number: int, first: str, last: str, cup: int = 0) -> bytes:
    out = bytes([6]) + struct.pack("<HB", idx, 24) + s(team) + struct.pack("<iBBHB", number, cup, 0, 2, 1)
    return out + s(first) + s(last) + s(last[:3].upper()) + struct.pack("<BH", 2, 2)


def track_data(conn: int, name: str = "Spa", meters: int = 7004) -> bytes:
    return bytes([5]) + struct.pack("<i", conn) + s(name) + struct.pack("<ii", 3, meters) + bytes([0, 0])


def _pack(layout: Layout, values: dict[str, Any]) -> bytes:
    args: list[Any] = []
    for name, code in layout.fields:
        if code.endswith("x"):
            continue
        count = int(code[:-1]) if code[:-1].isdigit() and not code.endswith("s") else 1
        v = values.get(name)
        if code.endswith("s"):
            args.append(v.encode("utf-16-le") if isinstance(v, str) else (v or b""))
        elif count > 1:
            vals = list(v) if v is not None else []
            args.extend(vals + [0] * (count - len(vals)))
        else:
            args.append(v if v is not None else 0)
    return layout.struct.pack(*args)


def physics(**kw: Any) -> bytes:
    v = {"packetId": 1, "gas": 0.9, "fuel": 55.0, "gear": 5, "rpms": 7000, "speedKmh": 220.0,
         "wheelsPressure": [27.6] * 4, "tyreTemp": [82.0, 83.0, 80.0, 81.0], "brakeTemp": [500.0] * 4,
         "airTemp": 22.0, "roadTemp": 31.0, "carDamage": [0.0] * 5, "slipRatio": [0.0] * 4}
    v.update(kw)
    return _pack(ACC_PHYSICS, v)


def graphics(**kw: Any) -> bytes:
    v = {"packetId": 1, "status": 2, "session": 2, "completedLaps": 4, "position": 3, "iCurrentTime": 60_000,
         "iLastTime": 121_000, "iBestTime": 120_500, "sessionTimeLeft": 1_200_000.0, "currentSectorIndex": 1,
         "normalizedCarPosition": 0.5, "activeCars": 2, "carCoordinates": [10.0, 0.0, 20.0, 30.0, 0.0, 40.0],
         "carID": [1, 2], "playerCarID": 1, "flag": 0, "fuelXLap": 2.8, "fuelEstimatedLaps": 19.6,
         "isValidLap": 1, "trackGripStatus": 2, "rainIntensity": 0, "rainIntensityIn10min": 0,
         "rainIntensityIn30min": 2, "gapAhead": 1500, "gapBehind": 800}
    v.update(kw)
    return _pack(ACC_GRAPHICS, v)


def static(**kw: Any) -> bytes:
    v = {"smVersion": "1.9", "acVersion": "1.10", "numCars": 2, "carModel": "porsche_992_gt3_r", "track": "spa",
         "playerName": "Kai", "playerSurname": "V", "maxRpm": 9250, "maxFuel": 120.0, "isOnline": 0,
         "dryTyresName": "DHE", "wetTyresName": "WH"}
    v.update(kw)
    return _pack(ACC_STATIC_33, v)
