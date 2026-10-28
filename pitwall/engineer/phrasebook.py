"""Phrasebook: vorgefertigte Funksprüche mit Zahlen-Templates (offline, < 1 ms).

Templates stehen in ``phrasebook_<lang>.yaml``. Platzhalter ``{gap}`` usw.
werden mit :class:`~pitwall.engineer.speech.Spoken`-Werten gefüllt – einmal
als Anzeige-Text, einmal als Sprechtext.
"""

from __future__ import annotations

import logging
import random
import string
from pathlib import Path
from typing import Any

import yaml

from .speech import Spoken

log = logging.getLogger(__name__)
_DIR = Path(__file__).resolve().parent


class _SafeDict(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


class Phrasebook:
    def __init__(self, lang: str = "de", seed: int = 1, extra_path: Path | None = None):
        self.lang = "en" if lang.startswith("en") else "de"
        path = _DIR / f"phrasebook_{self.lang}.yaml"
        with open(path, encoding="utf-8") as fh:
            self.phrases: dict[str, list[str]] = _flatten(yaml.safe_load(fh) or {})
        if extra_path and extra_path.exists():
            with open(extra_path, encoding="utf-8") as fh:
                self.phrases.update(_flatten(yaml.safe_load(fh) or {}))
        self.rng = random.Random(seed)
        self._last_choice: dict[str, int] = {}

    def has(self, key: str) -> bool:
        return key in self.phrases

    def keys(self) -> list[str]:
        return sorted(self.phrases)

    def placeholders(self, key: str) -> set[str]:
        out: set[str] = set()
        for tpl in self.phrases.get(key, []):
            for _, name, _, _ in string.Formatter().parse(tpl):
                if name:
                    out.add(name)
        return out

    def render(self, key: str, values: dict[str, Any] | None = None) -> tuple[str, str] | None:
        """Gibt (Anzeige-Text, Sprechtext) zurück, oder None wenn der Schlüssel fehlt."""
        variants = self.phrases.get(key)
        if not variants:
            log.debug("Phrasebook: Schlüssel fehlt: %s", key)
            return None
        values = values or {}
        # nur Varianten, deren Platzhalter alle vorhanden sind
        usable = [i for i, v in enumerate(variants) if _fields(v) <= set(values)]
        if not usable:
            usable = list(range(len(variants)))
        last = self._last_choice.get(key)
        if len(usable) > 1 and last in usable:
            usable.remove(last)  # nicht zweimal hintereinander gleich
        idx = self.rng.choice(usable)
        self._last_choice[key] = idx
        tpl = variants[idx]
        disp = _SafeDict({k: (v.display if isinstance(v, Spoken) else v) for k, v in values.items()})
        spok = _SafeDict({k: (v.spoken if isinstance(v, Spoken) else v) for k, v in values.items()})
        display = _tidy(tpl.format_map(disp))
        spoken = _tidy(tpl.format_map(spok))
        return display, spoken


def _fields(tpl: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(tpl) if name}


def _tidy(text: str) -> str:
    text = " ".join(text.split())
    for a, b in ((" ,", ","), (" .", "."), ("..", "."), (",,", ",")):
        text = text.replace(a, b)
    return text.strip()


def _flatten(tree: dict[str, Any], prefix: str = "") -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for key, val in tree.items():
        full = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(val, dict):
            out.update(_flatten(val, full))
        elif isinstance(val, list):
            out[full] = [str(v) for v in val]
        else:
            out[full] = [str(val)]
    return out
