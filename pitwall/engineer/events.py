"""Funk-Ereignisse zwischen Detektoren, Director und Sprachausgabe."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

_ids = itertools.count(1)


class Priority(IntEnum):
    CHATTER = 0  # Lob, Smalltalk
    INFO = 1  # Gaps, Sprit, Rundenzeiten
    HIGH = 2  # Undercut-Gefahr, Gelb, Boxenfenster
    ANSWER = 3  # Antwort auf eine Fahrerfrage (vor proaktiven Infos)
    CRITICAL = 4  # Box jetzt, SC, Schaden, Plattfuß


@dataclass(slots=True)
class RadioEvent:
    key: str  # Phrasebook-Schlüssel, z. B. "gap.ahead_closing"
    priority: Priority
    category: str  # gaps | fuel | tyres | strategy | flags | race | weather | coaching | quali | truck | answer
    data: dict[str, Any] = field(default_factory=dict)
    created_t: float = 0.0
    ttl_s: float = 10.0  # danach veraltet
    cooldown_key: str | None = None
    cooldown_s: float = 0.0
    interrupt: bool = False  # darf laufende Ausgabe niedrigerer Priorität abbrechen
    allow_in_silence: bool = False  # auch während Quali-Push-Lap / Funkstille
    text: str | None = None  # bereits formulierter Text (LLM/Antwort)
    spoken: str | None = None
    id: int = field(default_factory=lambda: next(_ids))
    source: str = "detector"

    @property
    def expires_t(self) -> float:
        return self.created_t + self.ttl_s

    def expired(self, now: float) -> bool:
        return now > self.expires_t


@dataclass(slots=True)
class Utterance:
    """Was tatsächlich gesprochen wird."""

    event: RadioEvent
    text: str  # Anzeige-Text
    spoken: str  # Text für TTS
    start_t: float
    duration_s: float
    priority: Priority
    lap: int | None = None
    interrupted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.event.id,
            "key": self.event.key,
            "text": self.text,
            "priority": self.priority.name,
            "category": self.event.category,
            "t": round(self.start_t, 2),
            "lap": self.lap,
            "source": self.event.source,
            "duration_s": round(self.duration_s, 2),
        }
