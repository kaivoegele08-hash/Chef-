"""Wetter: Regen in X Minuten, Regen beginnt, Crossover Slicks/Inters, Streckentemperatur."""

from __future__ import annotations

from ...core.frame import Capability
from ...core.state import StateEventKind
from ..events import Priority, RadioEvent
from ..speech import Spoken
from .base import Detector, EngineerContext


class WeatherDetector(Detector):
    name = "weather"

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.rain_soon_called: set[int] = set()
        self.raining = False
        self.last_rec: str | None = None
        self.last_rec_t = -1e9
        self.ref_track_temp: float | None = None
        self.ref_set_t: float | None = None

    def on_flashback(self, ctx: EngineerContext) -> None:
        pass

    def update(self, ctx: EngineerContext) -> list[RadioEvent]:
        out: list[RadioEvent] = []
        f = ctx.frame
        if not f.has(Capability.WEATHER) or ctx.state.finished:
            return out
        sp = ctx.speaker
        w = f.session.weather
        adv = ctx.strategy.weather
        if adv and adv.rain_in_min is not None and f.has(Capability.WEATHER_FORECAST) and not self.raining:
            for mark in (15, 5):
                if adv.rain_in_min <= mark and mark not in self.rain_soon_called:
                    self.rain_soon_called.update(m for m in (15, 5) if m >= mark)
                    prob = sp.percent(adv.rain_probability) if adv.rain_probability is not None else \
                        Spoken("hoher", "hoher") if sp.lang == "de" else Spoken("high", "high")
                    out.append(ctx.event("weather.rain_soon", Priority.INFO, "weather", ttl=40,
                                         minutes=sp.minutes(max(1.0, adv.rain_in_min)), prob=prob))
                    break
        intensity = w.rain_intensity or 0.0
        if intensity > 0.1 and not self.raining:
            self.raining = True
            out.append(ctx.event("weather.rain_started", Priority.HIGH, "weather", ttl=15))
        elif intensity < 0.02 and self.raining and (w.track_wetness or 0) < 0.3:
            self.raining = False
            out.append(ctx.event("weather.drying", Priority.INFO, "weather", ttl=20))
        if adv and adv.recommendation != self.last_rec:
            if adv.recommendation and f.t - self.last_rec_t > ctx.avg_lap() * 1.5:
                key = {"inters": "weather.crossover_inters", "wets": "weather.crossover_wets",
                       "slicks": "weather.crossover_slicks"}[adv.recommendation]
                out.append(ctx.event(key, Priority.HIGH, "weather", ttl=40))
                self.last_rec_t = f.t
            self.last_rec = adv.recommendation
        # Streckentemperatur
        if w.track_temp_c is not None:
            if self.ref_track_temp is None:
                self.ref_track_temp = w.track_temp_c
                self.ref_set_t = f.t
            elif ctx.has_event(StateEventKind.LAP_COMPLETED):
                delta = w.track_temp_c - self.ref_track_temp
                if abs(delta) >= 5:
                    de = sp.lang == "de"
                    direction = ("gestiegen" if delta > 0 else "gefallen") if de else ("up" if delta > 0 else "down")
                    out.append(ctx.event("weather.track_temp", Priority.CHATTER, "weather", ttl=30,
                                         direction=Spoken(direction, direction), delta=sp.temp(abs(delta)),
                                         temp=sp.temp(w.track_temp_c)))
                    self.ref_track_temp = w.track_temp_c
        return out
