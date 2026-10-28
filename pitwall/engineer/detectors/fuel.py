"""Sprit: knapp, Sparziel mit Lift-and-Coast, Bestätigung, Überschuss."""

from __future__ import annotations

from ...core.frame import Capability
from ...core.state import StateEventKind
from ..events import Priority, RadioEvent
from .base import Detector, EngineerContext


class FuelDetector(Detector):
    name = "fuel"

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.ok_called = False
        self.surplus_called = False
        self.critical_since_lap: int | None = None
        self.last_critical_lap = -10
        self.saving = False
        self.empty_called_lap: int | None = None

    def update(self, ctx: EngineerContext) -> list[RadioEvent]:
        out: list[RadioEvent] = []
        f = ctx.frame
        me = ctx.me
        fuel = ctx.strategy.fuel
        if not f.has(Capability.FUEL) or fuel is None or me is None or not ctx.is_race or ctx.state.finished:
            return out
        sp = ctx.speaker
        # dringend: fast leer
        if fuel.laps_in_tank is not None and fuel.laps_to_go is not None and fuel.laps_in_tank < 1.1 and \
                fuel.laps_to_go > fuel.laps_in_tank + 0.1 and self.empty_called_lap != me.lap and \
                (fuel.capacity_l or 0) > 0 and fuel.per_lap_source == "measured":
            self.empty_called_lap = me.lap
            from ...strategy.engine import sim_profile

            refuel = bool(sim_profile(str(f.sim)).get("rules", {}).get("refuelling", not ctx.formula))
            key = "fuel.empty" if refuel else "fuel.empty_no_refuel"
            out.append(ctx.event(key, Priority.CRITICAL, "fuel", ttl=20, interrupt=True))
            return out
        if not ctx.has_event(StateEventKind.LAP_COMPLETED) or fuel.per_lap_source != "measured":
            return out
        measured_laps = sum(1 for lap in ctx.state.laps if lap.fuel_used_l and lap.clean)
        if measured_laps < 2 or fuel.surplus_laps is None:
            return out
        lap = me.lap
        if fuel.surplus_laps < -0.3:
            if lap - self.last_critical_lap >= 3:
                self.last_critical_lap = lap
                deficit = -fuel.surplus_laps
                target = fuel.target_per_lap_l or 0.0
                if not self.saving:
                    out.append(ctx.event("fuel.critical", Priority.HIGH, "fuel", ttl=30,
                                         deficit=sp.laps(deficit, 1), target=sp.fuel(target, ctx.frame.player.fuel_unit, 2)))
                else:
                    out.append(ctx.event("fuel.save", Priority.INFO, "fuel", ttl=30, target=sp.fuel(target, ctx.frame.player.fuel_unit, 2),
                                         lift=sp.metres(fuel.lift_coast_m or 30)))
                self.saving = True
            return out
        if self.saving and fuel.surplus_laps >= 0.6:
            self.saving = False
            out.append(ctx.event("fuel.save_done", Priority.INFO, "fuel", ttl=30))
            self.ok_called = True
            return out
        if not self.ok_called and fuel.surplus_laps >= 0:
            self.ok_called = True
            out.append(ctx.event("fuel.ok", Priority.INFO, "fuel", ttl=40, surplus=sp.laps(fuel.surplus_laps, 1)))
        elif not self.surplus_called and fuel.surplus_laps > 1.5 and measured_laps >= 4:
            self.surplus_called = True
            out.append(ctx.event("fuel.surplus", Priority.CHATTER, "fuel", ttl=40,
                                 surplus=sp.laps(fuel.surplus_laps, 1)))
        return out
