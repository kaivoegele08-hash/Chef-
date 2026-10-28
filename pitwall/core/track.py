"""Streckenmodell, das sich aus der eigenen Telemetrie selbst lernt.

Die Strecke wird in Distanz-Bins (Standard 10 m) zerlegt. Pro Bin werden
gleitende Mittelwerte von Bremse, Gas, Lenkwinkel und Geschwindigkeit gelernt.
Daraus entstehen:

* Zonen: Bremszone, Kurve, Gerade, Beschleunigung
* Kurven T1..Tn (zusammenhängende Brems-/Kurvenbereiche mit Speed-Minimum)
* Streckenkarte aus X/Z-Positionen

Der Radio-Director fragt damit: "Passt ein Funkspruch von 3 s vor die nächste
Bremszone?" – genau wie ein echter Renningenieur, der nicht in die Bremszone
hineinredet.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any


class Zone(StrEnum):
    UNKNOWN = "unknown"
    STRAIGHT = "straight"
    BRAKING = "braking"
    CORNER = "corner"
    ACCEL = "accel"


@dataclass(slots=True)
class Corner:
    number: int
    start_m: float
    apex_m: float
    end_m: float
    min_speed_ms: float
    brake_start_m: float | None = None
    name: str = ""

    @property
    def label(self) -> str:
        return self.name or f"Kurve {self.number}"

    def label_for(self, lang: str) -> str:
        return self.name or (f"Kurve {self.number}" if lang == "de" else f"Turn {self.number}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "start_m": round(self.start_m, 1),
            "apex_m": round(self.apex_m, 1),
            "end_m": round(self.end_m, 1),
            "min_speed_kmh": round(self.min_speed_ms * 3.6, 1),
            "brake_start_m": None if self.brake_start_m is None else round(self.brake_start_m, 1),
            "name": self.name,
        }


@dataclass(slots=True)
class _Bin:
    n: int = 0
    brake: float = 0.0
    throttle: float = 0.0
    steer: float = 0.0
    speed: float = 0.0
    x: float = 0.0
    z: float = 0.0
    xy_n: int = 0

    def add(self, brake: float, throttle: float, steer: float, speed: float, alpha: float) -> None:
        if self.n == 0:
            self.brake, self.throttle, self.steer, self.speed = brake, throttle, steer, speed
        else:
            a = max(alpha, 1.0 / (self.n + 1))
            self.brake += a * (brake - self.brake)
            self.throttle += a * (throttle - self.throttle)
            self.steer += a * (steer - self.steer)
            self.speed += a * (speed - self.speed)
        self.n += 1


@dataclass
class TrackModel:
    key: str
    length_m: float | None = None
    bin_m: float = 10.0
    bins: list[_Bin] = field(default_factory=list)
    corners: list[Corner] = field(default_factory=list)
    corner_names: dict[int, str] = field(default_factory=dict)
    laps_learned: int = 0
    _zones: list[Zone] = field(default_factory=list)
    _dirty: bool = True

    # Schwellen
    BRAKE_ON: float = 0.15
    STEER_CORNER: float = 0.08
    MIN_SAMPLES: int = 2

    def ensure_length(self, length_m: float | None) -> None:
        if not length_m or length_m <= 100:
            return
        if self.length_m and abs(self.length_m - length_m) < 1.0 and self.bins:
            return
        self.length_m = float(length_m)
        n = int(math.ceil(self.length_m / self.bin_m))
        if len(self.bins) != n:
            self.bins = [_Bin() for _ in range(n)]
            self._dirty = True

    def _idx(self, dist_m: float) -> int:
        n = len(self.bins)
        if n == 0:
            return 0
        return int(dist_m // self.bin_m) % n

    # ------------------------------------------------------------ Lernen
    def observe(
        self,
        dist_m: float | None,
        speed_ms: float,
        throttle: float,
        brake: float,
        steering: float,
        x: float | None = None,
        z: float | None = None,
        in_pit: bool = False,
    ) -> None:
        if dist_m is None or not self.bins or in_pit or speed_ms < 3.0:
            return
        if dist_m < 0 or (self.length_m and dist_m > self.length_m * 1.05):
            return
        b = self.bins[self._idx(dist_m)]
        b.add(brake, throttle, abs(steering), speed_ms, alpha=0.2)
        if x is not None and z is not None:
            if b.xy_n == 0:
                b.x, b.z = x, z
            else:
                b.x += 0.2 * (x - b.x)
                b.z += 0.2 * (z - b.z)
            b.xy_n += 1
        self._dirty = True

    def lap_completed(self) -> None:
        self.laps_learned += 1
        self._dirty = True

    @property
    def ready(self) -> bool:
        if not self.bins:
            return False
        covered = sum(1 for b in self.bins if b.n >= self.MIN_SAMPLES)
        return covered / len(self.bins) > 0.8

    # --------------------------------------------------------- Auswertung
    def _rebuild(self) -> None:
        if not self._dirty:
            return
        self._dirty = False
        zones: list[Zone] = []
        for b in self.bins:
            if b.n < self.MIN_SAMPLES:
                zones.append(Zone.UNKNOWN)
            elif b.brake > self.BRAKE_ON:
                zones.append(Zone.BRAKING)
            elif b.steer > self.STEER_CORNER and b.throttle < 0.85:
                zones.append(Zone.CORNER)
            elif b.throttle > 0.9 and b.steer < self.STEER_CORNER:
                zones.append(Zone.STRAIGHT)
            elif b.throttle > 0.5:
                zones.append(Zone.ACCEL)
            else:
                zones.append(Zone.CORNER)
        self._zones = zones
        self.corners = self._segment_corners(zones)

    def _segment_corners(self, zones: list[Zone]) -> list[Corner]:
        n = len(zones)
        if n == 0 or not self.ready:
            return []
        busy = [z in (Zone.BRAKING, Zone.CORNER) for z in zones]
        if all(busy) or not any(busy):
            return []
        # Start an einer ruhigen Stelle, damit Kurven über Start/Ziel nicht zerschnitten werden
        start = next(i for i in range(n) if not busy[i])
        segments: list[tuple[int, int]] = []
        i = 0
        while i < n:
            j = (start + i) % n
            if busy[j]:
                k = i
                while k < n and busy[(start + k) % n]:
                    k += 1
                segments.append(((start + i) % n, (start + k - 1) % n))
                i = k
            else:
                i += 1
        # Kleine Lücken (< 30 m) zwischen Segmenten zusammenführen (Schikanen bleiben getrennt,
        # wenn dazwischen Gas gegeben wird)
        merged: list[tuple[int, int]] = []
        gap_bins = max(1, int(30 / self.bin_m))
        for seg in segments:
            if merged:
                prev = merged[-1]
                gap = (seg[0] - prev[1]) % n
                if gap <= gap_bins:
                    merged[-1] = (prev[0], seg[1])
                    continue
            merged.append(seg)
        corners: list[Corner] = []
        for a, b in merged:
            length = (b - a) % n + 1
            if length * self.bin_m < 20:
                continue
            idxs = [(a + k) % n for k in range(length)]
            apex = min(idxs, key=lambda q: self.bins[q].speed)
            brake_idx = next((q for q in idxs if zones[q] is Zone.BRAKING), None)
            corners.append(
                Corner(
                    number=0,
                    start_m=a * self.bin_m,
                    apex_m=apex * self.bin_m + self.bin_m / 2,
                    end_m=(b + 1) * self.bin_m,
                    min_speed_ms=self.bins[apex].speed,
                    brake_start_m=None if brake_idx is None else brake_idx * self.bin_m,
                )
            )
        corners.sort(key=lambda c: c.start_m)
        for num, c in enumerate(corners, start=1):
            c.number = num
            c.name = self.corner_names.get(num, "")
        return corners

    def zone_at(self, dist_m: float | None) -> Zone:
        if dist_m is None or not self.bins:
            return Zone.UNKNOWN
        self._rebuild()
        return self._zones[self._idx(dist_m)]

    def corner_at(self, dist_m: float | None) -> Corner | None:
        if dist_m is None:
            return None
        self._rebuild()
        for c in self.corners:
            if c.start_m <= dist_m <= c.end_m:
                return c
        return None

    def distance_to_next_busy(self, dist_m: float | None) -> float | None:
        """Meter bis zur nächsten Brems-/Kurvenzone (None = unbekannt)."""
        if dist_m is None or not self.ready:
            return None
        self._rebuild()
        n = len(self._zones)
        i0 = self._idx(dist_m)
        for k in range(n):
            z = self._zones[(i0 + k) % n]
            if z in (Zone.BRAKING, Zone.CORNER):
                return max(0.0, k * self.bin_m - (dist_m % self.bin_m))
        return None

    def corners_remaining(self, dist_m: float | None) -> int | None:
        if dist_m is None or not self.corners:
            return None
        self._rebuild()
        return sum(1 for c in self.corners if c.apex_m > dist_m)

    def talk_window_s(self, dist_m: float | None, speed_ms: float) -> float | None:
        """Wie viele Sekunden bis zur nächsten Bremszone/Kurve (None = unbekannt)."""
        d = self.distance_to_next_busy(dist_m)
        if d is None:
            return None
        return d / max(speed_ms, 5.0)

    def map_points(self, step: int = 2) -> list[tuple[float, float]]:
        pts = [(b.x, b.z) for b in self.bins[::step] if b.xy_n > 0]
        return [(round(x, 1), round(z, 1)) for x, z in pts]

    # -------------------------------------------------------- Persistenz
    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "length_m": self.length_m,
            "bin_m": self.bin_m,
            "laps_learned": self.laps_learned,
            "corner_names": {str(k): v for k, v in self.corner_names.items()},
            "bins": [
                [b.n, round(b.brake, 3), round(b.throttle, 3), round(b.steer, 3), round(b.speed, 2),
                 round(b.x, 1), round(b.z, 1), b.xy_n]
                for b in self.bins
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TrackModel:
        tm = cls(key=data["key"], length_m=data.get("length_m"), bin_m=data.get("bin_m", 10.0))
        tm.laps_learned = int(data.get("laps_learned", 0))
        tm.corner_names = {int(k): v for k, v in data.get("corner_names", {}).items()}
        for row in data.get("bins", []):
            n, br, th, st, sp, x, z, xyn = row
            tm.bins.append(_Bin(n=n, brake=br, throttle=th, steer=st, speed=sp, x=x, z=z, xy_n=xyn))
        tm._dirty = True
        return tm

    def save(self, folder: Path) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{_safe(self.key)}.json"
        path.write_text(json.dumps(self.to_dict()), encoding="utf-8")
        return path

    @classmethod
    def load_or_new(cls, folder: Path, key: str) -> TrackModel:
        path = folder / f"{_safe(key)}.json"
        if path.exists():
            try:
                return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (ValueError, KeyError, TypeError):
                pass
        return cls(key=key)


def _safe(key: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in key.lower())[:120]
