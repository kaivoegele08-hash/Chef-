"""Leistung: persönliche Bestzeit, schnellste Runde, Rundenzeit im Training."""

from __future__ import annotations

from ...core.frame import GameEventKind
from ...core.state import LapRecord, StateEventKind
from ..describe import delta_text
from ..events import Priority, RadioEvent
from .base import Detector, EngineerContext


class PerformanceDetector(Detector):
    name = "performance"

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.best: float | None = None
        self.fastest_other_called: dict[int, float] = {}

    def on_flashback(self, ctx: EngineerContext) -> None:
        self.best = ctx.state.best_lap_s

    def update(self, ctx: EngineerContext) -> list[RadioEvent]:
        out: list[RadioEvent] = []
        me = ctx.me
        if me is None or ctx.is_quali:
            return out
        sp = ctx.speaker
        lap_ev = ctx.has_event(StateEventKind.LAP_COMPLETED)
        fastest_me = any(e.kind is GameEventKind.FASTEST_LAP and e.car_idx == ctx.frame.player_idx
                         for e in ctx.frame.events)
        if lap_ev:
            rec: LapRecord = lap_ev.data["record"]
            t = rec.time_s
            if t and rec.valid:
                improved = self.best is None or t < self.best - 0.001
                if improved and self.best is not None and not fastest_me:
                    prio = Priority.INFO if not ctx.is_race else Priority.CHATTER
                    out.append(ctx.event("perf.personal_best", prio, "performance", ttl=12, time=sp.laptime(t),
                                         cooldown_key="perf.pb", cooldown_s=ctx.avg_lap() * 2))
                elif ctx.is_practice and not improved and self.best is not None:
                    out.append(ctx.event("perf.lap_time", Priority.CHATTER, "performance", ttl=12,
                                         time=sp.laptime(t), delta_text=delta_text(t - self.best, sp)))
                if improved:
                    self.best = t
        for ge in ctx.frame.events:
            if ge.kind is not GameEventKind.FASTEST_LAP:
                continue
            lt = ge.data.get("lap_time")
            if not lt:
                continue
            if ge.car_idx == ctx.frame.player_idx and ctx.is_race:
                out.append(ctx.event("perf.fastest_lap", Priority.INFO, "performance", ttl=12, time=sp.laptime(lt)))
            elif ctx.is_race and ge.car_idx is not None:
                car = ctx.frame.car(ge.car_idx)
                last = self.fastest_other_called.get(ge.car_idx)
                if car and car.position and car.position <= 5 and (last is None or ctx.t - last > 300):
                    self.fastest_other_called[ge.car_idx] = ctx.t
                    out.append(ctx.event("perf.fastest_lap_other", Priority.CHATTER, "performance", ttl=10,
                                         name=sp.name(car.driver_name), time=sp.laptime(lt)))
        return out
