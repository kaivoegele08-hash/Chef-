"""Radio Director: entscheidet *ob*, *wann* und *was* gesprochen wird.

Regeln (wie im echten Boxenfunk):

* Prioritäten: CRITICAL > ANSWER > HIGH > INFO > CHATTER
* Timing nach Streckenposition: Nicht-Kritisches nur auf Geraden, nie in
  Bremszonen (gelernt aus der eigenen Telemetrie). CRITICAL darf sofort.
* Ablaufzeit: veraltete Infos verfallen, statt verspätet zu kommen.
* Rationierung: max. N Infos pro Runde, Cooldowns je Kategorie, keine
  Wiederholungen. Geplauder stark begrenzt.
* Unterbrechen: CRITICAL bricht laufendes INFO/CHATTER ab.
* Funkstille: Quali-Push-Lap, letzte Kurven vor der Linie, während der Fahrer
  spricht (PTT gedrückt).
* Modi: chatty | normal | important | silent.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .events import Priority, RadioEvent, Utterance
from .phrasebook import Phrasebook

if TYPE_CHECKING:
    from ..core.track import TrackModel

log = logging.getLogger(__name__)

MODES = ("chatty", "normal", "important", "silent")


@dataclass(slots=True)
class DirectorInput:
    t: float
    lap: int = 0
    lap_distance_m: float | None = None
    speed_ms: float = 0.0
    throttle: float = 0.0
    brake: float = 0.0
    steering: float = 0.0
    push_lap: bool = False
    ptt_active: bool = False
    in_pit: bool = False
    track: TrackModel | None = None
    track_length_m: float | None = None
    quiet_corners: int = 2
    racing: bool = True  # False = Menü/Garage → keine Zonenprüfung


@dataclass
class DirectorStats:
    spoken: int = 0
    dropped_expired: int = 0
    dropped_rationed: int = 0
    dropped_cooldown: int = 0
    dropped_duplicate: int = 0
    interrupted: int = 0
    held_zone: int = 0
    by_lap: dict[int, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if k != "by_lap"}


class RadioDirector:
    def __init__(self, phrasebook: Phrasebook, mode: str = "normal", max_info_per_lap: int = 3,
                 words_per_second: float = 2.8, on_utterance: Callable[[Utterance], None] | None = None,
                 on_interrupt: Callable[[Utterance], None] | None = None):
        self.pb = phrasebook
        self.mode = mode if mode in MODES else "normal"
        self.max_info_per_lap = max_info_per_lap
        self.wps = words_per_second
        self.on_utterance = on_utterance
        self.on_interrupt = on_interrupt
        self.queue: list[RadioEvent] = []
        self.current: Utterance | None = None
        self.busy_until = -1e9
        self.cooldowns: dict[str, float] = {}
        self.recent_texts: dict[str, float] = {}
        self.lap_counts: dict[int, int] = {}
        self.last_chatter_lap = -10
        self.answer_wait_since: dict[int, float] = {}
        self.ptt_release_t = -1e9
        self._ptt_prev = False
        self.stats = DirectorStats()
        self.history: list[Utterance] = []

    # ---------------------------------------------------------- Steuerung
    def set_mode(self, mode: str) -> None:
        if mode in MODES:
            self.mode = mode

    def submit(self, event: RadioEvent) -> None:
        # Fragen haben Vorrang: offene proaktive Infos derselben Kategorie verwerfen
        if event.priority is Priority.ANSWER:
            self.queue = [e for e in self.queue if not (e.category == event.category and e.priority <= Priority.INFO)]
        self.queue.append(event)

    def submit_many(self, events: list[RadioEvent]) -> None:
        for e in events:
            self.submit(e)

    def clear(self, keep_critical: bool = False) -> None:
        """Funk-Queue leeren (z. B. nach Flashback/Neustart)."""
        if keep_critical:
            self.queue = [e for e in self.queue if e.priority is Priority.CRITICAL]
        else:
            self.queue.clear()

    def reset(self) -> None:
        self.clear()
        self.cooldowns.clear()
        self.recent_texts.clear()
        self.lap_counts.clear()
        self.last_chatter_lap = -10
        self.current = None
        self.busy_until = -1e9

    def speech_finished(self, t: float) -> None:
        """Von der Audio-Ausgabe gemeldet: Wiedergabe tatsächlich beendet."""
        self.busy_until = min(self.busy_until, t)
        self.current = None

    def speech_duration(self, t: float, actual_duration: float) -> None:
        """Audio-Ausgabe kennt die echte Länge erst nach der Synthese."""
        if self.current is not None:
            self.busy_until = self.current.start_t + actual_duration + 0.3

    @property
    def speaking(self) -> bool:
        return self.current is not None

    # ------------------------------------------------------------- Takt
    def tick(self, inp: DirectorInput) -> Utterance | None:
        t = inp.t
        if self.current is not None and t >= self.busy_until:
            self.current = None
        if self._ptt_prev and not inp.ptt_active:
            self.ptt_release_t = t
        self._ptt_prev = inp.ptt_active

        # verfallen lassen
        alive: list[RadioEvent] = []
        for e in self.queue:
            if e.expired(t):
                self.stats.dropped_expired += 1
            else:
                alive.append(e)
        self.queue = alive
        if not self.queue:
            return None

        self.queue.sort(key=lambda e: (-int(e.priority), e.created_t))
        for event in list(self.queue):
            verdict = self._admissible(event, inp)
            if verdict == "drop":
                self.queue.remove(event)
                continue
            if verdict == "hold":
                # Höhere Priorität blockiert nachrangige: nicht an einer gehaltenen CRITICAL vorbeisprechen
                if event.priority >= Priority.HIGH:
                    return None
                continue
            # sprechen!
            if self.current is not None:
                if event.interrupt or event.priority is Priority.CRITICAL:
                    if self.current.priority < Priority.HIGH or (
                            event.priority is Priority.CRITICAL and self.current.priority < Priority.CRITICAL):
                        self._interrupt(t)
                    else:
                        return None
                else:
                    return None
            rendered = self._render(event)
            if rendered is None:
                self.queue.remove(event)
                continue
            text, spoken = rendered
            if self._duplicate(event, text, t):
                self.queue.remove(event)
                self.stats.dropped_duplicate += 1
                continue
            self.queue.remove(event)
            return self._speak(event, text, spoken, inp)
        return None

    # ----------------------------------------------------------- intern
    def _admissible(self, e: RadioEvent, inp: DirectorInput) -> str:
        """'ok' | 'hold' | 'drop'"""
        t = inp.t
        prio = e.priority
        critical = prio is Priority.CRITICAL
        answer = prio is Priority.ANSWER
        # Cooldown
        if e.cooldown_key and not answer:
            until = self.cooldowns.get(e.cooldown_key)
            if until is not None and t < until:
                self.stats.dropped_cooldown += 1
                return "drop"
        # Modus
        if not critical and not answer:
            if self.mode == "silent":
                self.stats.dropped_rationed += 1
                return "drop"
            if self.mode == "important" and prio < Priority.HIGH:
                self.stats.dropped_rationed += 1
                return "drop"
            if prio is Priority.CHATTER:
                gap = 1 if self.mode == "chatty" else 2
                if inp.lap - self.last_chatter_lap < gap:
                    self.stats.dropped_rationed += 1
                    return "drop"
            if prio <= Priority.INFO:
                limit = self.max_info_per_lap + (2 if self.mode == "chatty" else 0)
                if self.lap_counts.get(inp.lap, 0) >= limit:
                    self.stats.dropped_rationed += 1
                    return "drop"
        # Fahrer spricht → warten (CRITICAL nach kurzer Zeit trotzdem)
        if inp.ptt_active:
            return "hold" if not critical or t - e.created_t < 1.5 else "ok"
        if t - self.ptt_release_t < 0.4 and not critical and not answer:
            return "hold"
        if critical:
            return "ok"
        # Funkstille
        if (inp.push_lap or self._in_final_corners(inp)) and not e.allow_in_silence and not answer:
            return "hold"
        if not inp.racing or inp.in_pit:
            return "ok"
        # Zonen-Timing
        duration = self._estimate_duration(e)
        if answer:
            # Antwort möglichst sofort, aber nicht mitten im harten Anbremsen (max. 3 s warten)
            waited = t - e.created_t
            if inp.brake > 0.3 and waited < 3.0:
                return "hold"
            return "ok"
        if self._zone_ok(inp, duration):
            return "ok"
        self.stats.held_zone += 1
        return "hold"

    def _zone_ok(self, inp: DirectorInput, duration: float) -> bool:
        track = inp.track
        if track is not None and track.ready and inp.lap_distance_m is not None:
            window = track.talk_window_s(inp.lap_distance_m, inp.speed_ms)
            if window is not None:
                # Anfang muss auf der Geraden liegen; lange Sätze dürfen in die nächste Kurve laufen,
                # aber nicht in die Bremszone beginnen
                return window >= min(duration, 4.0) + 0.3
        # Ohne gelernte Strecke: Heuristik aus den Eingaben
        return inp.brake < 0.05 and inp.throttle > 0.8 and abs(inp.steering) < 0.15

    def _in_final_corners(self, inp: DirectorInput) -> bool:
        if inp.quiet_corners <= 0 or inp.track is None or not inp.track.corners:
            return False
        remaining = inp.track.corners_remaining(inp.lap_distance_m)
        if remaining is None:
            return False
        return 0 < remaining <= inp.quiet_corners

    def _estimate_duration(self, e: RadioEvent) -> float:
        rendered = e.spoken or e.text
        if rendered is None:
            tpl = self.pb.phrases.get(e.key, [""])[0]
            rendered = tpl
        words = max(1, len(rendered.split()))
        return words / self.wps + 0.6

    def _render(self, e: RadioEvent) -> tuple[str, str] | None:
        if e.text is not None:
            return e.text, e.spoken or e.text
        return self.pb.render(e.key, e.data)

    def _duplicate(self, e: RadioEvent, text: str, t: float) -> bool:
        if e.priority >= Priority.ANSWER:
            return False
        last = self.recent_texts.get(text)
        return last is not None and t - last < 45.0

    def _interrupt(self, t: float) -> None:
        if self.current is None:
            return
        self.current.interrupted = True
        self.stats.interrupted += 1
        if self.on_interrupt:
            self.on_interrupt(self.current)
        self.current = None
        self.busy_until = t

    def _speak(self, e: RadioEvent, text: str, spoken: str, inp: DirectorInput) -> Utterance:
        t = inp.t
        duration = max(1.0, len(spoken.split()) / self.wps + 0.6)
        utt = Utterance(event=e, text=text, spoken=spoken, start_t=t, duration_s=duration, priority=e.priority,
                        lap=inp.lap)
        self.current = utt
        self.busy_until = t + duration
        self.recent_texts[text] = t
        if len(self.recent_texts) > 200:
            cutoff = t - 120
            self.recent_texts = {k: v for k, v in self.recent_texts.items() if v > cutoff}
        if e.cooldown_key:
            self.cooldowns[e.cooldown_key] = t + e.cooldown_s
        if e.priority <= Priority.INFO:
            self.lap_counts[inp.lap] = self.lap_counts.get(inp.lap, 0) + 1
        if e.priority is Priority.CHATTER:
            self.last_chatter_lap = inp.lap
        self.stats.spoken += 1
        self.history.append(utt)
        self.history = self.history[-300:]
        if self.on_utterance:
            self.on_utterance(utt)
        return utt
