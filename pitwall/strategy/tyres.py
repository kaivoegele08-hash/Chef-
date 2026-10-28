"""Reifen: sprit-korrigierte Degradation, Verschleiß-Hochrechnung, Temperaturfenster."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..core.frame import WHEELS, PlayerState
from ..core.state import LapRecord


@dataclass(slots=True)
class DegradationFit:
    compound: str | None
    laps_used: int
    deg_s_per_lap: float | None  # sprit-korrigiert
    base_s: float | None  # korrigierte Zeit bei Alter 0 und leerem Tank
    r2: float | None
    raw_trend_s_per_lap: float | None  # unkorrigiert (was der Fahrer spürt)

    def predict(self, age: int, fuel_l: float, time_per_litre: float) -> float | None:
        if self.base_s is None or self.deg_s_per_lap is None:
            return None
        return self.base_s + self.deg_s_per_lap * age + fuel_l * time_per_litre


def fit_degradation(laps: list[LapRecord], time_per_litre: float = 0.03, min_laps: int = 3) -> DegradationFit:
    """Lineare Regression der sprit-korrigierten Rundenzeit über das Reifenalter (aktueller Stint)."""
    compound = laps[-1].compound if laps else None
    stint: list[LapRecord] = []
    for lap in reversed(laps):
        if lap.pit_in or lap.pit_out:
            if lap.pit_out:
                break
            continue
        if lap.compound != compound:
            break
        stint.append(lap)
    stint.reverse()
    pts = [(lap.tyre_age, lap.time_s, lap.fuel_start_l) for lap in stint if lap.clean and lap.time_s and
           lap.tyre_age is not None]
    if len(pts) < min_laps:
        return DegradationFit(compound, len(pts), None, None, None, None)
    ages = np.array([p[0] for p in pts], dtype=float)
    times = np.array([p[1] for p in pts], dtype=float)
    fuel = np.array([p[2] if p[2] is not None else 0.0 for p in pts], dtype=float)
    # Ausreißer (Fehler, Verkehr) per Median-Filter entfernen
    med = np.median(times)
    keep = np.abs(times - med) < max(1.5, 4 * np.median(np.abs(times - med)) + 0.3)
    ages, times, fuel = ages[keep], times[keep], fuel[keep]
    if len(ages) < min_laps or np.ptp(ages) == 0:
        return DegradationFit(compound, int(len(ages)), None, None, None, None)
    corrected = times - fuel * time_per_litre
    slope, intercept = np.polyfit(ages, corrected, 1)
    raw_slope = float(np.polyfit(ages, times, 1)[0])
    pred = slope * ages + intercept
    ss_res = float(np.sum((corrected - pred) ** 2))
    ss_tot = float(np.sum((corrected - corrected.mean()) ** 2)) or 1e-9
    return DegradationFit(compound, int(len(ages)), float(slope), float(intercept), 1 - ss_res / ss_tot, raw_slope)


@dataclass(slots=True)
class WearProjection:
    wear_now: list[float | None]
    wear_per_lap: list[float | None]
    limiting_wheel: str | None
    laps_to_limit: float | None
    limit_pct: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "wear_now": self.wear_now,
            "wear_per_lap": self.wear_per_lap,
            "limiting_wheel": self.limiting_wheel,
            "laps_to_limit": self.laps_to_limit,
            "limit_pct": self.limit_pct,
        }


def project_wear(laps: list[LapRecord], player: PlayerState, limit_pct: float = 70.0) -> WearProjection:
    now = [w.wear_pct for w in player.wheels[:4]]
    rates: list[float | None] = [None] * 4
    stint: list[LapRecord] = []
    for lap in reversed(laps):
        if lap.pit_out or (stint and lap.compound != stint[-1].compound):
            if lap.pit_out:
                stint.append(lap)
            break
        stint.append(lap)
    stint.reverse()
    for w in range(4):
        series = [(i, lap.wear_end[w]) for i, lap in enumerate(stint) if len(lap.wear_end) > w and
                  lap.wear_end[w] is not None]
        if len(series) >= 2:
            x = np.array([s[0] for s in series], dtype=float)
            y = np.array([s[1] for s in series], dtype=float)
            if np.ptp(x) > 0:
                rates[w] = max(0.0, float(np.polyfit(x, y, 1)[0]))
    limiting = None
    laps_to_limit = None
    for w in range(4):
        if now[w] is None or not rates[w]:
            continue
        left = (limit_pct - (now[w] or 0.0)) / rates[w]  # type: ignore[operator]
        if laps_to_limit is None or left < laps_to_limit:
            laps_to_limit = left
            limiting = WHEELS[w]
    return WearProjection(now, rates, limiting, laps_to_limit, limit_pct)


@dataclass(slots=True)
class TempStatus:
    window: tuple[float, float] | None
    temps: list[float | None]
    states: list[str]  # cold | ok | hot | unknown
    worst_wheel: str | None = None
    worst_delta: float | None = None  # °C außerhalb des Fensters (+heiß/−kalt)
    pressures_kpa: list[float | None] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "window": self.window,
            "temps": [None if t is None else round(t, 1) for t in self.temps],
            "states": self.states,
            "worst_wheel": self.worst_wheel,
            "worst_delta": self.worst_delta,
            "pressures_kpa": [None if p is None else round(p, 1) for p in self.pressures_kpa],
        }


def temperature_status(temps: list[float | None], window: tuple[float, float] | None,
                       pressures: list[float | None] | None = None) -> TempStatus:
    states: list[str] = []
    worst: tuple[str, float] | None = None
    for i, t in enumerate(temps):
        if t is None or window is None:
            states.append("unknown")
            continue
        lo, hi = window
        if t < lo:
            states.append("cold")
            delta = t - lo
        elif t > hi:
            states.append("hot")
            delta = t - hi
        else:
            states.append("ok")
            delta = 0.0
        if delta and (worst is None or abs(delta) > abs(worst[1])):
            worst = (WHEELS[i], delta)
    return TempStatus(window, temps, states, worst[0] if worst else None, worst[1] if worst else None,
                      pressures or [])
