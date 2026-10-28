"""Boxenstopp: Boxenverlust, Wiedereinstiegsposition, Verkehr, Under-/Overcut."""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any

from ..core.frame import CarStatus, SafetyCar


@dataclass(slots=True)
class PitLossModel:
    """Boxenverlust je Strecke; Startwert aus Konfig, verfeinert durch echte Stopps."""

    base_s: float = 22.0
    sc_factor: float = 0.55
    vsc_factor: float = 0.7
    samples: list[float] = field(default_factory=list)

    def learn(self, sample: float) -> None:
        if 5.0 < sample < 90.0:
            self.samples.append(sample)
            self.samples = self.samples[-12:]

    @property
    def loss_s(self) -> float:
        if not self.samples:
            return self.base_s
        learned = statistics.median(self.samples)
        # sanft gewichten: ein einzelner Stopp ersetzt den Startwert nicht komplett
        w = min(1.0, len(self.samples) / 3)
        return (1 - w) * self.base_s + w * learned

    def effective(self, sc: SafetyCar) -> float:
        if sc in (SafetyCar.SC, SafetyCar.ENDING):
            return self.loss_s * self.sc_factor
        if sc in (SafetyCar.VSC, SafetyCar.FCY):
            return self.loss_s * self.vsc_factor
        return self.loss_s


@dataclass(slots=True)
class RejoinCar:
    idx: int
    name: str
    gap_after_s: float  # + = vor mir nach dem Stopp
    position: int | None
    compound: str | None
    tyre_age: int | None


@dataclass(slots=True)
class RejoinPrediction:
    pit_loss_s: float
    position: int | None
    ahead: RejoinCar | None
    behind: RejoinCar | None
    traffic: list[RejoinCar]  # Autos innerhalb ±1,5 s
    free_air: bool

    def to_dict(self) -> dict[str, Any]:
        def car(c: RejoinCar | None) -> dict[str, Any] | None:
            if c is None:
                return None
            return {"idx": c.idx, "name": c.name, "gap_s": round(c.gap_after_s, 2), "position": c.position,
                    "compound": c.compound, "tyre_age": c.tyre_age}

        return {
            "pit_loss_s": round(self.pit_loss_s, 1),
            "position": self.position,
            "ahead": car(self.ahead),
            "behind": car(self.behind),
            "traffic": [car(c) for c in self.traffic],
            "free_air": self.free_air,
        }


def predict_rejoin(gaps: dict[int, float], cars_info: dict[int, dict[str, Any]], pit_loss_s: float) -> RejoinPrediction:
    """``gaps``: idx → Zeitabstand jetzt (+ vor mir). Gibt Position nach dem Stopp zurück."""
    after: list[RejoinCar] = []
    for idx, gap in gaps.items():
        info = cars_info.get(idx, {})
        if info.get("status", CarStatus.ACTIVE) not in (CarStatus.ACTIVE,) or info.get("in_pit"):
            # Autos, die selbst gerade in der Box sind, verfälschen das Bild
            if info.get("in_pit"):
                gap = gap - pit_loss_s * 0.5
            else:
                continue
        after.append(RejoinCar(idx, info.get("name", f"Auto {idx}"), gap + pit_loss_s, info.get("position"),
                               info.get("compound"), info.get("tyre_age")))
    ahead_cars = [c for c in after if c.gap_after_s > 0]
    behind_cars = [c for c in after if c.gap_after_s <= 0]
    position = len(ahead_cars) + 1 if after else None
    ahead = min(ahead_cars, key=lambda c: c.gap_after_s) if ahead_cars else None
    behind = max(behind_cars, key=lambda c: c.gap_after_s) if behind_cars else None
    traffic = sorted((c for c in after if abs(c.gap_after_s) < 1.5), key=lambda c: -c.gap_after_s)
    return RejoinPrediction(pit_loss_s, position, ahead, behind, traffic, not traffic)


@dataclass(slots=True)
class CutAssessment:
    rival_idx: int
    rival_name: str
    gap_s: float  # + = Rivale vor mir
    kind: str  # undercut | overcut | defend_undercut
    expected_gain_s: float
    feasible: bool
    laps_considered: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "rival_idx": self.rival_idx,
            "rival": self.rival_name,
            "gap_s": round(self.gap_s, 2),
            "kind": self.kind,
            "gain_s": round(self.expected_gain_s, 2),
            "feasible": self.feasible,
            "laps": self.laps_considered,
        }


def assess_undercut(
    rival_idx: int,
    rival_name: str,
    gap_s: float,
    rival_recent_lap_s: float | None,
    rival_tyre_age: int | None,
    my_new_tyre_lap_s: float | None,
    deg_s_per_lap: float,
    outlap_penalty_s: float = 1.2,
    laps: int = 2,
) -> CutAssessment | None:
    """Undercut auf einen Gegner vor mir (gap > 0) oder Gefahr durch Gegner hinter mir (gap < 0).

    Gewinn ≈ Summe(Rivale alte Reifen − ich neue Reifen) über ``laps`` Runden − Outlap-Aufwärmverlust.
    """
    if rival_recent_lap_s is None or my_new_tyre_lap_s is None:
        return None
    gain = 0.0
    age = rival_tyre_age or 0
    for k in range(laps):
        rival_time = rival_recent_lap_s + deg_s_per_lap * (k + 1)
        gain += rival_time - my_new_tyre_lap_s
    gain -= outlap_penalty_s
    _ = age
    if gap_s > 0:
        return CutAssessment(rival_idx, rival_name, gap_s, "undercut", gain, gain > gap_s + 0.3, laps)
    return CutAssessment(rival_idx, rival_name, gap_s, "defend_undercut", gain, gain > -gap_s, laps)
