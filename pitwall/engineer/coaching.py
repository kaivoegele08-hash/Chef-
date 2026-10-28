"""Fahrcoaching aus der eigenen Telemetrie.

Jede Runde wird als Spur über die Distanz (5-m-Raster) gespeichert: Zeit,
Speed, Gas, Bremse, Lenkung, Blockieren/Wheelspin, Ausritt. Pro (automatisch
gelernter) Kurve wird die aktuelle Runde mit der Referenz (persönliche
Bestrunde) verglichen:

* Zeitverlust im Kurvensegment (Anbremsen bis Ausgang)
* Bremspunkt früher/später, minimale Geschwindigkeit, Gas-Wiederaufnahme,
  Rollphasen ohne Gas/Bremse, Blockieren, Wheelspin, Ausritte
* Verknüpfung mit dem limitierenden Reifen (welche Gewohnheit verschleißt ihn)
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import numpy as np

from ..core.frame import Capability, PitState, SessionPhase, TelemetryFrame
from ..core.state import LapRecord, RaceState, StateEvent, StateEventKind
from .detectors.base import Detector, EngineerContext
from .events import Priority, RadioEvent
from .speech import Speaker, Spoken

if TYPE_CHECKING:
    from ..config import Config
    from ..core.track import Corner, TrackModel

BIN = 5.0


@dataclass
class LapTrace:
    lap: int
    length_m: float
    n: int
    t: np.ndarray = field(init=False)
    speed: np.ndarray = field(init=False)
    throttle: np.ndarray = field(init=False)
    brake: np.ndarray = field(init=False)
    steer: np.ndarray = field(init=False)
    lock: np.ndarray = field(init=False)
    spin: np.ndarray = field(init=False)
    off: np.ndarray = field(init=False)
    filled: np.ndarray = field(init=False)
    time_s: float | None = None
    valid: bool = True

    def __post_init__(self) -> None:
        self.t = np.full(self.n, np.nan)
        self.speed = np.full(self.n, np.nan)
        self.throttle = np.full(self.n, np.nan)
        self.brake = np.full(self.n, np.nan)
        self.steer = np.full(self.n, np.nan)
        self.lock = np.zeros(self.n, dtype=bool)
        self.spin = np.zeros(self.n, dtype=bool)
        self.off = np.zeros(self.n, dtype=bool)
        self.filled = np.zeros(self.n, dtype=bool)
        self._prev: tuple[int, float, float, float, float, float] | None = None

    def add(self, dist: float, t: float, speed: float, throttle: float, brake: float, steer: float, lock: bool,
            spin: bool, off: bool) -> None:
        i = int(dist / BIN)
        if not 0 <= i < self.n:
            return
        # Lücken seit dem letzten Sample linear füllen (niedrige Abtastraten: 10 Hz × 70 m/s = 7 m > BIN)
        prev = self._prev
        if prev is not None and 1 < i - prev[0] <= 12:
            pi, pt, ps, pth, pb, pst = prev
            for j in range(pi + 1, i):
                if self.filled[j]:
                    continue
                a = (j - pi) / (i - pi)
                self.t[j] = pt + (t - pt) * a
                self.speed[j] = ps + (speed - ps) * a
                self.throttle[j] = pth + (throttle - pth) * a
                self.brake[j] = pb + (brake - pb) * a
                self.steer[j] = pst + (steer - pst) * a
                self.filled[j] = True
        self._prev = (i, t, speed, throttle, brake, steer)
        if not self.filled[i]:
            self.t[i] = t
        self.speed[i] = speed if np.isnan(self.speed[i]) else min(self.speed[i], speed) if brake > 0.1 else speed
        self.throttle[i] = throttle
        self.brake[i] = brake
        self.steer[i] = steer
        self.lock[i] |= lock
        self.spin[i] |= spin
        self.off[i] |= off
        self.filled[i] = True

    @property
    def coverage(self) -> float:
        return float(self.filled.mean()) if self.n else 0.0

    def interpolated_time(self) -> np.ndarray:
        idx = np.arange(self.n)
        ok = self.filled & ~np.isnan(self.t)
        if ok.sum() < 2:
            return np.full(self.n, np.nan)
        return np.interp(idx, idx[ok], self.t[ok])

    def to_dict(self, step: int = 4) -> dict[str, Any]:
        def arr(a: np.ndarray, nd: int = 2) -> list[float | None]:
            return [None if np.isnan(x) else round(float(x), nd) for x in a[::step]]

        return {"lap": self.lap, "time_s": self.time_s, "bin_m": BIN * step, "speed_kmh": arr(self.speed * 3.6, 1),
                "throttle": arr(self.throttle), "brake": arr(self.brake), "steer": arr(self.steer)}


@dataclass
class CornerFinding:
    corner: int
    name: str
    loss_s: float
    cause: str  # brake_early | brake_late | min_speed | throttle_late | coasting | lockup | wheelspin | offtrack | ""
    brake_delta_m: float | None = None  # − = früher als Referenz
    min_speed_delta_kmh: float | None = None
    throttle_delta_m: float | None = None
    coasting_m: float | None = None
    lockup: bool = False
    wheelspin: bool = False
    offtrack: bool = False

    def to_dict(self) -> dict[str, Any]:
        def r(v: float | None, n: int = 1) -> float | None:
            return None if v is None else round(v, n)

        return {"corner": self.corner, "name": self.name, "loss_s": round(self.loss_s, 3), "cause": self.cause,
                "brake_delta_m": r(self.brake_delta_m), "min_speed_delta_kmh": r(self.min_speed_delta_kmh),
                "throttle_delta_m": r(self.throttle_delta_m), "coasting_m": r(self.coasting_m),
                "lockup": self.lockup, "wheelspin": self.wheelspin, "offtrack": self.offtrack}


def analyse_corner(cur: LapTrace, ref: LapTrace, corner: Corner, length: float,
                   lang: str = "de") -> CornerFinding | None:
    """Vergleicht ein Kurvensegment zweier Runden."""
    n = cur.n
    approach = 150.0
    start_m = max(0.0, (corner.brake_start_m if corner.brake_start_m is not None else corner.start_m) - approach)
    end_m = min(length - BIN, corner.end_m + 80.0)
    a, b = int(start_m / BIN), int(end_m / BIN)
    if b <= a + 4 or b >= n:
        return None
    seg = slice(a, b)
    if cur.filled[seg].mean() < 0.8 or ref.filled[seg].mean() < 0.8:
        return None
    tc = cur.interpolated_time()
    tr = ref.interpolated_time()
    loss = float((tc[b] - tc[a]) - (tr[b] - tr[a]))
    if np.isnan(loss):
        return None

    def first_idx(mask: np.ndarray) -> int | None:
        w = np.nonzero(mask)[0]
        return int(w[0]) if w.size else None

    brake_cur = first_idx(np.nan_to_num(cur.brake[seg]) > 0.2)
    brake_ref = first_idx(np.nan_to_num(ref.brake[seg]) > 0.2)
    brake_delta = None if brake_cur is None or brake_ref is None else (brake_cur - brake_ref) * BIN
    apex = int(corner.apex_m / BIN)
    ca, cb = max(a, int(corner.start_m / BIN)), min(b, int(corner.end_m / BIN) + 1)
    min_cur = np.nanmin(cur.speed[ca:cb]) if cb > ca else np.nan
    min_ref = np.nanmin(ref.speed[ca:cb]) if cb > ca else np.nan
    min_delta = None if np.isnan(min_cur) or np.isnan(min_ref) else float(min_cur - min_ref) * 3.6
    after = slice(apex, b)
    thr_cur = first_idx(np.nan_to_num(cur.throttle[after]) > 0.8)
    thr_ref = first_idx(np.nan_to_num(ref.throttle[after]) > 0.8)
    thr_delta = None if thr_cur is None or thr_ref is None else (thr_cur - thr_ref) * BIN

    def coast(tr_: LapTrace) -> float:
        th = np.nan_to_num(tr_.throttle[seg], nan=1.0)
        br = np.nan_to_num(tr_.brake[seg])
        sp = np.nan_to_num(tr_.speed[seg])
        return float(((th < 0.1) & (br < 0.05) & (sp > 15)).sum() * BIN)

    coasting = coast(cur) - coast(ref)
    lock = bool(cur.lock[seg].any() and not ref.lock[seg].any())
    spin = bool(cur.spin[seg].any() and not ref.spin[seg].any())
    off = bool(cur.off[seg].any())
    cause = ""
    if loss > 0.03:
        if off:
            cause = "offtrack"
        elif lock:
            cause = "lockup"
        elif brake_delta is not None and brake_delta <= -8:
            cause = "brake_early"
        elif brake_delta is not None and brake_delta >= 8 and (min_delta or 0) < -2:
            cause = "brake_late"
        elif min_delta is not None and min_delta <= -4:
            cause = "min_speed"
        elif thr_delta is not None and thr_delta >= 10:
            cause = "throttle_late"
        elif coasting >= 15:
            cause = "coasting"
        elif spin:
            cause = "wheelspin"
    return CornerFinding(corner.number, corner.label_for(lang), loss, cause, brake_delta, min_delta, thr_delta,
                         coasting if coasting > 0 else None, lock, spin, off)


class Coach:
    def __init__(self, config: Config):
        self.config = config
        self.reset()

    def reset(self) -> None:
        self.current: LapTrace | None = None
        self.best: LapTrace | None = None
        self.last: LapTrace | None = None
        self.last_findings: list[CornerFinding] = []
        self.history: dict[int, list[CornerFinding]] = defaultdict(list)
        self.habits: Counter[str] = Counter()  # Gewohnheiten über die Session (für Reifen-Verknüpfung)
        self.wheel_lock: Counter[int] = Counter()
        self.track: TrackModel | None = None
        self._lap = -1
        self._last_t: float | None = None

    def observe(self, frame: TelemetryFrame, state: RaceState, events: list[StateEvent],
                track: TrackModel | None) -> None:
        self.track = track
        me = frame.me
        length = frame.session.track_length_m
        if me is None or not length or not frame.has(Capability.INPUTS):
            return
        if any(e.kind in (StateEventKind.SESSION_RESET, StateEventKind.FLASHBACK) for e in events):
            self.current = None
            self._lap = -1
        lap_ev = next((e for e in events if e.kind is StateEventKind.LAP_COMPLETED), None)
        if lap_ev is not None and self.current is not None:
            rec: LapRecord = lap_ev.data["record"]
            self._finish_lap(self.current, rec, frame)
            self.current = None
        if me.lap != self._lap or self.current is None:
            self._lap = me.lap
            self.current = LapTrace(me.lap, length, int(length / BIN) + 1)
        if me.lap_distance_m is None or me.pit_state is not PitState.ON_TRACK or \
                frame.session.phase is not SessionPhase.RUNNING:
            if me.pit_state is not PitState.ON_TRACK and self.current is not None:
                self.current.valid = False
            return
        p = frame.player
        slips = [w.slip_ratio for w in p.wheels[:4] if w.slip_ratio is not None]
        lock = bool(slips) and min(slips) < -0.25 and p.brake > 0.3
        spin = bool(slips) and max(slips) > 0.25 and p.throttle > 0.5
        if lock:
            for i, w in enumerate(p.wheels[:4]):
                if w.slip_ratio is not None and w.slip_ratio < -0.25:
                    self.wheel_lock[i] += 1
        lap_t = me.current_lap_s if me.current_lap_s is not None else frame.t
        self.current.add(me.lap_distance_m, lap_t, p.speed_ms, p.throttle, p.brake, p.steering, lock, spin,
                         p.off_track)
        if p.lap_invalid:
            self.current.valid = False

    def _finish_lap(self, trace: LapTrace, rec: LapRecord, frame: TelemetryFrame) -> None:
        trace.time_s = rec.time_s
        trace.valid = trace.valid and rec.valid and not rec.pit_in and not rec.pit_out and not rec.under_sc
        if trace.coverage < 0.9 or trace.time_s is None:
            return
        self.last = trace
        findings: list[CornerFinding] = []
        if self.best is not None and self.track is not None and self.track.corners:
            for c in self.track.corners:
                f = analyse_corner(trace, self.best, c, trace.length_m, self.config.language)
                if f is not None:
                    findings.append(f)
                    self.history[c.number].append(f)
                    self.history[c.number] = self.history[c.number][-20:]
                    if f.cause:
                        self.habits[f.cause] += 1
        self.last_findings = sorted(findings, key=lambda x: -x.loss_s)
        if trace.valid and (self.best is None or (self.best.time_s or 1e9) > trace.time_s):
            self.best = trace

    # --------------------------------------------------------- Ausgabe
    def analysis(self, limit: int = 5) -> dict[str, Any]:
        if self.best is None:
            return {"available": False, "reason": "noch keine gültige Referenzrunde"}
        agg = self.session_corner_summary()
        return {
            "reference_lap": self.best.lap,
            "reference_time_s": self.best.time_s,
            "last_lap": self.last.lap if self.last else None,
            "last_lap_findings": [f.to_dict() for f in self.last_findings[:limit]],
            "session_weak_corners": agg[:limit],
            "tyre_habit": self.tyre_habit(),
        }

    def session_corner_summary(self) -> list[dict[str, Any]]:
        out = []
        for corner, items in self.history.items():
            if not items:
                continue
            losses = [i.loss_s for i in items[-8:]]
            causes = [i.cause for i in items[-8:] if i.cause]
            out.append({"corner": corner, "name": items[-1].name, "loss_s": round(statistics.median(losses), 3),
                        "cause": Counter(causes).most_common(1)[0][0] if causes else "", "samples": len(items)})
        return sorted(out, key=lambda x: -x["loss_s"])

    def tyre_habit(self) -> dict[str, Any] | None:
        """Welche Gewohnheit belastet welchen Reifen am meisten (Heuristik)."""
        if not self.habits and not self.wheel_lock:
            return None
        fronts = self.habits["lockup"] + self.habits["brake_late"] + self.habits["min_speed"]
        rears = self.habits["wheelspin"] + self.habits["throttle_late"] * 0  # spätes Gas schont eher
        wheel = None
        if self.wheel_lock:
            idx, _ = self.wheel_lock.most_common(1)[0]
            wheel = ["FL", "FR", "RL", "RR"][idx]
        if fronts >= rears:
            return {"axle": "front", "wheel": wheel or "FL", "habit": "lockup" if self.habits["lockup"] else
                    "brake_late" if self.habits["brake_late"] else "min_speed", "count": fronts}
        return {"axle": "rear", "wheel": "RL" if wheel in (None, "FL", "FR") else wheel, "habit": "wheelspin",
                "count": rears}

    def summary_text(self, sp: Speaker) -> Spoken | None:
        agg = self.session_corner_summary()
        if not agg:
            return None
        top = agg[0]
        if top["loss_s"] < 0.03:
            text = "Saubere Runden, kein klarer Schwachpunkt." if sp.lang == "de" else "Clean laps, no clear weak spot."
            return Spoken(text, text)
        gain = sp.seconds(top["loss_s"], 2 if top["loss_s"] < 0.1 else 1)
        corner = top["name"]
        cause = CAUSE_TEXT_DE.get(top["cause"], "") if sp.lang == "de" else CAUSE_TEXT_EN.get(top["cause"], "")
        if sp.lang == "de":
            return Spoken(f"Größtes Potenzial in {corner}, etwa {gain.display}. {cause}".strip(),
                          f"Größtes Potenzial in {corner}, etwa {gain.spoken}. {cause}".strip())
        return Spoken(f"Biggest gain in {corner}, about {gain.display}. {cause}".strip(),
                      f"Biggest gain in {corner}, about {gain.spoken}. {cause}".strip())

    def detector(self) -> Detector:
        return CoachingDetector(self)


CAUSE_TEXT_DE = {
    "brake_early": "Du bremst zu früh.",
    "brake_late": "Du bremst zu spät und verpasst den Scheitel.",
    "min_speed": "Zu viel Speed abgebaut.",
    "throttle_late": "Zu spät am Gas.",
    "coasting": "Du rollst zu lange ohne Gas.",
    "lockup": "Blockieren beim Anbremsen.",
    "wheelspin": "Wheelspin am Ausgang.",
    "offtrack": "Ausritt.",
}
CAUSE_TEXT_EN = {
    "brake_early": "You're braking too early.",
    "brake_late": "Braking too late, missing the apex.",
    "min_speed": "Scrubbing too much speed.",
    "throttle_late": "Late on the throttle.",
    "coasting": "Coasting too long.",
    "lockup": "Locking up on entry.",
    "wheelspin": "Wheelspin on exit.",
    "offtrack": "Off track.",
}
HABIT_DE = {"lockup": "Blockieren beim Anbremsen", "brake_late": "zu spätes Anbremsen",
            "min_speed": "Rutschen am Scheitel", "wheelspin": "Wheelspin am Ausgang"}
HABIT_EN = {"lockup": "locking up on entry", "brake_late": "late braking", "min_speed": "sliding at the apex",
            "wheelspin": "wheelspin on exit"}


class CoachingDetector(Detector):
    name = "coaching"

    def __init__(self, coach: Coach):
        self.coach = coach
        self.reset()

    def reset(self) -> None:
        self.last_call_lap = -10
        self.habit_called = False

    def update(self, ctx: EngineerContext) -> list[RadioEvent]:
        out: list[RadioEvent] = []
        if not ctx.has_event(StateEventKind.LAP_COMPLETED) or ctx.me is None:
            return out
        if ctx.is_race:
            mode = str(ctx.config.get("engineer.coaching_in_race", "on_request"))
            every, min_loss = (5, 0.2) if mode == "sparse" else (0, 0.0)
            if mode != "sparse":
                return out
        elif ctx.is_quali:
            return out  # Quali: Funkstille, Analyse im Dashboard
        else:
            if str(ctx.config.get("engineer.coaching_in_practice", "on")) == "off":
                return out
            every, min_loss = 1, 0.08
        lap = ctx.me.lap
        if lap - self.last_call_lap < every:
            return out
        findings = [f for f in self.coach.last_findings if f.cause and f.loss_s >= min_loss]
        sp = ctx.speaker
        if findings:
            f = findings[0]
            self.last_call_lap = lap
            key = {"brake_early": "coaching.corner_brake_early", "brake_late": "coaching.corner_brake_late",
                   "min_speed": "coaching.corner_min_speed", "throttle_late": "coaching.corner_throttle_late",
                   "coasting": "coaching.corner_coasting", "lockup": "coaching.lockup",
                   "wheelspin": "coaching.wheelspin", "offtrack": "coaching.offtrack"}[f.cause]
            data: dict[str, Any] = {"corner": sp.text(f.name), "gain": sp.seconds(f.loss_s, 2 if f.loss_s < 0.1 else 1)}
            if f.brake_delta_m is not None:
                data["metres"] = sp.metres(abs(f.brake_delta_m))
            if f.min_speed_delta_kmh is not None:
                v = int(round(abs(f.min_speed_delta_kmh)))
                data["speed"] = Spoken(f"{v} km/h", f"{sp.int_(v).spoken} {'km/h' if sp.lang != 'de' else 'Kilometer pro Stunde'}")
            out.append(ctx.event(key, Priority.INFO if not ctx.is_race else Priority.CHATTER, "coaching", ttl=25,
                                 cooldown_key="coaching", cooldown_s=ctx.avg_lap() * 0.8, **data))
        # Reifen-Gewohnheit einmal pro Session, wenn genug Material
        habit = self.coach.tyre_habit()
        if habit and not self.habit_called and habit["count"] >= 4 and not ctx.is_race:
            self.habit_called = True
            table = HABIT_DE if sp.lang == "de" else HABIT_EN
            h = table.get(habit["habit"], habit["habit"])
            out.append(ctx.event("coaching.tyre_habit", Priority.CHATTER, "coaching", ttl=30,
                                 wheel=sp.wheel(habit["wheel"]), habit=Spoken(h, h)))
        return out
