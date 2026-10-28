"""Synthetischer SCS-Telemetry-Block (Plugin Rev 12) für Tests."""

from __future__ import annotations

import struct


class ScsMap:
    def __init__(self, game: int = 1):
        self.b = bytearray(32 * 1024)
        self.b[0] = 1  # sdkActive
        self.t = 1_000_000
        self.u32(40, 12)
        self.u32(52, game)
        self.f32(700, 19.0)  # time scale
        self.f32(704, 400.0)
        self.f32(740, 2500.0)

    def u32(self, o: int, v: int) -> ScsMap:
        struct.pack_into("<I", self.b, o, v)
        return self

    def i32(self, o: int, v: int) -> ScsMap:
        struct.pack_into("<i", self.b, o, v)
        return self

    def f32(self, o: int, v: float) -> ScsMap:
        struct.pack_into("<f", self.b, o, v)
        return self

    def i64(self, o: int, v: int) -> ScsMap:
        struct.pack_into("<q", self.b, o, v)
        return self

    def s(self, o: int, text: str, n: int = 64) -> ScsMap:
        d = text.encode("utf-8")[: n - 1]
        self.b[o:o + n] = d + bytes(n - len(d))
        return self

    def flag(self, o: int, v: bool) -> ScsMap:
        self.b[o] = int(v)
        return self

    def toggle(self, o: int) -> ScsMap:
        self.b[o] ^= 1
        return self

    def drive(self, speed_ms: float = 22.0, limit_ms: float = 25.0, fuel: float = 300.0, rng: float = 900.0,
              game_min: int = 8 * 60, nav_m: float = 400_000.0, nav_s: float = 400_000 / 22,
              rest: int = 600) -> ScsMap:
        self.u32(64, game_min).i32(500, rest).f32(948, speed_ms).f32(1000, fuel).f32(1008, rng)
        self.f32(1060, nav_m).f32(1064, nav_s).f32(1068, limit_ms).flag(1576, True)
        return self

    def job(self, cargo: str = "Bagger", city: str = "Berlin", company: str = "Kaarfor", deadline_min: int = 20 * 60,
            km: int = 420, income: int = 12450) -> ScsMap:
        self.flag(4300, True).s(2620, cargo).s(2748, city).s(2876, company).u32(88, deadline_min).u32(100, km)
        struct.pack_into("<Q", self.b, 4000, income)
        return self

    def snapshot(self) -> bytes:
        self.t += 50_000
        struct.pack_into("<Q", self.b, 8, self.t)
        return bytes(self.b)
