"""Reifen: Temperaturfenster, Verschleiß, Plattfuß/Druckverlust, Verbremser/Wheelspin, Schäden."""

from __future__ import annotations

from collections import Counter

from ...core.frame import WHEELS, Capability, PitState, SafetyCar
from ...core.state import StateEventKind
from ..describe import damage_part
from ..events import Priority, RadioEvent
from .base import Detector, EngineerContext


class TyreDetector(Detector):
    name = "tyres"

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.ema: list[float | None] = [None, None, None, None]
        self.hot_since: float | None = None
        self.cold_since: float | None = None
        self.last_temp_call_t = -1e9
        self.was_cold = False
        self.wear_called_stint: int | None = None
        self.limit_called_stint: int | None = None
        self.puncture_called = [False] * 4
        self.pressure_called = [False] * 4
        self.lock_t = -1e9
        self.spin_t = -1e9
        self.lockups: Counter[str] = Counter()
        self.spins = 0
        self.last_lock_call_lap = -10
        self.damage_seen: dict[str, float] = {}
        self.last_damage_call_t = -1e9

    def update(self, ctx: EngineerContext) -> list[RadioEvent]:
        out: list[RadioEvent] = []
        f = ctx.frame
        p = f.player
        me = ctx.me
        if me is None:
            return out
        sp = ctx.speaker
        moving = p.speed_ms > 25 and me.pit_state is PitState.ON_TRACK and f.session.safety_car is SafetyCar.NONE
        dt = 0.0 if ctx.state.prev is None else max(0.0, min(0.5, f.t - ctx.state.prev.t))

        # --- Temperaturen ---
        temps = ctx.strategy.temps
        if f.has(Capability.TYRE_TEMPS) and temps is not None and temps.window is not None:
            alpha = min(1.0, dt / 12.0) if dt else 0.1
            for i, t in enumerate(temps.temps):
                if t is None:
                    continue
                cur = self.ema[i]
                self.ema[i] = t if cur is None else cur + alpha * (t - cur)
            lo, hi = temps.window
            vals = [v for v in self.ema if v is not None]
            if vals and moving:
                hot = [i for i, v in enumerate(self.ema) if v is not None and v > hi + 5]
                cold = [i for i, v in enumerate(self.ema) if v is not None and v < lo - 10]
                self.hot_since = (self.hot_since or f.t) if hot else None
                self.cold_since = (self.cold_since or f.t) if cold else None
                cooldown_ok = f.t - self.last_temp_call_t > ctx.avg_lap() * 1.8
                if hot and self.hot_since is not None and f.t - self.hot_since > 20 and cooldown_ok:
                    self.last_temp_call_t = f.t
                    if 0 in hot and 1 in hot:
                        out.append(ctx.event("tyres.hot_fronts", Priority.INFO, "tyres", ttl=15))
                    elif 2 in hot and 3 in hot:
                        out.append(ctx.event("tyres.hot_rears", Priority.INFO, "tyres", ttl=15))
                    else:
                        w = max(hot, key=lambda i: self.ema[i] or 0)
                        out.append(ctx.event("tyres.hot", Priority.INFO, "tyres", ttl=15, wheel=sp.wheel(WHEELS[w]),
                                             temp=sp.temp(self.ema[w] or 0)))
                elif cold and self.cold_since is not None and f.t - self.cold_since > 30 and cooldown_ok and \
                        (p.tyre_age_laps or me.lap) >= 2:
                    self.last_temp_call_t = f.t
                    self.was_cold = True
                    w = min(cold, key=lambda i: self.ema[i] or 0)
                    out.append(ctx.event("tyres.cold", Priority.INFO, "tyres", ttl=15, wheel=sp.wheel(WHEELS[w]),
                                         temp=sp.temp(self.ema[w] or 0)))
                elif self.was_cold and not cold and all(lo <= v <= hi + 5 for v in vals):
                    self.was_cold = False
                    out.append(ctx.event("tyres.in_window", Priority.CHATTER, "tyres", ttl=10))

        # --- Verschleiß ---
        wear = ctx.strategy.wear
        stint_no = len(ctx.state.stints)
        if wear is not None and wear.laps_to_limit is not None and wear.limiting_wheel and ctx.is_race:
            idx = WHEELS.index(wear.limiting_wheel)
            wnow = wear.wear_now[idx] or 0.0
            remaining_race = (f.session.total_laps or 999) - me.lap
            if wear.laps_to_limit < 1.0 and self.limit_called_stint != stint_no and remaining_race > 1:
                self.limit_called_stint = stint_no
                out.append(ctx.event("tyres.wear_limit", Priority.HIGH, "tyres", ttl=20,
                                     wheel=sp.wheel(wear.limiting_wheel), wear=sp.percent(wnow)))
            elif wear.laps_to_limit < 5 and self.wear_called_stint != stint_no and remaining_race > wear.laps_to_limit:
                self.wear_called_stint = stint_no
                out.append(ctx.event("tyres.wear_high", Priority.INFO, "tyres", ttl=20,
                                     wheel=sp.wheel(wear.limiting_wheel), wear=sp.percent(wnow),
                                     laps=sp.n_laps(max(1, int(wear.laps_to_limit)))))

        # --- Plattfuß / Druckverlust ---
        pressures = [w.pressure_kpa for w in p.wheels[:4]]
        known = [x for x in pressures if x]
        for i, w in enumerate(p.wheels[:4]):
            if w.puncture and not self.puncture_called[i]:
                self.puncture_called[i] = True
                out.append(ctx.event("tyres.puncture", Priority.CRITICAL, "tyres", ttl=20, interrupt=True,
                                     allow_in_silence=True, wheel=sp.wheel(WHEELS[i])))
            elif len(known) == 4 and w.pressure_kpa and not self.pressure_called[i]:
                others = [x for j, x in enumerate(pressures) if j != i and x]
                avg = sum(others) / len(others)
                if avg - w.pressure_kpa > 45 and p.speed_ms > 10:
                    self.pressure_called[i] = True
                    out.append(ctx.event("tyres.puncture", Priority.CRITICAL, "tyres", ttl=20, interrupt=True,
                                         allow_in_silence=True, wheel=sp.wheel(WHEELS[i])))
                elif avg - w.pressure_kpa > 18 and p.speed_ms > 10:
                    self.pressure_called[i] = True
                    out.append(ctx.event("tyres.pressure_loss", Priority.HIGH, "tyres", ttl=20,
                                         wheel=sp.wheel(WHEELS[i])))
        if ctx.has_event(StateEventKind.PIT_EXIT):
            self.puncture_called = [False] * 4
            self.pressure_called = [False] * 4

        # --- Verbremser / Wheelspin (zählen, am Rundenende melden) ---
        if f.has(Capability.WHEEL_SLIP) and moving:
            slips = [w.slip_ratio for w in p.wheels[:4] if w.slip_ratio is not None]
            if slips and min(slips) < -0.25 and p.brake > 0.3 and f.t - self.lock_t > 1.0:
                self.lock_t = f.t
                corner = ctx.track.corner_at(me.lap_distance_m) if ctx.track else None
                self.lockups[corner.label if corner else "?"] += 1
            if slips and max(slips) > 0.25 and p.throttle > 0.5 and f.t - self.spin_t > 1.0:
                self.spin_t = f.t
                self.spins += 1
        if ctx.has_event(StateEventKind.LAP_COMPLETED):
            total_locks = sum(self.lockups.values())
            if total_locks >= 3 and me.lap - self.last_lock_call_lap >= 3:
                self.last_lock_call_lap = me.lap
                corner, _ = self.lockups.most_common(1)[0]
                corner_sp = sp.text(corner if corner != "?" else ("verschiedenen Kurven" if sp.lang == "de"
                                                                   else "several corners"))
                prio = Priority.INFO if not ctx.is_race else Priority.CHATTER
                out.append(ctx.event("tyres.lockups", prio, "tyres", ttl=20, count=sp.int_(total_locks),
                                     corner=corner_sp))
            elif self.spins >= 6 and me.lap - self.last_lock_call_lap >= 3:
                self.last_lock_call_lap = me.lap
                out.append(ctx.event("tyres.wheelspin", Priority.CHATTER, "tyres", ttl=20))
            self.lockups.clear()
            self.spins = 0

        # --- Schäden ---
        if f.has(Capability.DAMAGE):
            dmg = p.damage
            for name in ("front_wing_left", "front_wing_right", "rear_wing", "floor", "diffuser", "sidepod", "aero",
                         "body", "suspension", "engine", "gearbox"):
                val = getattr(dmg, name)
                if val is None:
                    continue
                seen = self.damage_seen.get(name)
                if seen is None:
                    self.damage_seen[name] = val
                    if val < 8 or name in ("engine", "gearbox"):
                        continue
                    seen = 0.0
                if name in ("engine", "gearbox"):
                    # Verschleiß steigt langsam – nur Sprünge melden
                    if val - seen > 15:
                        self.damage_seen[name] = val
                        out.append(ctx.event("damage.reported", Priority.HIGH, "damage", ttl=15,
                                             part=damage_part(name, sp), pct=sp.percent(val)))
                    continue
                if val - seen >= 10 and f.t - self.last_damage_call_t > 5:
                    self.damage_seen[name] = val
                    self.last_damage_call_t = f.t
                    severe = val >= 60 and name in ("front_wing_left", "front_wing_right", "suspension", "rear_wing")
                    if severe:
                        out.append(ctx.event("damage.severe", Priority.CRITICAL, "damage", ttl=20, interrupt=True,
                                             part=damage_part(name, sp)))
                    elif name.startswith("front_wing") and ctx.formula:
                        out.append(ctx.event("damage.front_wing_box", Priority.HIGH, "damage", ttl=20,
                                             pct=sp.percent(val)))
                    else:
                        out.append(ctx.event("damage.reported", Priority.HIGH, "damage", ttl=15,
                                             part=damage_part(name, sp), pct=sp.percent(val)))
                elif val < seen - 5:
                    self.damage_seen[name] = val  # repariert
        return out
