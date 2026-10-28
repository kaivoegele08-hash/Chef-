"""Replay-Adapter: spielt Aufnahmen durch denselben Decoder wie live ab.

Echtzeit, beschleunigt (``speed``) oder so schnell wie möglich (``speed=0``),
optional ab Runde X (``start_lap``).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..core.frame import SimId, TelemetryFrame
from ..core.recorder import read_recording
from ..core.serialize import frame_from_dict
from .base import Decoder, RawPacket, SimAdapter

if TYPE_CHECKING:
    from ..config import Config


def decoder_for(adapter_key: str, config: Config) -> Decoder | None:
    """Passenden Decoder für eine Aufnahme bauen (None = Frame-Aufnahme)."""
    if adapter_key in ("simulator", "frame", "replay"):
        return None
    if adapter_key == "f1":
        from .f1 import F1Decoder

        return F1Decoder()
    if adapter_key == "gt7":
        from .gt7 import GT7Decoder

        return GT7Decoder()
    if adapter_key == "assetto_corsa":
        from .assetto_corsa import ACDecoder

        return ACDecoder()
    if adapter_key == "acc":
        from .acc import ACCDecoder

        return ACCDecoder()
    if adapter_key == "iracing":
        from .iracing import IRacingDecoder

        return IRacingDecoder()
    if adapter_key in ("rfactor2", "lmu"):
        from .rfactor2 import RF2Decoder

        return RF2Decoder(lmu=adapter_key == "lmu")
    if adapter_key == "ets2":
        from .ets2 import ETS2Decoder

        return ETS2Decoder()
    raise KeyError(adapter_key)


def iter_frames(path: Path, config: Config) -> tuple[dict[str, Any], Iterator[tuple[float, TelemetryFrame]]]:
    header, records = read_recording(path)
    decoder = decoder_for(str(header.get("adapter", "frame")), config)

    def gen() -> Iterator[tuple[float, TelemetryFrame]]:
        for rec in records:
            if rec.channel == "frame":
                yield rec.t, frame_from_dict(json.loads(rec.data))
            elif decoder is not None:
                frame = decoder.safe_feed(RawPacket(rec.channel, rec.data, rec.t))
                if frame is not None:
                    yield rec.t, frame

    return header, gen()


class ReplayAdapter(SimAdapter):
    sim_id = SimId.REPLAY
    key = "replay"
    display_name = "Replay"

    def __init__(self, config: Config, path: str | Path | None = None, speed: float = 1.0, start_lap: int = 0,
                 loop: bool = False):
        super().__init__(config)
        self.path = Path(path) if path else None
        self.speed = speed
        self.start_lap = start_lap
        self.loop = loop
        self.header: dict[str, Any] = {}
        self.position_t = 0.0
        self.paused = False

    async def frames(self) -> AsyncIterator[TelemetryFrame]:
        if self.path is None or not self.path.exists():
            self.status_text = "keine Aufnahme gewählt"
            return
        self.running = True
        while self.running:
            header, it = iter_frames(self.path, self.config)
            self.header = header
            self.display_name = f"Replay: {header.get('sim', '?')} ({self.path.name})"
            self.status_text = "spielt ab"
            wall0: float | None = None
            rec0: float | None = None
            for t, frame in it:
                if not self.running:
                    return
                if self.start_lap and frame.me and frame.me.lap < self.start_lap:
                    continue
                while self.paused and self.running:
                    await asyncio.sleep(0.1)
                    wall0 = None
                if self.speed > 0:
                    if wall0 is None or rec0 is None:
                        wall0, rec0 = time.monotonic(), t
                    delay = (t - rec0) / self.speed - (time.monotonic() - wall0)
                    if delay > 0:
                        await asyncio.sleep(delay)
                elif frame.seq % 300 == 0:
                    await asyncio.sleep(0)
                self.position_t = t
                self.last_packet_t = time.monotonic()
                self.capabilities = frame.capabilities
                yield frame
            if not self.loop:
                self.status_text = "Ende der Aufnahme"
                return
