"""Baut entschlüsselte GT7-Pakete über absolute Offsets (docs/protocols/gt7.md §3) – unabhängig vom Parser."""

from __future__ import annotations

import struct


def packet(size: int = 344, *, packet_id=1000, pos=(100.0, 5.0, -50.0), speed=50.0, rpm=7000.0, gas=55.0,
           gas_cap=100.0, tyres=(80.0, 81.0, 75.0, 76.0), lap=2, total=10, best_ms=95123, last_ms=96456,
           start_pos=3, num_cars=16, flags=0x1, gear=0x34, throttle=255, brake=0, wheel_rps=None, radius=0.33,
           wheel_rot=0.2, category=b"GR3\0", cur_lap_ms=-1) -> bytes:
    b = bytearray(size)
    struct.pack_into("<i", b, 0x00, 0x47375330)
    struct.pack_into("<3f", b, 0x04, *pos)
    struct.pack_into("<3f", b, 0x2C, 0.0, 0.12, 0.0)
    struct.pack_into("<f", b, 0x3C, rpm)
    struct.pack_into("<f", b, 0x44, gas)
    struct.pack_into("<f", b, 0x48, gas_cap)
    struct.pack_into("<f", b, 0x4C, speed)
    struct.pack_into("<4f", b, 0x60, *tyres)
    struct.pack_into("<i", b, 0x70, packet_id)
    struct.pack_into("<h", b, 0x74, lap)
    struct.pack_into("<h", b, 0x76, total)
    struct.pack_into("<i", b, 0x78, best_ms)
    struct.pack_into("<i", b, 0x7C, last_ms)
    struct.pack_into("<h", b, 0x84, start_pos)
    struct.pack_into("<h", b, 0x86, num_cars)
    struct.pack_into("<h", b, 0x8A, 8500)
    struct.pack_into("<H", b, 0x8E, flags)
    struct.pack_into("<BBB", b, 0x90, gear, throttle, brake)
    rps = wheel_rps if wheel_rps is not None else [speed / radius] * 4
    struct.pack_into("<4f", b, 0xA4, *rps)
    struct.pack_into("<4f", b, 0xB4, *([radius] * 4))
    struct.pack_into("<i", b, 0x124, 3456)
    if size >= 316:
        struct.pack_into("<f", b, 0x128, wheel_rot)
        struct.pack_into("<3f", b, 0x130, 4.0, 0.0, -2.0)
    if size >= 368:
        b[0x158:0x15C] = b"TTTT"
        struct.pack_into("<i", b, 0x15C, cur_lap_ms)
        b[0x16C:0x170] = category
    return bytes(b)
