"""Strategie-Engine: verbindet alle Rechenmodule zu einem Lagebild.

Alles hier ist deterministische Mathematik. Das LLM bekommt ausschließlich
diese Zahlen (über Tools) und erfindet nichts dazu.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from ..config import Config, deep_merge, load_toml_dir
from ..core.frame import Capability, CarStatus, PitState, SafetyCar, SessionType, TelemetryFrame
from ..core.state import RaceState
from .fuel import FuelStatus, fuel_status
from .gaps import GapInfo, build_gap_info
from .pit import CutAssessment, PitLossModel, RejoinPrediction, assess_undercut, predict_rejoin
from .plans import CompoundModel, PlanResult, _evaluate, plan_race
from .tyres import (
    DegradationFit,
    TempStatus,
    WearProjection,
    fit_degradation,
    project_wear,
    temperature_status,
)

log = logging.getLogger(__name__)


def sim_profile(sim_key: str) -> dict[str, Any]:
    """Gemeinsame Defaults + sim-spezifische Werte aus config/sims/."""
    sims = load_toml_dir("sims")
    base = sims.get("_common", {})
    key = {"f1_24": "f1", "f1_25": "f1"}.get(sim_key, sim_key)
    return deep_merge(base, sims.get(key, {}))


def track_profile(track_key: str) -> dict[str, Any]:
    tracks = load_toml_dir("tracks")
    return tracks.get(track_key, {})


@dataclass(slots=True)
class WeatherAdvice:
    recommendation: str | None  # inters | wets | slicks | None
    reason: str = ""
    rain_in_min: float | None = None
    rain_probability: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"recommendation": self.recommendation, "reason": self.reason, "rain_in_min": self.rain_in_min,
                "rain_probability": self.rain_probability}


@dataclass(slots=True)
class StrategySnapshot:
    computed_at: float = 0.0
    lap: int = 0
    fuel: FuelStatus | None = None
    degradation: DegradationFit | None = None
    wear: WearProjection | None = None
    temps: TempStatus | None = None
    pit_loss_s: float | None = None
    pit_loss_effective_s: float | None = None
    rejoin: RejoinPrediction | None = None
    plans: PlanResult | None = None
    ahead: GapInfo | None = None
    behind: GapInfo | None = None
    grid: list[GapInfo] = field(default_factory=list)
    next_stop_lap: int | None = None
    window: tuple[int, int] | None = None
    window_open: bool = False
    box_this_lap: bool = False
    box_under_sc: bool = False
    sc_gain_s: float | None = None
    weather: WeatherAdvice | None = None
    undercut_threat: CutAssessment | None = None
    undercut_chance: CutAssessment | None = None
    rivals_to_stop: list[int] = field(default_factory=list)
    teammate_in_pit: bool = False
    used_compounds: list[str] = field(default_factory=list)
    plan_changed: bool = False
    previous_plan_label: str | None = None
    compound_models: dict[str, dict[str, float]] = field(default_factory=dict)
    avg_lap_s: float | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "lap": self.lap,
            "fuel": self.fuel.to_dict() if self.fuel else None,
            "degradation": None if self.degradation is None else {
                "compound": self.degradation.compound,
                "laps": self.degradation.laps_used,
                "deg_s_per_lap": _r(self.degradation.deg_s_per_lap, 3),
                "raw_trend_s_per_lap": _r(self.degradation.raw_trend_s_per_lap, 3),
                "r2": _r(self.degradation.r2, 2),
            },
            "wear": self.wear.to_dict() if self.wear else None,
            "temps": self.temps.to_dict() if self.temps else None,
            "pit_loss_s": _r(self.pit_loss_s, 1),
            "pit_loss_effective_s": _r(self.pit_loss_effective_s, 1),
            "rejoin": self.rejoin.to_dict() if self.rejoin else None,
            "plans": self.plans.to_dict() if self.plans else None,
            "ahead": self.ahead.to_dict() if self.ahead else None,
            "behind": self.behind.to_dict() if self.behind else None,
            "next_stop_lap": self.next_stop_lap,
            "window": self.window,
            "window_open": self.window_open,
            "box_this_lap": self.box_this_lap,
            "box_under_sc": self.box_under_sc,
            "sc_gain_s": _r(self.sc_gain_s, 1),
            "weather": self.weather.to_dict() if self.weather else None,
            "undercut_threat": self.undercut_threat.to_dict() if self.undercut_threat else None,
            "undercut_chance": self.undercut_chance.to_dict() if self.undercut_chance else None,
            "rivals_to_stop": self.rivals_to_stop,
            "teammate_in_pit": self.teammate_in_pit,
            "used_compounds": self.used_compounds,
            "avg_lap_s": _r(self.avg_lap_s, 3),
            "notes": self.notes,
        }


def _r(v: float | None, n: int) -> float | None:
    return None if v is None else round(v, n)


class StrategyEngine:
    def __init__(self, config: Config, memory: Any | None = None):
        self.config = config
        self.memory = memory  # LongTermMemory (optional)
        self.sc = config.section("strategy")
        self.profile: dict[str, Any] = {}
        self.profile_sim = ""
        self.pit = PitLossModel(base_s=float(self.sc.get("default_pit_loss_s", 22.0)),
                                sc_factor=float(self.sc.get("sc_pit_loss_factor", 0.55)),
                                vsc_factor=float(self.sc.get("vsc_pit_loss_factor", 0.7)))
        self.track_key = ""
        self.snapshot = StrategySnapshot()
        self._last_plan_lap = -1
        self._last_plan_sc = SafetyCar.NONE
        self._last_plan_key: tuple[object, ...] = ()
        self._last_fast_t = -1e9
        self._learned_samples = 0
        self.plan_compute_ms = 0.0

    # --------------------------------------------------------- Profile
    def _ensure_profile(self, frame: TelemetryFrame, state: RaceState) -> None:
        sim_key = str(frame.sim)
        if sim_key != self.profile_sim:
            self.profile = sim_profile(sim_key)
            self.profile_sim = sim_key
        tkey = f"{_sim_prefix(sim_key)}_{_norm(frame.session.track_id or frame.session.track_name)}"
        if tkey != self.track_key:
            self.track_key = tkey
            tp = track_profile(tkey) or track_profile(_norm(frame.session.track_id or frame.session.track_name))
            base = float(tp.get("pit_loss_s", self.sc.get("default_pit_loss_s", 22.0)))
            self.pit = PitLossModel(base_s=base, sc_factor=float(tp.get("sc_pit_loss_factor", self.pit.sc_factor)),
                                    vsc_factor=self.pit.vsc_factor)
            if self.memory is not None:
                learned = self.memory.pit_loss(sim_key, frame.session.track_id or frame.session.track_name)
                if learned:
                    self.pit.samples = list(learned)
            self._learned_samples = 0
        while self._learned_samples < len(state.pit_loss_samples):
            sample = state.pit_loss_samples[self._learned_samples]
            self.pit.learn(sample)
            if self.memory is not None and self.sc.get("learn_pit_loss", True):
                self.memory.record_pit_loss(sim_key, frame.session.track_id or frame.session.track_name, sample)
            self._learned_samples += 1

    def compound_models(self, frame: TelemetryFrame, deg: DegradationFit | None,
                        wear: WearProjection | None) -> dict[str, CompoundModel]:
        cfg = self.profile.get("compounds", {})
        vclass = frame.session.vehicle_class
        allowed = None
        if vclass.value in ("gt", "touring", "road", "prototype"):
            allowed = {"dry", "wet", "soft", "medium", "hard"}
        current = frame.player.tyre_compound
        models: dict[str, CompoundModel] = {}
        for name, c in cfg.items():
            if allowed is not None and name not in allowed and name != current:
                continue
            models[name] = CompoundModel(name, float(c.get("pace", 0.0)), float(c.get("deg", 0.05)),
                                         int(c.get("cliff", 25)), max_laps=int(c.get("cliff", 25)) + 14)
        # Sims mit nur einer Trockenmischung (GT) → nur diese betrachten
        if current == "dry":
            models = {k: v for k, v in models.items() if k in ("dry", "wet")}
        elif current in ("soft", "medium", "hard"):
            models.pop("dry", None)
        # gelernte Degradation der aktuellen Mischung einsetzen, andere proportional skalieren
        if deg is not None and deg.deg_s_per_lap is not None and current in models and deg.laps_used >= 4:
            m = models[current]
            measured = max(0.0, deg.deg_s_per_lap)
            scale = measured / m.deg_s if m.deg_s > 0 else 1.0
            scale = min(3.0, max(0.3, scale))
            for k, v in list(models.items()):
                models[k] = CompoundModel(v.name, v.pace_s, v.deg_s * scale, v.cliff_lap, v.cliff_quad, v.max_laps)
        if wear is not None and wear.laps_to_limit is not None and current in models:
            m = models[current]
            age = frame.player.tyre_age_laps or 0
            max_laps = int(age + max(1.0, wear.laps_to_limit))
            models[current] = CompoundModel(m.name, m.pace_s, m.deg_s, min(m.cliff_lap, max_laps), m.cliff_quad,
                                            max_laps + 2)
        return models

    # ---------------------------------------------------------- Update
    def update(self, frame: TelemetryFrame, state: RaceState, force_plan: bool = False) -> StrategySnapshot:
        self._ensure_profile(frame, state)
        snap = self.snapshot
        snap.computed_at = frame.t
        me = frame.me
        p = frame.player
        s = frame.session
        lap = me.lap if me else 0
        snap.lap = lap
        reserve = float(self.sc.get("fuel_reserve_laps", 0.5))

        clean = [lap_rec.time_s for lap_rec in state.clean_laps(5) if lap_rec.time_s]
        avg_lap = sorted(clean)[len(clean) // 2] if clean else (me.best_lap_s if me and me.best_lap_s else None)
        snap.avg_lap_s = avg_lap

        if frame.has(Capability.FUEL):
            learned = None
            if self.memory is not None:
                learned = self.memory.fuel_per_lap(str(frame.sim), s.track_id or s.track_name, s.car_name)
            snap.fuel = fuel_status(
                state.laps, p.fuel_l, p.fuel_capacity_l, lap, me.lap_pct if me else None,
                s.total_laps if s.session_type.is_race else None,
                s.time_remaining_s, avg_lap, reserve, learned, p.fuel_remaining_laps_game,
            )
        tpl = float(self.sc.get("fuel_time_per_litre_s", 0.03))
        snap.degradation = fit_degradation(state.laps, tpl) if frame.has(Capability.LAP_TIMES) else None
        tyre_cfg = self.profile.get("tyres", {})
        if frame.has(Capability.TYRE_WEAR):
            snap.wear = project_wear(state.laps, p, float(tyre_cfg.get("wear_limit_pct", 70.0)))
        if frame.has(Capability.TYRE_TEMPS):
            window = tyre_cfg.get("window", {}).get(p.tyre_compound or "", None)
            snap.temps = temperature_status([w.surface_temp() for w in p.wheels[:4]],
                                            tuple(window) if window else None,
                                            [w.pressure_kpa for w in p.wheels[:4]])
        snap.pit_loss_s = self.pit.loss_s
        snap.pit_loss_effective_s = self.pit.effective(s.safety_car)
        snap.used_compounds = sorted({st.compound for st in state.stints if st.compound})

        # Gaps & Feld
        if frame.has(Capability.OPPONENTS) and me is not None:
            self._update_field(frame, state, snap)

        # Pläne: pro Runde oder bei SC-Wechsel
        sc_changed = s.safety_car is not self._last_plan_sc
        plan_key = (p.tyre_compound, state.pit_stops_made, me.in_pit if me else False)
        if plan_key != self._last_plan_key:
            force_plan = True
            self._last_plan_key = plan_key
        if s.session_type.is_race and s.total_laps and me is not None and (
            force_plan or lap != self._last_plan_lap or sc_changed
        ) and p.tyre_compound:
            self._plan(frame, state, snap)
            self._last_plan_lap = lap
            self._last_plan_sc = s.safety_car
        elif not s.session_type.is_race:
            snap.plans = None
            snap.box_this_lap = False
            snap.window_open = False

        snap.weather = self._weather(frame)
        snap.teammate_in_pit = any(c.is_teammate and c.pit_state in (PitState.PIT_LANE, PitState.PIT_BOX)
                                   for c in frame.cars)
        return snap

    def _update_field(self, frame: TelemetryFrame, state: RaceState, snap: StrategySnapshot) -> None:
        me = frame.me
        assert me is not None
        drs = float(self.sc.get("drs_range_s", 1.0))
        my_age = frame.player.tyre_age_laps
        infos: list[GapInfo] = []
        gaps: dict[int, float] = {}
        cars_info: dict[int, dict[str, Any]] = {}
        for car in frame.cars:
            if car.idx == me.idx or car.status not in (CarStatus.ACTIVE, CarStatus.FINISHED):
                continue
            g = state.gap_to(car.idx)
            info = build_gap_info(car, g, state.opponents.get(car.idx), my_age, drs)
            infos.append(info)
            if g is not None:
                gaps[car.idx] = g
                cars_info[car.idx] = {"name": info.name, "position": car.position, "compound": car.tyre_compound,
                                      "tyre_age": car.tyre_age_laps, "status": car.status, "in_pit": car.in_pit}
        infos.sort(key=lambda i: i.position or 999)
        snap.grid = infos
        ahead_car = state.car_ahead()
        behind_car = state.car_behind()
        snap.ahead = next((i for i in infos if ahead_car and i.idx == ahead_car.idx), None)
        snap.behind = next((i for i in infos if behind_car and i.idx == behind_car.idx), None)
        if gaps and frame.session.session_type.is_race:
            snap.rejoin = predict_rejoin(gaps, cars_info, self.pit.effective(frame.session.safety_car))
        # Gegner, die noch stoppen müssen (Zwei-Mischungen-Regel, trocken)
        rule = bool(self.profile.get("rules", {}).get("two_compound_rule", False))
        if rule and frame.has(Capability.OPPONENT_TYRES):
            snap.rivals_to_stop = [
                i.idx for i in infos
                if i.pit_stops == 0 and len(set(_opp_compounds(state, i.idx))) < 2
            ]
        # Undercut-Bewertung
        snap.undercut_threat = snap.undercut_chance = None
        deg = (snap.degradation.deg_s_per_lap if snap.degradation and snap.degradation.deg_s_per_lap else
               float(self.profile.get("compounds", {}).get(frame.player.tyre_compound or "medium", {}).get("deg", 0.05)))
        new_tyre_gain = max(0.6, deg * (frame.player.tyre_age_laps or 0) + 0.4)
        my_recent = snap.avg_lap_s
        if snap.ahead and snap.ahead.gap_s is not None and my_recent and snap.ahead.last_lap_s:
            snap.undercut_chance = assess_undercut(snap.ahead.idx, snap.ahead.name, snap.ahead.gap_s,
                                                   snap.ahead.last_lap_s, snap.ahead.tyre_age,
                                                   my_recent - new_tyre_gain, deg)
        if snap.behind and snap.behind.gap_s is not None and snap.behind.last_lap_s and my_recent:
            their_gain = max(0.6, deg * (snap.behind.tyre_age or 0) + 0.4)
            snap.undercut_threat = assess_undercut(snap.behind.idx, snap.behind.name, snap.behind.gap_s,
                                                   my_recent, frame.player.tyre_age_laps,
                                                   snap.behind.last_lap_s - their_gain, deg)

    def _plan(self, frame: TelemetryFrame, state: RaceState, snap: StrategySnapshot) -> None:
        s = frame.session
        me = frame.me
        assert me is not None and s.total_laps
        t0 = time.perf_counter()
        models = self.compound_models(frame, snap.degradation, snap.wear)
        compound = frame.player.tyre_compound or "medium"
        if compound not in models:
            models[compound] = CompoundModel(compound, 0.0, 0.05, 25, max_laps=45)
        age = frame.player.tyre_age_laps or 0
        rule = bool(self.profile.get("rules", {}).get("two_compound_rule", False))
        wet = s.weather.is_wet and compound in ("inter", "wet")
        laps_left = s.total_laps - me.lap + 1
        max_stops = int(self.sc.get("max_stops", 3))
        if laps_left > 45:
            max_stops = min(max_stops, 2)
        loss = self.pit.loss_s
        prev_label = snap.plans.plan_a.label if snap.plans and snap.plans.plan_a else None
        prev_a = snap.plans.plan_a if snap.plans else None
        result = plan_race(me.lap, s.total_laps, compound, age, models, loss, max_stops,
                           set(snap.used_compounds), rule, wet)
        snap.plans = result
        snap.compound_models = {k: {"pace": v.pace_s, "deg": round(v.deg_s, 4), "max_laps": v.max_laps}
                                for k, v in models.items()}
        a = result.plan_a
        snap.next_stop_lap = a.pit_laps[0] if a and a.pit_laps else None
        snap.window = tuple(a.window[0]) if a and a.window else None  # type: ignore[assignment]
        tol = int(self.sc.get("pit_window_tolerance_laps", 3))
        snap.window_open = bool(snap.window and snap.window[0] <= me.lap <= snap.window[1])
        stopped_this_lap = bool(state.stints and state.stints[-1].start_lap == me.lap and state.pit_stops_made)
        snap.box_this_lap = bool(snap.next_stop_lap is not None and snap.next_stop_lap == me.lap
                                 and not stopped_this_lap and not me.in_pit)
        snap.plan_changed = bool(prev_label and a and prev_label != a.label and prev_a is not None)
        snap.previous_plan_label = prev_label
        # Safety Car: lohnt sich ein Stopp jetzt?
        snap.box_under_sc = False
        snap.sc_gain_s = None
        if s.safety_car in (SafetyCar.SC, SafetyCar.VSC, SafetyCar.ENDING) and a and a.pit_laps:
            sc_loss = self.pit.effective(s.safety_car)
            trial_laps = [me.lap, *a.pit_laps[1:]]
            t_now = _evaluate(trial_laps, a.compounds, me.lap, result.laps_remaining, age, models, loss)
            if t_now is not None:
                t_now -= loss - sc_loss
                gain = a.total_time_s - t_now
                snap.sc_gain_s = gain
                near = abs(a.pit_laps[0] - me.lap) <= max(tol, 6)
                snap.box_under_sc = gain > 0.5 and near and me.pit_state is PitState.ON_TRACK
        self.plan_compute_ms = (time.perf_counter() - t0) * 1000

    def _weather(self, frame: TelemetryFrame) -> WeatherAdvice:
        w = frame.session.weather
        compound = frame.player.tyre_compound or ""
        rain_in = prob = None
        for f in sorted(w.forecast, key=lambda f: f.minutes_ahead):
            if f.condition in ("light_rain", "heavy_rain", "storm") and (f.rain_percentage or 100) >= 50:
                rain_in, prob = f.minutes_ahead, f.rain_percentage
                break
        wet = w.track_wetness if w.track_wetness is not None else (w.rain_intensity or 0.0)
        rec = None
        reason = ""
        if compound in ("soft", "medium", "hard", "dry") and wet > 0.3:
            rec = "inters" if frame.session.vehicle_class.has_drs and wet < 0.7 else "wets"
            reason = "Strecke nass"
        elif compound == "inter" and wet > 0.75:
            rec, reason = "wets", "sehr nass"
        elif compound in ("inter", "wet") and wet < 0.12 and not (w.rain_intensity or 0) > 0.05:
            rec, reason = "slicks", "Strecke trocknet"
        return WeatherAdvice(rec, reason, rain_in, prob)

    def is_race(self, frame: TelemetryFrame) -> bool:
        return frame.session.session_type is SessionType.RACE


def _norm(name: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in name.lower()).strip("_")


def _sim_prefix(sim_key: str) -> str:
    return {"f1_24": "f1", "f1_25": "f1"}.get(sim_key, sim_key)


def _opp_compounds(state: RaceState, idx: int) -> list[str]:
    hist = state.opponents.get(idx)
    return list(hist.compounds) if hist is not None else []
