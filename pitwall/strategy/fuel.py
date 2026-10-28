"""Spritrechnung: Verbrauch/Runde, Bedarf bis Ziel, Sparziel, Lift-and-Coast."""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Any

from ..core.state import LapRecord


def robust_mean(values: list[float], window: int = 5) -> float | None:
    """Mittelwert der letzten Werte ohne Ausreißer (Median ± 3·MAD)."""
    vals = [v for v in values if v is not None and v > 0][-max(window * 2, 6):]
    if not vals:
        return None
    if len(vals) >= 3:
        med = statistics.median(vals)
        mad = statistics.median(abs(v - med) for v in vals) or med * 0.02
        vals = [v for v in vals if abs(v - med) <= 3 * mad + 1e-9]
    vals = vals[-window:]
    # jüngere Runden stärker gewichten
    weights = list(range(1, len(vals) + 1))
    return sum(v * w for v, w in zip(vals, weights, strict=True)) / sum(weights)


@dataclass(slots=True)
class FuelStatus:
    fuel_l: float | None
    capacity_l: float | None
    per_lap_l: float | None
    per_lap_source: str  # measured | learned | game | none
    laps_in_tank: float | None
    laps_to_go: float | None  # inkl. Rest der aktuellen Runde
    needed_l: float | None  # bis Ziel inkl. Reserve
    surplus_l: float | None  # + zu viel, − zu wenig
    surplus_laps: float | None
    target_per_lap_l: float | None  # um ins Ziel zu kommen
    save_per_lap_l: float | None  # nötige Ersparnis pro Runde (>0)
    save_pct: float | None
    lift_coast_m: float | None  # grobe Empfehlung pro großer Bremszone
    reserve_laps: float
    refuel_needed_l: float | None = None  # bei Tankstopp-Sims: Menge beim nächsten Stopp

    @property
    def critical(self) -> bool:
        return self.surplus_laps is not None and self.surplus_laps < -0.3

    @property
    def ok_to_finish(self) -> bool:
        return self.surplus_l is not None and self.surplus_l >= 0

    def to_dict(self) -> dict[str, Any]:
        return {k: (round(v, 3) if isinstance(v, float) else v) for k, v in
                ((name, getattr(self, name)) for name in self.__slots__)}  # type: ignore[attr-defined]


def laps_remaining(
    current_lap: int,
    lap_pct: float | None,
    total_laps: int | None,
    time_remaining_s: float | None,
    lap_time_s: float | None,
) -> float | None:
    """Verbleibende Runden inkl. Rest der laufenden Runde."""
    frac_left = 1.0 - (lap_pct or 0.0)
    if total_laps:
        return max(0.0, (total_laps - current_lap) + frac_left)
    if time_remaining_s is not None and lap_time_s:
        # Zeitrennen: nach Ablauf wird die Runde noch zu Ende gefahren
        laps_by_time = time_remaining_s / lap_time_s
        return math.ceil(laps_by_time - frac_left + 1e-6) + frac_left if laps_by_time > frac_left else frac_left
    return None


def fuel_status(
    laps: list[LapRecord],
    fuel_l: float | None,
    capacity_l: float | None,
    current_lap: int,
    lap_pct: float | None,
    total_laps: int | None,
    time_remaining_s: float | None,
    lap_time_s: float | None,
    reserve_laps: float = 0.5,
    learned_per_lap: float | None = None,
    game_laps_remaining: float | None = None,
) -> FuelStatus:
    measured = [lap.fuel_used_l for lap in laps if lap.fuel_used_l and lap.clean]
    per_lap = robust_mean([m for m in measured if m is not None])
    source = "measured"
    if per_lap is None or len(measured) < 2:
        if learned_per_lap:
            per_lap = learned_per_lap if per_lap is None else 0.5 * (per_lap + learned_per_lap)
            source = "learned"
        elif per_lap is None and fuel_l is not None and game_laps_remaining and game_laps_remaining > 0.5:
            # F1 liefert "Sprit für X Runden" relativ zum Ziel; als Notbehelf nutzbar
            per_lap = None
            source = "game"
        elif per_lap is None:
            source = "none"
    to_go = laps_remaining(current_lap, lap_pct, total_laps, time_remaining_s, lap_time_s)
    laps_in_tank = fuel_l / per_lap if (fuel_l is not None and per_lap) else None
    needed = surplus = surplus_laps = target = save = save_pct = lift = None
    if per_lap and to_go is not None and fuel_l is not None:
        needed = (to_go + reserve_laps) * per_lap
        surplus = fuel_l - needed
        surplus_laps = surplus / per_lap
        if to_go > 0.2:
            target = max(0.0, (fuel_l - reserve_laps * per_lap) / to_go)
            save = per_lap - target
            save_pct = 100.0 * save / per_lap
            if save > 0:
                # Faustregel: ~1 % Ersparnis pro 10 m Rollen vor jeder großen Bremszone
                lift = min(150.0, max(20.0, save_pct * 10.0))
    elif source == "game" and game_laps_remaining is not None:
        surplus_laps = game_laps_remaining
    refuel = None
    if needed is not None and capacity_l and needed > capacity_l and fuel_l is not None:
        refuel = needed - fuel_l
    return FuelStatus(
        fuel_l=fuel_l,
        capacity_l=capacity_l,
        per_lap_l=per_lap,
        per_lap_source=source,
        laps_in_tank=laps_in_tank,
        laps_to_go=to_go,
        needed_l=needed,
        surplus_l=surplus,
        surplus_laps=surplus_laps,
        target_per_lap_l=target,
        save_per_lap_l=save if save and save > 0 else None,
        save_pct=save_pct if save_pct and save_pct > 0 else None,
        lift_coast_m=lift,
        reserve_laps=reserve_laps,
        refuel_needed_l=refuel,
    )
