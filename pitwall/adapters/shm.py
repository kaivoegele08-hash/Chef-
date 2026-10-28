"""Shared-Memory-Quellen für AC/ACC/iRacing/rF2/LMU/ETS2.

Drei Wege, je nach Plattform automatisch gewählt:

* **Windows**: benannte Mappings direkt lesen (``mmap`` mit ``tagname``).
* **Linux, Datei-Spiegel**: ``/dev/shm/<name>`` (z. B. von simshmbridge/simd,
  oder vom ETS2-Linux-Plugin).
* **Linux/Proton, Relay**: ``pitwall-relay.exe`` läuft im Proton-Prefix des
  Spiels und schickt geänderte Seiten per UDP an 127.0.0.1:20790
  (siehe ``pitwall/bridge/pitwall_relay.c`` und ``docs/linux-proton.md``).
"""

from __future__ import annotations

import asyncio
import logging
import mmap
import struct
import sys
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from .base import RawPacket, RawSource

log = logging.getLogger(__name__)

RELAY_PORT = 20790
PAGE = 4096
_HDR = struct.Struct("<4sHHIIHH")  # 20 Bytes
assert _HDR.size == 20


@dataclass
class MapSpec:
    name: str  # Windows-Name, z. B. "Local\\acpmf_physics"
    channel: str  # Kanalname im RawPacket, z. B. "physics"
    size: int = 0  # 0 = automatisch
    hz: float = 60.0
    optional: bool = False

    @property
    def short(self) -> str:
        """Name ohne 'Local\\' (für /dev/shm-Dateien)."""
        return self.name.split("\\")[-1]


# ------------------------------------------------------------------- Relay


@dataclass
class _Mirror:
    name: str
    size: int = 0
    buf: bytearray = field(default_factory=bytearray)
    seq: int = -1
    complete: bool = False  # mindestens ein Vollbild empfangen
    updated_t: float = 0.0
    snapshots: int = 0


class RelayHub:
    """Ein UDP-Listener (Port 20790) für alle Maps; spiegelt die Seiten in Bytearrays."""

    _instances: ClassVar[dict[int, RelayHub]] = {}

    def __init__(self, port: int = RELAY_PORT, host: str = "127.0.0.1"):
        self.port = port
        self.host = host
        self.id_to_name: dict[int, str] = {}
        self.mirrors: dict[str, _Mirror] = {}
        self.transport: asyncio.DatagramTransport | None = None
        self.listeners: list[Callable[[str], None]] = []
        self.last_rx: float | None = None
        self.hello: dict[str, dict[str, object]] = {}
        self.datagrams = 0
        self.users = 0
        self._lock: asyncio.Lock | None = None

    @classmethod
    def get(cls, port: int = RELAY_PORT) -> RelayHub:
        hub = cls._instances.get(port)
        if hub is None:
            hub = cls._instances[port] = RelayHub(port)
        return hub

    async def start(self) -> None:
        # mehrere Adapter (Auto-Erkennung) starten parallel → nur einer bindet den Port
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            if self.transport is None:
                await self._bind()
            self.users += 1

    async def _bind(self) -> None:
        loop = asyncio.get_running_loop()
        hub = self

        class _P(asyncio.DatagramProtocol):
            def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
                hub.handle(data)

        transport, _ = await loop.create_datagram_endpoint(_P, local_addr=(self.host, self.port))
        self.transport = transport
        sock = transport.get_extra_info("socket")
        if sock is not None:
            import socket

            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8 * 1024 * 1024)
            except OSError:
                pass

    async def stop(self) -> None:
        self.users = max(0, self.users - 1)
        if self.users == 0 and self.transport is not None:
            self.transport.close()
            self.transport = None
            RelayHub._instances.pop(self.port, None)

    def handle(self, data: bytes) -> None:
        self.datagrams += 1
        self.last_rx = time.monotonic()
        if data[:4] == b"PWRH":
            for line in data[4:].decode("utf-8", errors="replace").splitlines():
                parts = line.split("|")
                if len(parts) == 4 and parts[0].isdigit():
                    idx, name, size, is_open = int(parts[0]), parts[1], int(parts[2] or 0), parts[3] == "1"
                    self.id_to_name[idx] = name
                    self.hello[name] = {"size": size, "open": is_open}
            return
        if len(data) < _HDR.size or data[:4] != b"PWR1":
            return
        _, map_id, flags, seq, size, pages, _ = _HDR.unpack_from(data)
        mname = self.id_to_name.get(map_id)
        if mname is None:
            return  # erst nach dem nächsten Hello zuordenbar
        m = self.mirrors.get(mname)
        if m is None or m.size != size:
            m = self.mirrors[mname] = _Mirror(mname, size, bytearray(size))
        off = _HDR.size
        for _ in range(pages):
            (idx,) = struct.unpack_from("<I", data, off)
            off += 4
            start = idx * PAGE
            length = min(PAGE, size - start)
            if length <= 0 or off + length > len(data):
                return
            m.buf[start:start + length] = data[off:off + length]
            off += length
        if flags & 4:
            m.complete = True
        if flags & 2:
            m.seq = seq
            if m.complete:
                m.snapshots += 1
                m.updated_t = time.monotonic()
                for cb in self.listeners:
                    cb(mname)

    def snapshot(self, name: str, max_age_s: float = 3.0) -> bytes | None:
        m = self.mirrors.get(name)
        if m is None or not m.complete or time.monotonic() - m.updated_t > max_age_s:
            return None
        return bytes(m.buf)

    def info(self) -> dict[str, object]:
        return {
            "port": self.port,
            "receiving": self.last_rx is not None and time.monotonic() - self.last_rx < 3,
            "maps": {n: {"size": m.size, "snapshots": m.snapshots, "complete": m.complete}
                     for n, m in self.mirrors.items()},
            "announced": self.hello,
        }


# ---------------------------------------------------------------- Backends


class _Reader:
    """Liest eine einzelne Map (Windows-Mapping oder /dev/shm-Datei)."""

    def open(self) -> bool:  # pragma: no cover - Interface
        raise NotImplementedError

    def read(self) -> bytes | None:  # pragma: no cover - Interface
        raise NotImplementedError

    def close(self) -> None:
        pass


class _WindowsReader(_Reader):
    def __init__(self, spec: MapSpec):
        self.spec = spec
        self.mm: mmap.mmap | None = None

    def open(self) -> bool:
        try:
            # Größe muss bekannt sein; tagname öffnet ein existierendes Mapping
            size = self.spec.size or 4096
            self.mm = mmap.mmap(-1, size, tagname=self.spec.name, access=mmap.ACCESS_READ)  # type: ignore[call-arg]
            # Existierte das Mapping nicht, legt mmap ein leeres an → an Nullbytes erkennen
            if not any(self.mm[: min(size, 256)]):
                self.mm.close()
                self.mm = None
                return False
            return True
        except (OSError, ValueError):
            self.mm = None
            return False

    def read(self) -> bytes | None:
        if self.mm is None:
            return None
        self.mm.seek(0)
        return self.mm.read()

    def close(self) -> None:
        if self.mm is not None:
            self.mm.close()
            self.mm = None


class _FileReader(_Reader):
    def __init__(self, path: Path):
        self.path = path
        self.mm: mmap.mmap | None = None
        self.fh: object | None = None

    def open(self) -> bool:
        try:
            if not self.path.exists() or self.path.stat().st_size == 0:
                return False
            fh = open(self.path, "rb")
            self.mm = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
            self.fh = fh
            return True
        except OSError:
            return False

    def read(self) -> bytes | None:
        if self.mm is None:
            return None
        return self.mm[:]

    def close(self) -> None:
        if self.mm is not None:
            self.mm.close()
            self.mm = None
        if self.fh is not None:
            self.fh.close()  # type: ignore[attr-defined]
            self.fh = None


class ShmSource(RawSource):
    """Liefert pro Abtastung ein RawPacket je Map (Kanal = MapSpec.channel)."""

    def __init__(self, maps: list[MapSpec], hz: float = 60.0, mirror_dir: str = "/dev/shm", prefix: str = "",
                 relay_port: int = RELAY_PORT, mode: str = "auto", stale_s: float = 2.0):
        self.maps = maps
        self.hz = hz
        self.mirror_dir = Path(mirror_dir)
        self.prefix = prefix
        self.relay_port = relay_port
        self.mode = mode
        self.stale_s = stale_s
        self.backend = "none"
        self.hub: RelayHub | None = None
        self.readers: dict[str, _Reader] = {}
        self._running = False
        self._last_change: dict[str, tuple[bytes, float]] = {}
        self.last_error = ""

    def _pick_backend(self) -> str:
        if self.mode != "auto":
            return self.mode
        if sys.platform == "win32":
            return "windows"
        for spec in self.maps:
            for cand in (self.mirror_dir / f"{self.prefix}{spec.short}", self.mirror_dir / spec.short,
                         self.mirror_dir / "SCS" / spec.short):
                if cand.exists():
                    return "file"
        return "relay"

    def _file_path(self, spec: MapSpec) -> Path:
        for cand in (self.mirror_dir / f"{self.prefix}{spec.short}", self.mirror_dir / spec.short,
                     self.mirror_dir / "SCS" / spec.short):
            if cand.exists():
                return cand
        return self.mirror_dir / spec.short

    async def start(self) -> None:
        self._running = True
        self.backend = self._pick_backend()
        if self.backend == "relay":
            self.hub = RelayHub.get(self.relay_port)
            try:
                await self.hub.start()
            except OSError as exc:
                self.last_error = f"Relay-Port {self.relay_port} belegt: {exc}"
                raise
        log.info("Shared Memory über %s (%s)", self.backend, ", ".join(m.short for m in self.maps))

    async def stop(self) -> None:
        self._running = False
        for r in self.readers.values():
            r.close()
        self.readers.clear()
        if self.hub is not None:
            await self.hub.stop()
            self.hub = None

    def _read_map(self, spec: MapSpec) -> bytes | None:
        if self.backend == "relay":
            assert self.hub is not None
            return self.hub.snapshot(spec.name)
        reader = self.readers.get(spec.name)
        if reader is None:
            reader = _WindowsReader(spec) if self.backend == "windows" else _FileReader(self._file_path(spec))
            if not reader.open():
                return None
            self.readers[spec.name] = reader
        try:
            return reader.read()
        except (OSError, ValueError):
            reader.close()
            self.readers.pop(spec.name, None)
            return None

    def read_all(self, now: float | None = None) -> list[RawPacket]:
        now = time.monotonic() if now is None else now
        out = []
        for spec in self.maps:
            data = self._read_map(spec)
            if data is None:
                continue
            # Datei-Spiegel bleiben nach Spielende stehen → veraltete Daten erkennen
            if self.backend == "file":
                prev = self._last_change.get(spec.name)
                if prev is None or prev[0] != data[:2048]:
                    self._last_change[spec.name] = (data[:2048], now)
                elif now - prev[1] > 30.0 and not spec.optional:
                    continue
            out.append(RawPacket(spec.channel, data, now))
        return out

    async def packets(self) -> AsyncIterator[RawPacket]:
        period = 1.0 / max(1.0, self.hz)
        while self._running:
            t0 = time.monotonic()
            for pkt in self.read_all(t0):
                yield pkt
            await asyncio.sleep(max(0.0, period - (time.monotonic() - t0)))

    def info(self) -> dict[str, object]:
        out: dict[str, object] = {"backend": self.backend, "maps": [m.name for m in self.maps]}
        if self.hub is not None:
            out["relay"] = self.hub.info()
        if self.last_error:
            out["error"] = self.last_error
        return out


def wstr(raw: bytes) -> str:
    """UTF-16-LE-String (wchar_t unter Windows) bis zum ersten Null-Zeichen."""
    text = raw.decode("utf-16-le", errors="replace")
    return text.split("\0", 1)[0].strip()


def cstr(raw: bytes, encoding: str = "utf-8") -> str:
    return raw.split(b"\0", 1)[0].decode(encoding, errors="replace").strip()
