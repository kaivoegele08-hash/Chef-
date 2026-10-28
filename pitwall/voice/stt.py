"""Spracherkennung (lokal, faster-whisper) + Mikrofonaufnahme nur bei gedrückter PTT-Taste.

Datenschutz: Das Mikrofon ist nur offen, solange PTT gedrückt ist.
CPU-/GPU-lastige Erkennung läuft in einem Thread, nie im Event-Loop.
"""

from __future__ import annotations

import io
import logging
import threading
import time
import wave
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from ..config import Config

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000

# Kontext-Prompt verbessert die Erkennung von Funk-Vokabular
PROMPT_DE = ("Boxenfunk. Box, Boxenstopp, Sprit, Reifen, Abstand nach vorne, Abstand nach hinten, Runden, "
             "Undercut, Safety Car, Soft, Medium, Hard, Intermediates, Untersteuern, Übersteuern, Balance, DRS.")
PROMPT_EN = ("Team radio. Box, pit stop, fuel, tyres, gap ahead, gap behind, laps, undercut, safety car, softs, "
             "mediums, hards, inters, understeer, oversteer, balance, DRS.")


class Recorder:
    """Nimmt vom Mikrofon auf, solange :meth:`start` … :meth:`stop` läuft."""

    def __init__(self, device: str | int | None = None, max_seconds: float = 12.0):
        self.device = device if device not in ("", None) else None
        self.max_seconds = max_seconds
        self._chunks: list[np.ndarray] = []
        self._stream: Any = None
        self._lock = threading.Lock()
        self.started_at: float | None = None
        self.available = True
        try:
            import sounddevice  # noqa: F401
        except (ImportError, OSError):
            self.available = False

    def start(self) -> bool:
        if not self.available:
            return False
        import sounddevice as sd

        with self._lock:
            self._chunks = []
            self.started_at = time.monotonic()

            def cb(indata: np.ndarray, frames: int, t: Any, status: Any) -> None:
                if self.started_at and time.monotonic() - self.started_at > self.max_seconds:
                    return
                self._chunks.append(indata[:, 0].copy())

            try:
                self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                                              device=self.device, callback=cb)
                self._stream.start()
            except Exception as exc:
                log.warning("Mikrofon konnte nicht geöffnet werden: %s", exc)
                self._stream = None
                return False
        return True

    def stop(self) -> np.ndarray:
        with self._lock:
            if self._stream is not None:
                try:
                    self._stream.stop()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None
            audio = np.concatenate(self._chunks) if self._chunks else np.zeros(0, np.float32)
            self._chunks = []
            self.started_at = None
        return audio


def decode_audio_bytes(data: bytes) -> np.ndarray:
    """WAV direkt, andere Formate (webm/ogg vom Browser) über PyAV (kommt mit faster-whisper)."""
    if data[:4] == b"RIFF":
        with wave.open(io.BytesIO(data), "rb") as w:
            sr = w.getframerate()
            ch = w.getnchannels()
            raw = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
        if ch > 1:
            raw = raw.reshape(-1, ch).mean(axis=1)
        return resample(raw, sr, SAMPLE_RATE)
    try:
        from faster_whisper.audio import decode_audio
    except ImportError as exc:
        raise RuntimeError("Für Browser-Audio wird faster-whisper (PyAV) benötigt") from exc
    return decode_audio(io.BytesIO(data), sampling_rate=SAMPLE_RATE).astype(np.float32)


def resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out or x.size == 0:
        return x.astype(np.float32)
    n_out = int(round(x.size * sr_out / sr_in))
    return np.interp(np.linspace(0, x.size - 1, n_out), np.arange(x.size), x).astype(np.float32)


class SpeechToText:
    def __init__(self, config: Config):
        vc = config.section("voice")
        self.provider = str(vc.get("stt_provider", "faster_whisper"))
        self.model_name = str(vc.get("stt_model", "small"))
        self.device = str(vc.get("stt_device", "auto"))
        self.compute_type = str(vc.get("stt_compute_type", "auto"))
        self.lang = config.language
        self._model: Any = None
        self._load_lock = threading.Lock()
        self.status = "nicht geladen"
        self.last_ms: float | None = None

    @property
    def available(self) -> bool:
        if self.provider == "none":
            return False
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            self.status = "faster-whisper nicht installiert (pip install 'pitwall[stt]')"
            return False
        return True

    def load(self) -> bool:
        """Modell laden (beim Start im Hintergrund, damit die erste Frage schnell ist)."""
        if not self.available:
            return False
        with self._load_lock:
            if self._model is not None:
                return True
            from faster_whisper import WhisperModel

            device = self.device
            compute = self.compute_type
            if device == "auto":
                device = "cuda" if _cuda_available() else "cpu"
            if compute == "auto":
                compute = "float16" if device == "cuda" else "int8"
            t0 = time.perf_counter()
            try:
                self._model = WhisperModel(self.model_name, device=device, compute_type=compute)
            except Exception as exc:
                if device == "cuda":
                    log.warning("STT auf CUDA fehlgeschlagen (%s) – nutze CPU int8", exc)
                    self._model = WhisperModel(self.model_name, device="cpu", compute_type="int8")
                    device, compute = "cpu", "int8"
                else:
                    self.status = f"Fehler: {exc}"
                    return False
            self.status = f"{self.model_name} auf {device}/{compute} ({time.perf_counter() - t0:.1f} s geladen)"
            log.info("STT bereit: %s", self.status)
            return True

    def transcribe(self, audio: np.ndarray) -> str:
        if audio.size < SAMPLE_RATE * 0.25:
            return ""
        if not self.load():
            return ""
        t0 = time.perf_counter()
        segments, _info = self._model.transcribe(
            audio, language=self.lang, beam_size=1, best_of=1, vad_filter=True, condition_on_previous_text=False,
            initial_prompt=PROMPT_DE if self.lang == "de" else PROMPT_EN, without_timestamps=True,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        self.last_ms = (time.perf_counter() - t0) * 1000
        return _strip_prompt_echo(text)


def _strip_prompt_echo(text: str) -> str:
    # Whisper halluziniert bei Stille gelegentlich den Prompt
    low = text.lower()
    if low.startswith("boxenfunk") or low.startswith("team radio"):
        return ""
    return text


def _cuda_available() -> bool:
    try:
        import ctranslate2

        return bool(ctranslate2.get_cuda_device_count())
    except Exception:
        return False
