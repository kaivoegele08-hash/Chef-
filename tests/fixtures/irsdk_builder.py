"""Synthetische irsdk-Map (Header + Var-Header + YAML + 3 Puffer) für Tests."""

from __future__ import annotations

import struct
from typing import Any

from pitwall.adapters.iracing import HEADER, VARBUF, VARHEADER

SIZES = {0: 1, 1: 1, 2: 4, 3: 4, 4: 4, 5: 8}
CODES = {0: "B", 1: "?", 2: "i", 3: "I", 4: "f", 5: "d"}

# name → (type, count)
DEFAULT_VARS: dict[str, tuple[int, int]] = {
    "SessionTime": (5, 1), "SessionNum": (2, 1), "SessionState": (2, 1), "SessionFlags": (3, 1),
    "SessionUniqueID": (2, 1), "SessionTimeRemain": (5, 1), "SessionLapsTotal": (2, 1),
    "PlayerCarIdx": (2, 1), "Lap": (2, 1), "LapCompleted": (2, 1), "LapDist": (4, 1), "LapDistPct": (4, 1),
    "LapCurrentLapTime": (4, 1), "LapLastLapTime": (4, 1), "LapBestLapTime": (4, 1),
    "Speed": (4, 1), "RPM": (4, 1), "Gear": (2, 1), "Throttle": (4, 1), "Brake": (4, 1), "Clutch": (4, 1),
    "SteeringWheelAngle": (4, 1), "SteeringWheelAngleMax": (4, 1), "FuelLevel": (4, 1), "FuelLevelPct": (4, 1),
    "EngineWarnings": (3, 1), "OnPitRoad": (1, 1), "IsOnTrack": (1, 1), "PlayerTrackSurface": (2, 1),
    "PlayerCarInPitStall": (1, 1), "PlayerCarMyIncidentCount": (2, 1), "AirTemp": (4, 1),
    "TrackTempCrew": (4, 1), "TrackWetness": (2, 1), "Precipitation": (4, 1),
    "LFtempCL": (4, 1), "LFtempCM": (4, 1), "LFtempCR": (4, 1), "LFwearL": (4, 1), "LFwearM": (4, 1),
    "LFwearR": (4, 1),
    "CarIdxLapDistPct": (4, 64), "CarIdxPosition": (2, 64), "CarIdxClassPosition": (2, 64),
    "CarIdxLap": (2, 64), "CarIdxLapCompleted": (2, 64), "CarIdxLastLapTime": (4, 64),
    "CarIdxBestLapTime": (4, 64), "CarIdxOnPitRoad": (1, 64), "CarIdxTrackSurface": (2, 64),
    "CarIdxSessionFlags": (3, 64), "CarIdxTireCompound": (2, 64),
}

YAML = """---
WeekendInfo:
 TrackName: spa 2024 up
 TrackID: 163
 TrackLength: 7.00 km
 TrackDisplayName: Circuit de Spa-Francorchamps
 TrackPitSpeedLimit: 80.00 kph
 TrackType: road course
 SubSessionID: 4711
 WeekendOptions:
  Skies: Partly Cloudy
SessionInfo:
 Sessions:
 - SessionNum: 0
   SessionLaps: 20
   SessionTime: unlimited
   SessionType: Race
   ResultsPositions:
DriverInfo:
 DriverCarIdx: 1
 DriverCarFuelMaxLtr: 100.000
 DriverCarMaxFuelPct: 1.000
 DriverCarRedLine: 8500.000
 DriverTires:
 - TireIndex: 0
   TireCompoundType: Hard
 - TireIndex: 1
   TireCompoundType: Wet
 Drivers:
 - CarIdx: 0
   UserName: Pace Car
   CarIsPaceCar: 1
   CarScreenName: safety pcporsche911cup
 - CarIdx: 1
   UserName: Kai Vögele
   TeamName: Team: #1 "Pit" Wall
   CarNumber: "007"
   CarScreenName: Porsche 911 GT3 R (992)
   CarClassShortName: GT3
   CarClassColor: 0xffda59
 - CarIdx: 2
   UserName: Max O'Neil, Jr.
   CarNumber: 12
   CarScreenName: BMW M4 GT3
   CarClassShortName: GT3
 - CarIdx: 3
   UserName: yes
   CarNumber: 3
   CarScreenName: Ferrari 296 GT3
   CarClassShortName: GT3
 - CarIdx: 5
   UserName: Zuschauer
   IsSpectator: 1
SplitTimeInfo:
 Sectors:
 - SectorNum: 0
   SectorStartPct: 0.000000
 - SectorNum: 1
   SectorStartPct: 0.330000
 - SectorNum: 2
   SectorStartPct: 0.660000
CarSetup:
 Tires: {broken: [yaml
...
"""


class IrsdkMap:
    def __init__(self, vars_: dict[str, tuple[int, int]] | None = None, yaml_text: str = YAML,
                 size: int = 256 * 1024):
        self.vars = dict(vars_ or DEFAULT_VARS)
        self.yaml = yaml_text
        self.size = size
        self.layout: dict[str, tuple[int, int, int]] = {}
        off = 0
        for name, (t, c) in self.vars.items():
            self.layout[name] = (t, off, c)
            off += SIZES[t] * c
        self.buf_len = (off + 15) // 16 * 16
        self.values: dict[str, Any] = {}
        self.tick = 0
        self.si_update = 1
        self.status = 1
        self.buf_index = 0
        self.rows = [bytes(self.buf_len)] * 3

    def set(self, **kw: Any) -> IrsdkMap:
        self.values.update(kw)
        return self

    def _row(self) -> bytes:
        row = bytearray(self.buf_len)
        for name, (t, off, c) in self.layout.items():
            v = self.values.get(name)
            if v is None:
                v = [-1.0 if name == "CarIdxLapDistPct" else (-1 if name == "CarIdxTrackSurface" else 0)] * c \
                    if c > 1 else 0
            vals = list(v) + [0] * (c - len(v)) if c > 1 else [v]
            struct.pack_into(f"<{c}{CODES[t]}", row, off, *vals)
        return bytes(row)

    def step(self) -> bytes:
        """Neue Zeile in den nächsten Puffer schreiben und die ganze Map liefern."""
        self.tick += 1
        self.buf_index = (self.buf_index + 1) % 3
        self.rows[self.buf_index] = self._row()
        return self.build()

    def build(self) -> bytes:
        m = bytearray(self.size)
        vh_off = 144
        yaml_b = self.yaml.encode("cp1252", errors="replace")
        si_off = vh_off + len(self.layout) * VARHEADER.size
        si_len = len(yaml_b) + 64
        buf0 = (si_off + si_len + 4095) // 4096 * 4096
        HEADER.pack_into(m, 0, 2, self.status, 60, self.si_update, si_len, si_off, len(self.layout), vh_off, 3,
                         self.buf_len)
        for i in range(3):
            tick = self.tick - ((self.buf_index - i) % 3)
            VARBUF.pack_into(m, 48 + 16 * i, tick, buf0 + i * self.buf_len, 0, 0)
            m[buf0 + i * self.buf_len: buf0 + (i + 1) * self.buf_len] = self.rows[i]
        for j, (name, (t, off, c)) in enumerate(self.layout.items()):
            VARHEADER.pack_into(m, vh_off + j * 144, t, off, c, False, name.encode(), b"", b"")
        m[si_off:si_off + len(yaml_b)] = yaml_b
        return bytes(m)


def race_values(lap: int = 3, pct: float = 0.5, **over: Any) -> dict[str, Any]:
    n = 64
    pcts = [-1.0] * n
    pos = [0] * n
    laps = [0] * n
    done = [0] * n
    last = [-1.0] * n
    surf = [-1] * n
    for idx, (p, position, lp) in {0: (0.1, 0, lap), 1: (pct, 2, lap), 2: (pct + 0.01, 1, lap),
                                    3: (pct - 0.02, 3, lap)}.items():
        pcts[idx], pos[idx], laps[idx], done[idx], surf[idx] = p, position, lp, lp - 1, 3
        last[idx] = 138.5 + idx * 0.3
    v = {
        "SessionTime": 600.0, "SessionNum": 0, "SessionState": 4, "SessionFlags": 0x4, "SessionUniqueID": 2,
        "SessionTimeRemain": 604800.0, "SessionLapsTotal": 20, "PlayerCarIdx": 1, "Lap": lap,
        "LapCompleted": lap - 1, "LapDist": pct * 7000.0, "LapDistPct": pct, "LapCurrentLapTime": 70.0,
        "LapLastLapTime": 138.8, "LapBestLapTime": 138.2, "Speed": 70.0, "RPM": 7000.0, "Gear": 4,
        "Throttle": 1.0, "Brake": 0.0, "Clutch": 1.0, "SteeringWheelAngle": 0.0, "SteeringWheelAngleMax": 9.42,
        "FuelLevel": 60.0, "FuelLevelPct": 0.6, "EngineWarnings": 0, "OnPitRoad": False, "IsOnTrack": True,
        "PlayerTrackSurface": 3, "PlayerCarInPitStall": False, "PlayerCarMyIncidentCount": 2, "AirTemp": 21.0,
        "TrackTempCrew": 30.0, "TrackWetness": 1, "Precipitation": 0.0,
        "LFtempCL": 80.0, "LFtempCM": 85.0, "LFtempCR": 90.0, "LFwearL": 0.9, "LFwearM": 0.9, "LFwearR": 0.9,
        "CarIdxLapDistPct": pcts, "CarIdxPosition": pos, "CarIdxClassPosition": pos, "CarIdxLap": laps,
        "CarIdxLapCompleted": done, "CarIdxLastLapTime": last, "CarIdxBestLapTime": last,
        "CarIdxOnPitRoad": [False] * n, "CarIdxTrackSurface": surf, "CarIdxSessionFlags": [0] * n,
        "CarIdxTireCompound": [0] * n,
    }
    v.update(over)
    return v
