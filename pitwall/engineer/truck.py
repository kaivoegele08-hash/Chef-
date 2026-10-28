"""Disponenten-/Beifahrer-Modus für ETS2/ATS.

Statt Rundenzeiten geht es um Auftrag, Deadline, Pausen, Sprit, Tempolimit und Schäden. Zeiten im
Spiel laufen komprimiert (``local.scale``): Deadline und Ankunft werden in **Spielminuten** verglichen,
gesprochene Fahrzeiten in echte Minuten umgerechnet.
"""

from __future__ import annotations

from ..core.frame import GameEventKind, SimId, TruckState
from .detectors.base import Detector, EngineerContext
from .events import Priority, RadioEvent
from .speech import Spoken

DAMAGE_PARTS = {
    "engine": ("Motor", "engine"), "transmission": ("Getriebe", "gearbox"), "cabin": ("Fahrerhaus", "cab"),
    "chassis": ("Fahrgestell", "chassis"), "wheels": ("Reifen", "tyres"), "trailer": ("Auflieger", "trailer"),
    "cargo": ("Ladung", "cargo"),
}
DAMAGE_STEPS = (10.0, 25.0, 50.0)


def money(ctx: EngineerContext, amount: int) -> Spoken:
    n = ctx.speaker.int_(abs(int(amount)))
    if ctx.frame.sim is SimId.ATS:
        return Spoken(f"${n.display}", f"{n.spoken} Dollar" if ctx.speaker.lang == "de" else f"{n.spoken} dollars")
    return Spoken(f"{n.display} €", f"{n.spoken} Euro" if ctx.speaker.lang == "de" else f"{n.spoken} euros")


def real_minutes(ctx: EngineerContext, game_s: float) -> float:
    scale = ctx.frame.raw_extra.get("time_scale") or 1.0
    return game_s / max(1.0, float(scale)) / 60.0


def _truck(ctx: EngineerContext) -> TruckState | None:
    return ctx.frame.player.truck


class JobDetector(Detector):
    """Neuer Auftrag, Zeitplan, Lieferung, Abbruch, Restdistanz-Meilensteine."""

    name = "truck_job"

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.job_key: tuple[str, str, int | None] | None = None
        self.job_started_t = 0.0
        self.ok_said = False
        self.last_tight_t = -1e9
        self.missed_said = False
        self.milestones_said: set[int] = set()

    def update(self, ctx: EngineerContext) -> list[RadioEvent]:
        out: list[RadioEvent] = []
        tr = _truck(ctx)
        if tr is None:
            return out
        sp = ctx.speaker
        for ev in ctx.frame.events:
            if ev.kind is GameEventKind.JOB_DELIVERED:
                out.append(ctx.event("truck.delivered", Priority.INFO, "truck", ttl=60,
                                     income=money(ctx, int(ev.data.get("revenue") or tr.job_income or 0))))
                self.job_key = None
            elif ev.kind is GameEventKind.JOB_CANCELLED:
                out.append(ctx.event("truck.cancelled", Priority.INFO, "truck", ttl=30))
                self.job_key = None
        if not tr.job_active or not tr.job_cargo:
            return out
        key = (tr.job_cargo, tr.job_destination_city, tr.job_delivery_time_min)
        if key != self.job_key:
            self.reset()
            self.job_key = key
            self.job_started_t = ctx.t
            dist = sp.km(tr.job_planned_distance_km) if tr.job_planned_distance_km else (
                sp.km(tr.nav_distance_m / 1000) if tr.nav_distance_m else sp.text("?"))
            deadline = sp.clock(tr.job_delivery_time_min) if tr.job_delivery_time_min else sp.text("–")
            out.append(ctx.event("truck.job_start", Priority.INFO, "truck", ttl=60, cargo=sp.text(tr.job_cargo),
                                 city=sp.text(tr.job_destination_city), distance=dist, deadline=deadline))
            return out
        if tr.game_time_min is None or tr.job_delivery_time_min is None:
            return out
        remaining = tr.job_delivery_time_min - tr.game_time_min  # Spielminuten
        eta_min = (tr.nav_time_s or 0) / 60.0
        if remaining < 0:
            if not self.missed_said:
                self.missed_said = True
                out.append(ctx.event("truck.deadline_missed", Priority.INFO, "truck", ttl=60))
            return out
        if tr.nav_time_s:
            arrival = sp.clock(int(tr.game_time_min + eta_min))
            deadline = sp.clock(tr.job_delivery_time_min)
            late = eta_min - remaining
            if late > 15 and ctx.t - self.last_tight_t > 900:
                self.last_tight_t = ctx.t
                out.append(ctx.event("truck.deadline_tight", Priority.INFO, "truck", ttl=60, deadline=deadline,
                                     late=sp.minutes(late)))
            elif not self.ok_said and ctx.t - self.job_started_t > 90 and late < -30:
                self.ok_said = True
                out.append(ctx.event("truck.deadline_ok", Priority.CHATTER, "truck", ttl=60, eta=arrival,
                                     deadline=deadline))
        if tr.nav_distance_m:
            km = tr.nav_distance_m / 1000.0
            for mark in (100, 50, 10):
                if km <= mark and mark not in self.milestones_said and (tr.job_planned_distance_km or 1e9) > mark * 1.5:
                    self.milestones_said.update(m for m in (100, 50, 10) if m >= mark)
                    out.append(ctx.event("truck.distance_left", Priority.CHATTER, "truck", ttl=30,
                                         distance=sp.km(km), time=sp.minutes(real_minutes(ctx, tr.nav_time_s or 0))))
                    break
        return out


class RestDetector(Detector):
    name = "truck_rest"

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.soon_said = False
        self.last_now_t = -1e9

    def update(self, ctx: EngineerContext) -> list[RadioEvent]:
        tr = _truck(ctx)
        if tr is None or tr.next_rest_stop_min is None:
            return []
        m = tr.next_rest_stop_min
        if m > 90:
            self.soon_said = False
            return []
        if m <= 0:
            if ctx.t - self.last_now_t > 600:
                self.last_now_t = ctx.t
                return [ctx.event("truck.rest_now", Priority.HIGH, "truck", ttl=60)]
            return []
        if m <= 60 and not self.soon_said:
            self.soon_said = True
            return [ctx.event("truck.rest_soon", Priority.INFO, "truck", ttl=60, minutes=ctx.speaker.minutes(m))]
        return []


class TruckFuelDetector(Detector):
    name = "truck_fuel"

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.low_said = False
        self.last_critical_t = -1e9
        self.last_fuel: float | None = None

    def update(self, ctx: EngineerContext) -> list[RadioEvent]:
        tr = _truck(ctx)
        fuel = ctx.frame.player.fuel_l
        if tr is None or tr.fuel_range_km is None:
            return []
        if fuel is not None and self.last_fuel is not None and fuel > self.last_fuel + 5:
            self.reset()  # getankt
        self.last_fuel = fuel
        rng = tr.fuel_range_km
        if rng < 40 and ctx.t - self.last_critical_t > 300:
            self.last_critical_t = ctx.t
            self.low_said = True
            return [ctx.event("truck.fuel_critical", Priority.HIGH, "truck", ttl=30, range=ctx.speaker.km(rng))]
        if rng < 150 and not self.low_said:
            self.low_said = True
            return [ctx.event("truck.fuel_low", Priority.INFO, "truck", ttl=60, range=ctx.speaker.km(rng))]
        return []


class SpeedDetector(Detector):
    name = "truck_speed"

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.over_since: float | None = None
        self.last_call_t = -1e9

    def update(self, ctx: EngineerContext) -> list[RadioEvent]:
        tr = _truck(ctx)
        if tr is None or not tr.speed_limit_kmh:
            self.over_since = None
            return []
        kmh = ctx.frame.player.speed_ms * 3.6
        over = kmh - tr.speed_limit_kmh
        if over < 8:
            self.over_since = None
            return []
        if self.over_since is None:
            self.over_since = ctx.t
        if ctx.t - self.over_since >= 5 and ctx.t - self.last_call_t > 120:
            self.last_call_t = ctx.t
            sp = ctx.speaker
            return [ctx.event("truck.speed_limit", Priority.INFO, "truck", ttl=8,
                              limit=sp.speed(tr.speed_limit_kmh / 3.6), over=sp.speed(over / 3.6))]
        return []


class DamageDetector(Detector):
    name = "truck_damage"

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.level: dict[str, int] = {}

    def update(self, ctx: EngineerContext) -> list[RadioEvent]:
        d = ctx.frame.player.damage
        out: list[RadioEvent] = []
        for part, (de, en) in DAMAGE_PARTS.items():
            val = getattr(d, part, None)
            if val is None:
                continue
            step = sum(1 for s in DAMAGE_STEPS if val >= s)
            prev = self.level.get(part)
            self.level[part] = step
            if prev is None or step <= prev:
                continue  # Startzustand bzw. Reparatur
            name = de if ctx.speaker.lang == "de" else en
            out.append(ctx.event("truck.damage", Priority.HIGH if step >= 3 else Priority.INFO, "truck", ttl=30,
                                 cooldown_key=f"dmg_{part}", cooldown_s=60, part=ctx.speaker.text(name),
                                 pct=ctx.speaker.percent(val)))
        return out


class TruckEventDetector(Detector):
    """Strafen, Fähre, Handbremse, Licht."""

    name = "truck_events"

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.brake_since: float | None = None
        self.last_brake_t = -1e9
        self.lights_said_day: int | None = None

    def update(self, ctx: EngineerContext) -> list[RadioEvent]:
        out: list[RadioEvent] = []
        tr = _truck(ctx)
        if tr is None:
            return out
        for ev in ctx.frame.events:
            if ev.kind is GameEventKind.FINE:
                out.append(ctx.event("truck.fine", Priority.INFO, "truck", ttl=20,
                                     amount=money(ctx, int(ev.data.get("amount") or 0))))
            elif ev.kind is GameEventKind.FERRY:
                out.append(ctx.event("truck.ferry", Priority.CHATTER, "truck", ttl=20))
        p = ctx.frame.player
        if tr.parking_brake and tr.engine_on and p.throttle > 0.5 and p.speed_ms < 1.0:
            if self.brake_since is None:
                self.brake_since = ctx.t
            if ctx.t - self.brake_since > 2.0 and ctx.t - self.last_brake_t > 60:
                self.last_brake_t = ctx.t
                out.append(ctx.event("truck.parking_brake", Priority.HIGH, "truck", ttl=5))
        else:
            self.brake_since = None
        if tr.game_time_min is not None and not tr.lights_on and p.speed_ms > 10:
            day, minute = divmod(tr.game_time_min, 24 * 60)
            hour = minute // 60
            if (hour >= 20 or hour < 5) and self.lights_said_day != day + (1 if hour >= 20 else 0):
                self.lights_said_day = day + (1 if hour >= 20 else 0)
                out.append(ctx.event("truck.lights", Priority.CHATTER, "truck", ttl=30))
        return out


def truck_detectors() -> list[Detector]:
    return [JobDetector(), RestDetector(), TruckFuelDetector(), SpeedDetector(), DamageDetector(),
            TruckEventDetector()]
