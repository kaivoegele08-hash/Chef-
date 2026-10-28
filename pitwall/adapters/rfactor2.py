"""rFactor 2 (rF2SharedMemoryMapPlugin) und Le Mans Ultimate (``LMU_Data`` + Plugin-Fallback).

Beide Spiele nutzen dieselben ISI-Structs (Pack 4, Windows x64). Layouts und Offsets:
``docs/protocols/rfactor2_lmu.md``. Kein Code aus dem (GPL-3) Plugin übernommen – nur das
Binärinterface ist hier eigenständig als Feldliste beschrieben.

Kanäle (kompakt, aufnahme- und replay-fähig):

* ``rf2_scoring``: Version-Block + Hint + ScoringInfo + n × VehicleScoring (ab Mapping-Offset 0)
* ``rf2_telemetry``: Version-Block + Hint + NumVehicles + **nur das Spielerauto**

:class:`RF2Splitter` baut beide Kanäle aus den rF2-Plugin-Maps oder aus ``LMU_Data``.
"""

from __future__ import annotations

import logging
import math
import re
import struct
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from ..core.frame import (
    Capability,
    CarState,
    CarStatus,
    Damage,
    Flag,
    PitState,
    PlayerState,
    SafetyCar,
    SessionInfo,
    SessionPhase,
    SessionType,
    SimId,
    TelemetryFrame,
    VehicleClass,
    Weather,
    WheelState,
    normalize_compound,
)
from .base import Decoder, RawPacket, RawSource, TransportAdapter
from .layout import Layout, Record
from .shm import MapSpec, ShmSource, cstr

if TYPE_CHECKING:
    from ..config import Config

log = logging.getLogger(__name__)

WHEEL = Layout("<", [
    ("suspensionDeflection", "d"), ("rideHeight", "d"), ("suspForce", "d"), ("brakeTemp", "d"),
    ("brakePressure", "d"), ("rotation", "d"), ("lateralPatchVel", "d"), ("longitudinalPatchVel", "d"),
    ("lateralGroundVel", "d"), ("longitudinalGroundVel", "d"), ("camber", "d"), ("lateralForce", "d"),
    ("longitudinalForce", "d"), ("tireLoad", "d"), ("gripFract", "d"), ("pressure", "d"), ("temperature", "3d"),
    ("wear", "d"), ("terrainName", "16s"), ("surfaceType", "B"), ("flat", "?"), ("detached", "?"),
    ("staticUndeflectedRadius", "B"), ("verticalTireDeflection", "d"), ("wheelYLocation", "d"), ("toe", "d"),
    ("tireCarcassTemperature", "d"), ("tireInnerLayerTemperature", "3d"),
    # LMU belegt die Expansion
    ("optimalTemp", "f"), ("compoundIndex", "B"), ("compoundType", "B"), ("_exp", "18x"),
])
VEH_TELEM = Layout("<", [
    ("id", "i"), ("deltaTime", "d"), ("elapsedTime", "d"), ("lapNumber", "i"), ("lapStartET", "d"),
    ("vehicleName", "64s"), ("trackName", "64s"), ("pos", "3d"), ("localVel", "3d"), ("localAccel", "3d"),
    ("ori", "9d"), ("localRot", "3d"), ("localRotAccel", "3d"), ("gear", "i"), ("engineRPM", "d"),
    ("engineWaterTemp", "d"), ("engineOilTemp", "d"), ("clutchRPM", "d"), ("unfilteredThrottle", "d"),
    ("unfilteredBrake", "d"), ("unfilteredSteering", "d"), ("unfilteredClutch", "d"), ("filteredThrottle", "d"),
    ("filteredBrake", "d"), ("filteredSteering", "d"), ("filteredClutch", "d"), ("steeringShaftTorque", "d"),
    ("front3rdDeflection", "d"), ("rear3rdDeflection", "d"), ("frontWingHeight", "d"), ("frontRideHeight", "d"),
    ("rearRideHeight", "d"), ("drag", "d"), ("frontDownforce", "d"), ("rearDownforce", "d"), ("fuel", "d"),
    ("engineMaxRPM", "d"), ("scheduledStops", "B"), ("overheating", "?"), ("detached", "?"), ("headlights", "?"),
    ("dentSeverity", "8B"), ("lastImpactET", "d"), ("lastImpactMagnitude", "d"), ("lastImpactPos", "3d"),
    ("engineTorque", "d"), ("currentSector", "i"), ("speedLimiter", "B"), ("maxGears", "B"),
    ("frontTireCompoundIndex", "B"), ("rearTireCompoundIndex", "B"), ("fuelCapacity", "d"),
    ("frontFlapActivated", "B"), ("rearFlapActivated", "B"), ("rearFlapLegalStatus", "B"),
    ("ignitionStarter", "B"), ("frontTireCompoundName", "18s"), ("rearTireCompoundName", "18s"),
    ("speedLimiterAvailable", "B"), ("antiStallActivated", "B"), ("_unused", "2x"),
    ("visualSteeringWheelRange", "f"), ("rearBrakeBias", "d"), ("turboBoostPressure", "d"),
    ("physicsToGraphicsOffset", "3f"), ("physicalSteeringWheelRange", "f"),
    # Variante 2 (mDeltaBest, = LMU-Layout), siehe Doku A.4.3
    ("deltaBest", "d"), ("batteryChargeFraction", "d"), ("electricBoostMotorTorque", "d"),
    ("electricBoostMotorRPM", "d"), ("electricBoostMotorTemperature", "d"), ("electricBoostWaterTemperature", "d"),
    ("electricBoostMotorState", "B"), ("lapInvalidated", "?"), ("absActive", "?"), ("tcActive", "?"),
    ("speedLimiterActive", "?"), ("wiperState", "B"), ("tc", "B"), ("tcMax", "B"), ("tcSlip", "B"),
    ("tcSlipMax", "B"), ("tcCut", "B"), ("tcCutMax", "B"), ("abs", "B"), ("absMax", "B"), ("motorMap", "B"),
    ("motorMapMax", "B"), ("migration", "B"), ("migrationMax", "B"), ("frontAntiSway", "B"),
    ("frontAntiSwayMax", "B"), ("rearAntiSway", "B"), ("rearAntiSwayMax", "B"), ("liftAndCoastProgress", "B"),
    ("trackLimitsSteps", "B"), ("regen", "f"), ("stateOfCharge", "f"), ("virtualEnergy", "f"),
    ("timeGapCarAhead", "f"), ("timeGapCarBehind", "f"), ("timeGapPlaceAhead", "f"), ("timeGapPlaceBehind", "f"),
    ("vehicleModel", "30s"), ("vehicleClassId", "B"), ("vehicleChampionship", "B"), ("_exp", "20x"),
])
SCORING_INFO = Layout("<", [
    ("trackName", "64s"), ("session", "i"), ("currentET", "d"), ("endET", "d"), ("maxLaps", "i"), ("lapDist", "d"),
    ("_ptr1", "8x"), ("numVehicles", "i"), ("gamePhase", "B"), ("yellowFlagState", "b"), ("sectorFlag", "3b"),
    ("startLight", "B"), ("numRedLights", "B"), ("inRealtime", "?"), ("playerName", "32s"), ("plrFileName", "64s"),
    ("darkCloud", "d"), ("raining", "d"), ("ambientTemp", "d"), ("trackTemp", "d"), ("wind", "3d"),
    ("minPathWetness", "d"), ("maxPathWetness", "d"), ("gameMode", "B"), ("isPasswordProtected", "?"),
    ("serverPort", "H"), ("serverPublicIP", "I"), ("maxPlayers", "i"), ("serverName", "32s"), ("startET", "f"),
    ("avgPathWetness", "d"),
    # LMU-Erweiterung
    ("sessionTimeRemaining", "f"), ("timeOfDay", "f"), ("isFixedSetup", "?"), ("trackGripLevel", "B"),
    ("cloudCoverage", "B"), ("trackLimitsStepsPerPenalty", "B"), ("trackLimitsStepsPerPoint", "B"),
    ("_exp", "187x"), ("_ptr2", "8x"),
])
VEH_SCORING = Layout("<", [
    ("id", "i"), ("driverName", "32s"), ("vehicleName", "64s"), ("totalLaps", "h"), ("sector", "b"),
    ("finishStatus", "b"), ("lapDist", "d"), ("pathLateral", "d"), ("trackEdge", "d"), ("bestSector1", "d"),
    ("bestSector2", "d"), ("bestLapTime", "d"), ("lastSector1", "d"), ("lastSector2", "d"), ("lastLapTime", "d"),
    ("curSector1", "d"), ("curSector2", "d"), ("numPitstops", "h"), ("numPenalties", "h"), ("isPlayer", "?"),
    ("control", "b"), ("inPits", "?"), ("place", "B"), ("vehicleClass", "32s"), ("timeBehindNext", "d"),
    ("lapsBehindNext", "i"), ("timeBehindLeader", "d"), ("lapsBehindLeader", "i"), ("lapStartET", "d"),
    ("pos", "3d"), ("localVel", "3d"), ("localAccel", "3d"), ("ori", "9d"), ("localRot", "3d"),
    ("localRotAccel", "3d"), ("headlights", "B"), ("pitState", "B"), ("serverScored", "B"),
    ("individualPhase", "B"), ("qualification", "i"), ("timeIntoLap", "d"), ("estimatedLapTime", "d"),
    ("pitGroup", "24s"), ("flag", "B"), ("underYellow", "?"), ("countLapFlag", "B"), ("inGarageStall", "?"),
    ("upgradePack", "16s"), ("pitLapDist", "f"), ("bestLapSector1", "f"), ("bestLapSector2", "f"),
    # LMU-Erweiterung
    ("steamID", "Q"), ("vehFilename", "32s"), ("attackMode", "h"), ("fuelFraction", "B"), ("drsState", "?"),
    ("_exp", "4x"),
])
assert (WHEEL.size, VEH_TELEM.size, SCORING_INFO.size, VEH_SCORING.size) == (260, 848, 548, 584)
assert VEH_TELEM.offset_of("fuel") == 524 and VEH_TELEM.offset_of("currentSector") == 600
assert VEH_TELEM.offset_of("virtualEnergy") == 776 and VEH_TELEM.offset_of("vehicleModel") == 796
assert SCORING_INFO.offset_of("numVehicles") == 104 and SCORING_INFO.offset_of("avgPathWetness") == 332
assert VEH_SCORING.offset_of("place") == 199 and VEH_SCORING.offset_of("pitLapDist") == 524
assert WHEEL.offset_of("tireCarcassTemperature") == 204

TELEM_STRIDE = 1888
WHEELS_OFFSET = 848
TELEM_BASE = 16
SCORING_BASE = 560
TELEM_MAP_SIZE = 16 + 128 * TELEM_STRIDE
SCORING_MAP_SIZE = SCORING_BASE + 128 * 584
assert (TELEM_MAP_SIZE, SCORING_MAP_SIZE) == (241680, 75312)

# LMU_Data
LMU_SIZE = 324824
LMU_SCORING_INFO = 1632
LMU_VEH_SCORING = 2192
LMU_TELEMETRY = 128464
LMU_TELEM_INFO = 128468
LMU_MAX_VEHICLES = 104

MAP_TELEMETRY = "$rFactor2SMMP_Telemetry$"
MAP_SCORING = "$rFactor2SMMP_Scoring$"
MAP_LMU = "LMU_Data"

KELVIN = 273.15

BASE_CAPS = frozenset({
    Capability.POSITION, Capability.LAP_TIMES, Capability.SECTOR_TIMES, Capability.LAP_DISTANCE,
    Capability.OPPONENTS, Capability.OPPONENT_LAP_TIMES, Capability.OPPONENT_PIT, Capability.NATIVE_GAPS,
    Capability.FUEL, Capability.TYRE_TEMPS, Capability.TYRE_PRESSURE, Capability.TYRE_WEAR,
    Capability.TYRE_COMPOUND, Capability.BRAKE_TEMPS, Capability.DAMAGE, Capability.INPUTS, Capability.YAW_RATE,
    Capability.TRACK_POSITION_XY, Capability.FLAGS, Capability.SECTOR_FLAGS, Capability.SAFETY_CAR,
    Capability.PIT_STATUS, Capability.WEATHER, Capability.SESSION_TIME, Capability.TOTAL_LAPS,
    Capability.START_LIGHTS,
})
SCORING_ONLY_CAPS = frozenset(BASE_CAPS - {
    Capability.FUEL, Capability.TYRE_TEMPS, Capability.TYRE_PRESSURE, Capability.TYRE_WEAR,
    Capability.TYRE_COMPOUND, Capability.BRAKE_TEMPS, Capability.DAMAGE, Capability.INPUTS, Capability.YAW_RATE,
})


def _u32x2(data: bytes) -> tuple[int, int]:
    return struct.unpack_from("<II", data, 0)  # type: ignore[return-value]


# ------------------------------------------------------------------ Splitter


class RF2Splitter:
    """Rohe Map-Schnappschüsse → kompakte Kanäle ``rf2_scoring`` / ``rf2_telemetry``."""

    def __init__(self) -> None:
        self.player_id: int | None = None
        self.last_scoring_key: bytes | None = None
        self.last_telem_key: bytes | None = None
        self.lmu_seen_t = -1e9
        self.torn = 0

    def split(self, pkt: RawPacket) -> list[RawPacket]:
        ch, data, t = pkt.channel, pkt.data, pkt.t
        if ch == "lmu_data":
            self.lmu_seen_t = t
            return self._split_lmu(data, t)
        if t - self.lmu_seen_t < 2.0:
            return []  # LMU_Data hat Vorrang vor dem Plugin
        if ch == "rf2_scoring_raw":
            return self._scoring_from_plugin(data, t)
        if ch == "rf2_telemetry_raw":
            return self._telemetry_from_plugin(data, t)
        return []

    # rF2-Plugin
    def _scoring_from_plugin(self, data: bytes, t: float) -> list[RawPacket]:
        if len(data) < SCORING_BASE:
            return []
        b, e = _u32x2(data)
        if b != e:
            self.torn += 1
            return []
        n = struct.unpack_from("<i", data, 12 + 104)[0]
        if not 0 <= n <= 128:
            return []
        size = SCORING_BASE + n * 584
        if len(data) < size:
            return []
        key = data[:8] + data[12 + 68:12 + 76]
        if key == self.last_scoring_key:
            return []
        self.last_scoring_key = key
        out = data[:size]
        self._remember_player(out, n)
        return [RawPacket("rf2_scoring", bytes(out), t)]

    def _telemetry_from_plugin(self, data: bytes, t: float) -> list[RawPacket]:
        if len(data) < TELEM_BASE:
            return []
        b, e = _u32x2(data)
        if b != e:
            self.torn += 1
            return []
        n = struct.unpack_from("<i", data, 12)[0]
        if not 0 <= n <= 128 or len(data) < TELEM_BASE + n * TELEM_STRIDE:
            return []
        blocks = [data[TELEM_BASE + i * TELEM_STRIDE:TELEM_BASE + (i + 1) * TELEM_STRIDE] for i in range(n)]
        return self._emit_telem(blocks, data[:8], t)

    def _emit_telem(self, blocks: list[bytes], version: bytes, t: float) -> list[RawPacket]:
        pick = None
        if self.player_id is not None:
            pick = next((blk for blk in blocks if struct.unpack_from("<i", blk)[0] == self.player_id), None)
        if pick is None:
            if not blocks:
                return []
            pick = blocks[0] if len(blocks) == 1 else None
            if pick is None:
                return []
        key = pick[:20]  # mID + deltaTime + elapsedTime
        if key == self.last_telem_key:
            return []
        self.last_telem_key = key
        out = version + struct.pack("<ii", TELEM_STRIDE + 4, 1) + pick
        return [RawPacket("rf2_telemetry", out, t)]

    def _remember_player(self, scoring: bytes, n: int) -> None:
        for i in range(n):
            off = SCORING_BASE + i * 584
            if scoring[off + 196]:  # mIsPlayer
                self.player_id = struct.unpack_from("<i", scoring, off)[0]
                return

    # LMU_Data
    def _split_lmu(self, data: bytes, t: float) -> list[RawPacket]:
        if len(data) < LMU_TELEM_INFO + TELEM_STRIDE:
            return []
        out: list[RawPacket] = []
        n = struct.unpack_from("<i", data, LMU_SCORING_INFO + 104)[0]
        if 0 <= n <= LMU_MAX_VEHICLES:
            key = data[LMU_SCORING_INFO + 68:LMU_SCORING_INFO + 76] + struct.pack("<i", n)
            if key != self.last_scoring_key:
                self.last_scoring_key = key
                scoring = (struct.pack("<IIi", 0, 0, 548 + n * 584) +
                           data[LMU_SCORING_INFO:LMU_SCORING_INFO + 548] +
                           data[LMU_VEH_SCORING:LMU_VEH_SCORING + n * 584])
                self._remember_player(scoring, n)
                out.append(RawPacket("rf2_scoring", scoring, t))
        active, player_idx, has_vehicle = struct.unpack_from("<BB?", data, LMU_TELEMETRY)
        if has_vehicle and active:
            blocks = [data[LMU_TELEM_INFO + i * TELEM_STRIDE:LMU_TELEM_INFO + (i + 1) * TELEM_STRIDE]
                      for i in range(min(active, LMU_MAX_VEHICLES))]
            if self.player_id is None and player_idx < len(blocks):
                blocks = [blocks[player_idx]]
            out.extend(self._emit_telem(blocks, b"\0" * 8, t))
        return out


class RF2Source(RawSource):
    def __init__(self, inner: ShmSource):
        self.inner = inner
        self.splitter = RF2Splitter()

    async def start(self) -> None:
        await self.inner.start()

    async def stop(self) -> None:
        await self.inner.stop()

    async def packets(self) -> AsyncIterator[RawPacket]:  # type: ignore[override]
        async for pkt in self.inner.packets():
            for out in self.splitter.split(pkt):
                yield out

    def info(self) -> dict[str, object]:
        return {**self.inner.info(), "player_id": self.splitter.player_id, "torn_reads": self.splitter.torn}


# ------------------------------------------------------------------ Decoder

SESSION_TYPES = {0: SessionType.PRACTICE, 9: SessionType.WARMUP}
for _i in range(1, 5):
    SESSION_TYPES[_i] = SessionType.PRACTICE
    SESSION_TYPES[_i + 4] = SessionType.QUALIFYING
    SESSION_TYPES[_i + 9] = SessionType.RACE

PHASES = {0: SessionPhase.GARAGE, 1: SessionPhase.FORMATION, 2: SessionPhase.GRID, 3: SessionPhase.FORMATION,
          4: SessionPhase.GRID, 5: SessionPhase.RUNNING, 6: SessionPhase.RUNNING, 7: SessionPhase.RUNNING,
          8: SessionPhase.CHECKERED, 9: SessionPhase.PAUSED}

_NUMBER = re.compile(r"#\s*(\d{1,3})\b")


def _vehicle_class(cls: str, name: str) -> VehicleClass:
    s = f"{cls} {name}".lower()
    if any(k in s for k in ("hyper", "lmh", "lmdh", "lmp", "gtp", "dpi")):
        return VehicleClass.PROTOTYPE
    if any(k in s for k in ("gt3", "gte", "gt2", "gt4", "lmgt")):
        return VehicleClass.GT
    if any(k in s for k in ("formula", "f1", "f2", "f3", "indy", "fr3.5", "usf", "open wheel")):
        return VehicleClass.FORMULA
    if any(k in s for k in ("tcr", "touring", "dtm", "btcc")):
        return VehicleClass.TOURING
    return VehicleClass.UNKNOWN


LMU_COMPOUND = {0: "soft", 1: "medium", 2: "hard", 3: "wet"}


class RF2Decoder(Decoder):
    def __init__(self, lmu: bool = False) -> None:
        super().__init__()
        self.lmu = lmu
        self.sim = SimId.LMU if lmu else SimId.RF2
        self.info: Record | None = None
        self.vehicles: list[Record] = []
        self.scoring_t = -1e9
        self.telem: Record | None = None
        self.wheels: list[Record] = []
        self.telem_t = -1e9
        self.session_n = 0
        self.prev_session: tuple[int, str] | None = None
        self.prev_et = 0.0
        self.sector_times: dict[int, list[float | None]] = {}

    def feed(self, packet: RawPacket) -> TelemetryFrame | None:
        if packet.channel in ("lmu_data", "rf2_scoring_raw", "rf2_telemetry_raw"):
            if not hasattr(self, "_splitter"):
                self._splitter = RF2Splitter()
            frame = None
            for sub in self._splitter.split(packet):
                frame = self.feed(sub) or frame
            return frame
        d = packet.data
        if packet.channel == "rf2_scoring":
            if len(d) < SCORING_BASE or _u32x2(d)[0] != _u32x2(d)[1]:
                return None
            info = SCORING_INFO.unpack(d, 12)
            n = max(0, min(info.numVehicles, (len(d) - SCORING_BASE) // 584))
            self.info = info
            self.vehicles = [VEH_SCORING.unpack(d, SCORING_BASE + i * 584) for i in range(n)]
            self.scoring_t = packet.t
            key = (info.session, cstr(info.trackName))
            if self.prev_session is not None and (key != self.prev_session or info.currentET < self.prev_et - 5):
                self.session_n += 1
            self.prev_session = key
            self.prev_et = info.currentET
            if packet.t - self.telem_t > 1.0:
                return self._frame(packet.t)  # ohne Telemetrie (z. B. nur Scoring verfügbar)
            return None
        if packet.channel == "rf2_telemetry":
            if len(d) < TELEM_BASE + TELEM_STRIDE or _u32x2(d)[0] != _u32x2(d)[1]:
                return None
            self.telem = VEH_TELEM.unpack(d, TELEM_BASE)
            self.wheels = [WHEEL.unpack(d, TELEM_BASE + WHEELS_OFFSET + j * 260) for j in range(4)]
            self.telem_t = packet.t
            if self.info is None:
                return None
            return self._frame(packet.t)
        return None

    # ------------------------------------------------------------- Frame
    def _frame(self, t: float) -> TelemetryFrame:
        info = self.info
        assert info is not None
        length = info.lapDist if info.lapDist > 0 else None
        stype = SESSION_TYPES.get(info.session, SessionType.UNKNOWN)
        gp = info.gamePhase
        phase = PHASES.get(gp, SessionPhase.UNKNOWN)
        if not info.inRealtime and gp != 9:
            phase = SessionPhase.GARAGE if gp in (0, 5, 6) else phase
        yellow = info.yellowFlagState
        sc = SafetyCar.NONE
        if gp == 6:
            sc = SafetyCar.ENDING if yellow in (5, 6) else SafetyCar.SC
        # mSectorFlag: Index 0 = Sektor 3 (wie mSector) – UNVERIFIED
        yellow_sectors = sorted({(3 if i == 0 else i) for i, v in enumerate(info.sectorFlag) if v})
        flag = Flag.GREEN if gp == 5 else Flag.NONE
        if yellow_sectors or gp == 6:
            flag = Flag.YELLOW
        if gp == 7:
            flag = Flag.RED
        if gp == 8:
            flag = Flag.CHEQUERED
        cars: list[CarState] = []
        player_idx: int | None = None
        for v in self.vehicles:
            car = self._car(v, length)
            if car.is_player:
                player_idx = car.idx
                if car.status is CarStatus.ACTIVE and v.flag == 6 and flag in (Flag.GREEN, Flag.NONE):
                    flag = Flag.BLUE
            cars.append(car)
        if player_idx is None and self.telem is not None:
            player_idx = self.telem.id
        if player_idx is None:
            player_idx = cars[0].idx if cars else 0
        me = next((c for c in cars if c.idx == player_idx), None)
        if me is None:
            me = CarState(idx=player_idx, is_player=True)
            cars.insert(0, me)
        telem_fresh = self.telem is not None and t - self.telem_t < 1.0 and self.telem.id == player_idx
        player = self._player() if telem_fresh else PlayerState(speed_ms=me.speed_ms or 0.0)
        if telem_fresh and self.telem is not None:
            me.speed_ms = player.speed_ms
            me.tyre_compound = player.tyre_compound
        max_laps = info.maxLaps
        total_laps = max_laps if (stype is SessionType.RACE and 0 < max_laps < 1_000_000) else None
        time_remaining: float | None = None
        if self.lmu and info.sessionTimeRemaining > 0:
            time_remaining = float(info.sessionTimeRemaining)
        elif info.endET > 0:
            time_remaining = max(0.0, info.endET - info.currentET)
        if total_laps is not None:
            time_remaining = None
        raining = info.raining
        wet = info.avgPathWetness
        condition = "clear"
        if raining > 0.5:
            condition = "heavy_rain"
        elif raining > 0.1:
            condition = "light_rain"
        elif info.darkCloud > 0.5:
            condition = "overcast"
        elif info.darkCloud > 0.2:
            condition = "light_cloud"
        me_v = next((v for v in self.vehicles if v.id == player_idx), None)
        vclass = _vehicle_class(cstr(me_v.vehicleClass) if me_v else "",
                                cstr(me_v.vehicleName) if me_v else "")
        car_name = cstr(self.telem.vehicleName) if telem_fresh and self.telem is not None else (
            cstr(me_v.vehicleName) if me_v else "")
        prefix = "lmu" if self.lmu else "rf2"
        session = SessionInfo(
            sim=self.sim, track_name=cstr(info.trackName), track_id=cstr(info.trackName).lower().replace(" ", "_"),
            track_length_m=length, session_type=stype, phase=phase, session_uid=f"{prefix}-{self.session_n}",
            total_laps=total_laps, duration_s=info.endET if info.endET > 0 else None,
            time_remaining_s=time_remaining, session_time_s=float(info.currentET), num_cars=len(cars),
            vehicle_class=vclass, car_name=car_name, flag=flag, safety_car=sc, yellow_sectors=yellow_sectors,
            weather=Weather(condition=condition, air_temp_c=info.ambientTemp, track_temp_c=info.trackTemp,
                            rain_intensity=raining, track_wetness=wet),
            start_lights=info.startLight if gp == 4 else None, paused=gp == 9,
        )
        caps = set(BASE_CAPS if telem_fresh else SCORING_ONLY_CAPS)
        extra: dict[str, Any] = {"game_phase": gp, "yellow_flag_state": yellow, "in_realtime": bool(info.inRealtime)}
        if telem_fresh and self.telem is not None:
            te = self.telem
            if te.rearFlapLegalStatus or te.rearFlapActivated:
                caps.add(Capability.DRS)
            if self.lmu:
                if te.electricBoostMotorState:
                    caps.add(Capability.ERS)
                extra["virtual_energy_pct"] = te.virtualEnergy * 100.0 if 0 <= te.virtualEnergy <= 1.0 else None
                extra["track_limits_steps"] = te.trackLimitsSteps
                extra["tc"] = te.tc
                extra["abs"] = te.abs
                extra["motor_map"] = te.motorMap
            extra["water_temp_c"] = te.engineWaterTemp
            extra["oil_temp_c"] = te.engineOilTemp
            extra["overheating"] = bool(te.overheating)
        return TelemetryFrame(seq=self.next_seq(), t=t, sim=self.sim, capabilities=frozenset(caps), session=session,
                              player=player, player_idx=player_idx, cars=cars, raw_extra=extra)

    def _car(self, v: Record, length: float | None) -> CarState:
        laps = max(0, int(v.totalLaps))
        lap_dist = float(v.lapDist)
        pct = (lap_dist / length) if length else None
        if pct is not None:
            pct = max(0.0, min(1.0, pct))
        sector = {0: 3, 1: 1, 2: 2}.get(int(v.sector))
        cur = [_t(v.curSector1), _diff(v.curSector2, v.curSector1), None]
        last = [_t(v.lastSector1), _diff(v.lastSector2, v.lastSector1), _diff(v.lastLapTime, v.lastSector2)]
        if v.inGarageStall:
            pit = PitState.GARAGE
        elif v.pitState == 3:
            pit = PitState.PIT_BOX
        elif v.inPits:
            pit = PitState.PIT_LANE
        else:
            pit = PitState.ON_TRACK
        status = {1: CarStatus.FINISHED, 2: CarStatus.DNF, 3: CarStatus.DSQ}.get(int(v.finishStatus),
                                                                                 CarStatus.ACTIVE)
        if v.control == -1 and status is CarStatus.ACTIVE:
            status = CarStatus.INACTIVE
        name = cstr(v.vehicleName)
        m = _NUMBER.search(name)
        vx, vy, vz = v.localVel
        gap_leader = float(v.timeBehindLeader) if v.lapsBehindLeader == 0 else None
        if v.place == 1:
            gap_leader = 0.0
        return CarState(
            idx=int(v.id), driver_name=cstr(v.driverName), team=cstr(v.pitGroup), car_number=m.group(1) if m else "",
            car_class=cstr(v.vehicleClass), position=int(v.place) or None, lap=laps + 1, laps_completed=laps,
            lap_distance_m=lap_dist if lap_dist >= 0 else None, lap_pct=pct,
            total_distance_m=(laps * length + lap_dist) if length else None, sector=sector,
            current_lap_s=_t(v.timeIntoLap), last_lap_s=_t(v.lastLapTime), best_lap_s=_t(v.bestLapTime),
            sector_times_s=cur, last_sector_times_s=last, gap_to_leader_s=gap_leader,
            gap_to_ahead_s=float(v.timeBehindNext) if v.lapsBehindNext == 0 and v.place > 1 else None,
            pit_state=pit, pit_stops=int(v.numPitstops), status=status, is_player=bool(v.isPlayer),
            x=float(v.pos[0]), z=float(v.pos[2]), speed_ms=math.sqrt(vx * vx + vy * vy + vz * vz),
        )

    def _player(self) -> PlayerState:
        te = self.telem
        assert te is not None
        vx, vy, vz = te.localVel
        speed = math.sqrt(vx * vx + vy * vy + vz * vz)
        wheels = []
        flat = False
        for j, w in enumerate(self.wheels):
            tl, tm, tr = (float(x) - KELVIN if x > 1 else None for x in w.temperature)
            # linke Räder (FL, RL): "links" = außen; rechte Räder: "links" = innen
            inner, outer = (tr, tl) if j in (0, 2) else (tl, tr)
            wear = float(w.wear)
            wheels.append(WheelState(
                temp_surface_c=tm, temp_inner_c=inner, temp_middle_c=tm, temp_outer_c=outer,
                temp_core_c=float(w.tireCarcassTemperature) - KELVIN if w.tireCarcassTemperature > 1 else None,
                pressure_kpa=float(w.pressure) or None,
                wear_pct=max(0.0, min(100.0, (1.0 - wear) * 100.0)) if 0.0 < wear <= 1.0 else None,
                brake_temp_c=float(w.brakeTemp) or None, puncture=bool(w.flat),
            ))
            flat = flat or bool(w.flat)
        compound = normalize_compound(cstr(te.frontTireCompoundName))
        if self.lmu and self.wheels and self.wheels[0].compoundType in LMU_COMPOUND:
            compound = LMU_COMPOUND[self.wheels[0].compoundType]
        dents = list(te.dentSeverity)
        dent_pct = max(dents) * 50.0 if dents else 0.0
        damage = Damage(body=dent_pct, aero=100.0 if te.detached else dent_pct,
                        engine=60.0 if te.overheating else 0.0)
        max_rpm = float(te.engineMaxRPM) or None
        ers = None
        if self.lmu and te.electricBoostMotorState and 0 <= te.batteryChargeFraction <= 1:
            ers = te.batteryChargeFraction * 100.0
        return PlayerState(
            speed_ms=speed, gear=int(te.gear), rpm=float(te.engineRPM), max_rpm=max_rpm,
            throttle=float(te.unfilteredThrottle), brake=float(te.unfilteredBrake),
            clutch=float(te.unfilteredClutch), steering=max(-1.0, min(1.0, float(te.unfilteredSteering))),
            fuel_l=float(te.fuel), fuel_capacity_l=float(te.fuelCapacity) or None, wheels=wheels,
            tyre_compound=compound, damage=damage, pit_limiter=bool(te.speedLimiter),
            drs_available=te.rearFlapLegalStatus == 2, drs_active=bool(te.rearFlapActivated),
            abs_active=bool(te.absActive) if self.lmu else False, tc_active=bool(te.tcActive) if self.lmu else False,
            yaw_rate=float(te.localRot[1]), lat_accel=float(te.localAccel[0]), long_accel=-float(te.localAccel[2]),
            lap_invalid=bool(te.lapInvalidated) if self.lmu else False, ers_store_pct=ers,
        )


def _t(v: float) -> float | None:
    return float(v) if v > 0 else None


def _diff(a: float, b: float) -> float | None:
    return float(a - b) if a > 0 and b > 0 and a > b else None


# ------------------------------------------------------------------ Adapter


def rf2_maps(hz: float) -> list[MapSpec]:
    return [MapSpec(MAP_TELEMETRY, "rf2_telemetry_raw", TELEM_MAP_SIZE, hz),
            MapSpec(MAP_SCORING, "rf2_scoring_raw", SCORING_MAP_SIZE, min(hz, 10.0))]


class RFactor2Adapter(TransportAdapter):
    sim_id = SimId.RF2
    key = "rfactor2"
    display_name = "rFactor 2"
    capabilities = BASE_CAPS
    lmu = False

    def __init__(self, config: Config):
        sec = config.section(self.key)
        shm_cfg = config.section("shm")
        hz = float(sec.get("poll_hz", 30))
        maps = rf2_maps(hz)
        if self.lmu:
            maps = [MapSpec(MAP_LMU, "lmu_data", LMU_SIZE, hz), *(MapSpec(m.name, m.channel, m.size, m.hz, True)
                                                                    for m in maps)]
        inner = ShmSource(maps, hz, str(shm_cfg.get("mirror_dir", "/dev/shm")), str(shm_cfg.get("prefix", "pitwall-")))
        super().__init__(config, RF2Source(inner), RF2Decoder(lmu=self.lmu))
        self.hint = ("Keine Daten von rFactor 2. Ist rFactor2SharedMemoryMapPlugin64.dll installiert und in "
                     "CustomPluginVariables.JSON aktiviert? Linux/Proton: pitwall-relay.exe --sim rf2 "
                     "(docs/setup-rfactor2-lmu.md).")



class LMUAdapter(RFactor2Adapter):
    sim_id = SimId.LMU
    key = "lmu"
    display_name = "Le Mans Ultimate"
    lmu = True

    def __init__(self, config: Config):
        super().__init__(config)
        self.hint = ("Keine Daten von Le Mans Ultimate. In LMU: Einstellungen → Gameplay → 'Enable Plugins' an und "
                     "Spiel neu starten. Linux/Proton: pitwall-relay.exe --sim lmu (docs/setup-rfactor2-lmu.md).")
