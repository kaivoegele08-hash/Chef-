"""Funk-Effekt: Bandpass ~300–3400 Hz, Kompression/Sättigung, Rauschen, Squelch-Klick/-Piep.

Rein mit numpy (FFT-Filter), damit keine zusätzliche Abhängigkeit nötig ist.
"""

from __future__ import annotations

import numpy as np


def _bandpass(x: np.ndarray, sr: int, lo: float, hi: float) -> np.ndarray:
    n = len(x)
    if n == 0:
        return x
    nfft = 1 << (int(n - 1).bit_length())
    spec = np.fft.rfft(x, nfft)
    freqs = np.fft.rfftfreq(nfft, 1.0 / sr)
    # weiche Flanken (12 dB/Oktave-ähnlich)
    hp = 1.0 / np.sqrt(1.0 + (lo / np.maximum(freqs, 1.0)) ** 4)
    lp = 1.0 / np.sqrt(1.0 + (freqs / hi) ** 4)
    # leichte Präsenz-Anhebung um 1,8 kHz (typischer Headset-Charakter)
    presence = 1.0 + 0.35 * np.exp(-(((freqs - 1800.0) / 700.0) ** 2))
    out = np.fft.irfft(spec * hp * lp * presence, nfft)[:n]
    return out.astype(np.float32)


def _compress(x: np.ndarray, drive: float) -> np.ndarray:
    peak = float(np.max(np.abs(x))) or 1.0
    y = x / peak
    y = np.tanh(y * (1.0 + 3.0 * drive)) / np.tanh(1.0 + 3.0 * drive)
    return y.astype(np.float32)


def _tone(sr: int, freq: float, dur: float, vol: float) -> np.ndarray:
    t = np.arange(int(sr * dur)) / sr
    env = np.minimum(1.0, np.minimum(t, dur - t) / 0.004)
    return (np.sin(2 * np.pi * freq * t) * env * vol).astype(np.float32)


def _click(sr: int, vol: float = 0.5) -> np.ndarray:
    n = int(sr * 0.012)
    rng = np.random.default_rng(42)
    env = np.exp(-np.arange(n) / (sr * 0.002))
    return (rng.uniform(-1, 1, n) * env * vol).astype(np.float32)


def apply_radio_fx(audio: np.ndarray, sr: int, intensity: float = 0.6, squelch: bool = True,
                   seed: int = 0) -> np.ndarray:
    """Float32-Mono [-1, 1] → Funk-Sound. ``intensity`` 0..1."""
    x = np.asarray(audio, dtype=np.float32).flatten()
    if x.size == 0 or intensity <= 0:
        return x
    k = float(max(0.0, min(1.0, intensity)))
    lo = 250.0 + 150.0 * k
    hi = 4200.0 - 1000.0 * k
    y = _bandpass(x, sr, lo, hi)
    y = _compress(y, 0.4 + 0.6 * k)
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, 1, y.size).astype(np.float32)
    noise = _bandpass(noise, sr, 500.0, 5000.0)
    noise_level = 0.012 + 0.03 * k
    y = 0.85 * y + noise_level * noise / (float(np.max(np.abs(noise))) or 1.0)
    if squelch:
        pre_noise = rng.normal(0, 1, int(sr * 0.06)).astype(np.float32)
        pre_noise = (_bandpass(pre_noise, sr, 800.0, 6000.0) * 0.05 * (0.5 + k)).astype(np.float32)
        start = np.concatenate([_click(sr, 0.35 + 0.3 * k), pre_noise])
        end_beep = _tone(sr, 1250.0, 0.055, 0.18 + 0.12 * k)
        tail_noise = rng.normal(0, 1, int(sr * 0.09)).astype(np.float32)
        tail_noise = (_bandpass(tail_noise, sr, 800.0, 6000.0) * np.linspace(0.12, 0.0, tail_noise.size)
                      * (0.5 + k)).astype(np.float32)
        end = np.concatenate([np.zeros(int(sr * 0.03), np.float32), end_beep, tail_noise.astype(np.float32),
                              _click(sr, 0.25)])
        y = np.concatenate([start, y, end])
    peak = float(np.max(np.abs(y))) or 1.0
    return (y / peak * 0.92).astype(np.float32)


def envelope(audio: np.ndarray, sr: int, fps: int = 30) -> list[float]:
    """Lautstärke-Hüllkurve (0..1) für die Wellenform im Overlay."""
    x = np.asarray(audio, dtype=np.float32)
    hop = max(1, sr // fps)
    n = x.size // hop
    if n == 0:
        return []
    frames = np.abs(x[: n * hop]).reshape(n, hop)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    peak = float(rms.max()) or 1.0
    return [round(float(v / peak), 3) for v in rms]
