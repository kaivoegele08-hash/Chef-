"""Adapter-Registry und Auto-Erkennung."""

from __future__ import annotations

import asyncio
import importlib
import logging
import time
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from ..core.frame import SimId, TelemetryFrame
from .base import SimAdapter

if TYPE_CHECKING:
    from ..config import Config

log = logging.getLogger(__name__)

# key → (Modul, Klasse, Anzeigename)
ADAPTERS: dict[str, tuple[str, str, str]] = {
    "simulator": ("pitwall.adapters.simulator", "SimulatorAdapter", "PIT WALL Simulator"),
    "replay": ("pitwall.adapters.replay", "ReplayAdapter", "Replay"),
    "f1": ("pitwall.adapters.f1", "F1Adapter", "EA SPORTS F1 24/25"),
    "gt7": ("pitwall.adapters.gt7", "GT7Adapter", "Gran Turismo 7"),
    "assetto_corsa": ("pitwall.adapters.assetto_corsa", "AssettoCorsaAdapter", "Assetto Corsa"),
    "acc": ("pitwall.adapters.acc", "ACCAdapter", "Assetto Corsa Competizione"),
    "iracing": ("pitwall.adapters.iracing", "IRacingAdapter", "iRacing"),
    "rfactor2": ("pitwall.adapters.rfactor2", "RFactor2Adapter", "rFactor 2"),
    "lmu": ("pitwall.adapters.rfactor2", "LMUAdapter", "Le Mans Ultimate"),
    "ets2": ("pitwall.adapters.ets2", "ETS2Adapter", "Euro Truck Simulator 2 / ATS"),
}


def adapter_class(key: str) -> type[SimAdapter]:
    if key not in ADAPTERS:
        raise KeyError(f"Unbekannter Adapter: {key}. Verfügbar: {', '.join(ADAPTERS)}")
    mod, cls, _ = ADAPTERS[key]
    module = importlib.import_module(mod)
    return getattr(module, cls)


def create_adapter(key: str, config: Config, **kwargs: Any) -> SimAdapter:
    if key == "auto":
        return AutoAdapter(config)
    return adapter_class(key)(config, **kwargs)


def available_adapters() -> list[dict[str, str]]:
    out = []
    for key, (mod, _cls, name) in ADAPTERS.items():
        try:
            importlib.import_module(mod)
            ok = "ok"
        except Exception as exc:
            ok = f"nicht ladbar: {exc}"
        out.append({"key": key, "name": name, "status": ok})
    return out


class AutoAdapter(SimAdapter):
    """Startet alle Kandidaten parallel und übernimmt den, der zuerst Daten liefert.

    Liefert der aktive Adapter 10 s keine Daten mehr, wird wieder gesucht.
    """

    sim_id = SimId.UNKNOWN
    key = "auto"
    display_name = "Auto-Erkennung"

    def __init__(self, config: Config, candidates: list[str] | None = None):
        super().__init__(config)
        self.candidates = candidates or list(config.get("sim.autodetect", ["f1", "gt7"]))
        self.active: SimAdapter | None = None
        self.adapters: dict[str, SimAdapter] = {}
        self.status_text = "sucht Spiel …"
        self._queue: asyncio.Queue[tuple[str, TelemetryFrame]] = asyncio.Queue(maxsize=256)
        self._tasks: list[asyncio.Task[None]] = []

    def info(self) -> dict[str, Any]:
        base = super().info()
        base["active"] = self.active.info() if self.active else None
        base["candidates"] = {k: a.info() for k, a in self.adapters.items()}
        return base

    async def start(self) -> None:
        self.running = True
        for key in self.candidates:
            try:
                adapter = create_adapter(key, self.config)
            except Exception as exc:
                log.warning("Auto-Erkennung: %s nicht verfügbar (%s)", key, exc)
                continue
            try:
                await adapter.start()
            except OSError as exc:
                log.warning("Auto-Erkennung: %s konnte nicht starten (%s)", key, exc)
                adapter.hint = str(exc)
                continue
            self.adapters[key] = adapter
            self._tasks.append(asyncio.create_task(self._pump(key, adapter), name=f"auto-{key}"))

    async def _pump(self, key: str, adapter: SimAdapter) -> None:
        try:
            async for frame in adapter.frames():
                if not self.running:
                    return
                if self.active is None or self.active is adapter:
                    try:
                        self._queue.put_nowait((key, frame))
                    except asyncio.QueueFull:
                        pass
        except Exception:
            log.exception("Auto-Erkennung: Adapter %s abgestürzt", key)

    async def stop(self) -> None:
        self.running = False
        for task in self._tasks:
            task.cancel()
        for adapter in self.adapters.values():
            try:
                await adapter.stop()
            except Exception:
                pass
        self._queue.put_nowait(("", None))  # type: ignore[arg-type]

    async def frames(self) -> AsyncIterator[TelemetryFrame]:
        last_frame_t = time.monotonic()
        while self.running:
            try:
                key, frame = await asyncio.wait_for(self._queue.get(), timeout=1.0)
            except TimeoutError:
                if self.active is not None and time.monotonic() - last_frame_t > 10.0:
                    log.info("Auto-Erkennung: %s liefert keine Daten mehr – suche erneut", self.active.display_name)
                    self.active = None
                    self.status_text = "sucht Spiel …"
                continue
            if frame is None:
                return
            if self.active is None:
                self.active = self.adapters.get(key)
                if self.active is not None:
                    log.info("Auto-Erkennung: %s erkannt", self.active.display_name)
                    self.status_text = f"Erkannt: {self.active.display_name}"
                    self.sim_id = frame.sim
            if self.active is not None and self.adapters.get(key) is self.active:
                last_frame_t = time.monotonic()
                self.last_packet_t = last_frame_t
                self.capabilities = frame.capabilities
                yield frame

    @property
    def recorder_target(self) -> SimAdapter | None:
        return self.active
