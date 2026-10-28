"""Assetto Corsa (2014): Shared Memory + UDP-Remote-Telemetrie (Port 9996) + optionale In-Game-App.

* Shared Memory (``acpmf_physics/graphics/static``): reich an Daten fürs eigene Auto.
  Unter Linux/Proton über ``pitwall-relay.exe`` (docs/linux-proton.md).
* UDP 9996: funktioniert sofort, auch unter Linux ohne Bridge, aber nur Basisdaten
  (kein Sprit, keine Reifentemperaturen).
* AC liefert über beide Wege **keine Gegner**. Die mitgelieferte In-Game-App
  ``PitWallAC`` (``pitwall/bridge/ac_app``) exportiert alle Autos per UDP an
  127.0.0.1:20791 – dann gibt es Gaps, Positionen und Verkehr.

Layouts: ``docs/protocols/assetto_corsa.md``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import struct
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from ..core.frame import (
    Capability,
    CarState,
    Damage,
    Flag,
    PitState,
    PlayerState,
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
from .shm import MapSpec, ShmSource, wstr

if TYPE_CHECKING:
    from ..config import Config

log = logging.getLogger(__name__)

PHYSICS = Layout("<", [
    ("packetId", "i"), ("gas", "f"), ("brake", "f"), ("fuel", "f"), ("gear", "i"), ("rpms", "i"),
    ("steerAngle", "f"), ("speedKmh", "f"), ("velocity", "3f"), ("accG", "3f"), ("wheelSlip", "4f"),
    ("wheelLoad", "4f"), ("wheelsPressure", "4f"), ("wheelAngularSpeed", "4f"), ("tyreWear", "4f"),
    ("tyreDirtyLevel", "4f"), ("tyreCoreTemperature", "4f"), ("camberRAD", "4f"), ("suspensionTravel", "4f"),
    ("drs", "f"), ("tc", "f"), ("heading", "f"), ("pitch", "f"), ("roll", "f"), ("cgHeight", "f"),
    ("carDamage", "5f"), ("numberOfTyresOut", "i"), ("pitLimiterOn", "i"), ("abs", "f"), ("kersCharge", "f"),
    ("kersInput", "f"), ("autoShifterOn", "i"), ("rideHeight", "2f"), ("turboBoost", "f"), ("ballast", "f"),
    ("airDensity", "f"), ("airTemp", "f"), ("roadTemp", "f"), ("localAngularVel", "3f"), ("finalFF", "f"),
    ("performanceMeter", "f"), ("engineBrake", "i"), ("ersRecoveryLevel", "i"), ("ersPowerLevel", "i"),
    ("ersHeatCharging", "i"), ("ersIsCharging", "i"), ("kersCurrentKJ", "f"), ("drsAvailable", "i"),
    ("drsEnabled", "i"), ("brakeTemp", "4f"), ("clutch", "f"), ("tyreTempI", "4f"), ("tyreTempM", "4f"),
    ("tyreTempO", "4f"), ("isAIControlled", "i"), ("tyreContactPoint", "12f"), ("tyreContactNormal", "12f"),
    ("tyreContactHeading", "12f"), ("brakeBias", "f"), ("localVelocity", "3f"),
])
GRAPHICS = Layout("<", [
    ("packetId", "i"), ("status", "i"), ("session", "i"), ("currentTime", "30s"), ("lastTime", "30s"),
    ("bestTime", "30s"), ("split", "30s"), ("completedLaps", "i"), ("position", "i"), ("iCurrentTime", "i"),
    ("iLastTime", "i"), ("iBestTime", "i"), ("sessionTimeLeft", "f"), ("distanceTraveled", "f"), ("isInPit", "i"),
    ("currentSectorIndex", "i"), ("lastSectorTime", "i"), ("numberOfLaps", "i"), ("tyreCompound", "66s"),
    ("_pad", "2x"), ("replayTimeMultiplier", "f"), ("normalizedCarPosition", "f"), ("carCoordinates", "3f"),
    ("penaltyTime", "f"), ("flag", "i"), ("idealLineOn", "i"), ("isInPitLane", "i"), ("surfaceGrip", "f"),
    ("mandatoryPitDone", "i"), ("windSpeed", "f"), ("windDirection", "f"),
])


def _static_layout(config_len: int) -> Layout:
    return Layout("<", [
        ("smVersion", "30s"), ("acVersion", "30s"), ("numberOfSessions", "i"), ("numCars", "i"),
        ("carModel", "66s"), ("track", "66s"), ("playerName", "66s"), ("playerSurname", "66s"),
        ("playerNick", "66s"), ("_pad1", "2x"), ("sectorCount", "i"), ("maxTorque", "f"), ("maxPower", "f"),
        ("maxRpm", "i"), ("maxFuel", "f"), ("suspensionMaxTravel", "4f"), ("tyreRadius", "4f"),
        ("maxTurboBoost", "f"), ("deprecated_1", "f"), ("deprecated_2", "f"), ("penaltiesEnabled", "i"),
        ("aidFuelRate", "f"), ("aidTireRate", "f"), ("aidMechanicalDamage", "f"), ("aidAllowTyreBlankets", "i"),
        ("aidStability", "f"), ("aidAutoClutch", "i"), ("aidAutoBlip", "i"), ("hasDRS", "i"), ("hasERS", "i"),
        ("hasKERS", "i"), ("kersMaxJ", "f"), ("engineBrakeSettingsCount", "i"), ("ersPowerControllerCount", "i"),
        ("trackSPlineLength", "f"), ("trackConfiguration", f"{config_len * 2}s"), ("_pad2", "2x"),
        ("ersMaxJ", "f"), ("isTimedRace", "i"), ("hasExtraLap", "i"), ("carSkin", "66s"), ("_pad3", "2x"),
        ("reversedGridPositions", "i"), ("PitWindowStart", "i"), ("PitWindowEnd", "i"),
    ])


STATIC_33 = _static_layout(33)
STATIC_15 = _static_layout(15)
assert (PHYSICS.size, GRAPHICS.size, STATIC_33.size, STATIC_15.size) == (580, 296, 684, 648)
assert PHYSICS.offset_of("brakeTemp") == 348 and PHYSICS.offset_of("localVelocity") == 568
assert GRAPHICS.offset_of("carCoordinates") == 252 and STATIC_33.offset_of("ersMaxJ") == 592

HANDSHAKER = struct.Struct("<iii")
HANDSHAKE_RESPONSE = struct.Struct("<100s100sii100s100s")
RTCARINFO = Layout("<", [
    ("identifier", "c"), ("_pad", "3x"), ("size", "i"), ("speed_Kmh", "f"), ("speed_Mph", "f"), ("speed_Ms", "f"),
    ("isAbsEnabled", "?"), ("isAbsInAction", "?"), ("isTcInAction", "?"), ("isTcEnabled", "?"), ("isInPit", "?"),
    ("isEngineLimiterOn", "?"), ("_pad2", "2x"), ("accG_vertical", "f"), ("accG_horizontal", "f"),
    ("accG_frontal", "f"), ("lapTime", "i"), ("lastLap", "i"), ("bestLap", "i"), ("lapCount", "i"), ("gas", "f"),
    ("brake", "f"), ("clutch", "f"), ("engineRPM", "f"), ("steer", "f"), ("gear", "i"), ("cgHeight", "f"),
    ("wheelAngularSpeed", "4f"), ("slipAngle", "4f"), ("slipAngle_ContactPatch", "4f"), ("slipRatio", "4f"),
    ("tyreSlip", "4f"), ("ndSlip", "4f"), ("load", "4f"), ("Dy", "4f"), ("Mz", "4f"), ("tyreDirtyLevel", "4f"),
    ("camberRAD", "4f"), ("tyreRadius", "4f"), ("tyreLoadedRadius", "4f"), ("suspensionHeight", "4f"),
    ("carPositionNormalized", "f"), ("carSlope", "f"), ("carCoordinates", "3f"),
])
RTLAP = struct.Struct("<ii100s100si")
assert (HANDSHAKE_RESPONSE.size, RTCARINFO.size, RTLAP.size) == (408, 328, 212)

AC_SESSION = {-1: SessionType.UNKNOWN, 0: SessionType.PRACTICE, 1: SessionType.QUALIFYING, 2: SessionType.RACE,
              3: SessionType.HOTLAP, 4: SessionType.TIME_TRIAL, 5: SessionType.PRACTICE, 6: SessionType.PRACTICE}
AC_FLAG = {0: Flag.NONE, 1: Flag.BLUE, 2: Flag.YELLOW, 3: Flag.BLACK, 4: Flag.WHITE, 5: Flag.CHEQUERED,
           6: Flag.BLACK_WHITE}

BASE_CAPS = {Capability.POSITION, Capability.LAP_TIMES, Capability.LAP_DISTANCE, Capability.FUEL,
             Capability.TYRE_TEMPS, Capability.TYRE_PRESSURE, Capability.TYRE_WEAR, Capability.TYRE_COMPOUND,
             Capability.BRAKE_TEMPS, Capability.DAMAGE, Capability.INPUTS, Capability.WHEEL_SLIP,
             Capability.TRACK_POSITION_XY, Capability.FLAGS, Capability.PIT_STATUS, Capability.PENALTIES,
             Capability.WEATHER, Capability.TOTAL_LAPS, Capability.YAW_RATE, Capability.SECTOR_TIMES}
UDP_CAPS = {Capability.LAP_TIMES, Capability.LAP_DISTANCE, Capability.INPUTS, Capability.WHEEL_SLIP,
            Capability.TRACK_POSITION_XY, Capability.PIT_STATUS}
EXT_CAPS = {Capability.OPPONENTS, Capability.OPPONENT_LAP_TIMES, Capability.OPPONENT_PIT}

# AC: 'carDamage' ist ein Schadenswert pro Zone ohne feste Skala; ~100 gilt als schwer
_DAMAGE_SCALE = 1.0


def vehicle_class_from_model(model: str) -> VehicleClass:
    m = model.lower()
    if any(k in m for k in ("formula", "f1", "_sf", "f2004", "f138", "sf15", "sf70", "rss_formula", "tatuus", "f3",
                            "f2")):
        return VehicleClass.FORMULA
    if any(k in m for k in ("lmp", "lmh", "dpi", "gtp")):
        return VehicleClass.PROTOTYPE
    if any(k in m for k in ("gt3", "gt2", "gte", "gt4", "gtd", "gt1")):
        return VehicleClass.GT
    if any(k in m for k in ("dtm", "btcc", "tcr", "touring")):
        return VehicleClass.TOURING
    return VehicleClass.ROAD


class ACDecoder(Decoder):
    sim = SimId.AC

    def __init__(self, sim_id: SimId = SimId.AC) -> None:
        super().__init__()
        self.sim = sim_id
        self.physics: Record | None = None
        self.graphics: Record | None = None
        self.static: dict[str, Any] = {}
        self.static_raw_hash: bytes | None = None
        self.udp: Record | None = None
        self.udp_names: dict[str, str] = {}
        self.udp_t = 0.0
        self.shm_t = 0.0
        self.ext: dict[str, Any] | None = None
        self.ext_t = 0.0
        self.last_physics_id = -1
        self.session_n = 0
        self.prev_session_type: int | None = None
        self.prev_laps: int | None = None
        self.last_sector_index: int | None = None
        self.sector_times: list[float | None] = [None, None, None]
        self.last_sector_times: list[float | None] = []

    # ---------------------------------------------------------- Kanäle
    def feed(self, packet: RawPacket) -> TelemetryFrame | None:
        ch = packet.channel
        if ch == "physics":
            if len(packet.data) >= PHYSICS.size:
                self.physics = PHYSICS.unpack(packet.data)
                self.shm_t = packet.t
            return None
        if ch == "static":
            if len(packet.data) >= STATIC_15.size and packet.data[:120] != self.static_raw_hash:
                self.static_raw_hash = packet.data[:120]
                self.static = _parse_static(packet.data)
            return None
        if ch == "graphics":
            if len(packet.data) < GRAPHICS.size:
                return None
            self.graphics = GRAPHICS.unpack(packet.data)
            self.shm_t = packet.t
            if self.physics is None:
                return None
            return self._frame_shm(packet.t)
        if ch == "udp":
            return self._feed_udp(packet)
        if ch == "acext":
            with contextlib.suppress(ValueError):
                self.ext = json.loads(packet.data)
                self.ext_t = packet.t
            return None
        return None

    def _feed_udp(self, packet: RawPacket) -> TelemetryFrame | None:
        d = packet.data
        if len(d) == HANDSHAKE_RESPONSE.size:
            car, driver, _ident, _ver, track, config = HANDSHAKE_RESPONSE.unpack(d)
            self.udp_names = {"car": _ac_str(car), "driver": _ac_str(driver), "track": _ac_str(track),
                              "config": _ac_str(config)}
            return None
        if len(d) == RTCARINFO.size:
            self.udp = RTCARINFO.unpack(d)
            self.udp_t = packet.t
            if packet.t - self.shm_t < 1.0:
                return None  # Shared Memory ist reichhaltiger und aktiv
            return self._frame_udp(packet.t)
        return None

    # ------------------------------------------------------------ Frames
    def _ext_cars(self, length: float | None, me: CarState) -> list[CarState]:
        if self.ext is None or not self.ext.get("cars"):
            return []
        cars = []
        for c in self.ext["cars"]:
            if not c.get("connected", True):
                continue
            idx = int(c.get("id", 0))
            if idx == int(self.ext.get("focus", 0)):
                continue
            spline = float(c.get("spline", 0.0))
            laps = int(c.get("laps", 0))
            cars.append(CarState(
                idx=idx, driver_name=str(c.get("name", "")), position=c.get("pos") or None,
                lap=laps + 1, laps_completed=laps, lap_pct=spline,
                lap_distance_m=spline * length if length else None,
                total_distance_m=(laps + spline) * length if length else None,
                last_lap_s=(c.get("last") or 0) / 1000.0 or None, best_lap_s=(c.get("best") or 0) / 1000.0 or None,
                pit_state=PitState.PIT_LANE if c.get("pit") else PitState.ON_TRACK,
                x=c.get("x"), z=c.get("z"), speed_ms=c.get("speed"),
            ))
        if cars:
            focus = int(self.ext.get("focus", 0))
            me.idx = focus
        return cars

    def _frame_shm(self, t: float) -> TelemetryFrame:
        p = self.physics
        g = self.graphics
        assert p is not None and g is not None
        st = self.static
        status = g.status
        stype = AC_SESSION.get(g.session, SessionType.UNKNOWN)
        if self.prev_session_type is not None and g.session != self.prev_session_type:
            self.session_n += 1
        self.prev_session_type = g.session
        length = st.get("spline_length") or None
        completed = g.completedLaps
        pos = g.position or None
        cur_ms, last_ms, best_ms = g.iCurrentTime, g.iLastTime, g.iBestTime
        in_pit, sector_idx, last_sector_ms, num_laps = g.isInPit, g.currentSectorIndex, g.lastSectorTime, g.numberOfLaps
        compound = normalize_compound(wstr(g.tyreCompound))
        spline = g.normalizedCarPosition
        cx, _cy, cz = g.carCoordinates
        penalty, flag = g.penaltyTime, g.flag
        in_pitlane = g.isInPitLane
        # Sektorzeiten aus lastSectorTime beim Sektorwechsel
        if self.last_sector_index is not None and sector_idx != self.last_sector_index:
            prev = self.last_sector_index
            if 0 <= prev < 3:
                self.sector_times[prev] = last_sector_ms / 1000.0 if last_sector_ms > 0 else None
            if sector_idx == 0:
                self.last_sector_times = list(self.sector_times)
                self.sector_times = [None, None, None]
        self.last_sector_index = sector_idx
        phase = {0: SessionPhase.MENU, 1: SessionPhase.RUNNING, 2: SessionPhase.RUNNING, 3: SessionPhase.PAUSED}.get(
            status, SessionPhase.UNKNOWN)
        speed = p.speedKmh / 3.6
        if stype is SessionType.RACE and completed == 0 and cur_ms <= 0 and status == 2:
            phase = SessionPhase.GRID
        if flag == 5:
            phase = SessionPhase.CHECKERED
        me = CarState(
            idx=0, driver_name=st.get("player") or "Du", is_player=True, position=pos, lap=completed + 1,
            laps_completed=completed, lap_distance_m=spline * length if length else None, lap_pct=spline,
            total_distance_m=(completed + spline) * length if length else None, sector=sector_idx + 1,
            current_lap_s=cur_ms / 1000.0 if cur_ms > 0 else None, last_lap_s=last_ms / 1000.0 if last_ms > 0 else None,
            best_lap_s=best_ms / 1000.0 if best_ms > 0 else None, sector_times_s=list(self.sector_times),
            last_sector_times_s=self.last_sector_times,
            pit_state=PitState.PIT_BOX if in_pit else PitState.PIT_LANE if in_pitlane else PitState.ON_TRACK,
            tyre_compound=compound, x=cx, z=cz, speed_ms=speed, penalties_s=float(penalty),
        )
        cars = [me, *self._ext_cars(length, me)] if t - self.ext_t < 3.0 else [me]
        player = _player_from_physics(p, st)
        player.tyre_compound = compound
        caps = set(BASE_CAPS)
        if len(cars) > 1:
            caps |= EXT_CAPS
        if st.get("has_drs"):
            caps.add(Capability.DRS)
        if st.get("has_ers") or st.get("has_kers"):
            caps.add(Capability.ERS)
        if not st.get("timed_race"):
            caps.add(Capability.TOTAL_LAPS)
        time_left = g.sessionTimeLeft / 1000.0 if g.sessionTimeLeft > 0 else None
        session = SessionInfo(
            sim=self.sim, track_name=_track_name(st), track_id=st.get("track", "") + (
                f"_{st['config']}" if st.get("config") else ""),
            track_length_m=length, session_type=stype, phase=phase, session_uid=f"ac-{self.session_n}",
            total_laps=num_laps if (num_laps and stype is SessionType.RACE and not st.get("timed_race")) else None,
            time_remaining_s=time_left if stype is not SessionType.RACE or st.get("timed_race") else None,
            num_cars=st.get("num_cars", 1), vehicle_class=vehicle_class_from_model(st.get("car", "")),
            car_name=st.get("car", ""), flag=AC_FLAG.get(flag, Flag.NONE),
            weather=Weather(condition="clear", air_temp_c=p.airTemp or None, track_temp_c=p.roadTemp or None),
            paused=status == 3,
        )
        return TelemetryFrame(seq=self.next_seq(), t=t, sim=self.sim, capabilities=frozenset(caps), session=session,
                              player=player, player_idx=me.idx, cars=cars,
                              raw_extra={"replay": status == 1, "mandatory_pit_done": bool(g.mandatoryPitDone)})

    def _frame_udp(self, t: float) -> TelemetryFrame:
        u = self.udp
        assert u is not None
        speed = u.speed_Ms
        lap_count = u.lapCount
        x, _y, z = u.carCoordinates
        me = CarState(idx=0, driver_name=self.udp_names.get("driver", "Du"), is_player=True, lap=lap_count + 1,
                      laps_completed=lap_count, lap_pct=u.carPositionNormalized,
                      current_lap_s=u.lapTime / 1000.0 if u.lapTime > 0 else None,
                      last_lap_s=u.lastLap / 1000.0 if u.lastLap > 0 else None,
                      best_lap_s=u.bestLap / 1000.0 if u.bestLap > 0 else None,
                      pit_state=PitState.PIT_LANE if u.isInPit else PitState.ON_TRACK, x=x, z=z, speed_ms=speed)
        player = PlayerState(speed_ms=speed, gear=_ac_gear(u.gear), rpm=u.engineRPM, throttle=u.gas, brake=u.brake,
                             clutch=u.clutch, steering=max(-1.0, min(1.0, u.steer)),
                             wheels=[WheelState(slip_ratio=max(-1.0, min(1.0, float(sr)))) for sr in u.slipRatio],
                             abs_active=bool(u.isAbsInAction), tc_active=bool(u.isTcInAction),
                             pit_limiter=bool(u.isEngineLimiterOn), lat_accel=u.accG_horizontal * 9.81,
                             long_accel=u.accG_frontal * 9.81)
        track = self.udp_names.get("track", "")
        session = SessionInfo(sim=self.sim, track_name=track,
                              track_id=track + (f"_{self.udp_names['config']}" if self.udp_names.get("config") else ""),
                              session_type=SessionType.UNKNOWN, phase=SessionPhase.RUNNING,
                              session_uid="ac-udp", car_name=self.udp_names.get("car", ""),
                              vehicle_class=vehicle_class_from_model(self.udp_names.get("car", "")))
        return TelemetryFrame(seq=self.next_seq(), t=t, sim=self.sim, capabilities=frozenset(UDP_CAPS),
                              session=session, player=player, player_idx=0, cars=[me])


def _ac_gear(g: int) -> int:
    return -1 if g == 0 else g - 1


def _ac_str(raw: bytes) -> str:
    s = raw.decode("utf-16-le", errors="replace")
    for stop in ("\0", "%"):
        s = s.split(stop, 1)[0]
    return s.strip()


def _track_name(st: dict[str, Any]) -> str:
    name = st.get("track", "")
    pretty = name.replace("ks_", "").replace("_", " ").title()
    if st.get("config"):
        pretty += f" ({st['config'].replace('_', ' ')})"
    return pretty


def _parse_static(raw: bytes) -> dict[str, Any]:
    v = STATIC_33.unpack(raw) if len(raw) >= STATIC_33.size else None
    # Plausibilitätscheck für die umstrittene Länge von trackConfiguration ([33] vs. [15])
    if v is None or v.isTimedRace not in (0, 1) or v.hasExtraLap not in (0, 1):
        v = STATIC_15.unpack(raw)
    player = " ".join(x for x in (wstr(v.playerName), wstr(v.playerSurname)) if x) or wstr(v.playerNick)
    return {
        "sm_version": wstr(v.smVersion), "ac_version": wstr(v.acVersion), "num_cars": v.numCars,
        "car": wstr(v.carModel), "track": wstr(v.track), "player": player, "max_rpm": v.maxRpm,
        "max_fuel": v.maxFuel, "has_drs": bool(v.hasDRS), "has_ers": bool(v.hasERS), "has_kers": bool(v.hasKERS),
        "spline_length": v.trackSPlineLength, "config": wstr(v.trackConfiguration), "timed_race": bool(v.isTimedRace),
        "pit_window": (v.PitWindowStart, v.PitWindowEnd),
    }


def _player_from_physics(p: Record, st: dict[str, Any]) -> PlayerState:
    wheels = []
    for i in range(4):
        remaining = float(p.tyreWear[i])
        # AC meldet "Restgummi" ~100 (neu) bis ~94 (kaum noch Grip) → grob in % Verschleiß umrechnen
        wear_pct = max(0.0, min(100.0, (100.0 - remaining) / 6.0 * 100.0)) if remaining > 50 else None
        slip = float(p.wheelSlip[i])
        slip_ratio = None
        if slip > 0.25:  # wheelSlip ist ein Betrag → mit Pedalen in Blockieren/Wheelspin unterscheiden
            slip_ratio = -min(1.0, slip) if p.brake > 0.3 else min(1.0, slip)
        wheels.append(WheelState(
            temp_inner_c=float(p.tyreTempI[i]) or None, temp_middle_c=float(p.tyreTempM[i]) or None,
            temp_outer_c=float(p.tyreTempO[i]) or None, temp_core_c=float(p.tyreCoreTemperature[i]) or None,
            pressure_kpa=(float(p.wheelsPressure[i]) * 6.894757) or None, wear_pct=wear_pct,
            brake_temp_c=float(p.brakeTemp[i]) or None, slip_ratio=slip_ratio,
        ))
    dmg = p.carDamage
    damage = Damage(aero=min(100.0, max(float(dmg[0]), float(dmg[1])) * _DAMAGE_SCALE),
                    body=min(100.0, max(float(dmg[2]), float(dmg[3]), float(dmg[4])) * _DAMAGE_SCALE))
    has_ers = st.get("has_ers") or st.get("has_kers")
    return PlayerState(
        speed_ms=p.speedKmh / 3.6, gear=_ac_gear(p.gear), rpm=float(p.rpms),
        max_rpm=float(st.get("max_rpm") or 0) or None, throttle=p.gas, brake=p.brake, clutch=p.clutch,
        steering=max(-1.0, min(1.0, p.steerAngle)), fuel_l=p.fuel, fuel_capacity_l=st.get("max_fuel") or None,
        wheels=wheels, damage=damage, pit_limiter=bool(p.pitLimiterOn), drs_available=bool(p.drsAvailable),
        drs_active=bool(p.drsEnabled), ers_store_pct=p.kersCharge * 100.0 if has_ers else None,
        tc_active=p.tc > 0, abs_active=p.abs > 0, yaw_rate=float(p.localAngularVel[1]),
        lat_accel=p.accG[0] * 9.81, long_accel=p.accG[2] * 9.81, off_track=p.numberOfTyresOut > 2,
    )


# ------------------------------------------------------------------ Quellen

class ACUdpSource(RawSource):
    """Handshake + Subscribe an AC (Port 9996), liefert Kanal 'udp'."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9996):
        self.host = host
        self.port = port
        self.queue: asyncio.Queue[RawPacket | None] = asyncio.Queue(maxsize=1024)
        self.transport: asyncio.DatagramTransport | None = None
        self.last_rx: float | None = None
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        src = self

        class _P(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
                src.last_rx = time.monotonic()
                with contextlib.suppress(asyncio.QueueFull):
                    src.queue.put_nowait(RawPacket("udp", data, src.last_rx))

        transport, _ = await loop.create_datagram_endpoint(_P, local_addr=("0.0.0.0", 0))
        self.transport = transport  # type: ignore[assignment]
        self._task = asyncio.create_task(self._keepalive())

    async def _keepalive(self) -> None:
        while self.transport is not None:
            if self.last_rx is None or time.monotonic() - self.last_rx > 3.0:
                self.transport.sendto(HANDSHAKER.pack(1, 1, 0), (self.host, self.port))
                await asyncio.sleep(0.3)
                self.transport.sendto(HANDSHAKER.pack(1, 1, 1), (self.host, self.port))
            await asyncio.sleep(2.0)

    async def stop(self) -> None:
        if self.transport is not None:
            with contextlib.suppress(Exception):
                self.transport.sendto(HANDSHAKER.pack(1, 1, 3), (self.host, self.port))
            self.transport.close()
            self.transport = None
        if self._task:
            self._task.cancel()
        with contextlib.suppress(asyncio.QueueFull):
            self.queue.put_nowait(None)

    async def packets(self) -> AsyncIterator[RawPacket]:  # type: ignore[override]
        while True:
            pkt = await self.queue.get()
            if pkt is None:
                return
            yield pkt


class ExtAppSource(RawSource):
    """Empfängt die JSON-Daten der In-Game-App PitWallAC (alle Autos) auf 127.0.0.1:20791."""

    def __init__(self, port: int = 20791):
        from .base import UdpSource

        self.inner = UdpSource("127.0.0.1", port, "acext")

    async def start(self) -> None:
        try:
            await self.inner.start()
        except OSError as exc:
            log.info("AC-App-Port nicht verfügbar: %s", exc)

    async def stop(self) -> None:
        await self.inner.stop()

    def packets(self) -> AsyncIterator[RawPacket]:
        return self.inner.packets()


class MergedSource(RawSource):
    """Mehrere Quellen parallel zu einem Paketstrom zusammenführen."""

    def __init__(self, sources: list[RawSource]):
        self.sources = sources
        self.queue: asyncio.Queue[RawPacket | None] = asyncio.Queue(maxsize=4096)
        self._tasks: list[asyncio.Task[None]] = []
        self.started: list[RawSource] = []

    async def start(self) -> None:
        for s in self.sources:
            try:
                await s.start()
                self.started.append(s)
            except OSError as exc:
                log.warning("Quelle %s nicht gestartet: %s", type(s).__name__, exc)
        for s in self.started:
            self._tasks.append(asyncio.create_task(self._pump(s)))

    async def _pump(self, s: RawSource) -> None:
        async for pkt in s.packets():
            try:
                self.queue.put_nowait(pkt)
            except asyncio.QueueFull:
                pass

    async def stop(self) -> None:
        for s in self.started:
            with contextlib.suppress(Exception):
                await s.stop()
        for t in self._tasks:
            t.cancel()
        with contextlib.suppress(asyncio.QueueFull):
            self.queue.put_nowait(None)

    async def packets(self) -> AsyncIterator[RawPacket]:  # type: ignore[override]
        while True:
            pkt = await self.queue.get()
            if pkt is None:
                return
            yield pkt


def ac_maps(hz: float) -> list[MapSpec]:
    return [MapSpec("Local\\acpmf_physics", "physics", 4096, hz),
            MapSpec("Local\\acpmf_graphics", "graphics", 4096, hz),
            MapSpec("Local\\acpmf_static", "static", 4096, 2)]


class AssettoCorsaAdapter(TransportAdapter):
    sim_id = SimId.AC
    key = "assetto_corsa"
    display_name = "Assetto Corsa"
    capabilities = frozenset(BASE_CAPS)

    def __init__(self, config: Config):
        sec = config.section("assetto_corsa")
        shm_cfg = config.section("shm")
        mode = str(sec.get("mode", "auto"))
        hz = float(shm_cfg.get("poll_hz", 60))
        sources: list[RawSource] = []
        if mode in ("auto", "shm"):
            sources.append(ShmSource(ac_maps(hz), hz, str(shm_cfg.get("mirror_dir", "/dev/shm")),
                                     str(shm_cfg.get("prefix", "pitwall-"))))
        if mode in ("auto", "udp"):
            sources.append(ACUdpSource(str(sec.get("udp_host", "127.0.0.1")), int(sec.get("udp_port", 9996))))
        sources.append(ExtAppSource())
        super().__init__(config, MergedSource(sources), ACDecoder())
        self.hint = ("Keine Daten von Assetto Corsa. Linux/Proton: pitwall-relay.exe per Steam-Startoption starten "
                     "(siehe docs/linux-proton.md) – oder es läuft nur die UDP-Basistelemetrie (Port 9996). "
                     "Für Gegnerdaten die In-Game-App 'PitWallAC' aktivieren.")
