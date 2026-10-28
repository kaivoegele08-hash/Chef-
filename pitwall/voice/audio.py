"""Audio-Ausgabe: sounddevice (PortAudio → PipeWire/Pulse/WASAPI) mit Fallbacks.

Reihenfolge: sounddevice → ``pw-play`` / ``paplay`` / ``aplay`` (Linux) → stumm.
Wiedergabe ist abbrechbar (CRITICAL unterbricht CHATTER).
"""

from __future__ import annotations

import io
import logging
import shutil
import subprocess
import tempfile
import threading
import wave
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)


def to_wav_bytes(audio: np.ndarray, sr: int) -> bytes:
    pcm = np.clip(np.asarray(audio, dtype=np.float32), -1.0, 1.0)
    data = (pcm * 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(data)
    return buf.getvalue()


def list_output_devices() -> list[dict[str, Any]]:
    try:
        import sounddevice as sd
    except (ImportError, OSError):
        return []
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d.get("max_output_channels", 0) > 0:
            out.append({"index": i, "name": d["name"], "default": i == sd.default.device[1]})
    return out


def list_input_devices() -> list[dict[str, Any]]:
    try:
        import sounddevice as sd
    except (ImportError, OSError):
        return []
    out = []
    for i, d in enumerate(sd.query_devices()):
        if d.get("max_input_channels", 0) > 0:
            out.append({"index": i, "name": d["name"], "default": i == sd.default.device[0]})
    return out


def _resolve_device(spec: str | int | None) -> int | str | None:
    if spec in (None, ""):
        return None
    if isinstance(spec, int):
        return spec
    s = str(spec)
    if s.isdigit():
        return int(s)
    return s  # sounddevice akzeptiert Teilstrings von Gerätenamen


class AudioPlayer:
    def __init__(self, device: str | int | None = None, volume: float = 1.0):
        self.device = _resolve_device(device)
        self.volume = volume
        self.backend = "none"
        self._sd: Any = None
        self._proc: subprocess.Popen[bytes] | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        try:
            import sounddevice as sd

            sd.query_devices()
            self._sd = sd
            self.backend = "sounddevice"
        except (ImportError, OSError) as exc:
            log.info("sounddevice nicht verfügbar (%s) – versuche Kommandozeilen-Player", exc)
            for cmd in ("pw-play", "paplay", "aplay"):
                if shutil.which(cmd):
                    self.backend = cmd
                    break
        log.info("Audio-Ausgabe: %s", self.backend)

    def play(self, audio: np.ndarray, sr: int) -> bool:
        """Blockiert bis Ende oder :meth:`stop`. True = vollständig abgespielt."""
        self._stop.clear()
        x = np.asarray(audio, dtype=np.float32) * float(self.volume)
        if self.backend == "sounddevice":
            return self._play_sd(x, sr)
        if self.backend in ("pw-play", "paplay", "aplay"):
            return self._play_cmd(x, sr)
        # stumm: Dauer abwarten, damit das Timing stimmt
        return not self._stop.wait(len(x) / sr)

    def _play_sd(self, x: np.ndarray, sr: int) -> bool:
        sd = self._sd
        block = 1024
        pos = 0
        try:
            with sd.OutputStream(samplerate=sr, channels=1, dtype="float32", device=self.device,
                                 blocksize=block) as stream:
                while pos < len(x):
                    if self._stop.is_set():
                        # kurzes Ausblenden statt hartem Schnitt
                        tail = x[pos:pos + 256] * np.linspace(1, 0, min(256, len(x) - pos), dtype=np.float32)
                        stream.write(tail.reshape(-1, 1))
                        return False
                    chunk = x[pos:pos + block]
                    stream.write(chunk.reshape(-1, 1))
                    pos += block
        except Exception as exc:
            log.warning("Audio-Ausgabe fehlgeschlagen: %s", exc)
            return False
        return True

    def _play_cmd(self, x: np.ndarray, sr: int) -> bool:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fh:
            fh.write(to_wav_bytes(x, sr))
            path = Path(fh.name)
        cmd = [self.backend, str(path)]
        if self.backend == "pw-play" and self.device:
            cmd = ["pw-play", "--target", str(self.device), str(path)]
        try:
            with self._lock:
                self._proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            while self._proc.poll() is None:
                if self._stop.wait(0.02):
                    self._proc.terminate()
                    return False
            return True
        finally:
            self._proc = None
            path.unlink(missing_ok=True)

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                self._proc.terminate()
