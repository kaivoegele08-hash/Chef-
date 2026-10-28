"""Assetto Corsa Competizione: Shared Memory (eigenes Auto) + Broadcasting-API (alle Autos).

* **Shared Memory** (``acpmf_physics/graphics/static``, ACC-Layout 800/1588/820 Bytes): Pedale, Reifen,
  Sprit, Flaggen, Wetter + Prognose. Unter Linux/Proton über ``pitwall-relay.exe --sim acc``.
* **Broadcasting-API** (UDP, ACC ist Server, Standardport 9000): Klassement, Rundenzeiten, Boxenstatus
  und Namen **aller** Autos. Funktioniert auch unter Proton ohne Bridge (127.0.0.1) und im LAN.
  Voraussetzung: ``Documents/Assetto Corsa Competizione/Config/broadcasting.json`` mit
  ``updListenerPort`` und ``connectionPassword`` (docs/setup-acc.md).

Ohne Shared Memory entstehen die Frames allein aus der Broadcasting-API (ohne Sprit/Reifen).
Protokolle: ``docs/protocols/acc.md`` (eigene Implementierung, kein Kunos-/AGPL-Code übernommen).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import struct
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..core.frame import (
    Capability,
    CarState,
    Damage,
    Flag,
    ForecastSample,
    GameEvent,
    GameEventKind,
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
from .assetto_corsa import PHYSICS, MergedSource, looks_like_acc
from .base import Decoder, RawPacket, RawSource, TransportAdapter
from .layout import Layout, Record
from .shm import MapSpec, ShmSource, wstr

if TYPE_CHECKING:
    from ..config import Config

log = logging.getLogger(__name__)

# ------------------------------------------------------------ Shared Memory

ACC_PHYSICS = Layout("<", [*PHYSICS.fields, ("P2PActivation", "i"), ("P2PStatus", "i"), ("currentMaxRpm", "f"),
                           ("mz", "4f"), ("fx", "4f"), ("fy", "4f"), ("slipRatio", "4f"), ("slipAngle", "4f"),
                           ("tcinAction", "i"), ("absInAction", "i"), ("suspensionDamage", "4f"), ("tyreTemp", "4f"),
                           ("waterTemp", "f"), ("brakePressure", "4f"), ("frontBrakeCompound", "i"),
                           ("rearBrakeCompound", "i"), ("padLife", "4f"), ("discLife", "4f"), ("ignitionOn", "i"),
                           ("starterEngineOn", "i"), ("isEngineRunning", "i"), ("kerbVibration", "f"),
                           ("slipVibrations", "f"), ("gVibrations", "f"), ("absVibrations", "f")])
ACC_GRAPHICS = Layout("<", [
    ("packetId", "i"), ("status", "i"), ("session", "i"), ("currentTime", "30s"), ("lastTime", "30s"),
    ("bestTime", "30s"), ("split", "30s"), ("completedLaps", "i"), ("position", "i"), ("iCurrentTime", "i"),
    ("iLastTime", "i"), ("iBestTime", "i"), ("sessionTimeLeft", "f"), ("distanceTraveled", "f"), ("isInPit", "i"),
    ("currentSectorIndex", "i"), ("lastSectorTime", "i"), ("numberOfLaps", "i"), ("tyreCompound", "66s"),
    ("_pad1", "2x"), ("replayTimeMultiplier", "f"), ("normalizedCarPosition", "f"), ("activeCars", "i"),
    ("carCoordinates", "180f"), ("carID", "60i"), ("playerCarID", "i"), ("penaltyTime", "f"), ("flag", "i"),
    ("penalty", "i"), ("idealLineOn", "i"), ("isInPitLane", "i"), ("surfaceGrip", "f"), ("mandatoryPitDone", "i"),
    ("windSpeed", "f"), ("windDirection", "f"), ("isSetupMenuVisible", "i"), ("mainDisplayIndex", "i"),
    ("secondaryDisplayIndex", "i"), ("TC", "i"), ("TCCUT", "i"), ("EngineMap", "i"), ("ABS", "i"),
    ("fuelXLap", "f"), ("rainLights", "i"), ("flashingLights", "i"), ("lightsStage", "i"),
    ("exhaustTemperature", "f"), ("wiperLV", "i"), ("driverStintTotalTimeLeft", "i"), ("driverStintTimeLeft", "i"),
    ("rainTyres", "i"), ("sessionIndex", "i"), ("usedFuel", "f"), ("deltaLapTime", "30s"), ("_pad2", "2x"),
    ("iDeltaLapTime", "i"), ("estimatedLapTime", "30s"), ("_pad3", "2x"), ("iEstimatedLapTime", "i"),
    ("isDeltaPositive", "i"), ("iSplit", "i"), ("isValidLap", "i"), ("fuelEstimatedLaps", "f"),
    ("trackStatus", "66s"), ("_pad4", "2x"), ("missingMandatoryPits", "i"), ("Clock", "f"),
    ("directionLightsLeft", "i"), ("directionLightsRight", "i"), ("GlobalYellow", "i"), ("GlobalYellow1", "i"),
    ("GlobalYellow2", "i"), ("GlobalYellow3", "i"), ("GlobalWhite", "i"), ("GlobalGreen", "i"),
    ("GlobalChequered", "i"), ("GlobalRed", "i"), ("mfdTyreSet", "i"), ("mfdFuelToAdd", "f"),
    ("mfdTyrePressureLF", "f"), ("mfdTyrePressureRF", "f"), ("mfdTyrePressureLR", "f"), ("mfdTyrePressureRR", "f"),
    ("trackGripStatus", "i"), ("rainIntensity", "i"), ("rainIntensityIn10min", "i"), ("rainIntensityIn30min", "i"),
    ("currentTyreSet", "i"), ("strategyTyreSet", "i"), ("gapAhead", "i"), ("gapBehind", "i"),
])


def _acc_static(config_len: int) -> Layout:
    return Layout("<", [
        ("smVersion", "30s"), ("acVersion", "30s"), ("numberOfSessions", "i"), ("numCars", "i"),
        ("carModel", "66s"), ("track", "66s"), ("playerName", "66s"), ("playerSurname", "66s"),
        ("playerNick", "66s"), ("_pad1", "2x"), ("sectorCount", "i"), ("maxTorque", "f"), ("maxPower", "f"),
        ("maxRpm", "i"), ("maxFuel", "f"), ("suspensionMaxTravel", "4f"), ("tyreRadius", "4f"),
        ("maxTurboBoost", "f"), ("deprecated_1", "f"), ("deprecated_2", "f"), ("penaltiesEnabled", "i"),
        ("aidFuelRate", "f"), ("aidTireRate", "f"), ("aidMechanicalDamage", "f"), ("AllowTyreBlankets", "f"),
        ("aidStability", "f"), ("aidAutoClutch", "i"), ("aidAutoBlip", "i"), ("hasDRS", "i"), ("hasERS", "i"),
        ("hasKERS", "i"), ("kersMaxJ", "f"), ("engineBrakeSettingsCount", "i"), ("ersPowerControllerCount", "i"),
        ("trackSplineLength", "f"), ("trackConfiguration", f"{config_len * 2}s"), ("_pad2", "2x"),
        ("ersMaxJ", "f"), ("isTimedRace", "i"), ("hasExtraLap", "i"), ("carSkin", "66s"), ("_pad3", "2x"),
        ("reversedGridPositions", "i"), ("PitWindowStart", "i"), ("PitWindowEnd", "i"), ("isOnline", "i"),
        ("dryTyresName", "66s"), ("wetTyresName", "66s"),
    ])


ACC_STATIC_33 = _acc_static(33)
ACC_STATIC_15 = _acc_static(15)
assert (ACC_PHYSICS.size, ACC_GRAPHICS.size, ACC_STATIC_33.size, ACC_STATIC_15.size) == (800, 1588, 820, 784)
assert ACC_PHYSICS.offset_of("tyreTemp") == 696 and ACC_PHYSICS.offset_of("padLife") == 740
assert ACC_GRAPHICS.offset_of("playerCarID") == 1216 and ACC_GRAPHICS.offset_of("fuelXLap") == 1284
assert ACC_GRAPHICS.offset_of("trackStatus") == 1416 and ACC_GRAPHICS.offset_of("GlobalYellow") == 1500
assert ACC_STATIC_33.offset_of("PitWindowStart") == 676 and ACC_STATIC_15.offset_of("PitWindowStart") == 640

SHM_SESSION = {-1: SessionType.UNKNOWN, 0: SessionType.PRACTICE, 1: SessionType.QUALIFYING, 2: SessionType.RACE,
               3: SessionType.HOTLAP, 4: SessionType.TIME_TRIAL, 5: SessionType.PRACTICE, 6: SessionType.PRACTICE,
               7: SessionType.HOTLAP, 8: SessionType.HOTLAP}
SHM_FLAG = {0: Flag.NONE, 1: Flag.BLUE, 2: Flag.YELLOW, 3: Flag.BLACK, 4: Flag.WHITE, 5: Flag.CHEQUERED,
            6: Flag.BLACK_WHITE, 7: Flag.GREEN, 8: Flag.MEATBALL}
RAIN = {0: "clear", 1: "light_rain", 2: "light_rain", 3: "heavy_rain", 4: "heavy_rain", 5: "storm"}
GRIP_WETNESS = {0: 0.0, 1: 0.0, 2: 0.0, 3: 0.1, 4: 0.3, 5: 0.6, 6: 1.0}
PSI_TO_KPA = 6.894757

# ------------------------------------------------------------ Broadcasting

PROTOCOL_VERSION = 4
MAXINT = 2147483647
# Inbound
MSG_REGISTRATION_RESULT, MSG_REALTIME_UPDATE, MSG_REALTIME_CAR_UPDATE = 1, 2, 3
MSG_ENTRY_LIST, MSG_TRACK_DATA, MSG_ENTRY_LIST_CAR, MSG_BROADCASTING_EVENT = 4, 5, 6, 7
# Outbound
OUT_REGISTER, OUT_UNREGISTER, OUT_REQUEST_ENTRY_LIST, OUT_REQUEST_TRACK_DATA = 1, 9, 10, 11

BC_SESSION = {0: SessionType.PRACTICE, 4: SessionType.QUALIFYING, 9: SessionType.QUALIFYING, 10: SessionType.RACE,
              11: SessionType.HOTLAP, 12: SessionType.HOTLAP, 13: SessionType.HOTLAP}
BC_PHASE = {0: SessionPhase.UNKNOWN, 1: SessionPhase.GRID, 2: SessionPhase.GRID, 3: SessionPhase.FORMATION,
            4: SessionPhase.GRID, 5: SessionPhase.RUNNING, 6: SessionPhase.CHECKERED, 7: SessionPhase.FINISHED,
            8: SessionPhase.FINISHED}
CUP = {0: "Pro", 1: "Pro-Am", 2: "Am", 3: "Silver", 4: "National"}


class _Reader:
    __slots__ = ("data", "o")

    def __init__(self, data: bytes, o: int = 0):
        self.data = data
        self.o = o

    def _u(self, fmt: str) -> Any:
        v = struct.unpack_from(fmt, self.data, self.o)[0]
        self.o += struct.calcsize(fmt)
        return v

    def u8(self) -> int:
        return int(self._u("<B"))

    def u16(self) -> int:
        return int(self._u("<H"))

    def i32(self) -> int:
        return int(self._u("<i"))

    def f32(self) -> float:
        return float(self._u("<f"))

    def string(self) -> str:
        n = self.u16()
        s = self.data[self.o:self.o + n].decode("utf-8", errors="replace")
        self.o += n
        return s

    def lap(self) -> LapInfo:
        ms = self.i32()
        car, driver = self.u16(), self.u16()
        n = self.u8()
        splits = [self.i32() for _ in range(n)]
        invalid, valid_best, outlap, inlap = self.u8(), self.u8(), self.u8(), self.u8()
        return LapInfo(None if ms in (MAXINT, -1) or ms <= 0 else ms / 1000.0, car, driver,
                       [None if s in (MAXINT, -1) or s <= 0 else s / 1000.0 for s in splits],
                       bool(invalid), bool(valid_best), bool(outlap), bool(inlap))


def _wstr(s: str) -> bytes:
    d = s.encode("utf-8")
    return struct.pack("<H", len(d)) + d


@dataclass(slots=True)
class LapInfo:
    time_s: float | None
    car_index: int
    driver_index: int
    splits: list[float | None]
    invalid: bool = False
    valid_for_best: bool = True
    outlap: bool = False
    inlap: bool = False


@dataclass(slots=True)
class BCCar:
    car_index: int
    driver_index: int = 0
    gear: int = 0
    location: int = 1
    kmh: int = 0
    position: int = 0
    cup_position: int = 0
    spline: float = 0.0
    laps: int = 0
    delta_ms: int = 0
    best: LapInfo | None = None
    last: LapInfo | None = None
    current: LapInfo | None = None
    pit_stops: int = 0
    was_in_pit: bool = False
    updated_t: float = 0.0


@dataclass(slots=True)
class BCEntry:
    car_index: int
    model: int = 0
    team: str = ""
    number: int = 0
    cup: int = 0
    driver_index: int = 0
    drivers: list[str] = field(default_factory=list)
    short: list[str] = field(default_factory=list)


def register_message(name: str, password: str, interval_ms: int = 250, command_password: str = "") -> bytes:
    return (bytes([OUT_REGISTER, PROTOCOL_VERSION]) + _wstr(name) + _wstr(password) +
            struct.pack("<i", interval_ms) + _wstr(command_password))


def request_message(kind: int, connection_id: int) -> bytes:
    return struct.pack("<Bi", kind, connection_id)


class BroadcastState:
    """Zustand aus der Broadcasting-API (wird vom Decoder gepflegt)."""

    def __init__(self) -> None:
        self.connection_id: int | None = None
        self.connected = False
        self.readonly = True
        self.error = ""
        self.session_type = 0
        self.phase = 0
        self.session_index = 0
        self.session_time_s = 0.0
        self.session_end_s: float | None = None
        self.focused_car = -1
        self.replay = False
        self.time_of_day_s = 0.0
        self.ambient_c: float | None = None
        self.track_c: float | None = None
        self.clouds = 0.0
        self.rain = 0.0
        self.wetness = 0.0
        self.best_session_lap: LapInfo | None = None
        self.track_name = ""
        self.track_id = 0
        self.track_m: float | None = None
        self.entries: dict[int, BCEntry] = {}
        self.cars: dict[int, BCCar] = {}
        self.entry_list_ids: list[int] = []
        self.realtime_t = 0.0
        self.events: list[tuple[int, str, int, int]] = []

    def handle(self, data: bytes, t: float) -> int:
        if not data:
            return -1
        r = _Reader(data, 1)
        kind = data[0]
        if kind == MSG_REGISTRATION_RESULT:
            self.connection_id = r.i32()
            self.connected = bool(r.u8())
            self.readonly = r.u8() == 0
            self.error = r.string()
        elif kind == MSG_REALTIME_UPDATE:
            self.realtime_t = t
            r.u16()  # eventIndex
            self.session_index = r.u16()
            self.session_type = r.u8()
            self.phase = r.u8()
            self.session_time_s = r.f32() / 1000.0
            end = r.f32()
            self.session_end_s = end / 1000.0 if 0 < end < 1e9 else None
            self.focused_car = r.i32()
            r.string()
            r.string()
            r.string()
            self.replay = bool(r.u8())
            if self.replay:
                r.f32()
                r.f32()
            self.time_of_day_s = r.f32()
            self.ambient_c = float(r.u8())
            self.track_c = float(r.u8())
            self.clouds = r.u8() / 10.0
            self.rain = r.u8() / 10.0
            self.wetness = r.u8() / 10.0
            self.best_session_lap = r.lap()
        elif kind == MSG_REALTIME_CAR_UPDATE:
            idx = r.u16()
            car = self.cars.get(idx) or BCCar(idx)
            car.driver_index = r.u16()
            r.u8()  # driverCount
            car.gear = r.u8() - 2
            r.f32()
            r.f32()
            r.f32()
            car.location = r.u8()
            car.kmh = r.u16()
            car.position = r.u16()
            car.cup_position = r.u16()
            r.u16()
            car.spline = r.f32()
            car.laps = r.u16()
            car.delta_ms = r.i32()
            car.best, car.last, car.current = r.lap(), r.lap(), r.lap()
            in_pit = car.location in (2, 3, 4)
            if in_pit and not car.was_in_pit and car.location in (2, 3) and car.laps > 0:
                car.pit_stops += 1
            car.was_in_pit = in_pit
            car.updated_t = t
            self.cars[idx] = car
            return idx
        elif kind == MSG_ENTRY_LIST:
            self.connection_id = r.i32()
            n = r.u16()
            self.entry_list_ids = [r.u16() for _ in range(n)]
            for idx in list(self.cars):
                if idx not in self.entry_list_ids:
                    self.cars.pop(idx, None)
        elif kind == MSG_TRACK_DATA:
            self.connection_id = r.i32()
            self.track_name = r.string()
            self.track_id = r.i32()
            meters = r.i32()
            self.track_m = float(meters) if meters > 0 else None
        elif kind == MSG_ENTRY_LIST_CAR:
            idx = r.u16()
            e = BCEntry(idx, model=r.u8(), team=r.string(), number=r.i32(), cup=r.u8(), driver_index=r.u8())
            r.u16()  # nationality
            for _ in range(r.u8()):
                first, last, short = r.string(), r.string(), r.string()
                r.u8()
                r.u16()
                e.drivers.append(f"{first} {last}".strip())
                e.short.append(short)
            self.entries[idx] = e
        elif kind == MSG_BROADCASTING_EVENT:
            etype = r.u8()
            msg = r.string()
            ms = r.i32()
            car_id = r.i32()
            self.events.append((etype, msg, ms, car_id))
        return -1


class ACCBroadcastSource(RawSource):
    """Registriert sich bei ACC, hält die Verbindung und fordert Entry List/Track Data nach."""

    def __init__(self, host: str = "127.0.0.1", port: int = 9000, password: str = "asd",
                 name: str = "PIT WALL", interval_ms: int = 250, command_password: str = ""):
        self.host = host
        self.port = port
        self.password = password
        self.name = name
        self.interval_ms = interval_ms
        self.command_password = command_password
        self.queue: asyncio.Queue[RawPacket | None] = asyncio.Queue(maxsize=4096)
        self.transport: asyncio.DatagramTransport | None = None
        self.state = BroadcastState()  # nur für Verbindungsverwaltung
        self.last_rx: float | None = None
        self.last_entry_request = 0.0
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        src = self

        class _P(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
                src._on_datagram(data)

        transport, _ = await loop.create_datagram_endpoint(_P, local_addr=("0.0.0.0", 0))
        self.transport = transport  # type: ignore[assignment]
        self._task = asyncio.create_task(self._keepalive())

    def _send(self, data: bytes) -> None:
        if self.transport is not None:
            self.transport.sendto(data, (self.host, self.port))

    def _on_datagram(self, data: bytes) -> None:
        now = time.monotonic()
        self.last_rx = now
        st = self.state
        kind = data[0] if data else 0
        was_connected = st.connected
        try:
            car_idx = st.handle(data, now)
        except (struct.error, IndexError):
            car_idx = -1
        if kind == MSG_REGISTRATION_RESULT:
            if st.connected and st.connection_id is not None and not was_connected:
                log.info("ACC-Broadcasting verbunden (ID %s)", st.connection_id)
                self._request_all()
            elif not st.connected:
                log.warning("ACC-Broadcasting abgelehnt: %s", st.error or "falsches Passwort?")
        elif car_idx >= 0 and car_idx not in st.entries and now - self.last_entry_request > 5.0:
            self._request_all()
        with contextlib.suppress(asyncio.QueueFull):
            self.queue.put_nowait(RawPacket("accb", data, now))

    def _request_all(self) -> None:
        if self.state.connection_id is None:
            return
        self.last_entry_request = time.monotonic()
        self._send(request_message(OUT_REQUEST_ENTRY_LIST, self.state.connection_id))
        self._send(request_message(OUT_REQUEST_TRACK_DATA, self.state.connection_id))

    async def _keepalive(self) -> None:
        while self.transport is not None:
            if self.last_rx is None or time.monotonic() - self.last_rx > 5.0:
                self.state.connected = False
                self._send(register_message(self.name, self.password, self.interval_ms, self.command_password))
            await asyncio.sleep(2.0)

    async def stop(self) -> None:
        if self.transport is not None:
            if self.state.connected and self.state.connection_id is not None:
                with contextlib.suppress(Exception):
                    self._send(request_message(OUT_UNREGISTER, self.state.connection_id))
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

    def info(self) -> dict[str, object]:
        return {"host": self.host, "port": self.port, "connected": self.state.connected,
                "error": self.state.error, "entries": len(self.state.entries)}


# ------------------------------------------------------------------ Decoder

class ACCDecoder(Decoder):
    sim = SimId.ACC

    def __init__(self) -> None:
        super().__init__()
        self.physics: Record | None = None
        self.graphics: Record | None = None
        self.static: dict[str, Any] = {}
        self.static_key: bytes | None = None
        self.bc = BroadcastState()
        self.shm_t = -1e9
        self.session_n = 0
        self.prev_session: tuple[int, int] | None = None
        self.prev_phase = 0
        self.last_sector_index: int | None = None
        self.sector_times: list[float | None] = [None, None, None]
        self.last_sector_times: list[float | None] = []
        self.is_acc: bool | None = None  # False = Shared Memory gehört zu AC (2014)

    def feed(self, packet: RawPacket) -> TelemetryFrame | None:
        ch = packet.channel
        d = packet.data
        if ch == "physics":
            if len(d) >= ACC_PHYSICS.size:
                if self.is_acc is None:
                    self.is_acc = looks_like_acc(None, d)
                self.physics = ACC_PHYSICS.unpack(d)
            return None
        if ch == "static":
            if len(d) >= ACC_STATIC_15.size and d[:200] != self.static_key:
                self.static_key = d[:200]
                self.static = _parse_static(d)
                self.is_acc = looks_like_acc(d, None)
            return None
        if ch == "graphics":
            if len(d) < ACC_GRAPHICS.size or self.physics is None or self.is_acc is False:
                return None
            self.graphics = ACC_GRAPHICS.unpack(d)
            if self.graphics.status == 0:
                return None
            self.shm_t = packet.t
            return self._frame(packet.t)
        if ch == "accb":
            kind = d[0] if d else 0
            self.bc.handle(d, packet.t)
            # ohne Shared Memory: Frame nach jedem Realtime-Update
            if kind == MSG_REALTIME_UPDATE and packet.t - self.shm_t > 1.0 and self.bc.cars:
                return self._frame(packet.t)
        return None

    # --------------------------------------------------------------- Frame
    def _session_tracking(self) -> None:
        g = self.graphics
        key = (self.bc.session_index, self.bc.session_type) if self.bc.realtime_t else (
            (g.sessionIndex, g.session) if g is not None else (0, 0))
        if self.prev_session is not None and key != self.prev_session:
            self.session_n += 1
        self.prev_session = key

    def _frame(self, t: float) -> TelemetryFrame:
        self._session_tracking()
        g, p, st, bc = self.graphics, self.physics, self.static, self.bc
        shm = g is not None and p is not None and t - self.shm_t < 1.0
        bc_fresh = t - bc.realtime_t < 3.0
        events: list[GameEvent] = []

        # ---- Session-Typ und Phase
        stype = SessionType.UNKNOWN
        if bc_fresh:
            stype = BC_SESSION.get(bc.session_type, SessionType.UNKNOWN)
        elif shm and g is not None:
            stype = SHM_SESSION.get(g.session, SessionType.UNKNOWN)
        phase = SessionPhase.RUNNING
        if bc_fresh:
            phase = BC_PHASE.get(bc.phase, SessionPhase.UNKNOWN)
            if stype is SessionType.RACE and bc.phase == 5 and self.prev_phase in (1, 2, 3, 4):
                events.append(GameEvent(GameEventKind.LIGHTS_OUT))
            self.prev_phase = bc.phase
        if shm and g is not None and g.status == 3:
            phase = SessionPhase.PAUSED

        length = bc.track_m
        # ---- Spieler-Index
        player_idx = bc.focused_car if bc.focused_car >= 0 else 0
        if shm and g is not None and g.playerCarID in bc.cars:
            player_idx = g.playerCarID

        cars = self._bc_cars(length, player_idx, t) if bc.cars else []
        me = next((c for c in cars if c.idx == player_idx), None)
        if me is None:
            me = CarState(idx=player_idx, is_player=True)
            cars.insert(0, me)
        me.is_player = True

        player = PlayerState(speed_ms=me.speed_ms or 0.0)
        flag = Flag.NONE
        yellow_sectors: list[int] = []
        weather = Weather(condition=RAIN.get(int(bc.rain * 5 + 0.01), "clear") if bc_fresh else "unknown",
                          air_temp_c=bc.ambient_c, track_temp_c=bc.track_c, rain_intensity=bc.rain if bc_fresh else None,
                          track_wetness=bc.wetness if bc_fresh else None)
        extra: dict[str, Any] = {"broadcasting": bc_fresh, "shared_memory": shm}
        time_left: float | None = None
        total_laps: int | None = None
        caps: set[Capability] = set()
        if bc_fresh:
            caps |= {Capability.POSITION, Capability.LAP_TIMES, Capability.SECTOR_TIMES, Capability.LAP_DISTANCE,
                     Capability.OPPONENTS, Capability.OPPONENT_LAP_TIMES, Capability.OPPONENT_PIT,
                     Capability.PIT_STATUS, Capability.WEATHER, Capability.SESSION_TIME}
            if bc.session_end_s is not None:
                time_left = bc.session_end_s
        if shm and g is not None and p is not None:
            caps |= {Capability.POSITION, Capability.LAP_TIMES, Capability.SECTOR_TIMES, Capability.LAP_DISTANCE,
                     Capability.FUEL, Capability.TYRE_TEMPS, Capability.TYRE_PRESSURE, Capability.TYRE_COMPOUND,
                     Capability.BRAKE_TEMPS, Capability.DAMAGE, Capability.INPUTS, Capability.WHEEL_SLIP,
                     Capability.YAW_RATE, Capability.FLAGS, Capability.SECTOR_FLAGS, Capability.PIT_STATUS,
                     Capability.PENALTIES, Capability.WEATHER, Capability.WEATHER_FORECAST,
                     Capability.TRACK_POSITION_XY, Capability.PIT_WINDOW}
            player = _player(p, g, st)
            self._apply_shm_to_me(me, g, length)
            flag = SHM_FLAG.get(g.flag, Flag.NONE)
            yellow_sectors = [i + 1 for i, v in enumerate((g.GlobalYellow1, g.GlobalYellow2, g.GlobalYellow3)) if v]
            if g.GlobalRed:
                flag = Flag.RED
            elif g.GlobalChequered:
                flag = Flag.CHEQUERED
            elif yellow_sectors and flag in (Flag.NONE, Flag.GREEN):
                flag = Flag.YELLOW
            elif g.GlobalWhite and flag is Flag.NONE:
                flag = Flag.WHITE
            elif g.GlobalGreen and flag is Flag.NONE:
                flag = Flag.GREEN
            if g.flag == 5 or g.GlobalChequered:
                phase = SessionPhase.CHECKERED if phase is SessionPhase.RUNNING else phase
            rain = int(g.rainIntensity)
            weather = Weather(
                condition=RAIN.get(rain, "clear") if rain else (
                    "overcast" if bc.clouds > 0.6 else "light_cloud" if bc.clouds > 0.3 else "clear"),
                air_temp_c=p.airTemp or bc.ambient_c, track_temp_c=p.roadTemp or bc.track_c,
                rain_intensity=rain / 5.0, track_wetness=GRIP_WETNESS.get(int(g.trackGripStatus)),
                forecast=[ForecastSample(10, RAIN.get(int(g.rainIntensityIn10min), "clear")),
                          ForecastSample(30, RAIN.get(int(g.rainIntensityIn30min), "clear"))],
            )
            if g.sessionTimeLeft > 0:
                time_left = g.sessionTimeLeft / 1000.0
            if stype is SessionType.RACE and g.numberOfLaps > 0 and not st.get("timed_race", True):
                total_laps = int(g.numberOfLaps)
            if g.penalty and g.penalty in range(1, 22):
                caps.add(Capability.PENALTIES)
            extra.update({
                "fuel_per_lap_game": g.fuelXLap or None, "fuel_laps_game": g.fuelEstimatedLaps or None,
                "mandatory_pit_done": bool(g.mandatoryPitDone), "missing_mandatory_pits": g.missingMandatoryPits,
                "pit_window": st.get("pit_window"), "stint_time_left_s": g.driverStintTimeLeft / 1000.0
                if g.driverStintTimeLeft > 0 else None, "tc": g.TC, "abs": g.ABS, "engine_map": g.EngineMap,
                "mfd_fuel_to_add": g.mfdFuelToAdd, "tyre_set": g.currentTyreSet, "penalty": g.penalty,
                "replay": g.status == 1, "gap_ahead_s": g.gapAhead / 1000.0 if 0 < g.gapAhead < MAXINT else None,
                "gap_behind_s": g.gapBehind / 1000.0 if 0 < g.gapBehind < MAXINT else None,
            })
            player.fuel_remaining_laps_game = g.fuelEstimatedLaps or None
        if stype is SessionType.RACE and total_laps is not None:
            time_left = None
        car_model = st.get("car", "")
        entry = bc.entries.get(player_idx)
        track_name = bc.track_name or st.get("track", "").replace("_", " ").title()
        session = SessionInfo(
            sim=self.sim, track_name=track_name, track_id=st.get("track") or bc.track_name.lower().replace(" ", "_"),
            track_length_m=length, session_type=stype, phase=phase, session_uid=f"acc-{self.session_n}",
            total_laps=total_laps, time_remaining_s=time_left, session_time_s=bc.session_time_s,
            num_cars=len(cars), vehicle_class=VehicleClass.GT, car_name=car_model or (entry.team if entry else ""),
            flag=flag, yellow_sectors=yellow_sectors, weather=weather,
            paused=phase is SessionPhase.PAUSED,
        )
        for etype, msg, _ms, car_id in bc.events:
            if etype == 6:
                events.append(GameEvent(GameEventKind.FASTEST_LAP, car_idx=car_id, data={"msg": msg}))
            elif etype == 4:
                events.append(GameEvent(GameEventKind.COLLISION, car_idx=car_id, data={"msg": msg}))
            elif etype == 3:
                events.append(GameEvent(GameEventKind.PENALTY, car_idx=car_id, data={"msg": msg}))
        bc.events.clear()
        return TelemetryFrame(seq=self.next_seq(), t=t, sim=self.sim, capabilities=frozenset(caps), session=session,
                              player=player, player_idx=player_idx, cars=cars, events=events, raw_extra=extra)

    def _apply_shm_to_me(self, me: CarState, g: Record, length: float | None) -> None:
        sector_idx = g.currentSectorIndex
        if self.last_sector_index is not None and sector_idx != self.last_sector_index:
            prev = self.last_sector_index
            if 0 <= prev < 3:
                self.sector_times[prev] = g.lastSectorTime / 1000.0 if g.lastSectorTime > 0 else None
            if sector_idx == 0:
                self.last_sector_times = list(self.sector_times)
                self.sector_times = [None, None, None]
        self.last_sector_index = sector_idx
        spline = float(g.normalizedCarPosition)
        completed = int(g.completedLaps)
        me.position = g.position or me.position
        me.laps_completed = completed
        me.lap = completed + 1
        me.lap_pct = spline
        if length:
            me.lap_distance_m = spline * length
            me.total_distance_m = (completed + spline) * length
        me.sector = sector_idx + 1
        me.current_lap_s = g.iCurrentTime / 1000.0 if g.iCurrentTime > 0 else None
        me.last_lap_s = g.iLastTime / 1000.0 if 0 < g.iLastTime < MAXINT else me.last_lap_s
        me.best_lap_s = g.iBestTime / 1000.0 if 0 < g.iBestTime < MAXINT else me.best_lap_s
        me.sector_times_s = list(self.sector_times)
        if self.last_sector_times:
            me.last_sector_times_s = self.last_sector_times
        me.pit_state = PitState.PIT_BOX if g.isInPit else PitState.PIT_LANE if g.isInPitLane else me.pit_state
        me.tyre_compound = "wet" if g.rainTyres else "dry"
        me.penalties_s = float(g.penaltyTime)
        # Weltposition des Spielers aus carCoordinates
        for i in range(min(int(g.activeCars), 60)):
            if g.carID[i] == g.playerCarID:
                me.x, me.z = float(g.carCoordinates[i * 3]), float(g.carCoordinates[i * 3 + 2])
                break

    def _bc_cars(self, length: float | None, player_idx: int, t: float) -> list[CarState]:
        bc = self.bc
        g = self.graphics
        coords: dict[int, tuple[float, float]] = {}
        if g is not None and t - self.shm_t < 1.0:
            for i in range(min(int(g.activeCars), 60)):
                coords[int(g.carID[i])] = (float(g.carCoordinates[i * 3]), float(g.carCoordinates[i * 3 + 2]))
        out = []
        for idx, c in bc.cars.items():
            if t - c.updated_t > 10.0:
                continue
            e = bc.entries.get(idx)
            name = ""
            if e is not None and e.drivers:
                di = c.driver_index if 0 <= c.driver_index < len(e.drivers) else e.driver_index
                name = e.drivers[min(di, len(e.drivers) - 1)]
            loc = c.location
            pit = PitState.PIT_LANE if loc in (2, 3, 4) else PitState.ON_TRACK
            if loc == 2 and c.kmh < 3:
                pit = PitState.PIT_BOX
            last = c.last
            cur = c.current
            xz = coords.get(idx)
            out.append(CarState(
                idx=idx, driver_name=name, team=e.team if e else "", car_number=str(e.number) if e else "",
                car_class=CUP.get(e.cup, "") if e else "", position=c.position or None,
                class_position=c.cup_position or None, lap=c.laps + 1, laps_completed=c.laps, lap_pct=c.spline,
                lap_distance_m=c.spline * length if length else None,
                total_distance_m=(c.laps + c.spline) * length if length else None,
                current_lap_s=cur.time_s if cur else None, last_lap_s=last.time_s if last else None,
                best_lap_s=c.best.time_s if c.best else None,
                last_sector_times_s=_sectors(last) if last else [], pit_state=pit, pit_stops=c.pit_stops,
                is_player=idx == player_idx, speed_ms=c.kmh / 3.6,
                x=xz[0] if xz else None, z=xz[1] if xz else None,
            ))
        return out


def _sectors(lap: LapInfo) -> list[float | None]:
    return list(lap.splits[:3]) + [None] * (3 - len(lap.splits[:3]))


def _parse_static(raw: bytes) -> dict[str, Any]:
    v = ACC_STATIC_33.unpack(raw) if len(raw) >= ACC_STATIC_33.size else None
    if v is None or v.isOnline not in (0, 1) or not wstr(v.dryTyresName).isprintable():
        v = ACC_STATIC_15.unpack(raw)
    player = " ".join(x for x in (wstr(v.playerName), wstr(v.playerSurname)) if x) or wstr(v.playerNick)
    return {"sm_version": wstr(v.smVersion), "ac_version": wstr(v.acVersion), "num_cars": v.numCars,
            "car": wstr(v.carModel), "track": wstr(v.track), "player": player, "max_rpm": v.maxRpm,
            "max_fuel": v.maxFuel, "pit_window": (v.PitWindowStart, v.PitWindowEnd), "online": bool(v.isOnline),
            "dry_tyres": wstr(v.dryTyresName), "wet_tyres": wstr(v.wetTyresName), "timed_race": True}


def _player(p: Record, g: Record, st: dict[str, Any]) -> PlayerState:
    wheels = []
    for i in range(4):
        core = float(p.tyreTemp[i]) or float(p.tyreCoreTemperature[i]) or None
        wheels.append(WheelState(
            # ACC zeigt die Kerntemperatur an; Kunos-Fenster (70–95 °C) bezieht sich darauf
            temp_surface_c=core, temp_core_c=core,
            pressure_kpa=(float(p.wheelsPressure[i]) * PSI_TO_KPA) or None,
            brake_temp_c=float(p.brakeTemp[i]) or None,
            slip_ratio=max(-1.0, min(1.0, float(p.slipRatio[i]))) if abs(p.slipRatio[i]) > 0.05 else None,
            slip_angle=float(p.slipAngle[i]), damage_pct=float(p.suspensionDamage[i]) * 100.0 or None,
        ))
    dmg = p.carDamage
    damage = Damage(aero=min(100.0, max(float(dmg[0]), float(dmg[1])) / 2.0),
                    body=min(100.0, max(float(x) for x in dmg) / 2.0),
                    suspension=min(100.0, max(float(x) for x in p.suspensionDamage) * 100.0))
    gear = int(p.gear)
    return PlayerState(
        speed_ms=p.speedKmh / 3.6, gear=-1 if gear == 0 else gear - 1, rpm=float(p.rpms),
        max_rpm=float(p.currentMaxRpm or st.get("max_rpm") or 0) or None, throttle=p.gas, brake=p.brake,
        clutch=p.clutch, steering=max(-1.0, min(1.0, p.steerAngle)), fuel_l=p.fuel,
        fuel_capacity_l=st.get("max_fuel") or None, wheels=wheels, damage=damage,
        tyre_compound=normalize_compound("wet" if g.rainTyres else "dry"), pit_limiter=bool(p.pitLimiterOn),
        abs_active=bool(p.absInAction), tc_active=bool(p.tcinAction), yaw_rate=float(p.localAngularVel[1]),
        lat_accel=p.accG[0] * 9.81, long_accel=p.accG[2] * 9.81, off_track=p.numberOfTyresOut > 2,
        lap_invalid=not bool(g.isValidLap),
    )


# ------------------------------------------------------------------ Adapter

def acc_maps(hz: float) -> list[MapSpec]:
    return [MapSpec("Local\\acpmf_physics", "physics", 4096, hz, True),
            MapSpec("Local\\acpmf_graphics", "graphics", 4096, hz, True),
            MapSpec("Local\\acpmf_static", "static", 4096, 2, True)]


class ACCAdapter(TransportAdapter):
    sim_id = SimId.ACC
    key = "acc"
    display_name = "Assetto Corsa Competizione"
    capabilities = frozenset({Capability.OPPONENTS, Capability.FUEL, Capability.TYRE_TEMPS})

    def __init__(self, config: Config):
        sec = config.section("acc")
        shm_cfg = config.section("shm")
        hz = float(shm_cfg.get("poll_hz", 60))
        mode = str(sec.get("mode", "auto"))
        from ..config import secret

        sources: list[RawSource] = []
        self.broadcast: ACCBroadcastSource | None = None
        if mode in ("auto", "broadcast"):
            self.broadcast = ACCBroadcastSource(
                str(sec.get("broadcast_host", "127.0.0.1")), int(sec.get("broadcast_port", 9000)),
                str(secret("ACC_BROADCAST_PASSWORD") or sec.get("broadcast_password", "asd")),
                name=str(sec.get("display_name", "PITWALL")),
                interval_ms=int(sec.get("update_interval_ms", 250)))
            sources.append(self.broadcast)
        if mode in ("auto", "shm"):
            sources.append(ShmSource(acc_maps(hz), hz, str(shm_cfg.get("mirror_dir", "/dev/shm")),
                                     str(shm_cfg.get("prefix", "pitwall-"))))
        super().__init__(config, MergedSource(sources), ACCDecoder())
        self.hint = ("Keine Daten von ACC. Broadcasting: broadcasting.json mit updListenerPort 9000 und "
                     "connectionPassword prüfen, ACC neu starten. Shared Memory unter Linux: pitwall-relay.exe "
                     "--sim acc (docs/setup-acc.md).")
