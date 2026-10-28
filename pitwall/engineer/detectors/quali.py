"""Qualifying: Out-Lap, Verkehrswarnung, Push-Lap-Funkstille, Ergebnis, Restzeit."""

from __future__ import annotations

from typing import Any

from ...core.frame import Capability, PitState
from ...core.state import LapRecord, StateEventKind
from ..describe import delta_text
from ..events import Priority, RadioEvent
from .base import Detector, EngineerContext


class QualiDetector(Detector):
    name = "quali"

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.lap_kind = "out"  # out | push | cool | in
        self.push_called_lap: int | None = None
        self.traffic_called_lap: int | None = None
        self.time_marks: set[int] = set()
        self.best: float | None = None
        self.last_lap_start_speed = 0.0
        self.final_called = False

    def update(self, ctx: EngineerContext) -> list[RadioEvent]:
        out: list[RadioEvent] = []
        me = ctx.me
        if me is None or not ctx.is_quali:
            ctx.flags["push_lap"] = False
            return out
        sp = ctx.speaker
        f = ctx.frame
        p = f.player

        if ctx.has_event(StateEventKind.PIT_EXIT):
            self.lap_kind = "out"
            out.append(ctx.event("quali.outlap", Priority.INFO, "quali", ttl=15))
        lap_ev = ctx.has_event(StateEventKind.LAP_COMPLETED)
        if lap_ev:
            rec: LapRecord = lap_ev.data["record"]
            prev_kind = self.lap_kind
            if prev_kind == "push" and rec.time_s:
                improved = self.best is None or rec.time_s < self.best
                pos = sp.position(me.position)
                if improved:
                    key = "quali.result_improved" if self.best is not None else "quali.result"
                    data: dict[str, Any] = {"time": sp.laptime(rec.time_s, 3 if (self.best or 0) else 1), "pos": pos,
                            "delta_text": delta_text(rec.time_s - self.best, sp) if self.best else delta_text(None, sp)}
                    self.best = rec.time_s
                else:
                    key = "quali.result_slower"
                    data = {"time": sp.laptime(rec.time_s), "pos": pos}
                out.append(ctx.event(key, Priority.HIGH, "quali", ttl=20, allow_in_silence=True, **data))
            # nächste Runde klassifizieren: schnell über die Linie = Push-Lap
            if me.pit_state is not PitState.ON_TRACK:
                self.lap_kind = "in"
            elif prev_kind in ("out", "cool"):
                self.lap_kind = "push"
            elif prev_kind == "push":
                # zwei Push-Runden am Stück, wenn der Fahrer am Limit bleibt
                self.lap_kind = "push" if p.throttle > 0.9 and p.speed_ms > 50 else "cool"
            else:
                self.lap_kind = "push"
        if self.lap_kind == "push" and me.lap_pct is not None and me.lap_pct > 0.97 and p.throttle < 0.3:
            self.lap_kind = "cool"
        ctx.flags["push_lap"] = self.lap_kind == "push" and me.pit_state is PitState.ON_TRACK

        # Verkehr auf der Out-Lap prüfen: langsames Auto vor mir auf der Strecke?
        if self.lap_kind == "out" and me.lap_pct and 0.55 < me.lap_pct < 0.9 and self.traffic_called_lap != me.lap \
                and f.has(Capability.OPPONENTS) and me.lap_distance_m is not None:
            self.traffic_called_lap = me.lap
            length = f.session.track_length_m or 5000.0
            closest = None
            for car in f.cars:
                if car.idx == me.idx or car.lap_distance_m is None or car.in_pit:
                    continue
                d = (car.lap_distance_m - me.lap_distance_m) % length
                if 0 < d < 600 and (closest is None or d < closest[0]):
                    closest = (d, car)
            if closest:
                d, car = closest
                speed = max(10.0, p.speed_ms)
                out.append(ctx.event("quali.traffic", Priority.HIGH, "quali", ttl=10, name=sp.name(car.driver_name),
                                     gap=sp.seconds(d / speed)))
            else:
                out.append(ctx.event("quali.space", Priority.INFO, "quali", ttl=10))
        if self.lap_kind == "out" and me.lap_pct and me.lap_pct > 0.9 and self.push_called_lap != me.lap:
            self.push_called_lap = me.lap
            out.append(ctx.event("quali.push", Priority.INFO, "quali", ttl=6))

        rem = f.session.time_remaining_s
        if rem is not None:
            for mark in (10, 5, 2):
                if rem <= mark * 60 and mark not in self.time_marks and rem > (mark - 1) * 60:
                    self.time_marks.add(mark)
                    out.append(ctx.event("quali.time_left", Priority.INFO, "quali", ttl=15,
                                         minutes=sp.minutes(mark)))
            if not self.final_called and self.lap_kind == "out" and rem < ctx.avg_lap() * 1.6 and rem > 0:
                self.final_called = True
                out.append(ctx.event("quali.final_chance", Priority.INFO, "quali", ttl=10))
        return out
