"""Aufnahme von Rohpaketen bzw. normalisierten Frames (``.pwrec``) und Lesen für Replays.

Format (komprimierter Stream, zstd wenn verfügbar, sonst gzip):

    Zeile 1:   JSON-Header (sim, adapter, created, meta) + "\\n"
    danach:    Datensätze  <d t> <H len(channel)> <I len(payload)> channel payload

Kanal ``frame`` enthält einen JSON-serialisierten :class:`TelemetryFrame`
(z. B. vom Simulator), alle anderen Kanäle sind Rohdaten des Adapters und
laufen beim Replay erneut durch dessen Decoder.
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import struct
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import IO, Any

from ..adapters.base import RawPacket
from .frame import TelemetryFrame
from .serialize import frame_to_dict

log = logging.getLogger(__name__)
MAGIC = b"PWREC1\n"
_REC = struct.Struct("<dHI")


def _open_write(path: Path, compression: str) -> tuple[IO[bytes], str]:
    if compression in ("auto", "zstd"):
        try:
            import zstandard

            raw = open(path, "wb")
            raw.write(MAGIC + b"zstd\n")
            return zstandard.ZstdCompressor(level=6).stream_writer(raw), "zstd"
        except ImportError:
            if compression == "zstd":
                raise
    if compression == "none":
        raw = open(path, "wb")
        raw.write(MAGIC + b"none\n")
        return raw, "none"
    raw = open(path, "wb")
    raw.write(MAGIC + b"gzip\n")
    return gzip.GzipFile(fileobj=raw, mode="wb", compresslevel=5), "gzip"  # type: ignore[return-value]


def _open_read(path: Path) -> IO[bytes]:
    raw = open(path, "rb")
    magic = raw.read(len(MAGIC))
    if magic != MAGIC:
        raw.close()
        raise ValueError(f"{path}: keine PIT-WALL-Aufnahme")
    kind = raw.readline().strip().decode()
    if kind == "zstd":
        import zstandard

        return zstandard.ZstdDecompressor().stream_reader(raw)
    if kind == "gzip":
        return gzip.GzipFile(fileobj=raw, mode="rb")  # type: ignore[return-value]
    return raw


class Recorder:
    def __init__(self, path: Path, sim: str, adapter: str, compression: str = "auto",
                 meta: dict[str, Any] | None = None):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh, self.compression = _open_write(path, compression)
        header = {"sim": sim, "adapter": adapter, "created": time.time(), "meta": meta or {}, "version": 1}
        self._fh.write(json.dumps(header).encode() + b"\n")
        self._lock = threading.Lock()
        self.records = 0
        self.bytes = 0
        self.t0: float | None = None
        self.closed = False

    def _write(self, channel: str, t: float, payload: bytes) -> None:
        if self.closed:
            return
        ch = channel.encode()
        with self._lock:
            self._fh.write(_REC.pack(t, len(ch), len(payload)))
            self._fh.write(ch)
            self._fh.write(payload)
            self.records += 1
            self.bytes += len(payload)

    def write_raw(self, pkt: RawPacket) -> None:
        if self.t0 is None:
            self.t0 = pkt.t
        self._write(pkt.channel, pkt.t - self.t0, pkt.data)

    def write_frame(self, frame: TelemetryFrame) -> None:
        if self.t0 is None:
            self.t0 = frame.t
        payload = json.dumps(frame_to_dict(frame), separators=(",", ":")).encode()
        self._write("frame", frame.t - self.t0, payload)

    def close(self) -> None:
        with self._lock:
            if self.closed:
                return
            self.closed = True
            try:
                self._fh.close()
            except Exception:
                pass
        log.info("Aufnahme gespeichert: %s (%d Datensätze, %.1f MB roh)", self.path, self.records, self.bytes / 1e6)

    def info(self) -> dict[str, Any]:
        return {"path": str(self.path), "records": self.records, "mb": round(self.bytes / 1e6, 2),
                "compression": self.compression}


def read_recording(path: Path) -> tuple[dict[str, Any], Iterator[RawPacket]]:
    fh = _open_read(path)
    reader = fh if isinstance(fh, io.BufferedReader) else io.BufferedReader(fh)  # type: ignore[type-var]
    header = json.loads(reader.readline().decode())

    def records() -> Iterator[RawPacket]:
        try:
            while True:
                head = reader.read(_REC.size)
                if len(head) < _REC.size:
                    return
                t, lch, lpl = _REC.unpack(head)
                channel = reader.read(lch).decode()
                payload = reader.read(lpl)
                if len(payload) < lpl:
                    return
                yield RawPacket(channel, payload, t)
        finally:
            reader.close()

    return header, records()
