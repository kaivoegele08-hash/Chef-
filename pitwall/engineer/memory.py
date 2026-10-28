"""Engineer-Gedächtnis pro Rennen: was gesagt/gefragt wurde, Fahrernotizen (Balance).

* Reset bei neuer Session / Rennneustart
* Flashback: Einträge aus zurückgespulten Runden werden entfernt
* Kontext für das LLM (kompakter Verlauf)
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class MemoryEntry:
    t: float
    lap: int
    role: str  # engineer | driver | note | system
    text: str
    key: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"t": round(self.t, 1), "lap": self.lap, "role": self.role, "text": self.text, "key": self.key}


@dataclass
class RaceMemory:
    entries: deque[MemoryEntry] = field(default_factory=lambda: deque(maxlen=400))
    notes: list[MemoryEntry] = field(default_factory=list)  # Balance etc. – bleiben bei Flashback
    awaiting_balance: bool = False
    said_keys: dict[str, float] = field(default_factory=dict)
    session_id: str = ""

    def reset(self, session_id: str = "") -> None:
        self.entries.clear()
        self.notes.clear()
        self.awaiting_balance = False
        self.said_keys.clear()
        self.session_id = session_id

    def add(self, t: float, lap: int, role: str, text: str, key: str = "") -> MemoryEntry:
        e = MemoryEntry(t, lap, role, text, key)
        self.entries.append(e)
        if role == "engineer" and key:
            self.said_keys[key] = t
        return e

    def note(self, t: float, lap: int, text: str) -> None:
        self.notes.append(MemoryEntry(t, lap, "note", text))
        self.notes = self.notes[-20:]

    def rollback(self, to_t: float, to_lap: int) -> int:
        """Flashback: alles nach ``to_t`` entfernen (Notizen des Fahrers bleiben)."""
        before = len(self.entries)
        kept = [e for e in self.entries if e.t <= to_t or e.role == "driver"]
        self.entries.clear()
        self.entries.extend(kept)
        self.said_keys = {k: v for k, v in self.said_keys.items() if v <= to_t}
        return before - len(self.entries)

    def recent(self, n: int = 12) -> list[MemoryEntry]:
        return list(self.entries)[-n:]

    def context_text(self, n: int = 12, lang: str = "de") -> str:
        """Kompakter Verlauf für den LLM-Prompt."""
        de = lang == "de"
        lines: list[str] = []
        if self.notes:
            lines.append("Fahrernotizen:" if de else "Driver notes:")
            lines.extend(f"- R{e.lap}: {e.text}" for e in self.notes[-6:])
        recent = self.recent(n)
        if recent:
            lines.append("Letzter Funkverkehr:" if de else "Recent radio:")
            label = {"engineer": "Engineer", "driver": "Fahrer" if de else "Driver", "system": "System",
                     "note": "Notiz" if de else "Note"}
            lines.extend(f"- R{e.lap} {label.get(e.role, e.role)}: {e.text}" for e in recent)
        return "\n".join(lines)
