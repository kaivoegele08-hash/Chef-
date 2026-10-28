"""Regelbasierte Event-Detektoren (je Thema eine Datei)."""

from __future__ import annotations

from .balance import BalanceDetector
from .base import Detector, EngineerContext
from .flags import FlagDetector
from .fuel import FuelDetector
from .gaps import GapDetector
from .performance import PerformanceDetector
from .quali import QualiDetector
from .race import RaceDetector
from .strategy import StrategyDetector
from .tyres import TyreDetector
from .weather import WeatherDetector


def racing_detectors() -> list[Detector]:
    return [
        FlagDetector(),
        RaceDetector(),
        StrategyDetector(),
        FuelDetector(),
        TyreDetector(),
        GapDetector(),
        WeatherDetector(),
        PerformanceDetector(),
        QualiDetector(),
        BalanceDetector(),
    ]


__all__ = [
    "BalanceDetector",
    "Detector",
    "EngineerContext",
    "FlagDetector",
    "FuelDetector",
    "GapDetector",
    "PerformanceDetector",
    "QualiDetector",
    "RaceDetector",
    "StrategyDetector",
    "TyreDetector",
    "WeatherDetector",
    "racing_detectors",
]
