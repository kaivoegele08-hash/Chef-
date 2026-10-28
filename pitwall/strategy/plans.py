"""Stopp-Pläne: Renndauer-Schätzung für 0/1/2/3-Stopp-Varianten mit Mischungswahl.

Rundenzeit-Modell (je Mischung c):

    t(age) = base + pace_c + deg_c·age + cliff_term(age) + fuel·k

Die Summe eines Stints wird geschlossen berechnet (O(1)), dadurch lassen sich
alle Kombinationen aus Stopp-Runden und Mischungen jede Runde neu durchrechnen.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class CompoundModel:
    name: str
    pace_s: float
    deg_s: float
    cliff_lap: int
    cliff_quad: float = 0.06
    max_laps: int = 60

    def stint_time(self, start_age: int, n: int) -> float:
        """Summe Reifen-Anteil der Rundenzeiten über n Runden ab Alter start_age (ohne Basis/Sprit)."""
        if n <= 0:
            return 0.0
        a0 = start_age + 1
        a1 = start_age + n
        # Σ age von a0..a1
        s_age = (a0 + a1) * n / 2
        t = n * self.pace_s + self.deg_s * s_age
        # Klippe: Σ (age − cliff)² für age > cliff
        c = self.cliff_lap
        lo = max(a0, c + 1)
        if a1 >= lo:
            # Σ_{k=lo-c}^{a1-c} k²
            def sq(m: int) -> float:
                return m * (m + 1) * (2 * m + 1) / 6

            t += self.cliff_quad * (sq(a1 - c) - sq(lo - c - 1))
        return t


@dataclass(slots=True)
class Plan:
    stops: int
    pit_laps: list[int]
    compounds: list[str]  # Stint-Mischungen inkl. aktuellem Stint
    total_time_s: float
    delta_to_best_s: float = 0.0
    window: list[tuple[int, int]] = field(default_factory=list)  # je Stopp (früheste, späteste) Runde

    @property
    def label(self) -> str:
        return f"{self.stops}-Stopp"

    def to_dict(self) -> dict[str, Any]:
        return {
            "stops": self.stops,
            "pit_laps": self.pit_laps,
            "compounds": self.compounds,
            "total_time_s": round(self.total_time_s, 1),
            "delta_s": round(self.delta_to_best_s, 1),
            "window": self.window,
        }


@dataclass(slots=True)
class PlanResult:
    plan_a: Plan | None
    plan_b: Plan | None
    by_stops: dict[int, Plan]
    laps_remaining: int
    pit_loss_s: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_a": self.plan_a.to_dict() if self.plan_a else None,
            "plan_b": self.plan_b.to_dict() if self.plan_b else None,
            "by_stops": {str(k): v.to_dict() for k, v in self.by_stops.items()},
            "laps_remaining": self.laps_remaining,
            "pit_loss_s": round(self.pit_loss_s, 1),
        }


def plan_race(
    current_lap: int,
    total_laps: int,
    current_compound: str,
    current_age: int,
    compounds: dict[str, CompoundModel],
    pit_loss_s: float,
    max_stops: int = 3,
    used_compounds: set[str] | None = None,
    two_compound_rule: bool = False,
    wet: bool = False,
    min_stint: int = 3,
) -> PlanResult:
    """``current_lap`` = Runde, die gerade gefahren wird. Stopps am Ende der Runde ``pit_lap``."""
    laps_left = max(0, total_laps - current_lap + 1)  # inkl. laufender Runde
    used = set(used_compounds or set()) | {current_compound}
    dry = [c for c in compounds if c not in ("inter", "wet")]
    wet_set = [c for c in compounds if c in ("inter", "wet")]
    choices = wet_set if wet else dry
    if not choices:
        choices = list(compounds)
    cur_model = compounds.get(current_compound) or next(iter(compounds.values()))
    by_stops: dict[int, Plan] = {}
    all_plans: list[Plan] = []
    if laps_left <= 0:
        return PlanResult(None, None, {}, 0, pit_loss_s)

    def needs_rule(stints: list[str]) -> bool:
        if not two_compound_rule or wet:
            return False
        dry_used = {c for c in (used | set(stints)) if c in dry}
        return len(dry_used) < 2

    for stops in range(0, max_stops + 1):
        if stops > 0 and laps_left < (stops + 1) * min_stint - min_stint + 1:
            break
        best: Plan | None = None
        # Stopp-Runden: relativ zur aktuellen Runde, 0..laps_left-1 (Stopp am Rundenende)
        step = 1 if stops <= 2 else 2
        candidates = range(0, laps_left - 1, step)
        for offsets in itertools.combinations(candidates, stops):
            # Stints: [0..o1], (o1..o2], ...
            bounds = [-1, *offsets, laps_left - 1]
            lengths = [bounds[i + 1] - bounds[i] for i in range(len(bounds) - 1)]
            if stops and min(lengths[1:]) < min_stint:
                continue
            if stops and lengths[0] + current_age < min_stint:
                continue
            if lengths[0] + current_age > cur_model.max_laps:
                continue
            for comps in itertools.product(choices, repeat=stops):
                stint_compounds = [current_compound, *comps]
                if needs_rule(stint_compounds):
                    continue
                total = cur_model.stint_time(current_age, lengths[0])
                ok = True
                for comp, n in zip(comps, lengths[1:], strict=True):
                    model = compounds[comp]
                    if n > model.max_laps:
                        ok = False
                        break
                    total += model.stint_time(0, n)
                if not ok:
                    continue
                total += stops * pit_loss_s
                if best is None or total < best.total_time_s:
                    best = Plan(stops, [current_lap + o for o in offsets], stint_compounds, total)
        if best is not None:
            by_stops[stops] = best
            all_plans.append(best)
    if not all_plans:
        return PlanResult(None, None, {}, laps_left, pit_loss_s)
    all_plans.sort(key=lambda p: p.total_time_s)
    best_time = all_plans[0].total_time_s
    for p in all_plans:
        p.delta_to_best_s = p.total_time_s - best_time
    plan_a = all_plans[0]
    plan_b = all_plans[1] if len(all_plans) > 1 else None
    _fill_windows(plan_a, current_lap, laps_left, current_compound, current_age, compounds, pit_loss_s)
    if plan_b is not None:
        _fill_windows(plan_b, current_lap, laps_left, current_compound, current_age, compounds, pit_loss_s)
    return PlanResult(plan_a, plan_b, by_stops, laps_left, pit_loss_s)


def _fill_windows(plan: Plan, current_lap: int, laps_left: int, current_compound: str, current_age: int,
                  compounds: dict[str, CompoundModel], pit_loss_s: float, tolerance_s: float = 1.0) -> None:
    """Für jeden Stopp: Bereich von Runden, in dem der Plan ≤ tolerance_s schlechter ist."""
    if plan.stops == 0:
        plan.window = []
        return
    plan.window = []
    for k in range(plan.stops):
        lo = hi = plan.pit_laps[k]
        base = plan.total_time_s
        for direction in (-1, 1):
            lap = plan.pit_laps[k]
            while True:
                lap += direction
                trial = list(plan.pit_laps)
                trial[k] = lap
                t = _evaluate(trial, plan.compounds, current_lap, laps_left, current_age, compounds, pit_loss_s)
                if t is None or t - base > tolerance_s:
                    break
                if direction < 0:
                    lo = lap
                else:
                    hi = lap
        plan.window.append((lo, hi))


def _evaluate(pit_laps: list[int], stint_compounds: list[str], current_lap: int, laps_left: int, current_age: int,
              compounds: dict[str, CompoundModel], pit_loss_s: float) -> float | None:
    offsets = [p - current_lap for p in pit_laps]
    if any(o < 0 or o >= laps_left - 1 for o in offsets) or offsets != sorted(offsets):
        return None
    bounds = [-1, *offsets, laps_left - 1]
    lengths = [bounds[i + 1] - bounds[i] for i in range(len(bounds) - 1)]
    if any(n <= 0 for n in lengths):
        return None
    first = compounds.get(stint_compounds[0]) or next(iter(compounds.values()))
    total = first.stint_time(current_age, lengths[0])
    for comp, n in zip(stint_compounds[1:], lengths[1:], strict=True):
        total += compounds[comp].stint_time(0, n)
    return total + len(pit_laps) * pit_loss_s
