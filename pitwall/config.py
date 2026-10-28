"""Konfiguration: default.toml + Benutzerdatei + Umgebungsvariablen (.env)."""

from __future__ import annotations

import copy
import os
import sys
import threading
import tomllib
from pathlib import Path
from typing import Any

_PKG_DIR = Path(__file__).resolve().parent


def _config_root() -> Path:
    """Ordner mit default.toml, sims/ und tracks/ (Repo oder installiertes Paket)."""
    repo = _PKG_DIR.parent / "config"
    if (repo / "default.toml").exists():
        return repo
    return _PKG_DIR / "_config"


CONFIG_ROOT = _config_root()


def user_config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "pitwall"


def default_data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "pitwall"


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, val in override.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], val)
        else:
            out[key] = copy.deepcopy(val)
    return out


def _dump_toml(data: dict[str, Any]) -> str:
    """Minimaler TOML-Writer für unsere flache Sektionsstruktur."""

    def fmt(v: Any) -> str:
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, int | float):
            return repr(v)
        if isinstance(v, str):
            return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
        if isinstance(v, list):
            return "[" + ", ".join(fmt(x) for x in v) + "]"
        raise TypeError(f"Nicht serialisierbar: {v!r}")

    lines: list[str] = []
    scalars = {k: v for k, v in data.items() if not isinstance(v, dict)}
    for k, v in scalars.items():
        lines.append(f"{k} = {fmt(v)}")
    for section, values in data.items():
        if not isinstance(values, dict):
            continue
        lines.append("")
        lines.append(f"[{section}]")
        for k, v in values.items():
            if isinstance(v, dict):
                continue
            lines.append(f"{k} = {fmt(v)}")
    return "\n".join(lines).strip() + "\n"


class Config:
    """Zugriff per ``cfg.get("engineer.name")`` oder ``cfg["engineer"]["name"]``."""

    def __init__(self, data: dict[str, Any], user_path: Path | None = None, user_data: dict[str, Any] | None = None):
        self._data = data
        self._user_path = user_path
        self._user_data = user_data or {}
        self._lock = threading.Lock()

    @classmethod
    def load(cls, path: str | Path | None = None, overrides: dict[str, Any] | None = None) -> Config:
        _load_dotenv()
        with open(CONFIG_ROOT / "default.toml", "rb") as fh:
            data = tomllib.load(fh)
        user_path: Path | None
        if path is not None:
            user_path = Path(path)
        elif Path("pitwall.toml").exists():
            user_path = Path("pitwall.toml").resolve()
        else:
            user_path = user_config_dir() / "config.toml"
        user_data: dict[str, Any] = {}
        if user_path.exists():
            with open(user_path, "rb") as fh:
                user_data = tomllib.load(fh)
            data = deep_merge(data, user_data)
        if overrides:
            data = deep_merge(data, overrides)
        return cls(data, user_path, user_data)

    @classmethod
    def defaults(cls, overrides: dict[str, Any] | None = None) -> Config:
        with open(CONFIG_ROOT / "default.toml", "rb") as fh:
            data = tomllib.load(fh)
        if overrides:
            data = deep_merge(data, overrides)
        return cls(data, None, {})

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def section(self, key: str) -> dict[str, Any]:
        val = self._data.get(key, {})
        return val if isinstance(val, dict) else {}

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self._data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def set(self, dotted: str, value: Any, persist: bool = False) -> None:
        with self._lock:
            parts = dotted.split(".")
            for target in (self._data, self._user_data) if persist else (self._data,):
                cur = target
                for part in parts[:-1]:
                    cur = cur.setdefault(part, {})
                cur[parts[-1]] = value
            if persist:
                self.save_user()

    def save_user(self) -> None:
        if self._user_path is None:
            return
        self._user_path.parent.mkdir(parents=True, exist_ok=True)
        self._user_path.write_text(_dump_toml(self._user_data), encoding="utf-8")

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)

    @property
    def data_dir(self) -> Path:
        d = self.get("general.data_dir") or ""
        path = Path(d).expanduser() if d else default_data_dir()
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def language(self) -> str:
        return str(self.get("general.language", "de"))


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover
        return
    for candidate in (Path.cwd() / ".env", user_config_dir() / ".env"):
        if candidate.exists():
            load_dotenv(candidate, override=False)


def load_toml_dir(sub: str) -> dict[str, dict[str, Any]]:
    """Lädt alle TOML-Dateien aus config/<sub>/ (z. B. sims, tracks)."""
    out: dict[str, dict[str, Any]] = {}
    folder = CONFIG_ROOT / sub
    if not folder.exists():
        return out
    for file in sorted(folder.glob("*.toml")):
        with open(file, "rb") as fh:
            out[file.stem] = tomllib.load(fh)
    return out


def secret(name: str) -> str | None:
    """API-Keys nur aus Umgebung/.env – nie aus Dateien, die das Dashboard schreibt."""
    val = os.environ.get(name, "").strip()
    return val or None
