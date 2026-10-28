"""iRacing: irsdk-Shared-Memory (``Local\\IRSDKMemMapFileName``).

Aufbau der Map (docs/protocols/iracing.md):

* ``irsdk_header`` (Offset 0) mit Zeiger auf Var-Header, Session-YAML und bis zu 4 Datenpuffer.
* Var-Header beschreiben sich selbst → Variablen werden **nach Namen** gesucht, nie über feste Offsets.
* Session-Info als YAML (Strecke, Fahrer, Sessions, Ergebnisse).

Die ~1,1 MB große Map wird nicht roh weitergereicht. :class:`IRacingSplitter` zerlegt jeden
Schnappschuss in drei kompakte Kanäle, die auch so aufgenommen werden (Replay-fähig):

* ``irsdk_vars``: Var-Header-Tabelle (bei Änderung + alle 5 s)
* ``irsdk_yaml``: Session-YAML (bei Änderung + alle 10 s)
* ``irsdk_row``: neueste Datenzeile (``bufLen`` Bytes, typ. wenige KB)

Hinweis: Reifentemperatur und -verschleiß misst bei iRacing die Crew in der Box; während der Fahrt
bleiben die Werte stehen. Sie werden deshalb nicht als Live-Fähigkeit gemeldet, sondern nur als
"in der Box gemessen" in ``raw_extra``.
"""

from __future__ import annotations

import logging
import re
import struct
import sys
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import yaml

from ..core.frame import (
    Capability,
    CarState,
    CarStatus,
    Flag,
    GameEvent,
    GameEventKind,
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
)
from .assetto_corsa import vehicle_class_from_model
from .base import Decoder, RawPacket, RawSource, TransportAdapter
from .shm import MapSpec, ShmSource

if TYPE_CHECKING:
    from ..config import Config

log = logging.getLogger(__name__)

MAP_NAME = "Local\\IRSDKMemMapFileName"
MAP_SIZE = 1164 * 1024
HEADER = struct.Struct("<10i")
VARBUF = struct.Struct("<4i")
VARHEADER = struct.Struct("<iii?3x32s64s32s")
assert (HEADER.size, VARBUF.size, VARHEADER.size) == (40, 16, 144)
VARBUF_OFFSET = 48
ST_CONNECTED = 1
UNLIMITED_LAPS = 32767
UNLIMITED_TIME = 604800.0

# irsdk_VarType → struct-Code
TYPE_CODES = {0: "B", 1: "?", 2: "i", 3: "I", 4: "f", 5: "d"}

# irsdk_Flags
F_CHECKERED, F_WHITE, F_GREEN, F_YELLOW, F_RED, F_BLUE = 0x1, 0x2, 0x4, 0x8, 0x10, 0x20
F_YELLOW_WAVING, F_ONE_LAP_TO_GREEN, F_CAUTION, F_CAUTION_WAVING = 0x100, 0x200, 0x4000, 0x8000
F_BLACK, F_DISQUALIFY, F_FURLED, F_REPAIR = 0x10000, 0x20000, 0x80000, 0x100000
F_START_SET, F_START_GO = 0x40000000, 0x80000000

# irsdk_TrkLoc
TRK_NOT_IN_WORLD, TRK_OFF_TRACK, TRK_IN_PIT_STALL, TRK_APPROACHING_PITS, TRK_ON_TRACK = -1, 0, 1, 2, 3

ENGINE_PIT_LIMITER = 0x10

# Reihenfolge FL, FR, RL, RR (iRacing: LF, RF, LR, RR)
TYRE_PREFIX = ("LF", "RF", "LR", "RR")

BASE_CAPS = frozenset({
    Capability.POSITION, Capability.LAP_TIMES, Capability.LAP_DISTANCE, Capability.OPPONENTS,
    Capability.OPPONENT_LAP_TIMES, Capability.OPPONENT_PIT, Capability.FUEL, Capability.INPUTS,
    Capability.YAW_RATE, Capability.FLAGS, Capability.SAFETY_CAR, Capability.PIT_STATUS,
    Capability.INCIDENTS, Capability.WEATHER, Capability.SESSION_TIME, Capability.TOTAL_LAPS,
    Capability.START_LIGHTS,
})


# ------------------------------------------------------------------ Splitter


class IRacingSplitter:
    """Zerlegt einen rohen Map-Schnappschuss in die Kanäle irsdk_vars / irsdk_yaml / irsdk_row."""

    def __init__(self, vars_every_s: float = 5.0, yaml_every_s: float = 10.0):
        self.vars_every_s = vars_every_s
        self.yaml_every_s = yaml_every_s
        self.last_vars: bytes | None = None
        self.last_vars_t = -1e9
        self.last_yaml_update: int | None = None
        self.last_yaml_t = -1e9
        self.last_tick: int | None = None
        self.connected = False

    def split(self, data: bytes, t: float) -> list[RawPacket]:
        if len(data) < 112:
            return []
        (_ver, status, _tick_rate, si_update, si_len, si_off, num_vars, vh_off, num_buf, buf_len) = \
            HEADER.unpack_from(data)
        if not status & ST_CONNECTED or num_buf <= 0 or buf_len <= 0:
            self.connected = False
            self.last_tick = None
            return []
        self.connected = True
        out: list[RawPacket] = []
        vend = vh_off + num_vars * VARHEADER.size
        if vh_off > 0 and vend <= len(data):
            blob = struct.pack("<i", buf_len) + data[vh_off:vend]
            if blob != self.last_vars or t - self.last_vars_t >= self.vars_every_s:
                self.last_vars = blob
                self.last_vars_t = t
                out.append(RawPacket("irsdk_vars", blob, t))
        yaml_due = si_update != self.last_yaml_update or t - self.last_yaml_t >= self.yaml_every_s
        if yaml_due and si_off > 0 and si_off + si_len <= len(data):
            raw = data[si_off:si_off + si_len].split(b"\0", 1)[0]
            self.last_yaml_update = si_update
            self.last_yaml_t = t
            out.append(RawPacket("irsdk_yaml", struct.pack("<i", si_update) + raw, t))
        # neueste Zeile = höchster tickCount
        bufs = [VARBUF.unpack_from(data, VARBUF_OFFSET + 16 * i)[:2] for i in range(min(num_buf, 4))]
        tick, off = max(bufs, key=lambda b: b[0])
        if self.last_tick is not None and tick == self.last_tick:
            return out
        if off <= 0 or off + buf_len > len(data):
            return out
        self.last_tick = tick
        out.append(RawPacket("irsdk_row", struct.pack("<i", tick) + data[off:off + buf_len], t))
        return out


class IRacingSource(RawSource):
    """ShmSource (Windows/Datei/Relay) + Splitter → kompakte irsdk-Kanäle."""

    def __init__(self, inner: ShmSource):
        self.inner = inner
        self.splitter = IRacingSplitter()

    async def start(self) -> None:
        await self.inner.start()

    async def stop(self) -> None:
        await self.inner.stop()

    async def packets(self) -> AsyncIterator[RawPacket]:
        async for pkt in self.inner.packets():
            for out in self.splitter.split(pkt.data, pkt.t):
                yield out

    def info(self) -> dict[str, object]:
        return {**self.inner.info(), "connected": self.splitter.connected}


# --------------------------------------------------------------------- YAML

_SECTIONS = ("WeekendInfo", "SessionInfo", "DriverInfo", "SplitTimeInfo", "QualifyResultsInfo")
_KV = re.compile(r"^(\s*(?:- )?[A-Za-z0-9_]+: )(.+?)\s*$")
_CP1252_UNDEFINED = bytes.maketrans(b"\x81\x8d\x8f\x90\x9d", b"     ")

_Loader: Any = getattr(yaml, "CBaseLoader", yaml.BaseLoader)


def decode_yaml_bytes(raw: bytes) -> str:
    if raw.startswith(b"---\nWeekendInfo:\n Encoding: UTF8") or b"\n Encoding: UTF8" in raw[:200]:
        return raw.decode("utf-8", errors="replace")
    return raw.translate(_CP1252_UNDEFINED).decode("cp1252", errors="replace")


def sanitize_yaml(text: str) -> str:
    """Nur benötigte Top-Level-Sektionen behalten; jeden Skalarwert in Anführungszeichen setzen.

    iRacing escaped Namen nicht (``UserName: Max "Mad" O'Neil: #1``). Mit dem BaseLoader sind ohnehin
    alle Werte Strings – Typumwandlung passiert in :func:`_num`/:func:`_int`.
    """
    out: list[str] = []
    keep = False
    for line in text.splitlines():
        if not line or line in ("---", "..."):
            continue
        if not line[0].isspace() and line.endswith(":"):
            keep = line[:-1] in _SECTIONS
        if not keep:
            continue
        m = _KV.match(line)
        if m:
            value = m.group(2)
            if not (len(value) >= 2 and value[0] == value[-1] == '"'):
                value = '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
            line = m.group(1) + value
        out.append("".join(ch for ch in line if ch.isprintable() or ch == "\t"))
    return "\n".join(out) + "\n"


def parse_session_yaml(raw: bytes) -> dict[str, Any]:
    data = yaml.load(sanitize_yaml(decode_yaml_bytes(raw)), Loader=_Loader)
    return data if isinstance(data, dict) else {}


_NUM = re.compile(r"^\s*(-?\d+(?:\.\d+)?)")


def _num(v: Any, default: float | None = None) -> float | None:
    if v is None:
        return default
    m = _NUM.match(str(v))
    return float(m.group(1)) if m else default


def _int(v: Any, default: int | None = None) -> int | None:
    f = _num(v)
    return int(f) if f is not None else default


def _length_m(v: Any) -> float | None:
    f = _num(v)
    if f is None:
        return None
    s = str(v).lower()
    if "mi" in s:
        return f * 1609.344
    if "km" in s or f < 50:
        return f * 1000.0
    return f


def _speed_kmh(v: Any) -> float | None:
    f = _num(v)
    if f is None:
        return None
    return f * 1.609344 if "mph" in str(v).lower() else f


def session_type_from_str(s: str) -> SessionType:
    s = s.lower()
    if "race" in s:
        return SessionType.RACE
    if "qual" in s:
        return SessionType.QUALIFYING
    if "warm" in s:
        return SessionType.WARMUP
    if "time trial" in s:
        return SessionType.TIME_TRIAL
    if "practice" in s or "test" in s:
        return SessionType.PRACTICE
    return SessionType.UNKNOWN


# ------------------------------------------------------------------ Decoder


class _Var:
    __slots__ = ("count", "name", "offset", "st", "type", "unit")

    def __init__(self, name: str, vtype: int, offset: int, count: int, unit: str):
        self.name = name
        self.type = vtype
        self.offset = offset
        self.count = count
        self.unit = unit
        self.st = struct.Struct(f"<{count}{TYPE_CODES[vtype]}")


def parse_var_headers(blob: bytes) -> tuple[int, dict[str, _Var]]:
    buf_len = struct.unpack_from("<i", blob)[0]
    out: dict[str, _Var] = {}
    for off in range(4, len(blob) - VARHEADER.size + 1, VARHEADER.size):
        vtype, offset, count, _as_time, name, _desc, unit = VARHEADER.unpack_from(blob, off)
        if vtype not in TYPE_CODES or count <= 0:
            continue
        n = name.split(b"\0", 1)[0].decode("latin-1")
        if n:
            out[n] = _Var(n, vtype, offset, count, unit.split(b"\0", 1)[0].decode("latin-1"))
    return buf_len, out


class IRacingDecoder(Decoder):
    sim = SimId.IRACING

    def __init__(self) -> None:
        super().__init__()
        self.vars: dict[str, _Var] = {}
        self.buf_len = 0
        self.info: dict[str, Any] = {}
        self.yaml_update: int | None = None
        self.pending_yaml: tuple[int, bytes] | None = None
        self.last_yaml_parse = -1e9
        self.yaml_errors = 0
        self.row: bytes = b""
        self.prev_flags = 0
        self.prev_state = 0
        self._drivers: dict[int, dict[str, Any]] = {}
        self._session_key: tuple[Any, ...] = ()

    # ----------------------------------------------------------- Zugriff
    def get(self, name: str, default: Any = None) -> Any:
        v = self.vars.get(name)
        if v is None or v.offset + v.st.size > len(self.row):
            return default
        vals = v.st.unpack_from(self.row, v.offset)
        return vals[0] if v.count == 1 else vals

    def arr(self, name: str) -> tuple[Any, ...] | None:
        v = self.vars.get(name)
        if v is None or v.offset + v.st.size > len(self.row):
            return None
        return v.st.unpack_from(self.row, v.offset)

    # ------------------------------------------------------------ Kanäle
    def feed(self, packet: RawPacket) -> TelemetryFrame | None:
        ch = packet.channel
        if ch == "irsdk":  # roher Map-Schnappschuss (z. B. Dump-Datei)
            frame = None
            if not hasattr(self, "_splitter"):
                self._splitter = IRacingSplitter()
            for sub in self._splitter.split(packet.data, packet.t):
                frame = self.feed(sub) or frame
            return frame
        if ch == "irsdk_vars":
            if len(packet.data) >= 4 + VARHEADER.size:
                self.buf_len, self.vars = parse_var_headers(packet.data)
            return None
        if ch == "irsdk_yaml":
            update = struct.unpack_from("<i", packet.data)[0]
            if update != self.yaml_update or not self.info:
                self.pending_yaml = (update, packet.data[4:])
                self._maybe_parse_yaml(packet.t)
            return None
        if ch == "irsdk_row":
            if not self.vars:
                return None
            self.row = packet.data[4:]
            self._maybe_parse_yaml(packet.t)
            return self._frame(packet.t)
        return None

    def _maybe_parse_yaml(self, t: float) -> None:
        # YAML parsen kostet einige ms → höchstens alle 2 s (beim ersten Mal sofort)
        if self.pending_yaml is None or (self.info and t - self.last_yaml_parse < 2.0):
            return
        update, raw = self.pending_yaml
        self.pending_yaml = None
        self.last_yaml_parse = t
        try:
            info = parse_session_yaml(raw)
        except yaml.YAMLError as exc:
            self.yaml_errors += 1
            if self.yaml_errors <= 3:
                log.warning("iRacing: Session-YAML nicht lesbar (%s) – behalte letzten Stand", exc)
            return
        if info:
            self.info = info
            self.yaml_update = update
            self._drivers = {}
            for d in (info.get("DriverInfo") or {}).get("Drivers") or []:
                idx = _int(d.get("CarIdx"))
                if idx is not None:
                    self._drivers[idx] = d

    # ------------------------------------------------------------- Frame
    def _session_entry(self, num: int | None) -> dict[str, Any]:
        for s in (self.info.get("SessionInfo") or {}).get("Sessions") or []:
            if _int(s.get("SessionNum")) == num:
                return s
        return {}

    def _frame(self, t: float) -> TelemetryFrame:
        g = self.get
        wk = self.info.get("WeekendInfo") or {}
        di = self.info.get("DriverInfo") or {}
        session_num = g("SessionNum", 0)
        sess = self._session_entry(session_num)
        stype = session_type_from_str(str(sess.get("SessionType", "")))
        state = g("SessionState", 0) or 0
        flags = g("SessionFlags", 0) or 0
        session_time = float(g("SessionTime", 0.0) or 0.0)
        length = _length_m(wk.get("TrackLength"))
        player_idx = g("PlayerCarIdx", _int(di.get("DriverCarIdx"), 0))
        events: list[GameEvent] = []

        # ---------- Phase, Flaggen, Safety Car
        phase = {0: SessionPhase.MENU, 1: SessionPhase.GARAGE, 2: SessionPhase.GRID, 3: SessionPhase.FORMATION,
                 4: SessionPhase.RUNNING, 5: SessionPhase.CHECKERED, 6: SessionPhase.FINISHED}.get(
            state, SessionPhase.UNKNOWN)
        if stype is SessionType.RACE and state == 4 and flags & F_START_SET and not flags & F_START_GO:
            phase = SessionPhase.GRID
        if (flags & F_START_GO and not self.prev_flags & F_START_GO) or (
                stype is SessionType.RACE and state == 4 and self.prev_state in (2, 3)):
            events.append(GameEvent(GameEventKind.LIGHTS_OUT))
        if flags & F_CHECKERED and not self.prev_flags & F_CHECKERED:
            events.append(GameEvent(GameEventKind.CHEQUERED_FLAG))
        if flags & F_RED and not self.prev_flags & F_RED:
            events.append(GameEvent(GameEventKind.RED_FLAG))
        safety_car = SafetyCar.NONE
        if flags & (F_CAUTION | F_CAUTION_WAVING):
            safety_car = SafetyCar.ENDING if flags & F_ONE_LAP_TO_GREEN else SafetyCar.SC
            if not self.prev_flags & (F_CAUTION | F_CAUTION_WAVING):
                events.append(GameEvent(GameEventKind.SAFETY_CAR, data={"type": "sc", "event": "deployed"}))
        elif self.prev_flags & (F_CAUTION | F_CAUTION_WAVING):
            events.append(GameEvent(GameEventKind.SAFETY_CAR, data={"type": "sc", "event": "returned"}))
        car_flags = self.arr("CarIdxSessionFlags")
        my_flags = int(car_flags[player_idx]) if car_flags and 0 <= player_idx < len(car_flags) else 0
        flag = _flag_from_bits(flags | (my_flags & (F_BLACK | F_DISQUALIFY | F_REPAIR | F_BLUE | F_FURLED)))
        self.prev_flags = flags
        self.prev_state = state

        # ---------- Autos
        cars = self._cars(player_idx, length)
        me = next((c for c in cars if c.idx == player_idx), None)
        if me is None:
            me = CarState(idx=player_idx, is_player=True)
            cars.insert(0, me)
        me.is_player = True
        me.current_lap_s = _pos(g("LapCurrentLapTime"))
        me.last_lap_s = _pos(g("LapLastLapTime")) or me.last_lap_s
        me.best_lap_s = _pos(g("LapBestLapTime")) or me.best_lap_s
        lap_dist = g("LapDist")
        if lap_dist is not None and lap_dist >= 0:
            me.lap_distance_m = float(lap_dist)
        surface = g("PlayerTrackSurface")
        if g("PlayerCarInPitStall") or surface == TRK_IN_PIT_STALL:
            me.pit_state = PitState.PIT_BOX
        elif g("OnPitRoad"):
            me.pit_state = PitState.PIT_LANE
        elif surface == TRK_OFF_TRACK:
            me.pit_state = PitState.OFF_TRACK
        me.sector = self._sector(me.lap_pct)
        if (not g("IsOnTrack", True) and state in (0, 1)) or g("IsInGarage"):
            phase = SessionPhase.GARAGE

        # ---------- Spieler
        player = self._player(di)
        my_d = self._drivers.get(player_idx, {})
        car_name = str(my_d.get("CarScreenName", ""))
        vclass = VehicleClass.OVAL if "oval" in str(wk.get("TrackType", "")).lower() else (
            vehicle_class_from_model(car_name + " " + str(my_d.get("CarClassShortName", ""))))
        if vclass is VehicleClass.ROAD and str(my_d.get("CarClassShortName", "")):
            vclass = vehicle_class_from_model(str(my_d.get("CarPath", "")))

        # ---------- Session
        laps_total = g("SessionLapsTotal")
        sess_laps = _int(sess.get("SessionLaps"))
        total_laps = None
        if stype is SessionType.RACE:
            for cand in (laps_total, sess_laps):
                if cand and 0 < cand < UNLIMITED_LAPS:
                    total_laps = int(cand)
                    break
        remain = g("SessionTimeRemain")
        time_remaining = float(remain) if remain is not None and 0 <= remain < UNLIMITED_TIME - 1 else None
        if total_laps is not None and stype is SessionType.RACE:
            time_remaining = None
        duration = _num(sess.get("SessionTime"))
        wetness = g("TrackWetness")
        precip = g("Precipitation")
        track_wet = max(0.0, min(1.0, (wetness - 1) / 6.0)) if wetness else None
        condition = "clear"
        if (precip is not None and precip > 0.5) or (track_wet or 0) > 0.6:
            condition = "heavy_rain"
        elif (precip is not None and precip > 0.05) or (track_wet or 0) > 0.3:
            condition = "light_rain"
        elif "overcast" in str((wk.get("WeekendOptions") or {}).get("Skies", "")).lower():
            condition = "overcast"
        weather = Weather(condition=condition, air_temp_c=_f(g("AirTemp")),
                          track_temp_c=_f(g("TrackTempCrew", g("TrackTemp"))),
                          rain_intensity=_f(precip), track_wetness=track_wet)
        uid = g("SessionUniqueID", 0)
        sub = wk.get("SubSessionID", "0")
        self._session_key = (sub, uid, session_num)
        session = SessionInfo(
            sim=self.sim, track_name=str(wk.get("TrackDisplayName") or wk.get("TrackName") or ""),
            track_id=str(wk.get("TrackName", "")).replace(" ", "_"), track_length_m=length,
            session_type=stype, phase=phase, session_uid=f"ir-{sub}-{uid}-{session_num}",
            total_laps=total_laps, duration_s=duration, time_remaining_s=time_remaining,
            session_time_s=session_time, num_cars=len(cars), vehicle_class=vclass, car_name=car_name,
            flag=flag, safety_car=safety_car, weather=weather,
            pit_speed_limit_kmh=_speed_kmh(wk.get("TrackPitSpeedLimit")),
            paused=bool(g("IsReplayPlaying", False)) and not g("IsOnTrack", True),
        )
        caps = set(BASE_CAPS)
        if any(c.tyre_compound for c in cars):
            caps |= {Capability.TYRE_COMPOUND, Capability.OPPONENT_TYRES}
        if self.vars.get("dcDRSToggle") or self.vars.get("DRS_Status"):
            caps.add(Capability.DRS)
        if self.vars.get("EnergyERSBatteryPct") or self.vars.get("PowerMGU_K"):
            caps.add(Capability.ERS)
        extra: dict[str, Any] = {
            "replay": bool(g("IsReplayPlaying", False)),
            "tyres_measured": [{"inner": w.temp_inner_c, "middle": w.temp_middle_c, "outer": w.temp_outer_c,
                                "wear_pct": w.wear_pct} for w in player.wheels],
            "pit_service": {"fuel_l": _f(g("PitSvFuel")), "flags": g("PitSvFlags"),
                            "repair_left_s": _f(g("PitRepairLeft")),
                            "opt_repair_left_s": _f(g("PitOptRepairLeft")),
                            "status": g("PlayerCarPitSvStatus")},
            "fuel_use_kg_h": _f(g("FuelUsePerHour")),
            "spotter": g("CarLeftRight"),
        }
        # Gemessene Reifenwerte nicht als Live-Werte ausgeben (sie stehen während der Fahrt still)
        if me.pit_state is not PitState.PIT_BOX:
            for w in player.wheels:
                w.temp_inner_c = w.temp_middle_c = w.temp_outer_c = None
                w.wear_pct = None
        else:
            caps |= {Capability.TYRE_TEMPS, Capability.TYRE_WEAR}
        player.tyre_compound = me.tyre_compound
        return TelemetryFrame(seq=self.next_seq(), t=t, sim=self.sim, capabilities=frozenset(caps),
                              session=session, player=player, player_idx=player_idx, cars=cars, events=events,
                              raw_extra=extra)

    def _sector(self, pct: float | None) -> int | None:
        if pct is None:
            return None
        starts = sorted(_num(s.get("SectorStartPct"), 0.0) or 0.0
                        for s in (self.info.get("SplitTimeInfo") or {}).get("Sectors") or [])
        if len(starts) < 2:
            return min(3, int(pct * 3) + 1)
        n = 1
        for i, s in enumerate(starts):
            if pct >= s:
                n = i + 1
        # iRacing hat oft >3 Sektoren → auf 3 abbilden
        return min(3, int((n - 1) * 3 / len(starts)) + 1)

    def _cars(self, player_idx: int, length: float | None) -> list[CarState]:
        pct = self.arr("CarIdxLapDistPct")
        if pct is None:
            return []
        pos = self.arr("CarIdxPosition") or ()
        cpos = self.arr("CarIdxClassPosition") or ()
        lap = self.arr("CarIdxLap") or ()
        done = self.arr("CarIdxLapCompleted") or ()
        last = self.arr("CarIdxLastLapTime") or ()
        best = self.arr("CarIdxBestLapTime") or ()
        pitroad = self.arr("CarIdxOnPitRoad") or ()
        surface = self.arr("CarIdxTrackSurface") or ()
        compound = self.arr("CarIdxTireCompound") or ()
        flags = self.arr("CarIdxSessionFlags") or ()
        results = self._results()
        cars: list[CarState] = []
        for idx in range(len(pct)):
            d = self._drivers.get(idx)
            if self._drivers and d is None:
                continue
            if d is not None and (_int(d.get("CarIsPaceCar")) == 1 or _int(d.get("IsSpectator")) == 1):
                continue
            p = float(pct[idx])
            surf = int(surface[idx]) if idx < len(surface) else TRK_ON_TRACK
            in_world = p >= 0 and surf != TRK_NOT_IN_WORLD
            position = int(pos[idx]) if idx < len(pos) and pos[idx] > 0 else None
            if not in_world and position is None and idx != player_idx:
                continue
            started = int(lap[idx]) if idx < len(lap) else 0
            completed = max(0, int(done[idx])) if idx < len(done) else max(0, started - 1)
            if in_world:
                base = started - 1 if started >= 1 else -1
                total = (base + p) * length if length else None
            else:
                total = None
            if surf == TRK_IN_PIT_STALL:
                pit_state = PitState.PIT_BOX
            elif idx < len(pitroad) and pitroad[idx]:
                pit_state = PitState.PIT_LANE
            elif surf == TRK_OFF_TRACK:
                pit_state = PitState.OFF_TRACK
            elif not in_world:
                pit_state = PitState.GARAGE
            else:
                pit_state = PitState.ON_TRACK
            status = CarStatus.ACTIVE
            res = results.get(idx, {})
            reason = _int(res.get("ReasonOutId"), 0)
            if reason:
                status = CarStatus.RETIRED
            fl = int(flags[idx]) if idx < len(flags) else 0
            if fl & F_DISQUALIFY:
                status = CarStatus.DSQ
            if not in_world and status is CarStatus.ACTIVE and idx != player_idx:
                status = CarStatus.INACTIVE
            d = d or {}
            color = str(d.get("CarClassColor", ""))
            comp_idx = int(compound[idx]) if idx < len(compound) else -1
            cars.append(CarState(
                idx=idx, driver_name=str(d.get("UserName", "")), team=str(d.get("TeamName", "")),
                car_number=str(d.get("CarNumber", "")), car_class=str(d.get("CarClassShortName", "")),
                team_color=_color(color), position=position,
                class_position=int(cpos[idx]) if idx < len(cpos) and cpos[idx] > 0 else None,
                lap=max(0, started), laps_completed=completed, lap_pct=p if in_world else None,
                lap_distance_m=p * length if (in_world and length) else None, total_distance_m=total,
                last_lap_s=_pos(last[idx]) if idx < len(last) else None,
                best_lap_s=_pos(best[idx]) if idx < len(best) else None,
                pit_state=pit_state, status=status, is_player=idx == player_idx,
                tyre_compound=self._compound_name(comp_idx),
            ))
        return cars

    def _compound_name(self, idx: int) -> str | None:
        if idx < 0:
            return None
        tyres = (self.info.get("DriverInfo") or {}).get("DriverTires") or []
        for t in tyres:
            if _int(t.get("TireIndex")) == idx:
                name = str(t.get("TireCompoundType", "")).lower()
                if "wet" in name or "rain" in name:
                    return "wet"
                if name:
                    return "dry" if name in ("hard", "dry") and len(tyres) <= 2 else name
        return "wet" if idx == 1 and len(tyres) == 2 else None

    def _results(self) -> dict[int, dict[str, Any]]:
        sess = self._session_entry(self.get("SessionNum", 0))
        out = {}
        for r in sess.get("ResultsPositions") or []:
            if isinstance(r, dict):
                idx = _int(r.get("CarIdx"))
                if idx is not None:
                    out[idx] = r
        return out

    def _player(self, di: dict[str, Any]) -> PlayerState:
        g = self.get
        fuel = _f(g("FuelLevel"))
        fuel_pct = g("FuelLevelPct")
        capacity = None
        max_l = _num(di.get("DriverCarFuelMaxLtr"))
        if max_l:
            capacity = max_l * (_num(di.get("DriverCarMaxFuelPct"), 1.0) or 1.0)
        elif fuel and fuel_pct and fuel_pct > 0.01:
            capacity = fuel / fuel_pct
        wheels = []
        for i, pre in enumerate(TYRE_PREFIX):
            left, mid, right = (_f(g(f"{pre}temp{s}")) for s in ("CL", "CM", "CR"))
            wl, wm, wr = (g(f"{pre}wear{s}") for s in ("L", "M", "R"))
            wear = None
            if wl is not None and wm is not None and wr is not None:
                wear = max(0.0, min(100.0, (1.0 - (wl + wm + wr) / 3.0) * 100.0))
            # linke Reifen: L = außen; rechte Reifen: L = innen
            inner, outer = (right, left) if i in (0, 2) else (left, right)
            wheels.append(WheelState(temp_inner_c=inner, temp_middle_c=mid, temp_outer_c=outer,
                                     temp_surface_c=None, wear_pct=wear,
                                     pressure_kpa=_f(g(f"{pre}pressure")) or _f(g(f"{pre}coldPressure"))))
        steer = float(g("SteeringWheelAngle", 0.0) or 0.0)
        steer_max = float(g("SteeringWheelAngleMax", 0.0) or 0.0) or 7.85
        clutch = g("Clutch")
        warnings = g("EngineWarnings", 0) or 0
        speed = float(g("Speed", 0.0) or 0.0)
        surface_mat = g("PlayerTrackSurfaceMaterial")
        return PlayerState(
            speed_ms=speed, gear=int(g("Gear", 0) or 0), rpm=float(g("RPM", 0.0) or 0.0),
            max_rpm=_num(di.get("DriverCarRedLine")), throttle=float(g("Throttle", 0.0) or 0.0),
            brake=float(g("Brake", 0.0) or 0.0),
            # iRacing: 1 = eingekuppelt → in Pedalweg umrechnen
            clutch=1.0 - float(clutch) if clutch is not None else 0.0,
            # positiver Winkel = nach links (gegen den Uhrzeigersinn); unser Format: -1 = links
            steering=max(-1.0, min(1.0, -steer / (steer_max / 2.0))),
            fuel_l=fuel, fuel_capacity_l=capacity, wheels=wheels,
            pit_limiter=bool(warnings & ENGINE_PIT_LIMITER), yaw_rate=_f(g("YawRate")),
            lat_accel=_f(g("LatAccel")), long_accel=_f(g("LongAccel")),
            incidents=g("PlayerCarMyIncidentCount"),
            off_track=surface_mat is not None and surface_mat >= 15 and speed > 5,
            abs_active=bool(g("BrakeABSactive", False)),
        )


def _flag_from_bits(bits: int) -> Flag:
    if bits & F_RED:
        return Flag.RED
    if bits & F_CHECKERED:
        return Flag.CHEQUERED
    if bits & (F_BLACK | F_DISQUALIFY):
        return Flag.BLACK
    if bits & F_REPAIR:
        return Flag.MEATBALL
    if bits & F_FURLED:
        return Flag.BLACK_WHITE
    if bits & F_YELLOW_WAVING:
        return Flag.DOUBLE_YELLOW
    if bits & (F_YELLOW | F_CAUTION | F_CAUTION_WAVING):
        return Flag.YELLOW
    if bits & F_BLUE:
        return Flag.BLUE
    if bits & F_WHITE:
        return Flag.WHITE
    if bits & F_GREEN:
        return Flag.GREEN
    return Flag.NONE


def _f(v: Any) -> float | None:
    return float(v) if v is not None else None


def _pos(v: Any) -> float | None:
    return float(v) if v is not None and v > 0 else None


def _color(v: str) -> str:
    v = v.strip().lower()
    if v.startswith("0x"):
        try:
            return f"#{int(v, 16):06x}"
        except ValueError:
            return ""
    return ""


# ------------------------------------------------------------------ Adapter

# irsdk_BroadcastMsg / irsdk_PitCommandMode
BROADCAST_PIT_COMMAND = 9
PIT_CLEAR, PIT_WS, PIT_FUEL, PIT_CLEAR_TIRES, PIT_FR = 0, 1, 2, 7, 8


def send_pit_command(mode: int, param: int = 0) -> bool:
    """Pit-Befehl per Windows-Broadcast (nur unter Windows bzw. im Wine-Prefix von iRacing)."""
    if sys.platform != "win32":
        return False
    import ctypes

    user32 = ctypes.windll.user32
    msg_id = user32.RegisterWindowMessageW("IRSDK_BROADCASTMSG")
    if not msg_id:
        return False
    wparam = (BROADCAST_PIT_COMMAND & 0xFFFF) | ((mode & 0xFFFF) << 16)
    return bool(user32.SendNotifyMessageW(0xFFFF, msg_id, wparam, param))


class IRacingAdapter(TransportAdapter):
    sim_id = SimId.IRACING
    key = "iracing"
    display_name = "iRacing"
    capabilities = BASE_CAPS

    def __init__(self, config: Config):
        sec = config.section("iracing")
        shm_cfg = config.section("shm")
        hz = float(sec.get("poll_hz", 30))
        self.allow_pit_commands = bool(sec.get("allow_pit_commands", False))
        inner = ShmSource([MapSpec(MAP_NAME, "irsdk", MAP_SIZE, hz)], hz, str(shm_cfg.get("mirror_dir", "/dev/shm")),
                          str(shm_cfg.get("prefix", "pitwall-")))
        super().__init__(config, IRacingSource(inner), IRacingDecoder())
        self.hint = ("Keine Daten von iRacing. Unter Linux/Proton: pitwall-relay.exe --sim iracing im Spiel-Prefix "
                     "starten (docs/linux-proton.md).")
        self.last_command_t = 0.0

    def command(self, name: str, **kwargs: Any) -> bool:
        """Boxen-Befehle – nur mit ausdrücklicher Freigabe (iracing.allow_pit_commands)."""
        if not self.allow_pit_commands:
            log.info("iRacing-Pit-Befehl '%s' ignoriert: iracing.allow_pit_commands ist aus", name)
            return False
        if time.monotonic() - self.last_command_t < 1.0:
            return False
        self.last_command_t = time.monotonic()
        if name == "fuel":
            liters = int(round(float(kwargs.get("liters", 0))))
            return send_pit_command(PIT_FUEL, max(0, liters))
        if name == "tyres":
            ok = True
            for mode in (3, 4, 5, 6):
                ok = send_pit_command(mode, 0) and ok
            return ok
        if name == "no_tyres":
            return send_pit_command(PIT_CLEAR_TIRES)
        if name == "fast_repair":
            return send_pit_command(PIT_FR)
        if name == "tearoff":
            return send_pit_command(PIT_WS)
        if name == "clear":
            return send_pit_command(PIT_CLEAR)
        return False

