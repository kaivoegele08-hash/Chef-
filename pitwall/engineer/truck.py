"""Disponenten-/Beifahrer-Modus für ETS2/ATS (wird in M7 ausgebaut)."""

from __future__ import annotations

from .detectors.base import Detector


def truck_detectors() -> list[Detector]:
    return []
