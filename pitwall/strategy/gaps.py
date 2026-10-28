"""Abstände: Vorder-/Hintermann, Trend pro Runde, DRS-Prognose, Reifen-Offset."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from ..core.frame import CarState
from ..core.state import OpponentHistory


@dataclass(slots=True)
class GapInfo:
    idx: int
    name: str
    position: int | None
    gap_s: float | None  # + = vor mir, − = hinter mir
    delta_per_lap_s: float | None  # + = Abstand wächst
    laps_to_drs: float | None  # nur wenn wir aufholen
    laps_to_catch: float | None  # bis Abstand 0
    compound: str | None
    tyre_age: int | None
    tyre_offset_laps: int | None  # + = seine Reifen sind älter
    pit_stops: int
    in_pit: bool
    last_lap_s: float | None
    is_teammate: bool

    def to_dict(self) -> dict[str, Any]:
        def r(v: float | None, n: int = 2) -> float | None:
            return None if v is None else round(v, n)

        return {
            "idx": self.idx,
            "name": self.name,
            "position": self.position,
            "gap_s": r(self.gap_s),
            "delta_per_lap_s": r(self.delta_per_lap_s),
            "laps_to_drs": r(self.laps_to_drs, 1),
            "laps_to_catch": r(self.laps_to_catch, 1),
            "compound": self.compound,
            "tyre_age": self.tyre_age,
            "tyre_offset_laps": self.tyre_offset_laps,
            "pit_stops": self.pit_stops,
            "in_pit": self.in_pit,
            "last_lap_s": r(self.last_lap_s, 3),
            "is_teammate": self.is_teammate,
        }


def gap_trend(history: OpponentHistory, current_gap: float | None, laps: int = 3) -> float | None:
    """Veränderung des |Abstands| pro Runde (Steigung über die letzten Linienüberquerungen)."""
    items = sorted(history.gap_to_me_at_line.items())[-laps - 1:]
    if current_gap is not None and items:
        items = [*items[:-1], (items[-1][0], items[-1][1])]
    if len(items) < 2:
        return None
    x = np.array([k for k, _ in items], dtype=float)
    y = np.array([abs(v) for _, v in items], dtype=float)
    if np.ptp(x) == 0:
        return None
    return float(np.polyfit(x, y, 1)[0])


def build_gap_info(car: CarState, gap: float | None, history: OpponentHistory | None, my_tyre_age: int | None,
                   drs_range_s: float = 1.0) -> GapInfo:
    delta = gap_trend(history, gap) if history else None
    laps_to_drs = laps_to_catch = None
    if gap is not None and delta is not None and delta < -0.02:
        closing = -delta
        dist = abs(gap)
        laps_to_catch = dist / closing
        if dist > drs_range_s:
            laps_to_drs = (dist - drs_range_s) / closing
        else:
            laps_to_drs = 0.0
    offset = None
    if car.tyre_age_laps is not None and my_tyre_age is not None:
        offset = car.tyre_age_laps - my_tyre_age
    return GapInfo(
        idx=car.idx,
        name=car.driver_name or f"Auto {car.car_number or car.idx}",
        position=car.position,
        gap_s=gap,
        delta_per_lap_s=delta,
        laps_to_drs=laps_to_drs,
        laps_to_catch=laps_to_catch,
        compound=car.tyre_compound,
        tyre_age=car.tyre_age_laps,
        tyre_offset_laps=offset,
        pit_stops=car.pit_stops,
        in_pit=car.in_pit,
        last_lap_s=car.last_lap_s,
        is_teammate=car.is_teammate,
    )
