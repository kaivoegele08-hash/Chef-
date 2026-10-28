"""Basis für regelbasierte Event-Detektoren."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ...core.frame import CarState, SessionType, TelemetryFrame
from ...core.state import RaceState, StateEvent, StateEventKind
from ..events import Priority, RadioEvent
from ..speech import Speaker

if TYPE_CHECKING:
    from ...config import Config
    from ...core.track import TrackModel
    from ...strategy.engine import StrategySnapshot
    from ..memory import RaceMemory


@dataclass
class EngineerContext:
    frame: TelemetryFrame
    state: RaceState
    strategy: StrategySnapshot
    track: TrackModel | None
    events: list[StateEvent]
    memory: RaceMemory
    config: Config
    speaker: Speaker
    vocabulary: str = "formula"  # formula | gt | prototype | truck
    flags: dict[str, Any] = field(default_factory=dict)  # gemeinsame Zustände (z. B. push_lap)

    @property
    def t(self) -> float:
        return self.frame.t

    @property
    def me(self) -> CarState | None:
        return self.frame.me

    @property
    def is_race(self) -> bool:
        return self.frame.session.session_type is SessionType.RACE

    @property
    def is_quali(self) -> bool:
        return self.frame.session.session_type.is_qualifying

    @property
    def is_practice(self) -> bool:
        return self.frame.session.session_type in (SessionType.PRACTICE, SessionType.WARMUP, SessionType.UNKNOWN)

    @property
    def formula(self) -> bool:
        return self.vocabulary == "formula"

    def has_event(self, kind: StateEventKind) -> StateEvent | None:
        for e in self.events:
            if e.kind is kind:
                return e
        return None

    def all_events(self, kind: StateEventKind) -> list[StateEvent]:
        return [e for e in self.events if e.kind is kind]

    def avg_lap(self) -> float:
        return self.strategy.avg_lap_s or (self.me.best_lap_s if self.me and self.me.best_lap_s else 90.0)

    def event(self, key: str, priority: Priority, category: str, ttl: float = 10.0,
              cooldown_key: str | None = None, cooldown_s: float = 0.0, interrupt: bool = False,
              allow_in_silence: bool = False, **data: Any) -> RadioEvent:
        return RadioEvent(key=key, priority=priority, category=category, data=data, created_t=self.t, ttl_s=ttl,
                          cooldown_key=cooldown_key, cooldown_s=cooldown_s, interrupt=interrupt,
                          allow_in_silence=allow_in_silence)


class Detector:
    name = "base"
    #: nur für diese Fahrzeugklassen-Vokabulare aktiv (None = alle)
    vocabularies: tuple[str, ...] | None = None

    def reset(self) -> None:
        """Bei neuer Session/Neustart."""

    def on_flashback(self, ctx: EngineerContext) -> None:
        """Nach einem Flashback (Standard: wie Reset)."""
        self.reset()

    def update(self, ctx: EngineerContext) -> list[RadioEvent]:
        raise NotImplementedError
