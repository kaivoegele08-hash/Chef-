"""Gemeinsames Adapter-Interface.

Ein Adapter besteht aus zwei Teilen:

* **Quelle** (``RawSource``): liefert Rohpakete – UDP-Datagramme,
  Shared-Memory-Schnappschüsse oder Pakete aus einer Aufnahme.
* **Decoder** (``Decoder``): zustandsbehaftete Übersetzung Rohpaket →
  :class:`~pitwall.core.frame.TelemetryFrame`.

Dadurch läuft ein Replay exakt durch denselben Parser wie Live-Daten, und
Parser lassen sich ohne Netzwerk testen.
"""

from __future__ import annotations

import abc
import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..core.frame import Capability, SimId, TelemetryFrame

if TYPE_CHECKING:
    from ..config import Config
    from ..core.recorder import Recorder

log = logging.getLogger(__name__)


@dataclass(slots=True)
class RawPacket:
    channel: str  # z. B. "udp", "physics", "graphics", "scoring"
    data: bytes
    t: float  # monotone Zeit (time.monotonic oder Replay-Zeit)
    meta: dict[str, Any] = field(default_factory=dict)


class Decoder(abc.ABC):
    """Rohpaket → Frame (oder None, wenn der Frame noch nicht vollständig ist)."""

    sim: SimId = SimId.UNKNOWN

    def __init__(self) -> None:
        self.seq = 0
        self.errors = 0
        self.packets = 0

    @abc.abstractmethod
    def feed(self, packet: RawPacket) -> TelemetryFrame | None: ...

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def safe_feed(self, packet: RawPacket) -> TelemetryFrame | None:
        """Kaputte Pakete loggen und verwerfen – nie abstürzen."""
        self.packets += 1
        try:
            return self.feed(packet)
        except Exception as exc:
            self.errors += 1
            if self.errors <= 5 or self.errors % 500 == 0:
                log.warning("%s: Paket verworfen (%s: %s), Fehler gesamt %d", self.sim, type(exc).__name__, exc,
                            self.errors)
            return None


class RawSource(abc.ABC):
    @abc.abstractmethod
    async def start(self) -> None: ...

    @abc.abstractmethod
    async def stop(self) -> None: ...

    @abc.abstractmethod
    def packets(self) -> AsyncIterator[RawPacket]: ...


class SimAdapter(abc.ABC):
    """Basisklasse aller Adapter."""

    sim_id: SimId = SimId.UNKNOWN
    key: str = "base"  # Konfig-Schlüssel, z. B. "f1"
    display_name: str = "Unbekannt"
    capabilities: frozenset[Capability] = frozenset()

    def __init__(self, config: Config):
        self.config = config
        self.recorder: Recorder | None = None
        self.running = False
        self.last_packet_t: float | None = None
        self.status_text = "nicht gestartet"
        self.hint = ""  # Fehlerhinweis für die UI

    async def start(self) -> None:
        self.running = True

    async def stop(self) -> None:
        self.running = False

    @abc.abstractmethod
    def frames(self) -> AsyncIterator[TelemetryFrame]: ...

    @classmethod
    async def detect(cls, config: Config, timeout: float = 1.0) -> bool:
        """True, wenn das Spiel gerade Daten liefert (für Auto-Erkennung)."""
        return False

    def receiving(self, within_s: float = 3.0) -> bool:
        return self.last_packet_t is not None and time.monotonic() - self.last_packet_t < within_s

    def info(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "sim": str(self.sim_id),
            "name": self.display_name,
            "status": self.status_text,
            "hint": self.hint,
            "receiving": self.receiving(),
            "capabilities": sorted(str(c) for c in self.capabilities),
        }


class TransportAdapter(SimAdapter):
    """Adapter = Quelle + Decoder, mit optionaler Rohdaten-Aufnahme."""

    def __init__(self, config: Config, source: RawSource, decoder: Decoder):
        super().__init__(config)
        self.source = source
        self.decoder = decoder

    async def start(self) -> None:
        await self.source.start()
        self.running = True
        self.status_text = "wartet auf Daten"

    async def stop(self) -> None:
        self.running = False
        await self.source.stop()
        self.status_text = "gestoppt"

    async def frames(self) -> AsyncIterator[TelemetryFrame]:  # type: ignore[override]
        async for pkt in self.source.packets():
            self.last_packet_t = time.monotonic()
            if self.recorder is not None:
                self.recorder.write_raw(pkt)
            frame = self.decoder.safe_feed(pkt)
            if frame is not None:
                self.status_text = "empfängt Daten"
                self.capabilities = frame.capabilities
                yield frame


class UdpSource(RawSource):
    """Asynchroner UDP-Empfänger mit Queue (Ingest blockiert nie)."""

    def __init__(self, host: str, port: int, channel: str = "udp", queue_size: int = 4096,
                 on_datagram: Callable[[bytes, tuple[str, int]], None] | None = None):
        self.host = host
        self.port = port
        self.channel = channel
        self.queue: asyncio.Queue[RawPacket | None] = asyncio.Queue(maxsize=queue_size)
        self.transport: asyncio.DatagramTransport | None = None
        self.on_datagram = on_datagram
        self.dropped = 0
        self.last_addr: tuple[str, int] | None = None

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        src = self

        class _Proto(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
                src.last_addr = addr
                if src.on_datagram:
                    src.on_datagram(data, addr)
                try:
                    src.queue.put_nowait(RawPacket(src.channel, data, time.monotonic(), {"addr": addr[0]}))
                except asyncio.QueueFull:
                    src.dropped += 1

        transport, _ = await loop.create_datagram_endpoint(
            _Proto, local_addr=(self.host, self.port), reuse_port=_reuse_port_supported(),
        )
        self.transport = transport  # type: ignore[assignment]

    async def stop(self) -> None:
        if self.transport is not None:
            self.transport.close()
            self.transport = None
        with _suppress():
            self.queue.put_nowait(None)

    def send(self, data: bytes, addr: tuple[str, int]) -> None:
        if self.transport is not None:
            self.transport.sendto(data, addr)

    async def packets(self) -> AsyncIterator[RawPacket]:  # type: ignore[override]
        while True:
            pkt = await self.queue.get()
            if pkt is None:
                return
            yield pkt


def _reuse_port_supported() -> bool | None:
    import socket
    import sys

    return True if hasattr(socket, "SO_REUSEPORT") and sys.platform != "win32" else None


class _suppress:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> bool:
        return True


class PollingSource(RawSource):
    """Pollt eine Lesefunktion (Shared Memory) mit fester Rate."""

    def __init__(self, read: Callable[[], list[RawPacket] | None], hz: float,
                 opener: Callable[[], bool] | None = None, closer: Callable[[], None] | None = None):
        self.read = read
        self.hz = hz
        self.opener = opener
        self.closer = closer
        self._running = False
        self.opened = False

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False
        if self.closer and self.opened:
            self.closer()
            self.opened = False

    async def packets(self) -> AsyncIterator[RawPacket]:  # type: ignore[override]
        period = 1.0 / max(1.0, self.hz)
        while self._running:
            start = time.monotonic()
            if not self.opened:
                self.opened = self.opener() if self.opener else True
                if not self.opened:
                    await asyncio.sleep(1.0)
                    continue
            try:
                pkts = self.read()
            except OSError:
                pkts = None
                if self.closer:
                    self.closer()
                self.opened = False
            if pkts:
                for p in pkts:
                    yield p
            await asyncio.sleep(max(0.0, period - (time.monotonic() - start)))
