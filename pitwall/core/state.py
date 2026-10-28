"""Race State: verdichtet den Frame-Strom zu Historie und Kennzahlen.

* Rundenhistorie (eigene + Gegner), Stints, Boxenstopps
* zeitbasierte Abstände (wann passierte Auto X denselben Streckenpunkt?)
* Erkennung von Flashback/Rewind und Rennneustart → Zurückrollen
* Schätzung der Rundendistanz, wenn die Sim keine liefert (GT7)

Alles basiert auf *Spielzeit* (``game_t``), nie auf Wanduhrzeit, damit Replays
und der beschleunigte Simulator identische Ergebnisse liefern.
"""

from __future__ import annotations

import bisect
import logging
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .frame import (
    Capability,
    CarState,
    CarStatus,
    GameEventKind,
    PitState,
    SafetyCar,
    SessionPhase,
    SessionType,
    TelemetryFrame,
)

log = logging.getLogger(__name__)


class StateEventKind(StrEnum):
    SESSION_RESET = "session_reset"  # neue Session oder Neustart
    FLASHBACK = "flashback"
    PHASE_CHANGED = "phase_changed"
    LIGHTS_OUT = "lights_out"
    LAP_COMPLETED = "lap_completed"  # eigene Runde
    OPPONENT_LAP = "opponent_lap"
    PIT_ENTRY = "pit_entry"  # eigenes Auto
    PIT_EXIT = "pit_exit"
    OPPONENT_PIT_ENTRY = "opponent_pit_entry"
    OPPONENT_PIT_EXIT = "opponent_pit_exit"
    POSITION_CHANGED = "position_changed"
    SECTOR_COMPLETED = "sector_completed"
    SAFETY_CAR_CHANGED = "safety_car_changed"
    FLAG_CHANGED = "flag_changed"
    CHEQUERED = "chequered"
    RETIREMENT = "retirement"


@dataclass(slots=True)
class StateEvent:
    kind: StateEventKind
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LapRecord:
    lap: int
    time_s: float | None
    fuel_start_l: float | None = None
    fuel_end_l: float | None = None
    compound: str | None = None
    tyre_age: int | None = None
    pit_in: bool = False
    pit_out: bool = False
    under_sc: bool = False
    wet: bool = False
    valid: bool = True
    position: int | None = None
    sectors: list[float | None] = field(default_factory=list)
    wear_end: list[float | None] = field(default_factory=list)
    end_game_t: float = 0.0
    max_tyre_temp: list[float | None] = field(default_factory=list)

    @property
    def fuel_used_l(self) -> float | None:
        if self.fuel_start_l is None or self.fuel_end_l is None:
            return None
        used = self.fuel_start_l - self.fuel_end_l
        return used if used > 0 else None

    @property
    def clean(self) -> bool:
        """Repräsentative Rennrunde (kein Boxen, kein SC, gültig, Zeit vorhanden)."""
        return self.time_s is not None and self.valid and not (self.pit_in or self.pit_out or self.under_sc)

    def to_dict(self) -> dict[str, Any]:
        return {
            "lap": self.lap,
            "time_s": self.time_s,
            "fuel_used_l": self.fuel_used_l,
            "compound": self.compound,
            "tyre_age": self.tyre_age,
            "pit_in": self.pit_in,
            "pit_out": self.pit_out,
            "under_sc": self.under_sc,
            "wet": self.wet,
            "valid": self.valid,
            "position": self.position,
            "sectors": self.sectors,
            "wear_end": self.wear_end,
        }


@dataclass(slots=True)
class Stint:
    compound: str | None
    start_lap: int
    end_lap: int | None = None
    start_age: int = 0

    def laps(self, current_lap: int) -> int:
        end = self.end_lap if self.end_lap is not None else current_lap
        return max(0, end - self.start_lap + 1)


@dataclass(slots=True)
class OpponentHistory:
    idx: int
    laps: dict[int, float] = field(default_factory=dict)  # Runde -> Zeit
    pit_laps: list[int] = field(default_factory=list)
    compounds: list[str] = field(default_factory=list)
    last_lap_seen: int = 0
    last_pit_state: PitState = PitState.ON_TRACK
    pit_entry_game_t: float | None = None
    last_pit_duration_s: float | None = None
    gap_to_me_at_line: dict[int, float] = field(default_factory=dict)  # meine Runde -> Gap (+ = vor mir)

    def recent_laps(self, n: int = 5) -> list[float]:
        keys = sorted(self.laps)[-n:]
        return [self.laps[k] for k in keys]


class GapTracker:
    """Zeitbasierte Abstände über Streckenpunkte (Gesamtdistanz → Spielzeit)."""

    def __init__(self, step_m: float = 25.0, horizon: int = 4000):
        self.step = step_m
        self.horizon = horizon
        self._dist: dict[int, deque[float]] = {}
        self._time: dict[int, deque[float]] = {}

    def reset(self) -> None:
        self._dist.clear()
        self._time.clear()

    def record(self, idx: int, total_dist: float, game_t: float) -> None:
        d = self._dist.setdefault(idx, deque(maxlen=self.horizon))
        t = self._time.setdefault(idx, deque(maxlen=self.horizon))
        if d and total_dist < d[-1] - 50:  # Distanz springt zurück → Historie verwerfen
            d.clear()
            t.clear()
        if not d or total_dist - d[-1] >= self.step:
            d.append(total_dist)
            t.append(game_t)

    def rollback(self, game_t: float) -> None:
        for idx in list(self._time):
            t = self._time[idx]
            d = self._dist[idx]
            while t and t[-1] > game_t:
                t.pop()
                d.pop()

    def time_at(self, idx: int, total_dist: float) -> float | None:
        d = self._dist.get(idx)
        t = self._time.get(idx)
        if not d or not t or total_dist < d[0] or total_dist > d[-1]:
            return None
        dl = list(d)
        i = bisect.bisect_left(dl, total_dist)
        if i == 0:
            return t[0]
        d0, d1 = dl[i - 1], dl[i] if i < len(dl) else dl[-1]
        t0, t1 = t[i - 1], t[i] if i < len(t) else t[-1]
        if d1 == d0:
            return t0
        return t0 + (t1 - t0) * (total_dist - d0) / (d1 - d0)

    def gap(self, me: int, other: int, me_dist: float, other_dist: float, now: float) -> float | None:
        """Positiv = ``other`` liegt vor mir (Sekunden), negativ = hinter mir."""
        if other_dist >= me_dist:
            t = self.time_at(other, me_dist)
            return None if t is None else now - t
        t = self.time_at(me, other_dist)
        return None if t is None else -(now - t)


class RaceState:
    """Hält den aktuellen Lagestand und die Historie einer Session."""

    def __init__(self) -> None:
        self.frame: TelemetryFrame | None = None
        self.prev: TelemetryFrame | None = None
        self.gaps = GapTracker()
        self.reset_count = 0
        self.flashback_count = 0
        self._reset()

    # ------------------------------------------------------------- reset
    def _reset(self) -> None:
        self.laps: list[LapRecord] = []
        self.stints: list[Stint] = []
        self.opponents: dict[int, OpponentHistory] = {}
        self.gaps.reset()
        self.session_uid = ""
        self.session_type = SessionType.UNKNOWN
        self.track_key = ""
        self.lights_out_game_t: float | None = None
        self.start_position: int | None = None
        self.position_after_lap1: int | None = None
        self._cur_lap = 0
        self._lap_fuel_start: float | None = None
        self._lap_sc = False
        self._lap_pit_in = False
        self._lap_pit_out = False
        self._lap_wet = False
        self._lap_valid = True
        self._lap_max_temp: list[float | None] = [None] * 4
        self._last_pit_state = PitState.ON_TRACK
        self._last_position: int | None = None
        self._last_sector: int | None = None
        self._last_phase = SessionPhase.UNKNOWN
        self._last_sc = SafetyCar.NONE
        self._last_flag: str = "none"
        self._last_game_t = -1.0
        self._est_dist = 0.0
        self._est_lap = -1
        self.pit_stops_made = 0
        self.pit_loss_samples: list[float] = []
        self._pit_entry_t: float | None = None
        self.max_game_t = 0.0
        self.finished = False
        self.best_lap_s: float | None = None
        self.best_sectors: list[float | None] = [None, None, None]

    # ------------------------------------------------------------ helpers
    @staticmethod
    def game_time(frame: TelemetryFrame) -> float:
        if frame.has(Capability.SESSION_TIME) and frame.session.session_time_s > 0:
            return frame.session.session_time_s
        return frame.t

    @property
    def current_lap(self) -> int:
        return self._cur_lap

    def clean_laps(self, n: int | None = None, compound: str | None = None) -> list[LapRecord]:
        laps = [lap for lap in self.laps if lap.clean and (compound is None or lap.compound == compound)]
        return laps[-n:] if n else laps

    def current_stint(self) -> Stint | None:
        return self.stints[-1] if self.stints else None

    def opponent(self, idx: int) -> OpponentHistory:
        hist = self.opponents.get(idx)
        if hist is None:
            hist = self.opponents[idx] = OpponentHistory(idx=idx)
        return hist

    def track_length(self) -> float | None:
        return self.frame.session.track_length_m if self.frame else None

    def total_distance(self, car: CarState) -> float | None:
        if car.total_distance_m is not None:
            return car.total_distance_m
        length = self.track_length()
        if length is None or car.lap_distance_m is None:
            return None
        laps_done = car.laps_completed if car.laps_completed else max(0, car.lap - 1)
        return laps_done * length + car.lap_distance_m

    def gap_to(self, other_idx: int) -> float | None:
        """Zeitabstand zu einem Auto: + = vor mir, − = hinter mir."""
        f = self.frame
        if f is None:
            return None
        me = f.me
        other = f.car(other_idx)
        if me is None or other is None:
            return None
        # 1) Sim liefert Abstände zum Führenden
        if (
            f.has(Capability.NATIVE_GAPS)
            and me.gap_to_leader_s is not None
            and other.gap_to_leader_s is not None
            and me.position is not None
            and other.position is not None
        ):
            native = me.gap_to_leader_s - other.gap_to_leader_s
            if (native >= 0) == (other.position < me.position):
                return native
        # 2) eigene Zeit-Distanz-Methode
        md, od = self.total_distance(me), self.total_distance(other)
        if md is None or od is None:
            return None
        g = self.gaps.gap(me.idx, other.idx, md, od, self.game_time(f))
        if g is None and abs(od - md) < 300:
            # sehr knapp (zwischen zwei Checkpoints): Distanz / Geschwindigkeit des hinteren Autos
            rear_speed = (f.player.speed_ms if od > md else (other.speed_ms or f.player.speed_ms)) or 0.0
            g = (od - md) / max(rear_speed, 10.0)
        return g

    def car_ahead(self) -> CarState | None:
        return self._neighbour(-1)

    def car_behind(self) -> CarState | None:
        return self._neighbour(+1)

    def _neighbour(self, delta: int) -> CarState | None:
        f = self.frame
        if f is None or f.me is None or f.me.position is None:
            return None
        target = f.me.position + delta
        for car in f.cars:
            if car.position == target and car.status in (CarStatus.ACTIVE, CarStatus.FINISHED):
                return car
        return None

    # ------------------------------------------------------------- update
    def update(self, frame: TelemetryFrame) -> list[StateEvent]:
        events: list[StateEvent] = []
        game_t = self.game_time(frame)
        s = frame.session

        # --- Neue Session / Neustart / Flashback ---
        reset_reason = None
        if s.session_uid and self.session_uid and s.session_uid != self.session_uid:
            reset_reason = "new_session"
        elif self.session_type is not SessionType.UNKNOWN and s.session_type is not self.session_type and (
            s.session_type is not SessionType.UNKNOWN
        ):
            reset_reason = "session_type"
        elif self._last_game_t >= 0 and game_t < self._last_game_t - 0.5:
            jump = self._last_game_t - game_t
            restart = game_t < 15.0 and self._last_game_t > 30.0
            if restart:
                reset_reason = "restart"
            else:
                self._rollback(game_t, frame)
                self.flashback_count += 1
                events.append(StateEvent(StateEventKind.FLASHBACK, {"jump_s": jump, "to_game_t": game_t}))
        if any(e.kind is GameEventKind.FLASHBACK for e in frame.events) and not any(
            e.kind is StateEventKind.FLASHBACK for e in events
        ):
            fb = next(e for e in frame.events if e.kind is GameEventKind.FLASHBACK)
            target = float(fb.data.get("session_time", game_t))
            self._rollback(target, frame)
            self.flashback_count += 1
            events.append(StateEvent(StateEventKind.FLASHBACK, {"jump_s": max(0.0, self._last_game_t - target),
                                                                "to_game_t": target}))
        if any(e.kind is GameEventKind.SESSION_STARTED for e in frame.events) and self.frame is not None and (
            reset_reason is None
        ):
            reset_reason = "session_started"
        if reset_reason:
            log.info("Session-Reset (%s)", reset_reason)
            self._reset()
            self.reset_count += 1
            events.append(StateEvent(StateEventKind.SESSION_RESET, {"reason": reset_reason}))

        self.session_uid = s.session_uid or self.session_uid
        if s.session_type is not SessionType.UNKNOWN:
            self.session_type = s.session_type
        self.track_key = f"{frame.sim}:{s.track_id or s.track_name}"
        self.prev, self.frame = self.frame, frame
        self._last_game_t = game_t
        self.max_game_t = max(self.max_game_t, game_t)

        me = frame.me
        if me is not None:
            self._estimate_distance(frame, me)

        # --- Phase / Start ---
        if s.phase is not self._last_phase:
            events.append(StateEvent(StateEventKind.PHASE_CHANGED, {"from": self._last_phase, "to": s.phase}))
            lights_out_now = (
                s.phase is SessionPhase.RUNNING
                and self._last_phase in (SessionPhase.GRID, SessionPhase.FORMATION)
                and s.session_type.is_race
            )
            if lights_out_now and self.lights_out_game_t is None:
                self._lights_out(game_t, me, events)
            self._last_phase = s.phase
        if any(e.kind is GameEventKind.LIGHTS_OUT for e in frame.events) and self.lights_out_game_t is None:
            self._lights_out(game_t, me, events)

        # --- SC / Flaggen ---
        if s.safety_car is not self._last_sc:
            events.append(StateEvent(StateEventKind.SAFETY_CAR_CHANGED, {"from": self._last_sc, "to": s.safety_car}))
            self._last_sc = s.safety_car
        if s.safety_car in (SafetyCar.SC, SafetyCar.VSC, SafetyCar.FCY, SafetyCar.ENDING):
            self._lap_sc = True
        if str(s.flag) != self._last_flag:
            events.append(StateEvent(StateEventKind.FLAG_CHANGED, {"from": self._last_flag, "to": s.flag}))
            self._last_flag = str(s.flag)
        if s.weather.is_wet:
            self._lap_wet = True
        if frame.player.lap_invalid:
            self._lap_valid = False
        for i, w in enumerate(frame.player.wheels[:4]):
            temp = w.surface_temp()
            if temp is not None:
                cur = self._lap_max_temp[i]
                self._lap_max_temp[i] = temp if cur is None else max(cur, temp)

        # --- Gap-Checkpoints für alle Autos ---
        for car in frame.cars:
            dist = self.total_distance(car)
            if dist is not None and car.status is CarStatus.ACTIVE:
                self.gaps.record(car.idx, dist, game_t)

        if me is not None:
            self._update_player(frame, me, game_t, events)
        self._update_opponents(frame, game_t, events)
        return events

    def _lights_out(self, game_t: float, me: CarState | None, events: list[StateEvent]) -> None:
        self.lights_out_game_t = game_t
        self.start_position = me.position if me else None
        events.append(StateEvent(StateEventKind.LIGHTS_OUT, {"grid_position": self.start_position}))

    def _estimate_distance(self, frame: TelemetryFrame, me: CarState) -> None:
        """Rundendistanz aus integrierter Geschwindigkeit, falls die Sim keine liefert."""
        if me.lap_distance_m is not None:
            return
        if self._est_lap != me.lap:
            self._est_lap = me.lap
            self._est_dist = 0.0
        elif self.prev is not None:
            dt = max(0.0, min(0.5, frame.t - self.prev.t))
            self._est_dist += frame.player.speed_ms * dt
        me.lap_distance_m = self._est_dist
        length = frame.session.track_length_m
        if length:
            me.lap_distance_m = min(self._est_dist, length)
            me.lap_pct = me.lap_distance_m / length

    def _update_player(self, frame: TelemetryFrame, me: CarState, game_t: float, events: list[StateEvent]) -> None:
        p = frame.player
        # Boxenstatus
        if me.pit_state is not self._last_pit_state:
            was_in = self._last_pit_state in (PitState.PIT_LANE, PitState.PIT_BOX)
            now_in = me.pit_state in (PitState.PIT_LANE, PitState.PIT_BOX)
            if now_in and not was_in:
                self._lap_pit_in = True
                self._pit_entry_t = game_t
                events.append(StateEvent(StateEventKind.PIT_ENTRY, {"lap": me.lap}))
            elif was_in and not now_in:
                self._lap_pit_out = True
                self.pit_stops_made += 1
                dur = None if self._pit_entry_t is None else game_t - self._pit_entry_t
                events.append(StateEvent(StateEventKind.PIT_EXIT, {"lap": me.lap, "pit_lane_s": dur}))
                compound = p.tyre_compound or me.tyre_compound
                cur = self.current_stint()
                if cur is not None:
                    cur.end_lap = max(cur.start_lap, me.lap - 1)
                self.stints.append(Stint(compound=compound, start_lap=me.lap, start_age=p.tyre_age_laps or 0))
            self._last_pit_state = me.pit_state

        # Stint initialisieren / Mischungswechsel ohne erkannten Stopp
        compound = p.tyre_compound or me.tyre_compound
        if not self.stints and me.lap >= 1:
            self.stints.append(Stint(compound=compound, start_lap=me.lap, start_age=p.tyre_age_laps or 0))
        elif self.stints and compound and self.stints[-1].compound is None:
            self.stints[-1].compound = compound

        # Position
        if me.position is not None and me.position != self._last_position:
            if self._last_position is not None and self.lights_out_game_t is not None:
                events.append(StateEvent(StateEventKind.POSITION_CHANGED,
                                         {"from": self._last_position, "to": me.position}))
            self._last_position = me.position

        # Sektor
        if me.sector is not None and self._last_sector is not None and me.sector != self._last_sector:
            completed = self._last_sector
            t_sector = None
            if me.sector_times_s and 0 < completed <= len(me.sector_times_s):
                t_sector = me.sector_times_s[completed - 1]
            if t_sector:
                idx = completed - 1
                best = self.best_sectors[idx]
                if best is None or t_sector < best:
                    self.best_sectors[idx] = t_sector
            events.append(StateEvent(StateEventKind.SECTOR_COMPLETED, {"sector": completed, "time_s": t_sector}))
        self._last_sector = me.sector

        # Rundenwechsel
        if self._cur_lap == 0 and me.lap > 0:
            self._cur_lap = me.lap
            self._lap_fuel_start = p.fuel_l
        elif me.lap > self._cur_lap:
            finished_lap = self._cur_lap
            lap_time = me.last_lap_s
            rec = LapRecord(
                lap=finished_lap,
                time_s=lap_time if lap_time and lap_time > 0 else None,
                fuel_start_l=self._lap_fuel_start,
                fuel_end_l=p.fuel_l,
                compound=compound,
                tyre_age=p.tyre_age_laps if p.tyre_age_laps is not None else me.tyre_age_laps,
                pit_in=self._lap_pit_in,
                pit_out=self._lap_pit_out or (finished_lap == 1 and not frame.session.session_type.is_race and False),
                under_sc=self._lap_sc,
                wet=self._lap_wet,
                valid=self._lap_valid,
                position=me.position,
                sectors=list(me.last_sector_times_s) if me.last_sector_times_s else [],
                wear_end=[w.wear_pct for w in p.wheels[:4]],
                end_game_t=game_t,
                max_tyre_temp=list(self._lap_max_temp),
            )
            if frame.session.session_type.is_race and finished_lap == 1:
                # Startrunde ist keine repräsentative Rundenzeit (stehender Start)
                rec.valid = rec.valid and frame.session.session_type is not SessionType.RACE
                self.position_after_lap1 = me.position
            # Out-Lap im Quali/Training: erste Runde nach Box ist nie repräsentativ
            self.laps.append(rec)
            if rec.time_s and rec.valid and (self.best_lap_s is None or rec.time_s < self.best_lap_s):
                self.best_lap_s = rec.time_s
            self._learn_pit_loss()
            events.append(StateEvent(StateEventKind.LAP_COMPLETED, {"lap": finished_lap, "record": rec}))
            # Gaps zu allen anderen an der Linie merken (Trend)
            # SC-Runden und eigene Stopps verfälschen den Trend → Historie neu beginnen
            reset_trend = rec.under_sc or rec.pit_in or rec.pit_out
            for car in frame.cars:
                if car.idx == me.idx:
                    continue
                hist = self.opponent(car.idx)
                if reset_trend or car.in_pit:
                    hist.gap_to_me_at_line.clear()
                    continue
                g = self.gap_to(car.idx)
                if g is not None:
                    hist.gap_to_me_at_line[finished_lap] = g
            self._cur_lap = me.lap
            self._lap_fuel_start = p.fuel_l
            self._lap_sc = frame.session.safety_car in (SafetyCar.SC, SafetyCar.VSC, SafetyCar.FCY, SafetyCar.ENDING)
            self._lap_pit_in = me.pit_state in (PitState.PIT_LANE, PitState.PIT_BOX)
            self._lap_pit_out = False
            self._lap_wet = frame.session.weather.is_wet
            self._lap_valid = not p.lap_invalid
            self._lap_max_temp = [None] * 4
            total = frame.session.total_laps
            if total and finished_lap >= total and frame.session.session_type.is_race and not self.finished:
                self.finished = True
                events.append(StateEvent(StateEventKind.CHEQUERED, {"position": me.position}))
        if frame.session.phase in (SessionPhase.CHECKERED, SessionPhase.FINISHED) and not self.finished:
            self.finished = True
            events.append(StateEvent(StateEventKind.CHEQUERED, {"position": me.position}))
        if any(e.kind is GameEventKind.CHEQUERED_FLAG for e in frame.events) and not self.finished:
            self.finished = True
            events.append(StateEvent(StateEventKind.CHEQUERED, {"position": me.position}))
        if self._lap_fuel_start is None and p.fuel_l is not None:
            self._lap_fuel_start = p.fuel_l
        elif self._lap_fuel_start is not None and p.fuel_l is not None and p.fuel_l > self._lap_fuel_start + 1.0:
            # nachgetankt → Rundenverbrauch nicht verwertbar
            self._lap_fuel_start = None

    def _learn_pit_loss(self) -> None:
        """Boxenverlust = (In-Lap + Out-Lap) − 2 × typische saubere Runde."""
        if len(self.laps) < 2:
            return
        out_lap = self.laps[-1]
        in_lap = self.laps[-2]
        if not (out_lap.pit_out and in_lap.pit_in) and not (out_lap.pit_in and out_lap.pit_out):
            return
        clean = [lap.time_s for lap in self.clean_laps(5) if lap.time_s]
        if len(clean) < 2:
            return
        ref = sorted(clean)[len(clean) // 2]
        if out_lap.pit_in and out_lap.pit_out and out_lap.time_s:
            loss = out_lap.time_s - ref
        elif in_lap.time_s and out_lap.time_s:
            loss = in_lap.time_s + out_lap.time_s - 2 * ref
        else:
            return
        if 5.0 < loss < 90.0 and not (in_lap.under_sc or out_lap.under_sc):
            self.pit_loss_samples.append(loss)

    def _update_opponents(self, frame: TelemetryFrame, game_t: float, events: list[StateEvent]) -> None:
        if not frame.has(Capability.OPPONENTS):
            return
        for car in frame.cars:
            if car.idx == frame.player_idx:
                continue
            hist = self.opponent(car.idx)
            if car.tyre_compound and (not hist.compounds or hist.compounds[-1] != car.tyre_compound):
                hist.compounds.append(car.tyre_compound)
            # Runde
            if car.lap > hist.last_lap_seen:
                if hist.last_lap_seen > 0 and car.last_lap_s:
                    hist.laps[hist.last_lap_seen] = car.last_lap_s
                    events.append(StateEvent(StateEventKind.OPPONENT_LAP, {"idx": car.idx, "lap": hist.last_lap_seen,
                                                                           "time_s": car.last_lap_s}))
                hist.last_lap_seen = car.lap
            # Box
            was_in = hist.last_pit_state in (PitState.PIT_LANE, PitState.PIT_BOX)
            now_in = car.pit_state in (PitState.PIT_LANE, PitState.PIT_BOX)
            if now_in and not was_in and car.status is CarStatus.ACTIVE:
                hist.pit_entry_game_t = game_t
                hist.pit_laps.append(car.lap)
                hist.gap_to_me_at_line.clear()
                events.append(StateEvent(StateEventKind.OPPONENT_PIT_ENTRY, {"idx": car.idx, "lap": car.lap}))
            elif was_in and not now_in:
                if hist.pit_entry_game_t is not None:
                    hist.last_pit_duration_s = game_t - hist.pit_entry_game_t
                events.append(StateEvent(StateEventKind.OPPONENT_PIT_EXIT, {"idx": car.idx, "lap": car.lap,
                                                                            "compound": car.tyre_compound}))
            hist.last_pit_state = car.pit_state
            if car.status in (CarStatus.DNF, CarStatus.RETIRED, CarStatus.DSQ) and car.idx not in self._retired:
                self._retired.add(car.idx)
                events.append(StateEvent(StateEventKind.RETIREMENT, {"idx": car.idx}))

    @property
    def _retired(self) -> set[int]:
        if not hasattr(self, "_retired_set"):
            self._retired_set: set[int] = set()
        return self._retired_set

    def _rollback(self, game_t: float, frame: TelemetryFrame) -> None:
        """Flashback: Historie nach ``game_t`` verwerfen."""
        me = frame.me
        new_lap = me.lap if me else self._cur_lap
        self.laps = [lap for lap in self.laps if lap.lap < new_lap and lap.end_game_t <= game_t + 0.5]
        for stint in self.stints:
            if stint.end_lap is not None and stint.end_lap >= new_lap:
                stint.end_lap = None
        self.stints = [s for s in self.stints if s.start_lap <= new_lap] or self.stints[:1]
        for hist in self.opponents.values():
            car = frame.car(hist.idx)
            hist.laps = {k: v for k, v in hist.laps.items() if car is None or k < car.lap}
            hist.gap_to_me_at_line = {k: v for k, v in hist.gap_to_me_at_line.items() if k < new_lap}
            car = frame.car(hist.idx)
            if car is not None:
                hist.last_lap_seen = car.lap
                hist.pit_laps = [lap for lap in hist.pit_laps if lap < car.lap]
        self.gaps.rollback(game_t)
        self._cur_lap = new_lap
        self._lap_fuel_start = frame.player.fuel_l
        self._last_game_t = game_t
        self.finished = False
        if self.lights_out_game_t is not None and game_t < self.lights_out_game_t:
            self.lights_out_game_t = None

    # ------------------------------------------------------- Ausgabe
    def snapshot(self) -> dict[str, Any]:
        return {
            "laps": [lap.to_dict() for lap in self.laps[-60:]],
            "stints": [{"compound": s.compound, "start_lap": s.start_lap, "end_lap": s.end_lap} for s in self.stints],
            "pit_stops": self.pit_stops_made,
            "best_lap_s": self.best_lap_s,
            "best_sectors": self.best_sectors,
            "start_position": self.start_position,
            "flashbacks": self.flashback_count,
        }
