"""Macht Phrasebook-Funksprüche lebendiger: gelegentliche Einstiege wie im echten Boxenfunk.

Echte Renningenieure beginnen oft mit "Okay," oder dem Namen des Fahrers, aber nicht jedes Mal.
Nur für Phrasebook-Texte; freie Antworten von Claude bleiben unverändert.
"""

from __future__ import annotations

import random

from .events import Priority

# Einstiege je Sprache und Situation (ohne Namen)
OPENERS = {
    "de": {
        "info": ["Okay.", "So.", "Gut.", "Kurze Info."],
        "answer": ["Okay.", "Verstanden.", "Moment.", "Ja."],
        "high": ["Achtung."],
    },
    "en": {
        "info": ["Okay.", "Right.", "So.", "Quick one."],
        "answer": ["Okay.", "Copy.", "Yeah.", "Stand by."],
        "high": ["Heads up."],
    },
}
# Wahrscheinlichkeit für einen Einstieg / für die Anrede mit Namen
P_OPENER = {Priority.CHATTER: 0.35, Priority.INFO: 0.35, Priority.HIGH: 0.25, Priority.ANSWER: 0.3,
            Priority.CRITICAL: 0.0}
P_NAME = {Priority.CHATTER: 0.12, Priority.INFO: 0.15, Priority.HIGH: 0.2, Priority.ANSWER: 0.1,
          Priority.CRITICAL: 0.35}
# Momente, die für sich stehen (Start, Ziel, Box-Ruf, Systemmeldungen) – dort kein Einstieg
_NO_OPENER = ("race.lights_out", "race.grid", "race.finished", "race.last_lap", "race.retired", "system.",
              "strategy.box", "flags.chequered", "quali.", "truck.delivered", "answer.radio_mode")
# "Achtung" nur vor echten Warnungen
_WARN_CATEGORIES = {"gaps", "tyres", "flags", "damage", "weather", "fuel", "truck"}
_ALREADY = ("okay", "ok,", "copy", "right", "so,", "gut,", "box", "achtung", "heads up", "verstanden", "ja,")


class Humanizer:
    def __init__(self, lang: str = "de", driver_name: str = "", enabled: bool = True, seed: int | None = None):
        self.lang = "en" if lang.startswith("en") else "de"
        self.driver = driver_name.strip()
        self.enabled = enabled
        self.rng = random.Random(seed)
        self._last_opener = ""
        self._name_used_t = -1e9

    def apply(self, display: str, spoken: str, priority: Priority, t: float = 0.0, key: str = "",
              category: str = "") -> tuple[str, str]:
        if not self.enabled or not display or key.startswith(_NO_OPENER):
            return display, spoken
        low = display.lower()
        if low.startswith(_ALREADY) or (self.driver and low.startswith(self.driver.lower())):
            return display, spoken
        # Anrede mit Namen – höchstens alle 45 s, sonst wirkt es aufdringlich
        if self.driver and t - self._name_used_t > 45 and self.rng.random() < P_NAME.get(priority, 0.0):
            self._name_used_t = t
            return f"{self.driver}, {_lower_first(display, self.lang)}", f"{self.driver}, {_lower_first(spoken, self.lang)}"
        if self.rng.random() >= P_OPENER.get(priority, 0.0):
            return display, spoken
        if priority is Priority.HIGH and category not in _WARN_CATEGORIES:
            return display, spoken
        kind = "answer" if priority is Priority.ANSWER else "high" if priority is Priority.HIGH else "info"
        # kein "Gut. … Gut gemacht." – Einstieg nicht nehmen, wenn das Wort ohnehin vorkommt
        options = [o for o in OPENERS[self.lang][kind]
                   if o != self._last_opener and o.rstrip(".").lower() not in low]
        if not options:
            return display, spoken
        opener = self.rng.choice(options)
        self._last_opener = opener
        return f"{opener} {display}", f"{opener} {spoken}"


_DE_LOWER = {"du", "dein", "deine", "deinen", "wir", "ich", "das", "die", "der", "den", "dem", "es", "noch", "jetzt",
             "gut", "sauber", "pass", "bleib", "fahr", "kein", "keine", "zwei", "drei", "vier", "fünf", "nur", "ab",
             "in", "auf", "mach", "schön", "starke", "starker", "starkes", "wird", "lass", "weiter", "da", "hier",
             "push", "achtung", "neue", "neuer", "neues", "letzte", "letzter"}


def _lower_first(text: str, lang: str) -> str:
    """Nach "Name," klein weiterschreiben – im Deutschen nur bei Wörtern, die sicher keine Nomen sind."""
    if not text:
        return text
    first = text.split(" ", 1)[0].strip(",.!?")
    if len(first) > 1 and first.isupper():
        return text  # Abkürzungen wie DRS, P5
    if lang == "de" and first.lower() not in _DE_LOWER:
        return text
    return text[:1].lower() + text[1:]
