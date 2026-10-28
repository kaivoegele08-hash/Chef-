"""Hilfe für Binär-Layouts: Feldliste → struct + benannter Zugriff (verhindert Index-Fehler)."""

from __future__ import annotations

import re
import struct
from typing import Any

_TOKEN = re.compile(r"(\d*)([a-zA-Z?])")


class Layout:
    """``Layout("<", [("packetId", "i"), ("gas", "f"), ("velocity", "3f"), ("_pad", "2x"), ("name", "30s")])``

    Arrays (``3f``) werden als Tupel geliefert, ``s`` als bytes, ``x`` übersprungen.
    """

    def __init__(self, endian: str, fields: list[tuple[str, str]]):
        self.fields = fields
        fmt = endian
        self.slices: dict[str, slice | int] = {}
        idx = 0
        for name, code in fields:
            fmt += code
            m = _TOKEN.fullmatch(code)
            if m is None:
                raise ValueError(code)
            count = int(m.group(1) or 1)
            kind = m.group(2)
            if kind == "x":
                continue
            if kind == "s" or count == 1:
                self.slices[name] = idx
                idx += 1
            else:
                self.slices[name] = slice(idx, idx + count)
                idx += count
        self.struct = struct.Struct(fmt)
        self.size = self.struct.size

    def offset_of(self, name: str) -> int:
        fmt = self.struct.format[0]
        for fname, code in self.fields:
            if fname == name:
                return struct.calcsize(fmt)
            fmt += code
        raise KeyError(name)

    def unpack(self, data: bytes | bytearray | memoryview, offset: int = 0) -> Record:
        return Record(self, self.struct.unpack_from(data, offset))


class Record:
    __slots__ = ("_layout", "_values")

    def __init__(self, layout: Layout, values: tuple[Any, ...]):
        self._layout = layout
        self._values = values

    def __getattr__(self, name: str) -> Any:
        try:
            sl = self._layout.slices[name]
        except KeyError:
            raise AttributeError(name) from None
        return self._values[sl]

    def get(self, name: str, default: Any = None) -> Any:
        sl = self._layout.slices.get(name)
        return default if sl is None else self._values[sl]
