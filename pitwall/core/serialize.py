"""(De-)Serialisierung von TelemetryFrames als JSON-kompatible Dicts (für Aufnahme/Replay)."""

from __future__ import annotations

import dataclasses
import enum
import functools
import types
import typing
from typing import Any

from . import frame as fr


def to_dict(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        out = {}
        for f in dataclasses.fields(obj):
            val = getattr(obj, f.name)
            if val is None or val == [] or val == {}:
                continue
            out[f.name] = to_dict(val)
        return out
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, (list, tuple)):
        return [to_dict(v) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted(to_dict(v) for v in obj)
    if isinstance(obj, dict):
        return {str(k): to_dict(v) for k, v in obj.items()}
    if isinstance(obj, float):
        return round(obj, 5)
    return obj


@functools.cache
def _hints(cls: type) -> dict[str, Any]:
    return typing.get_type_hints(cls, vars(fr))


def _convert(tp: Any, val: Any) -> Any:
    if val is None:
        return None
    origin = typing.get_origin(tp)
    args = typing.get_args(tp)
    if origin in (typing.Union, types.UnionType):
        non_none = [a for a in args if a is not type(None)]
        for a in non_none:
            try:
                return _convert(a, val)
            except (TypeError, ValueError, KeyError):
                continue
        return val
    if origin in (list,):
        return [_convert(args[0], v) for v in val] if args else list(val)
    if origin in (frozenset, set):
        conv = [_convert(args[0], v) for v in val] if args else val
        return frozenset(conv) if origin is frozenset else set(conv)
    if origin is dict:
        return dict(val)
    if isinstance(tp, type):
        if issubclass(tp, enum.Enum):
            return tp(val)
        if dataclasses.is_dataclass(tp):
            return from_dict(tp, val)
        if tp is float and isinstance(val, int | float):
            return float(val)
        if tp is int and isinstance(val, int | float):
            return int(val)
    return val


def from_dict(cls: type, data: dict[str, Any]) -> Any:
    hints = _hints(cls)
    kwargs = {}
    for f in dataclasses.fields(cls):
        if f.name in data:
            kwargs[f.name] = _convert(hints[f.name], data[f.name])
    return cls(**kwargs)


def frame_to_dict(frame: fr.TelemetryFrame) -> dict[str, Any]:
    return to_dict(frame)


def frame_from_dict(data: dict[str, Any]) -> fr.TelemetryFrame:
    data = dict(data)
    data.setdefault("capabilities", [])
    data.setdefault("session", {})
    data.setdefault("player", {})
    return from_dict(fr.TelemetryFrame, data)
