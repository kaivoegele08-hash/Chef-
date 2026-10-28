"""Synthetische rF2-Plugin-Maps und LMU_Data für Tests."""

from __future__ import annotations

import struct
from typing import Any

from pitwall.adapters.layout import Layout
from pitwall.adapters.rfactor2 import (
    LMU_SCORING_INFO,
    LMU_SIZE,
    LMU_TELEM_INFO,
    LMU_TELEMETRY,
    LMU_VEH_SCORING,
    SCORING_BASE,
    SCORING_INFO,
    SCORING_MAP_SIZE,
    TELEM_BASE,
    TELEM_MAP_SIZE,
    TELEM_STRIDE,
    VEH_SCORING,
    VEH_TELEM,
    WHEEL,
    WHEELS_OFFSET,
)


def _pack(layout: Layout, values: dict[str, Any]) -> bytes:
    args: list[Any] = []
    for name, code in layout.fields:
        if code.endswith("x"):
            continue
        count = int(code[:-1]) if code[:-1].isdigit() and not code.endswith("s") else 1
        v = values.get(name)
        if code.endswith("s"):
            args.append(v.encode("cp1252") if isinstance(v, str) else (v or b""))
        elif count > 1:
            args.extend(list(v) if v is not None else [0] * count)
        else:
            args.append(v if v is not None else 0)
    return layout.struct.pack(*args)


def scoring_info(**kw: Any) -> dict[str, Any]:
    v = {"trackName": "Spa-Francorchamps", "session": 10, "currentET": 900.0, "endET": 0.0, "maxLaps": 30,
         "lapDist": 7004.0, "gamePhase": 5, "yellowFlagState": 0, "sectorFlag": (0, 0, 0), "inRealtime": True,
         "playerName": "Kai", "ambientTemp": 22.0, "trackTemp": 31.0, "raining": 0.0, "darkCloud": 0.1,
         "avgPathWetness": 0.0, "sessionTimeRemaining": 0.0}
    v.update(kw)
    return v


def vehicle(idx: int, place: int, laps: int, lap_dist: float, player: bool = False, **kw: Any) -> dict[str, Any]:
    v = {"id": idx, "driverName": f"Fahrer {idx}", "vehicleName": f"Porsche 963 #{idx + 4}", "totalLaps": laps,
         "sector": 2, "finishStatus": 0, "lapDist": lap_dist, "bestLapTime": 125.0 + idx, "lastSector1": 40.0,
         "lastSector2": 82.0, "lastLapTime": 126.0 + idx, "curSector1": 40.5, "curSector2": 0.0, "isPlayer": player,
         "control": 0 if player else 1, "place": place, "vehicleClass": "Hypercar", "timeBehindNext": 1.5,
         "lapsBehindNext": 0, "timeBehindLeader": 1.5 * (place - 1), "lapsBehindLeader": 0, "pos": (100.0, 0, 50.0),
         "localVel": (0.0, 0.0, -60.0), "pitGroup": f"Team {idx}", "timeIntoLap": 60.0}
    v.update(kw)
    return v


def telemetry(idx: int, **kw: Any) -> dict[str, Any]:
    v = {"id": idx, "elapsedTime": 900.0, "lapNumber": 5, "vehicleName": "Porsche 963 #5",
         "trackName": "Spa-Francorchamps", "localVel": (0.0, 0.0, -60.0), "gear": 5, "engineRPM": 8000.0,
         "unfilteredThrottle": 0.8, "unfilteredBrake": 0.0, "unfilteredSteering": -0.1, "fuel": 40.0,
         "fuelCapacity": 90.0, "engineMaxRPM": 9000.0, "frontTireCompoundName": "Medium", "dentSeverity": [0] * 8,
         "virtualEnergy": 0.55, "electricBoostMotorState": 2, "batteryChargeFraction": 0.4,
         "localAccel": (1.0, 0.0, -2.0), "localRot": (0.0, 0.1, 0.0)}
    v.update(kw)
    return v


def wheel(**kw: Any) -> dict[str, Any]:
    v = {"brakeTemp": 450.0, "pressure": 170.0, "temperature": (363.15, 368.15, 373.15), "wear": 0.9,
         "tireCarcassTemperature": 360.0, "compoundType": 1}
    v.update(kw)
    return v


def scoring_map(info: dict[str, Any], vehicles: list[dict[str, Any]], version: int = 7) -> bytes:
    m = bytearray(SCORING_MAP_SIZE)
    struct.pack_into("<IIi", m, 0, version, version, 548 + len(vehicles) * 584)
    info = dict(info, numVehicles=len(vehicles))
    m[12:12 + 548] = _pack(SCORING_INFO, info)
    for i, v in enumerate(vehicles):
        m[SCORING_BASE + i * 584:SCORING_BASE + (i + 1) * 584] = _pack(VEH_SCORING, v)
    return bytes(m)


def telem_block(t: dict[str, Any], wheels: list[dict[str, Any]] | None = None) -> bytes:
    blk = bytearray(TELEM_STRIDE)
    blk[:WHEELS_OFFSET] = _pack(VEH_TELEM, t)
    for j, w in enumerate(wheels or [wheel()] * 4):
        blk[WHEELS_OFFSET + j * 260:WHEELS_OFFSET + (j + 1) * 260] = _pack(WHEEL, w)
    return bytes(blk)


def telemetry_map(blocks: list[bytes], version: int = 3, torn: bool = False) -> bytes:
    m = bytearray(TELEM_MAP_SIZE)
    struct.pack_into("<IIii", m, 0, version + (1 if torn else 0), version, 4 + len(blocks) * TELEM_STRIDE,
                     len(blocks))
    for i, b in enumerate(blocks):
        m[TELEM_BASE + i * TELEM_STRIDE:TELEM_BASE + (i + 1) * TELEM_STRIDE] = b
    return bytes(m)


def lmu_map(info: dict[str, Any], vehicles: list[dict[str, Any]], blocks: list[bytes], player_idx: int) -> bytes:
    m = bytearray(LMU_SIZE)
    m[LMU_SCORING_INFO:LMU_SCORING_INFO + 548] = _pack(SCORING_INFO, dict(info, numVehicles=len(vehicles)))
    for i, v in enumerate(vehicles):
        m[LMU_VEH_SCORING + i * 584:LMU_VEH_SCORING + (i + 1) * 584] = _pack(VEH_SCORING, v)
    struct.pack_into("<BB?", m, LMU_TELEMETRY, len(blocks), player_idx, True)
    for i, b in enumerate(blocks):
        m[LMU_TELEM_INFO + i * TELEM_STRIDE:LMU_TELEM_INFO + (i + 1) * TELEM_STRIDE] = b
    return bytes(m)
