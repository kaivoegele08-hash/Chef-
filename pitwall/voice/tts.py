"""TTS-Provider: Piper (lokal, Standard), ElevenLabs (optional), espeak-ng, stumm.

Alle Provider liefern ``(audio_float32_mono, sample_rate)``. Synthese läuft in
einem Thread, damit der Event-Loop (Telemetrie!) nie blockiert.
"""

from __future__ import annotations

import abc
import io
import json
import logging
import shutil
import subprocess
import urllib.request
import wave
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from ..config import secret

if TYPE_CHECKING:
    from ..config import Config

log = logging.getLogger(__name__)

PIPER_VOICES_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"

# Getestete/empfohlene Stimmen (Name → Pfad im piper-voices-Repo)
PIPER_VOICES = {
    "de_DE-thorsten-medium": "de/de_DE/thorsten/medium",
    "de_DE-thorsten-high": "de/de_DE/thorsten/high",
    "de_DE-thorsten_emotional-medium": "de/de_DE/thorsten_emotional/medium",
    "de_DE-karlsson-low": "de/de_DE/karlsson/low",
    "de_DE-kerstin-low": "de/de_DE/kerstin/low",
    "de_DE-ramona-low": "de/de_DE/ramona/low",
    "de_DE-pavoque-low": "de/de_DE/pavoque/low",
    "de_DE-eva_k-x_low": "de/de_DE/eva_k/x_low",
    "de_DE-mls-medium": "de/de_DE/mls/medium",
    "en_GB-alan-medium": "en/en_GB/alan/medium",
    "en_GB-northern_english_male-medium": "en/en_GB/northern_english_male/medium",
    "en_US-ryan-high": "en/en_US/ryan/high",
    "en_US-lessac-medium": "en/en_US/lessac/medium",
}


class TTSProvider(abc.ABC):
    name = "base"

    @abc.abstractmethod
    def synthesize(self, text: str) -> tuple[np.ndarray, int]: ...

    def available(self) -> bool:
        return True

    def warmup(self) -> None:
        """Modell vorab laden (erste Antwort sonst langsam)."""


class SilentTTS(TTSProvider):
    """Keine Sprachausgabe – erzeugt Stille mit realistischer Länge (Tests, Dry-Run)."""

    name = "none"

    def __init__(self, words_per_second: float = 2.8, sr: int = 16000):
        self.wps = words_per_second
        self.sr = sr

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        dur = max(0.4, len(text.split()) / self.wps)
        return np.zeros(int(dur * self.sr), dtype=np.float32), self.sr


class PiperTTS(TTSProvider):
    name = "piper"

    def __init__(self, voice: str, voices_dir: Path, length_scale: float = 0.92, use_cuda: bool = False):
        self.voice_name = voice
        self.voices_dir = voices_dir
        self.length_scale = length_scale
        self.use_cuda = use_cuda
        self._voice = None
        self._error: str | None = None

    def model_path(self) -> Path:
        return self.voices_dir / f"{self.voice_name}.onnx"

    def available(self) -> bool:
        try:
            import piper  # noqa: F401
        except ImportError:
            self._error = "piper-tts nicht installiert (pip install 'pitwall[tts]')"
            return False
        if not self.model_path().exists():
            self._error = f"Stimme fehlt: {self.model_path()} – 'pitwall voices download {self.voice_name}'"
            return False
        return True

    def _load(self) -> object:
        if self._voice is None:
            from piper import PiperVoice

            self._voice = PiperVoice.load(self.model_path(), use_cuda=self.use_cuda)
        return self._voice

    def warmup(self) -> None:
        if self.available():
            self._load()

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        from piper import SynthesisConfig

        voice = self._load()
        cfg = SynthesisConfig(length_scale=self.length_scale)
        chunks = list(voice.synthesize(text, syn_config=cfg))  # type: ignore[attr-defined]
        if not chunks:
            return np.zeros(1, dtype=np.float32), 22050
        sr = chunks[0].sample_rate
        audio = np.concatenate([c.audio_float_array.astype(np.float32).flatten() for c in chunks])
        return audio, sr


class ElevenLabsTTS(TTSProvider):
    """ElevenLabs Streaming-HTTP-API (Flash-Modell für niedrige Latenz). Kostenpflichtig."""

    name = "elevenlabs"

    def __init__(self, voice_id: str, model: str = "eleven_flash_v2_5", sr: int = 22050, language: str = "de"):
        self.voice_id = voice_id
        self.model = model
        self.sr = sr
        self.language = language

    def available(self) -> bool:
        return bool(secret("ELEVENLABS_API_KEY") and self.voice_id)

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        key = secret("ELEVENLABS_API_KEY")
        if not key:
            raise RuntimeError("ELEVENLABS_API_KEY fehlt in .env")
        url = (f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}/stream"
               f"?output_format=pcm_{self.sr}&optimize_streaming_latency=3")
        body = json.dumps({"text": text, "model_id": self.model, "language_code": self.language}).encode()
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"xi-api-key": key, "Content-Type": "application/json",
                                              "Accept": "audio/pcm"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            pcm = resp.read()
        audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        return audio, self.sr


class EspeakTTS(TTSProvider):
    """Notlösung ohne Modelle: espeak-ng (klingt robotisch, ist aber überall verfügbar)."""

    name = "espeak"

    def __init__(self, language: str = "de", speed: int = 175):
        self.language = language
        self.speed = speed
        self.binary = shutil.which("espeak-ng") or shutil.which("espeak")

    def available(self) -> bool:
        return self.binary is not None

    def synthesize(self, text: str) -> tuple[np.ndarray, int]:
        assert self.binary
        out = subprocess.run([self.binary, "-v", self.language, "-s", str(self.speed), "--stdout", text],
                             capture_output=True, check=True, timeout=10).stdout
        with wave.open(io.BytesIO(out), "rb") as w:
            sr = w.getframerate()
            frames = w.readframes(w.getnframes())
        return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0, sr


def voices_dir(config: Config) -> Path:
    d = config.data_dir / "voices"
    d.mkdir(parents=True, exist_ok=True)
    return d


def download_piper_voice(name: str, target: Path) -> list[Path]:
    """Lädt ``<name>.onnx`` + ``.onnx.json`` von Hugging Face (rhasspy/piper-voices)."""
    sub = PIPER_VOICES.get(name)
    if sub is None:
        lang, rest = name.split("-", 1)
        speaker, quality = rest.rsplit("-", 1)
        sub = f"{lang.split('_')[0]}/{lang}/{speaker}/{quality}"
    target.mkdir(parents=True, exist_ok=True)
    paths = []
    for suffix in (".onnx", ".onnx.json"):
        url = f"{PIPER_VOICES_BASE}/{sub}/{name}{suffix}?download=true"
        dest = target / f"{name}{suffix}"
        log.info("Lade %s …", url)
        with urllib.request.urlopen(url, timeout=60) as resp, open(dest, "wb") as fh:
            shutil.copyfileobj(resp, fh)
        paths.append(dest)
    return paths


def make_tts(config: Config) -> TTSProvider:
    """Bevorzugten Provider bauen, mit Fallback-Kette bis 'stumm'."""
    lang = config.language
    provider = str(config.get("voice.tts_provider", "piper"))
    wps = float(config.get("voice.words_per_second", 2.8))
    chain: list[TTSProvider] = []
    if provider == "elevenlabs":
        voice_id = str(config.get("voice.elevenlabs_voice_id") or secret("ELEVENLABS_VOICE_ID") or "")
        chain.append(ElevenLabsTTS(voice_id, str(config.get("voice.elevenlabs_model", "eleven_flash_v2_5")),
                                   language=lang))
    if provider in ("piper", "elevenlabs"):
        voice = str(config.get("voice.piper_voice" if lang == "de" else "voice.piper_voice_en"))
        chain.append(PiperTTS(voice, voices_dir(config), float(config.get("voice.piper_length_scale", 0.92))))
    if provider in ("piper", "elevenlabs", "espeak"):
        chain.append(EspeakTTS(lang))
    for p in chain:
        if p.available():
            log.info("TTS: %s", p.name)
            return p
        log.info("TTS %s nicht verfügbar%s", p.name, f": {getattr(p, '_error', '')}" if getattr(p, "_error", "") else "")
    log.warning("Keine Sprachausgabe verfügbar – Funk nur als Text (Dashboard/Overlay)")
    return SilentTTS(wps)
