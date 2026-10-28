"""Sprach-Pipeline: Text → (satzweise) TTS → Funk-Effekt → Audio, abbrechbar.

* Synthese und Wiedergabe laufen in Threads (Event-Loop bleibt frei).
* Satz 1 wird schon abgespielt, während Satz 2 synthetisiert wird.
* Meldet echte Dauer + Hüllkurve an Director und Overlay.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from .audio import AudioPlayer
from .radio_fx import apply_radio_fx, envelope
from .tts import TTSProvider

log = logging.getLogger(__name__)

_SENT_RE = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENT_RE.split(text.strip()) if p.strip()]
    # sehr kurze Fragmente an den Vorgänger hängen ("Box, Box." + "Soft.")
    merged: list[str] = []
    for p in parts:
        if merged and len(p.split()) <= 1:
            merged[-1] = f"{merged[-1]} {p}"
        else:
            merged.append(p)
    return merged or [text]


@dataclass
class SpeechEvent:
    kind: str  # start | sentence | end | interrupted
    utterance_id: int
    text: str = ""
    duration_s: float = 0.0
    envelope: list[float] | None = None
    priority: str = ""
    latency_ms: float | None = None


class VoiceOutput:
    def __init__(self, tts: TTSProvider, player: AudioPlayer, radio_fx: bool = True, intensity: float = 0.6,
                 on_event: Callable[[SpeechEvent], None] | None = None):
        self.tts = tts
        self.player = player
        self.radio_fx = radio_fx
        self.intensity = intensity
        self.on_event = on_event
        self._current_id: int | None = None
        self._cancel = False
        self._lock = asyncio.Lock()
        self.last_synth_ms: float | None = None

    def _emit(self, ev: SpeechEvent) -> None:
        if self.on_event:
            try:
                self.on_event(ev)
            except Exception:  # noqa: BLE001
                log.exception("Speech-Event-Sink fehlgeschlagen")

    def interrupt(self) -> None:
        """Laufende Ausgabe sofort abbrechen."""
        self._cancel = True
        self.player.stop()

    @property
    def busy(self) -> bool:
        return self._current_id is not None

    def _render(self, sentence: str) -> tuple[np.ndarray, int]:
        t0 = time.perf_counter()
        audio, sr = self.tts.synthesize(sentence)
        self.last_synth_ms = (time.perf_counter() - t0) * 1000
        if self.radio_fx:
            audio = apply_radio_fx(audio, sr, self.intensity, squelch=False)
        return audio, sr

    def _squelch(self, sr: int) -> tuple[np.ndarray, np.ndarray]:
        if not self.radio_fx:
            return np.zeros(0, np.float32), np.zeros(0, np.float32)
        full = apply_radio_fx(np.zeros(1, np.float32), sr, self.intensity, squelch=True)
        start_len = int(sr * 0.072)
        return full[:start_len], full[start_len + 1:]

    async def speak(self, utterance_id: int, text: str, priority: str = "INFO",
                    t_request: float | None = None) -> float:
        """Spricht ``text`` vollständig (oder bis Interrupt). Gibt gespielte Dauer zurück."""
        return await self.speak_stream(utterance_id, _aiter(split_sentences(text)), priority, t_request)

    async def speak_stream(self, utterance_id: int, sentences: AsyncIterator[str], priority: str = "INFO",
                           t_request: float | None = None) -> float:
        """Satzweises Streaming: Satz n spielt, während Satz n+1 geholt und synthetisiert wird."""
        async with self._lock:
            self._cancel = False
            self._current_id = utterance_id
            loop = asyncio.get_running_loop()
            it = sentences.__aiter__()

            async def fetch_render() -> tuple[str, tuple[np.ndarray, int]] | None:
                text = await _anext(it)
                while text is not None and not text.strip():
                    text = await _anext(it)
                if text is None:
                    return None
                return text, await loop.run_in_executor(None, self._render, text)

            total = 0.0
            first = True
            sr_used = 22050
            pending: asyncio.Task[tuple[str, tuple[np.ndarray, int]] | None] | None = None
            try:
                pending = asyncio.create_task(fetch_render())
                while pending is not None:
                    item = await pending
                    pending = None
                    if item is None or self._cancel:
                        break
                    text, (audio, sr) = item
                    sr_used = sr
                    if first:
                        start, _ = self._squelch(sr)
                        audio = np.concatenate([start, audio])
                        lat = None if t_request is None else (time.monotonic() - t_request) * 1000
                        self._emit(SpeechEvent("start", utterance_id, priority=priority, latency_ms=lat))
                    pending = asyncio.create_task(fetch_render())
                    dur = len(audio) / sr
                    self._emit(SpeechEvent("sentence", utterance_id, text, dur, envelope(audio, sr), priority))
                    ok = await loop.run_in_executor(None, self.player.play, audio, sr)
                    total += dur
                    first = False
                    if not ok or self._cancel:
                        if pending is not None:
                            pending.cancel()
                        self._emit(SpeechEvent("interrupted", utterance_id, text, total, priority=priority))
                        return total
                if not first and not self._cancel:
                    _, end = self._squelch(sr_used)
                    if end.size:
                        await loop.run_in_executor(None, self.player.play, end, sr_used)
                        total += end.size / sr_used
                self._emit(SpeechEvent("end", utterance_id, "", total, priority=priority))
                return total
            except asyncio.CancelledError:
                if pending is not None:
                    pending.cancel()
                self.player.stop()
                raise
            except Exception:  # noqa: BLE001
                log.exception("Sprachausgabe fehlgeschlagen")
                self._emit(SpeechEvent("end", utterance_id, "", total, priority=priority))
                return total
            finally:
                self._current_id = None

    def info(self) -> dict[str, Any]:
        return {"tts": self.tts.name, "audio": self.player.backend, "radio_fx": self.radio_fx,
                "synth_ms": None if self.last_synth_ms is None else round(self.last_synth_ms)}


async def _aiter(items: list[str]) -> AsyncIterator[str]:
    for i in items:
        yield i


async def _anext(it: AsyncIterator[str]) -> str | None:
    try:
        return await it.__anext__()
    except StopAsyncIteration:
        return None
