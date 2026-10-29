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
from typing import TYPE_CHECKING, Any

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


# Sprechtempo je Dringlichkeit (Faktor auf length_scale: < 1 = schneller). Ein echter Engineer
# spricht bei "Box, Box!" knapp und zügig, bei Infos ruhig.
STYLE_RATE = {"CRITICAL": 0.88, "HIGH": 0.95, "ANSWER": 0.98, "INFO": 1.0, "CHATTER": 1.04}


def trim_silence(audio: np.ndarray, threshold: float = 0.01, pad: int = 240) -> np.ndarray:
    """Stille am Anfang/Ende entfernen (Piper hängt je Satz eigene Stille an)."""
    idx = np.flatnonzero(np.abs(audio) > threshold)
    if idx.size == 0:
        return audio[:0]
    return audio[max(0, idx[0] - pad): min(audio.size, idx[-1] + 1 + pad)]


class PiperTTS(TTSProvider):
    name = "piper"

    def __init__(self, voice: str, voices_dir: Path, length_scale: float = 0.92, use_cuda: bool = False,
                 noise_scale: float | None = None, noise_w_scale: float | None = None, natural: bool = True,
                 seed: int | None = None):
        self.voice_name = voice
        self.voices_dir = voices_dir
        self.length_scale = length_scale
        self.use_cuda = use_cuda
        # Piper-Standard: noise 0.667 / noise_w 0.8. Etwas mehr Variation klingt weniger monoton.
        self.noise_scale = noise_scale
        self.noise_w_scale = noise_w_scale
        self.natural = natural
        self.style = "INFO"
        self.rng = np.random.default_rng(seed)
        self._voice: Any = None
        self._error: str | None = None

    def set_style(self, priority: str) -> None:
        self.style = priority

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

    def _load(self) -> Any:
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
        length = self.length_scale
        if self.natural:
            # Tempo nach Lage + leichte Schwankung von Spruch zu Spruch (niemand spricht immer exakt gleich)
            length *= STYLE_RATE.get(self.style, 1.0) * float(self.rng.uniform(0.97, 1.03))
        cfg = SynthesisConfig(length_scale=length, noise_scale=self.noise_scale, noise_w_scale=self.noise_w_scale)
        chunks = list(voice.synthesize(text, syn_config=cfg))
        if not chunks:
            return np.zeros(1, dtype=np.float32), 22050
        sr = chunks[0].sample_rate
        parts = [c.audio_float_array.astype(np.float32).flatten() for c in chunks]
        if not self.natural or len(parts) == 1:
            return np.concatenate(parts), sr
        # Piper liefert je Satz ein Stück: dazwischen eine natürliche, leicht variierende Atempause
        out: list[np.ndarray] = []
        for i, part in enumerate(parts):
            part = trim_silence(part)
            if i:
                gap = float(self.rng.uniform(0.16, 0.28)) * (0.6 if self.style == "CRITICAL" else 1.0)
                out.append(np.zeros(int(sr * gap), np.float32))
            out.append(part)
        return np.concatenate(out), sr


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
        part = dest.with_name(dest.name + ".part")
        log.info("Lade %s …", url)
        try:
            with urllib.request.urlopen(url, timeout=60) as resp, open(part, "wb") as fh:
                shutil.copyfileobj(resp, fh)
            part.replace(dest)  # erst nach vollständigem Download – nie eine halbe Stimme liegen lassen
        finally:
            part.unlink(missing_ok=True)
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
        def _opt(key: str) -> float | None:
            v = config.get(key)
            return float(v) if v not in (None, "") else None

        chain.append(PiperTTS(voice, voices_dir(config), float(config.get("voice.piper_length_scale", 0.92)),
                              noise_scale=_opt("voice.piper_noise_scale"), noise_w_scale=_opt("voice.piper_noise_w"),
                              natural=bool(config.get("voice.natural_style", True))))
    if provider in ("piper", "elevenlabs", "espeak"):
        chain.append(EspeakTTS(lang))
    for p in chain:
        if p.available():
            log.info("TTS: %s", p.name)
            return p
        log.info("TTS %s nicht verfügbar%s", p.name, f": {getattr(p, '_error', '')}" if getattr(p, "_error", "") else "")
    log.warning("Keine Sprachausgabe verfügbar – Funk nur als Text (Dashboard/Overlay)")
    return SilentTTS(wps)
