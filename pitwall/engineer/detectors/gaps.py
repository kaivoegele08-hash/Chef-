"""Abstände: Vordermann in Reichweite/DRS, Hintermann kommt, Gap stabil, Reifen-Offset."""

from __future__ import annotations

from ...core.frame import Capability, SafetyCar
from ...core.state import StateEventKind
from ...strategy.gaps import GapInfo
from ..events import Priority, RadioEvent
from .base import Detector, EngineerContext


class GapDetector(Detector):
    name = "gaps"

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.laps_since_report = 0
        self.last_drs_ahead_lap = -10
        self.last_drs_behind_lap = -10
        self.pulling_streak = 0

    def update(self, ctx: EngineerContext) -> list[RadioEvent]:
        out: list[RadioEvent] = []
        f = ctx.frame
        if not ctx.is_race or not f.has(Capability.OPPONENTS) or ctx.me is None:
            return out
        if f.session.safety_car is not SafetyCar.NONE or ctx.state.finished:
            return out
        lap_ev = ctx.has_event(StateEventKind.LAP_COMPLETED)
        if lap_ev is None or ctx.me.lap <= 2 or ctx.me.in_pit:
            return out
        lap = ctx.me.lap
        sp = ctx.speaker
        snap = ctx.strategy
        ahead, behind = snap.ahead, snap.behind
        ttl = 15.0
        drs = float(ctx.config.get("strategy.drs_range_s", 1.0))
        cd = ctx.avg_lap() * 0.8
        said = False

        def ev(key: str, prio: Priority, **data: object) -> RadioEvent:
            return ctx.event(key, prio, "gaps", ttl=ttl, cooldown_key="gaps.lap", cooldown_s=cd, **data)

        # Gefahr von hinten hat Vorrang
        if behind and _valid(behind) and not behind.in_pit:
            g = abs(behind.gap_s or 99)
            closing = behind.delta_per_lap_s is not None and behind.delta_per_lap_s < -0.08
            if ctx.formula and g <= drs and lap - self.last_drs_behind_lap >= 2:
                self.last_drs_behind_lap = lap
                out.append(ev("gap.behind_drs", Priority.HIGH, name=sp.name(behind.name), gap=sp.seconds(g)))
                said = True
            elif not ctx.formula and g < 0.8 and lap - self.last_drs_behind_lap >= 2:
                self.last_drs_behind_lap = lap
                out.append(ev("gap.behind_close_gt", Priority.INFO, name=sp.name(behind.name), gap=sp.seconds(g)))
                said = True
            elif closing and g < 3.0:
                key = "gap.behind_closing"
                data: dict[str, object] = {"name": sp.name(behind.name), "gap": sp.seconds(g),
                                           "delta": sp.seconds(-(behind.delta_per_lap_s or 0), 1)}
                if behind.tyre_offset_laps is not None and behind.tyre_offset_laps <= -5:
                    key = "gap.behind_tyre_offset"
                    data["laps"] = sp.n_laps(-behind.tyre_offset_laps)
                out.append(ev(key, Priority.INFO, **data))
                said = True
        if not said and ahead and _valid(ahead) and not ahead.in_pit:
            g = abs(ahead.gap_s or 99)
            closing = ahead.delta_per_lap_s is not None and ahead.delta_per_lap_s < -0.08
            if ctx.formula and g <= drs and lap - self.last_drs_ahead_lap >= 2:
                self.last_drs_ahead_lap = lap
                out.append(ev("gap.in_drs", Priority.INFO, name=sp.name(ahead.name), gap=sp.seconds(g)))
                said = True
            elif not ctx.formula and g < 1.0 and lap - self.last_drs_ahead_lap >= 2:
                self.last_drs_ahead_lap = lap
                out.append(ev("gap.close_ahead_gt", Priority.INFO, name=sp.name(ahead.name), gap=sp.seconds(g)))
                said = True
            elif closing and g < 6.0:
                if ctx.formula and ahead.laps_to_drs is not None and 0.5 < ahead.laps_to_drs <= 6:
                    out.append(ev("gap.drs_soon", Priority.INFO, name=sp.name(ahead.name), gap=sp.seconds(g),
                                  laps=sp.n_laps(max(1, round(ahead.laps_to_drs)), "dat")))
                elif ahead.tyre_offset_laps is not None and ahead.tyre_offset_laps >= 5:
                    out.append(ev("gap.ahead_tyre_offset", Priority.INFO, name=sp.name(ahead.name),
                                  gap=sp.seconds(g), laps=sp.n_laps(ahead.tyre_offset_laps)))
                else:
                    out.append(ev("gap.ahead_closing", Priority.INFO, name=sp.name(ahead.name), gap=sp.seconds(g),
                                  delta=sp.seconds(-(ahead.delta_per_lap_s or 0), 1)))
                said = True
        # wegfahren
        if not said and behind and _valid(behind) and behind.delta_per_lap_s is not None and \
                behind.delta_per_lap_s > 0.25 and abs(behind.gap_s or 0) < 8:
            self.pulling_streak += 1
            if self.pulling_streak >= 2:
                self.pulling_streak = 0
                out.append(ev("gap.pulling_away", Priority.CHATTER, name=sp.name(behind.name),
                              gap=sp.seconds(abs(behind.gap_s or 0)), delta=sp.seconds(behind.delta_per_lap_s, 1)))
                said = True
        else:
            self.pulling_streak = 0
        if said:
            self.laps_since_report = 0
        else:
            self.laps_since_report += 1
            if self.laps_since_report >= 4 and ahead and behind and _valid(ahead) and _valid(behind):
                self.laps_since_report = 0
                out.append(ev("gap.stable", Priority.CHATTER, gap=sp.seconds(abs(ahead.gap_s or 0)),
                              gap_behind=sp.seconds(abs(behind.gap_s or 0))))
        return out


def _valid(g: GapInfo) -> bool:
    return g.gap_s is not None and abs(g.gap_s) < 60
