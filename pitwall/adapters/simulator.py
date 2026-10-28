"""Deterministischer Renn-Simulator (20+ Autos) als Ersatz für ein laufendes Spiel.

Modell in Kürze:

* Strecke = Liste von Kurven → Referenz-Geschwindigkeitsprofil (Bremsen/
  Beschleunigen physikalisch plausibel, 5-m-Raster).
* Jedes Auto fährt das Profil skaliert mit seinem Pace-Faktor; die
  Ziel-Rundenzeit hängt ab von Fahrer-Pace, Reifenmischung + Alter
  (Degradation mit "Klippe"), Spritmenge, Nässe, Safety Car.
* Boxenstopps mit Boxengasse (Tempolimit) und Standzeit, d. h. der
  Boxenverlust entsteht emergent und kann gelernt werden.
* Szenarien: default, undercut, sc_window, fuel_critical, rain, flashback,
  restart, quali, gt – jedes löst gezielt Detektoren aus.

Mit ``time_scale = 0`` läuft der Simulator so schnell wie möglich (Dry-Run,
Tests); alle Zeitstempel sind virtuell, Ergebnisse per Seed reproduzierbar.
"""

from __future__ import annotations

import asyncio
import copy
import math
import random
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..core.frame import (
    Capability,
    CarState,
    CarStatus,
    Damage,
    Flag,
    ForecastSample,
    GameEvent,
    GameEventKind,
    PitState,
    PlayerState,
    SafetyCar,
    SessionInfo,
    SessionPhase,
    SessionType,
    SimId,
    TelemetryFrame,
    VehicleClass,
    Weather,
    WheelState,
)
from .base import SimAdapter

if TYPE_CHECKING:
    from ..config import Config

# --------------------------------------------------------------------- Strecke


@dataclass(frozen=True, slots=True)
class CornerDef:
    start_m: float
    length_m: float
    min_speed_kmh: float  # Formel-Auto
    direction: int  # -1 links, +1 rechts
    name: str = ""


@dataclass(frozen=True, slots=True)
class TrackDef:
    name: str
    track_id: str
    length_m: float
    corners: tuple[CornerDef, ...]
    pit_entry_m: float  # ab hier Boxengasse (vor der Linie)
    pit_exit_m: float  # bis hierhin Boxengasse (nach der Linie)
    pit_box_m: float  # Position der Box (nach der Linie)
    drs_zones: tuple[tuple[float, float], ...] = ()
    sectors_m: tuple[float, float] = (1700.0, 3400.0)

    def corner_at(self, s: float) -> CornerDef | None:
        for c in self.corners:
            if c.start_m <= s < c.start_m + c.length_m:
                return c
        return None


PITWALL_RING = TrackDef(
    name="PIT WALL Ring",
    track_id="pitwall_ring",
    length_m=5000.0,
    corners=(
        CornerDef(380, 120, 95, +1, "Haarnadel"),
        CornerDef(900, 160, 175, -1, "Waldkurve"),
        CornerDef(1350, 90, 135, +1),
        CornerDef(1520, 80, 140, -1),
        CornerDef(2300, 220, 230, +1, "Parabolica"),
        CornerDef(3050, 110, 110, -1),
        CornerDef(3500, 140, 160, +1),
        CornerDef(3850, 70, 85, -1, "Schikane"),
        CornerDef(3960, 70, 90, +1),
        CornerDef(4450, 150, 145, +1, "Zielkurve"),
    ),
    pit_entry_m=4650.0,
    pit_exit_m=350.0,
    pit_box_m=150.0,
    drs_zones=((4650.0, 5000.0), (0.0, 360.0), (1600.0, 2280.0)),
)


@dataclass(frozen=True, slots=True)
class ClassDef:
    vclass: VehicleClass
    vmax_kmh: float
    brake_decel: float  # m/s²
    accel_low: float  # m/s² bei niedriger Geschwindigkeit
    corner_scale: float  # Kurvenspeed relativ zur Formel
    fuel_capacity_l: float
    fuel_per_lap_l: float
    tyre_window: tuple[float, float]
    compounds: tuple[str, ...]
    pressure_kpa: float
    has_ers: bool
    has_drs: bool


FORMULA = ClassDef(VehicleClass.FORMULA, 330, 42.0, 13.0, 1.0, 110.0, 1.85, (88.0, 105.0),
                   ("soft", "medium", "hard", "inter", "wet"), 158.0, True, True)
GT = ClassDef(VehicleClass.GT, 268, 17.0, 6.5, 0.72, 120.0, 3.1, (75.0, 95.0),
              ("dry", "wet"), 185.0, False, False)


@dataclass(frozen=True, slots=True)
class CompoundDef:
    pace_s: float  # Offset zur Referenz (neg. = schneller)
    deg_s: float  # lineare Degradation pro Runde
    cliff_lap: int  # danach quadratisch
    wear_per_lap: float  # % pro Runde (Vorderrad links Referenz)
    wet_pace_s: float  # Zeitverlust bei voller Nässe (1.0)
    dry_penalty_s: float  # Zeitverlust bei trockener Strecke (Regenreifen)


COMPOUNDS: dict[str, CompoundDef] = {
    "soft": CompoundDef(-0.65, 0.085, 16, 3.6, 14.0, 0.0),
    "medium": CompoundDef(0.0, 0.055, 26, 2.4, 14.0, 0.0),
    "hard": CompoundDef(0.45, 0.035, 38, 1.7, 15.0, 0.0),
    "inter": CompoundDef(1.2, 0.05, 30, 2.0, 2.5, 4.0),
    "wet": CompoundDef(2.6, 0.04, 40, 1.6, 0.8, 7.0),
    "dry": CompoundDef(0.0, 0.045, 32, 1.9, 14.0, 0.0),
}

DRIVERS: tuple[tuple[str, str, str], ...] = (
    ("Lukas Brandt", "Falken Racing", "#e10600"),
    ("Mateo Ruiz", "Falken Racing", "#e10600"),
    ("Noah Keller", "Nordwerk", "#00a19c"),
    ("Elias Varga", "Nordwerk", "#00a19c"),
    ("Jonas Weber", "Vulcan GP", "#ff8000"),
    ("Arthur Laine", "Vulcan GP", "#ff8000"),
    ("Theo Marchetti", "Sirocco", "#3671c6"),
    ("Finn Olsen", "Sirocco", "#3671c6"),
    ("Kenji Sato", "Aurora", "#229971"),
    ("Liam Brooks", "Aurora", "#229971"),
    ("Hugo Duval", "Polaris", "#ff87bc"),
    ("Oskar Lind", "Polaris", "#ff87bc"),
    ("Rafael Costa", "Titan", "#64c4ff"),
    ("Milan Novak", "Titan", "#64c4ff"),
    ("Yusuf Demir", "Kestrel", "#b6babd"),
    ("Daniel Moreau", "Kestrel", "#b6babd"),
    ("Samuel Becker", "Orbit", "#52e252"),
    ("Pavel Horak", "Orbit", "#52e252"),
    ("Ivan Petrov", "Zenith", "#6692ff"),
    ("Leon Fischer", "Zenith", "#6692ff"),
    ("Marco Bellini", "Apex", "#ffd700"),
    ("Erik Sundqvist", "Apex", "#ffd700"),
)


def build_profile(track: TrackDef, cd: ClassDef, ds: float = 5.0) -> list[float]:
    """Referenz-Geschwindigkeit (m/s) je 5-m-Bin."""
    n = int(track.length_m / ds)
    vmax = cd.vmax_kmh / 3.6
    v = [vmax] * n
    for c in track.corners:
        vc = c.min_speed_kmh * cd.corner_scale / 3.6
        vc = min(vc, vmax)
        i0 = int(c.start_m / ds)
        i1 = int((c.start_m + c.length_m) / ds)
        for i in range(i0, i1 + 1):
            # leicht gewölbtes Profil: Scheitel in der Mitte
            frac = (i - i0) / max(1, i1 - i0)
            v[i % n] = min(v[i % n], vc * (1.0 + 0.10 * abs(frac - 0.5) * 2))
    # zwei Durchläufe wegen Rundenschluss
    for _ in range(2):
        for i in range(n - 1, -1, -1):  # Bremsen rückwärts
            nxt = v[(i + 1) % n]
            v[i] = min(v[i], math.sqrt(nxt * nxt + 2 * cd.brake_decel * ds))
        for i in range(n):  # Beschleunigen vorwärts
            prev = v[i - 1]
            acc = cd.accel_low * max(0.15, 1.0 - prev / vmax)
            v[i] = min(v[i], math.sqrt(prev * prev + 2 * acc * ds))
    return v


def profile_lap_time(profile: list[float], ds: float = 5.0) -> float:
    return sum(ds / max(1.0, x) for x in profile)


# ------------------------------------------------------------------ Autos


@dataclass
class SimCar:
    idx: int
    name: str
    team: str
    color: str
    skill_s: float  # Pace-Offset in Sekunden
    consistency: float
    dist: float  # Gesamtdistanz (m); <0 = vor der Startlinie
    compound: str
    tyre_age: int = 0
    tyre_laps_start: int = 0
    fuel_l: float = 100.0
    lap: int = 1
    laps_completed: int = 0
    lap_start_t: float = 0.0
    last_lap_s: float | None = None
    best_lap_s: float | None = None
    sector_times: list[float | None] = field(default_factory=lambda: [None, None, None])
    last_sector_times: list[float | None] = field(default_factory=lambda: [None, None, None])
    sector_start_t: float = 0.0
    sector: int = 1
    pit_plan: list[tuple[int, str]] = field(default_factory=list)  # (Runde, Mischung)
    pit_state: PitState = PitState.ON_TRACK
    pitting: bool = False  # diese Runde in die Box
    box_timer: float = 0.0
    boxed: bool = False
    pit_stops: int = 0
    status: CarStatus = CarStatus.ACTIVE
    lap_target_s: float = 90.0
    speed: float = 0.0
    finished: bool = False
    wear: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    is_player: bool = False
    retire_lap: int | None = None
    best_lap_quali: float | None = None
    lap_kind: str = "race"  # race | out | push | cool | in
    damage_front_wing: float = 0.0
    penalties_s: float = 0.0
    pit_plan_done: int = 0  # Runde, in der der Stopp ausgelöst wurde
    entered_lane: bool = False  # in dieser Runde in die Boxengasse eingebogen


# --------------------------------------------------------------- Simulator


@dataclass
class ScenarioDef:
    name: str
    session_type: SessionType = SessionType.RACE
    vehicle: str = "formula"
    sc_lap: int | None = None
    sc_laps: int = 3
    vsc_lap: int | None = None
    rain_start_lap: int | None = None
    rain_peak: float = 0.65
    fuel_short_laps: float = 0.0
    flashback_at_lap: int | None = None
    restart_at_lap: int | None = None
    undercut_rival: bool = False
    player_pit_laps: tuple[int, ...] = ()
    player_compounds: tuple[str, ...] = ("medium", "hard")
    player_grid: int = 6
    quali_minutes: float = 15.0
    retire_car_lap: int | None = 8
    player_mistakes: float = 0.25  # Wahrscheinlichkeit eines Fehlers pro Runde
    hot_tyres: bool = False
    damage_lap: int | None = None


SCENARIOS: dict[str, ScenarioDef] = {
    "default": ScenarioDef("default", sc_lap=None, player_pit_laps=(9,)),
    "undercut": ScenarioDef("undercut", undercut_rival=True, player_pit_laps=(12,)),
    "sc_window": ScenarioDef("sc_window", sc_lap=5, player_pit_laps=(12,)),
    "fuel_critical": ScenarioDef("fuel_critical", fuel_short_laps=1.6, player_pit_laps=(10,)),
    "rain": ScenarioDef("rain", rain_start_lap=7, player_pit_laps=(9,)),
    "flashback": ScenarioDef("flashback", flashback_at_lap=4, player_pit_laps=(10,)),
    "restart": ScenarioDef("restart", restart_at_lap=3, player_pit_laps=(10,)),
    "quali": ScenarioDef("quali", session_type=SessionType.QUALIFYING, retire_car_lap=None),
    "gt": ScenarioDef("gt", vehicle="gt", player_pit_laps=(11,), player_compounds=("dry", "dry")),
    "damage": ScenarioDef("damage", damage_lap=5, player_pit_laps=(9,)),
    "hot_tyres": ScenarioDef("hot_tyres", hot_tyres=True, player_pit_laps=(9,)),
}


class RaceSimulator:
    """Das eigentliche Modell (ohne asyncio), Schritt für Schritt."""

    def __init__(
        self,
        seed: int = 7,
        n_cars: int = 20,
        laps: int = 20,
        scenario: str = "default",
        vehicle_class: str | None = None,
        hz: float = 20.0,
        track: TrackDef = PITWALL_RING,
    ):
        self.seed = seed
        self.n_cars = max(2, min(n_cars, len(DRIVERS)))
        self.total_laps = laps
        self.scn = copy.deepcopy(SCENARIOS.get(scenario, SCENARIOS["default"]))
        if vehicle_class:
            self.scn.vehicle = vehicle_class
        self.cd = GT if self.scn.vehicle == "gt" else FORMULA
        if self.cd is GT and self.scn.player_compounds[0] not in ("dry", "wet"):
            self.scn.player_compounds = ("dry", "dry")
        self.hz = hz
        self.dt = 1.0 / hz
        self.track = track
        self.ds = 5.0
        self.profile = build_profile(track, self.cd, self.ds)
        self.ref_lap = profile_lap_time(self.profile, self.ds)
        self.player_idx = 0
        self.pending_player_pit = False
        self.pending_compound: str | None = None
        self.player_fuel_saving = False
        self._snapshots: list[tuple[float, Any]] = []
        self._did_flashback = False
        self._did_restart = False
        self._init_session()

    # ------------------------------------------------------------ Setup
    def _init_session(self, keep_uid: bool = False) -> None:
        self.rng = random.Random(self.seed + (1000 if self._did_restart else 0))
        self.t = 0.0
        self.session_t = 0.0
        self.seq = 0
        self.uid = f"sim-{self.seed}-{int(self._did_restart)}"
        self.quali = self.scn.session_type is SessionType.QUALIFYING
        self.phase = SessionPhase.RUNNING if self.quali else SessionPhase.GRID
        self.grid_t = 0.0 if self.quali else 6.0
        self.lights = 0
        self.sc = SafetyCar.NONE
        self.sc_until_lap: int | None = None
        self.vsc_until_t: float | None = None
        self.flag = Flag.GREEN if self.quali else Flag.NONE
        self.wetness = 0.0
        self.rain = 0.0
        self.events: list[GameEvent] = []
        self.leader_finished = False
        self.chequered_sent = False
        self.cars: list[SimCar] = []
        n = self.n_cars
        order = list(range(n))
        player_grid = min(self.scn.player_grid, n)
        names = list(DRIVERS[:n])
        for grid_pos in range(1, n + 1):
            i = order[grid_pos - 1]
            is_player = grid_pos == player_grid
            name, team, color = names[i]
            if is_player:
                name, team, color = "Du", "PIT WALL Racing", "#f5c518"
            skill = (grid_pos - 1) * 0.2 + self.rng.uniform(-0.15, 0.15)
            start_compound = self.scn.player_compounds[0] if is_player else self._ai_start_compound(grid_pos)
            car = SimCar(
                idx=i,
                name=name,
                team=team,
                color=color,
                skill_s=skill,
                consistency=self.rng.uniform(0.08, 0.25),
                dist=-(grid_pos - 1) * 8.0 - 5.0,
                compound=start_compound,
                fuel_l=self._start_fuel(is_player),
                is_player=is_player,
            )
            if is_player:
                self.player_idx = i
                car.pit_plan = [(lap, self.scn.player_compounds[min(k + 1, len(self.scn.player_compounds) - 1)])
                                for k, lap in enumerate(self.scn.player_pit_laps)]
            else:
                car.pit_plan = self._ai_pit_plan(grid_pos, start_compound)
            if self.quali:
                car.dist = -self.rng.uniform(50, 2000) - grid_pos * 60
                car.lap_kind = "out"
            car.lap_target_s = self._target_lap(car)
            self.cars.append(car)
        if self.scn.retire_car_lap and not self.quali:
            victim = self.cars[min(n - 1, 14)]
            if not victim.is_player:
                victim.retire_lap = self.scn.retire_car_lap
        if self.scn.undercut_rival:
            rival = self._car_at_grid(player_grid - 1)
            if rival is not None:
                rival.pit_plan = [(10, "hard")]
                rival.skill_s = self._player().skill_s - 0.05
        self._snapshots.clear()

    def _car_at_grid(self, grid_pos: int) -> SimCar | None:
        if 1 <= grid_pos <= len(self.cars):
            return self.cars[grid_pos - 1]
        return None

    def _player(self) -> SimCar:
        return next(c for c in self.cars if c.is_player)

    def _start_fuel(self, is_player: bool) -> float:
        need = self.cd.fuel_per_lap_l * (self.total_laps + 1.2)
        if is_player:
            need -= self.scn.fuel_short_laps * self.cd.fuel_per_lap_l
        if self.scn.session_type is SessionType.QUALIFYING:
            need = self.cd.fuel_per_lap_l * 6
        return min(self.cd.fuel_capacity_l, need)

    def _ai_start_compound(self, grid_pos: int) -> str:
        if self.cd is GT:
            return "dry"
        if self.scn.session_type is SessionType.QUALIFYING:
            return "soft"
        return "soft" if grid_pos > 10 and self.rng.random() < 0.5 else "medium"

    def _ai_pit_plan(self, grid_pos: int, start: str) -> list[tuple[int, str]]:
        if self.quali:
            return []
        laps = self.total_laps
        if self.cd is GT:
            return [(max(3, int(laps * self.rng.uniform(0.45, 0.6))), "dry")]
        if start == "soft":
            return [(max(3, int(laps * self.rng.uniform(0.3, 0.42))), "hard")]
        return [(max(3, int(laps * self.rng.uniform(0.42, 0.62))), "hard")]

    # ----------------------------------------------------------- Befehle
    def request_player_pit(self, compound: str | None = None) -> None:
        """Fahrer kommt am Ende der laufenden Runde rein (falls die Boxeneinfahrt noch vor ihm liegt)."""
        self.pending_compound = compound
        player = self._player()
        if player.pitting or player.pit_state is not PitState.ON_TRACK:
            return
        if 0 <= self.lap_distance(player) < self.track.pit_entry_m - 30:
            player.pitting = True
            player.pit_plan_done = player.lap
        else:
            self.pending_player_pit = True

    # ------------------------------------------------------------- Modell
    def lap_distance(self, car: SimCar) -> float:
        L = self.track.length_m
        if car.dist < 0:
            return car.dist
        return car.dist - car.laps_completed * L

    def _target_lap(self, car: SimCar) -> float:
        comp = COMPOUNDS.get(car.compound, COMPOUNDS["medium"])
        age = car.tyre_age
        deg = comp.deg_s * age
        if age > comp.cliff_lap:
            deg += 0.06 * (age - comp.cliff_lap) ** 2
        fuel_effect = 0.03 * car.fuel_l
        wet = self.wetness
        wet_effect = comp.wet_pace_s * wet * wet + comp.dry_penalty_s * max(0.0, 1 - wet * 1.6)
        mistakes = 0.0
        if car.is_player and self.rng.random() < self.scn.player_mistakes:
            mistakes = self.rng.uniform(0.15, 0.6)
        noise = self.rng.gauss(0, car.consistency)
        quali_mode = 0.0
        if self.quali:
            quali_mode = {"out": 18.0, "cool": 14.0, "in": 16.0, "push": -0.4}.get(car.lap_kind, 0.0)
        damage = car.damage_front_wing * 0.025
        if car.is_player and self.player_fuel_saving:
            damage += 0.45  # Lift and Coast kostet Zeit
        return (self.ref_lap + car.skill_s + comp.pace_s + deg + fuel_effect + wet_effect + mistakes + noise
                + quali_mode + damage)

    def _speed_at(self, car: SimCar, s_lap: float) -> float:
        n = len(self.profile)
        v_ref = self.profile[int((s_lap % self.track.length_m) / self.ds) % n] if s_lap >= 0 else self.profile[0]
        k = self.ref_lap / max(30.0, car.lap_target_s)
        v = v_ref * k
        if self.sc in (SafetyCar.SC, SafetyCar.ENDING):
            v = min(v, 120 / 3.6 if self.cd is FORMULA else 100 / 3.6)
        elif self.sc is SafetyCar.VSC:
            v = v * 0.62
        if self.phase is SessionPhase.CHECKERED and car.finished:
            v = min(v, 25.0)
        return v

    def _in_pit_lane(self, s_lap: float) -> bool:
        return s_lap >= self.track.pit_entry_m or 0 <= s_lap <= self.track.pit_exit_m

    def step(self) -> list[GameEvent]:
        """Ein Zeitschritt. Gibt neue Spiel-Ereignisse zurück."""
        self.events = []
        dt = self.dt
        self.t += dt
        L = self.track.length_m
        lap_leader = max((c.laps_completed for c in self.cars), default=0)

        # --- Start ---
        if self.phase is SessionPhase.GRID:
            self.session_t += dt
            lights_before = self.lights
            self.lights = min(5, int(self.session_t))
            if self.lights != lights_before and self.lights > 0:
                self.events.append(GameEvent(GameEventKind.START_LIGHTS, data={"num_lights": self.lights}))
            if self.session_t >= self.grid_t:
                self.phase = SessionPhase.RUNNING
                self.lights = 0
                self.flag = Flag.GREEN
                self.events.append(GameEvent(GameEventKind.LIGHTS_OUT))
                for car in self.cars:
                    car.lap_start_t = self.session_t
                    car.sector_start_t = self.session_t
                    car.lap_target_s = self._target_lap(car) + 1.5  # stehender Start
            return self.events

        self.session_t += dt

        # --- Szenario-Trigger ---
        player = self._player()
        if self.scn.sc_lap and self.sc is SafetyCar.NONE and lap_leader + 1 == self.scn.sc_lap and (
            self.sc_until_lap is None
        ):
            self.sc = SafetyCar.SC
            self.sc_until_lap = self.scn.sc_lap + self.scn.sc_laps
            self.flag = Flag.YELLOW
            self.events.append(GameEvent(GameEventKind.SAFETY_CAR, data={"type": "sc", "event": "deployed"}))
        if self.sc is SafetyCar.SC and self.sc_until_lap is not None and lap_leader + 1 == self.sc_until_lap:
            self.sc = SafetyCar.ENDING
            self.events.append(GameEvent(GameEventKind.SAFETY_CAR, data={"type": "sc", "event": "returning"}))
        if self.sc is SafetyCar.ENDING and self.sc_until_lap is not None and lap_leader + 1 > self.sc_until_lap:
            self.sc = SafetyCar.NONE
            self.flag = Flag.GREEN
            self.events.append(GameEvent(GameEventKind.SAFETY_CAR, data={"type": "sc", "event": "returned"}))
        if self.scn.rain_start_lap and player.lap >= self.scn.rain_start_lap:
            self.rain = min(self.scn.rain_peak, self.rain + dt / 240.0)
        self.wetness = max(0.0, min(1.0, self.wetness + (self.rain - self.wetness) * dt / 90.0))
        if (self.scn.flashback_at_lap and not self._did_flashback and player.lap == self.scn.flashback_at_lap
                and self.lap_distance(player) > 2600):
            self._did_flashback = True
            self._flashback(8.0)
            return self.events
        if (self.scn.restart_at_lap and not self._did_restart and player.lap == self.scn.restart_at_lap
                and self.lap_distance(player) > 1500):
            self._did_restart = True
            self._init_session()
            self.events.append(GameEvent(GameEventKind.SESSION_STARTED, data={"reason": "restart"}))
            return self.events
        if self.scn.damage_lap and player.lap == self.scn.damage_lap and player.damage_front_wing == 0 and (
            self.lap_distance(player) > 1000
        ):
            player.damage_front_wing = 35.0
            self.events.append(GameEvent(GameEventKind.COLLISION, car_idx=player.idx))

        # Snapshots für Flashback (alle 2 s, 30 s Historie)
        if int(self.t * self.hz) % int(2 * self.hz) == 0:
            self._snapshots.append((self.session_t, self._capture()))
            if len(self._snapshots) > 15:
                self._snapshots.pop(0)

        # --- Autos bewegen ---
        for car in self.cars:
            if car.status is not CarStatus.ACTIVE:
                continue
            if self.quali and self.session_t > self.scn.quali_minutes * 60 and car.lap_kind != "push":
                car.finished = True
            s_lap = self.lap_distance(car)
            if car.finished and self.phase in (SessionPhase.CHECKERED, SessionPhase.FINISHED) and s_lap > 600:
                car.speed = 0.0
                car.pit_state = PitState.GARAGE
                continue
            v = self._speed_at(car, s_lap)
            # Boxengasse
            if car.pitting and s_lap >= self.track.pit_entry_m and not car.entered_lane:
                car.entered_lane = True
                car.pit_state = PitState.PIT_LANE
            in_lane = (car.entered_lane and self._in_pit_lane(s_lap) and not car.boxed) or (
                car.boxed and 0 <= s_lap <= self.track.pit_exit_m)
            if in_lane:
                v = min(v, 80 / 3.6)
                if (not car.boxed and 0 <= s_lap <= self.track.pit_exit_m and s_lap >= self.track.pit_box_m
                        and car.pit_state is not PitState.PIT_BOX and car.box_timer == 0.0):
                    car.pit_state = PitState.PIT_BOX
                    car.box_timer = 2.4 + abs(self.rng.gauss(0, 0.3)) + (6.0 if car.damage_front_wing > 0 else 0)
                if car.pit_state is PitState.PIT_BOX:
                    v = 0.0
                    car.box_timer -= dt
                    if car.box_timer <= 0:
                        self._service(car)
                        car.pit_state = PitState.PIT_LANE
                        car.boxed = True
            elif car.boxed and s_lap > self.track.pit_exit_m:
                car.boxed = False
                car.pitting = False
                car.entered_lane = False
                car.box_timer = 0.0
                car.pit_state = PitState.ON_TRACK
            # Safety-Car-Bunching: nicht näher als ~25 m an den Vordermann
            if self.sc in (SafetyCar.SC, SafetyCar.ENDING) and not in_lane:
                ahead = self._car_ahead_on_road(car)
                if ahead is not None:
                    gap_m = ahead.dist - car.dist
                    if gap_m < 25:
                        v = min(v, ahead.speed * 0.95)
                    elif gap_m < 200:
                        v = min(v * 1.25, 140 / 3.6) if self.cd is FORMULA else min(v * 1.2, 120 / 3.6)
            car.speed = v
            car.dist += v * dt
            saving = 0.9 if (car.is_player and self.player_fuel_saving) else 1.0
            car.fuel_l = max(0.0, car.fuel_l - self.cd.fuel_per_lap_l * (v * dt) / L * saving *
                             (0.55 if self.sc is not SafetyCar.NONE else 1.0))
            self._sector_and_lap(car)
            if car.is_player and car.fuel_l <= 0.0:
                car.status = CarStatus.RETIRED
                self.events.append(GameEvent(GameEventKind.RETIREMENT, car_idx=car.idx, data={"reason": "fuel"}))

        # --- Rennende ---
        if not self.quali and not self.leader_finished:
            leader = max(self.cars, key=lambda c: c.dist)
            if leader.laps_completed >= self.total_laps:
                self.leader_finished = True
                self.phase = SessionPhase.CHECKERED
                self.flag = Flag.CHEQUERED
                self.events.append(GameEvent(GameEventKind.CHEQUERED_FLAG, car_idx=leader.idx))
        if self.quali and self.session_t > self.scn.quali_minutes * 60 and self.phase is SessionPhase.RUNNING:
            self.phase = SessionPhase.CHECKERED
            self.flag = Flag.CHEQUERED
            self.events.append(GameEvent(GameEventKind.CHEQUERED_FLAG))
        if self.phase is SessionPhase.CHECKERED and all(
                c.finished or c.status is not CarStatus.ACTIVE for c in self.cars):
            self.phase = SessionPhase.FINISHED
            self.events.append(GameEvent(GameEventKind.SESSION_ENDED))
        return self.events

    def _car_ahead_on_road(self, car: SimCar) -> SimCar | None:
        best: SimCar | None = None
        for o in self.cars:
            if o is car or o.status is not CarStatus.ACTIVE or o.pit_state is not PitState.ON_TRACK:
                continue
            if o.dist > car.dist and (best is None or o.dist < best.dist):
                best = o
        return best

    def _service(self, car: SimCar) -> None:
        new = None
        if car.is_player and self.pending_compound:
            new = self.pending_compound
        elif car.pit_plan:
            due = [p for p in car.pit_plan if p[0] <= car.lap]
            if due:
                new = due[0][1]
                car.pit_plan.remove(due[0])
        if self.wetness > 0.35 and self.cd is FORMULA:
            new = "inter" if self.wetness < 0.75 else "wet"
        elif self.wetness > 0.35:
            new = "wet"
        if new is None:
            new = "hard" if car.compound != "hard" or self.cd is GT else "medium"
            if self.cd is GT:
                new = "dry"
        car.compound = new
        car.tyre_age = 0
        car.wear = [0.0, 0.0, 0.0, 0.0]
        car.pit_stops += 1
        car.damage_front_wing = 0.0
        if car.is_player:
            self.pending_player_pit = False
            self.pending_compound = None
            car.fuel_l = max(car.fuel_l, 0.0)

    def _sector_and_lap(self, car: SimCar) -> None:
        L = self.track.length_m
        s_lap = self.lap_distance(car)
        s1, s2 = self.track.sectors_m
        sector = 1 if s_lap < s1 else 2 if s_lap < s2 else 3
        if s_lap >= 0 and sector != car.sector and sector > car.sector:
            car.sector_times[car.sector - 1] = self.session_t - car.sector_start_t
            car.sector_start_t = self.session_t
            car.sector = sector
        if car.dist >= (car.laps_completed + 1) * L:
            # Linie überquert (Zeitpunkt innerhalb des Schritts interpolieren)
            overshoot_t = (car.dist - (car.laps_completed + 1) * L) / max(1.0, car.speed)
            cross_t = self.session_t - min(overshoot_t, self.dt)
            lap_time = cross_t - car.lap_start_t
            car.sector_times[2] = self.session_t - car.sector_start_t
            car.last_sector_times = list(car.sector_times)
            car.sector_times = [None, None, None]
            car.sector = 1
            car.sector_start_t = self.session_t
            car.laps_completed += 1
            if self.phase is not SessionPhase.GRID and lap_time > 20:
                car.last_lap_s = lap_time
                valid = not (self.quali and car.lap_kind != "push")
                if valid and (car.best_lap_s is None or lap_time < car.best_lap_s):
                    car.best_lap_s = lap_time
                    if self.quali or car.laps_completed > 1:
                        fastest = min((c.best_lap_s for c in self.cars if c.best_lap_s), default=lap_time)
                        if lap_time <= fastest:
                            self.events.append(GameEvent(GameEventKind.FASTEST_LAP, car_idx=car.idx,
                                                         data={"lap_time": lap_time}))
            car.lap_start_t = cross_t
            car.lap += 1
            car.tyre_age += 1
            wear_rate = COMPOUNDS.get(car.compound, COMPOUNDS["medium"]).wear_per_lap
            if self.scn.hot_tyres and car.is_player:
                wear_rate *= 1.5
            for w, f in enumerate((1.0, 0.82, 0.78, 0.7)):
                car.wear[w] = min(100.0, car.wear[w] + wear_rate * f * self.rng.uniform(0.9, 1.1))
            if self.leader_finished or (self.quali and self.session_t > self.scn.quali_minutes * 60):
                car.finished = True
            if car.retire_lap and car.lap >= car.retire_lap and not car.is_player:
                car.status = CarStatus.RETIRED
                self.events.append(GameEvent(GameEventKind.RETIREMENT, car_idx=car.idx))
                return
            # Boxenplanung für die kommende Runde
            plan_due = any(p[0] == car.lap for p in car.pit_plan)
            if car.is_player:
                plan_due = (plan_due and not self._player_follows_engineer) or self.pending_player_pit
            ai_sc_stop = (not car.is_player and self.sc is SafetyCar.SC and car.pit_stops == 0 and car.pit_plan and
                          car.pit_plan[0][0] - car.lap <= 6)
            ai_rain_stop = (not car.is_player and self.wetness > 0.35 and car.compound not in ("inter", "wet") and
                            self.rng.random() < 0.6)
            if (plan_due or ai_sc_stop or ai_rain_stop) and not car.finished and self.total_laps - car.laps_completed > 1:
                car.pitting = True
                car.pit_plan_done = car.lap
            if self.quali:
                car.lap_kind = {"out": "push", "push": "cool", "cool": "push", "in": "out"}.get(car.lap_kind, "push")
            car.lap_target_s = self._target_lap(car)

    _player_follows_engineer = False

    # ----------------------------------------------------------- Flashback
    def _capture(self) -> Any:
        return (self.session_t, self.t, copy.deepcopy(self.cars), self.sc, self.sc_until_lap, self.flag,
                self.wetness, self.rain, self.phase, self.leader_finished)

    def _flashback(self, seconds: float) -> None:
        target = self.session_t - seconds
        snap = None
        for st, data in reversed(self._snapshots):
            if st <= target:
                snap = data
                break
        if snap is None:
            return
        (self.session_t, _t, cars, self.sc, self.sc_until_lap, self.flag, self.wetness, self.rain, self.phase,
         self.leader_finished) = snap
        self.cars = copy.deepcopy(cars)
        self.events.append(GameEvent(GameEventKind.FLASHBACK, data={"session_time": self.session_t}))

    # ------------------------------------------------------------- Frames
    def positions(self) -> list[SimCar]:
        if self.quali:
            return sorted(self.cars, key=lambda c: (c.best_lap_s is None, c.best_lap_s or 0, c.idx))
        active = [c for c in self.cars]
        return sorted(active, key=lambda c: (c.status is not CarStatus.ACTIVE and not c.finished, -c.dist))

    def frame(self) -> TelemetryFrame:
        self.seq += 1
        L = self.track.length_m
        order = self.positions()
        pos_of = {c.idx: i + 1 for i, c in enumerate(order)}
        player = self._player()
        cars: list[CarState] = []
        for c in self.cars:
            s_lap = self.lap_distance(c)
            state = CarState(
                idx=c.idx,
                driver_name=c.name,
                team=c.team,
                team_color=c.color,
                car_number=str(c.idx + 1),
                position=pos_of[c.idx],
                lap=c.lap,
                laps_completed=c.laps_completed,
                lap_distance_m=s_lap,
                lap_pct=max(0.0, s_lap) / L,
                total_distance_m=c.dist,
                sector=c.sector,
                current_lap_s=self.session_t - c.lap_start_t if self.phase is not SessionPhase.GRID else 0.0,
                last_lap_s=c.last_lap_s,
                best_lap_s=c.best_lap_s,
                sector_times_s=list(c.sector_times),
                last_sector_times_s=list(c.last_sector_times),
                pit_state=c.pit_state,
                pit_stops=c.pit_stops,
                tyre_compound=c.compound,
                tyre_age_laps=c.tyre_age,
                status=CarStatus.FINISHED if c.finished and c.status is CarStatus.ACTIVE else c.status,
                is_player=c.is_player,
                is_teammate=(c.team == player.team and not c.is_player),
                speed_ms=c.speed,
            )
            x, z = self._xy(s_lap)
            state.x, state.z = x, z
            cars.append(state)
        session = SessionInfo(
            sim=SimId.SIMULATOR,
            track_name=self.track.name,
            track_id=self.track.track_id,
            track_length_m=L,
            session_type=self.scn.session_type,
            phase=self.phase,
            session_uid=self.uid,
            total_laps=None if self.quali else self.total_laps,
            duration_s=self.scn.quali_minutes * 60 if self.quali else None,
            time_remaining_s=max(0.0, self.scn.quali_minutes * 60 - self.session_t) if self.quali else None,
            session_time_s=self.session_t,
            num_cars=len(self.cars),
            vehicle_class=self.cd.vclass,
            car_name="PW-01" if self.cd is FORMULA else "PW GT3",
            flag=self.flag,
            safety_car=self.sc,
            weather=self._weather(),
            pit_speed_limit_kmh=80.0,
            start_lights=self.lights if self.phase is SessionPhase.GRID else None,
        )
        frame = TelemetryFrame(
            seq=self.seq,
            t=self.t,
            sim=SimId.SIMULATOR,
            capabilities=self.capabilities(),
            session=session,
            player=self._player_state(player),
            player_idx=player.idx,
            cars=cars,
            events=list(self.events),
        )
        return frame

    def capabilities(self) -> frozenset[Capability]:
        caps = {
            Capability.POSITION, Capability.LAP_TIMES, Capability.SECTOR_TIMES, Capability.LAP_DISTANCE,
            Capability.OPPONENTS, Capability.OPPONENT_LAP_TIMES, Capability.OPPONENT_TYRES, Capability.OPPONENT_PIT,
            Capability.TEAMMATE, Capability.FUEL, Capability.TYRE_TEMPS, Capability.TYRE_PRESSURE,
            Capability.TYRE_WEAR, Capability.TYRE_COMPOUND, Capability.TYRE_AGE, Capability.BRAKE_TEMPS,
            Capability.DAMAGE, Capability.INPUTS, Capability.WHEEL_SLIP, Capability.TRACK_POSITION_XY,
            Capability.FLAGS, Capability.SAFETY_CAR, Capability.PIT_STATUS, Capability.WEATHER,
            Capability.WEATHER_FORECAST, Capability.SESSION_TIME, Capability.TOTAL_LAPS, Capability.START_LIGHTS,
            Capability.FLASHBACK, Capability.YAW_RATE,
        }
        if self.cd.has_ers:
            caps.add(Capability.ERS)
        if self.cd.has_drs:
            caps.add(Capability.DRS)
        return frozenset(caps)

    def _weather(self) -> Weather:
        cond = "clear" if self.rain < 0.05 else "light_rain" if self.rain < 0.5 else "heavy_rain"
        forecast: list[ForecastSample] = []
        if self.scn.rain_start_lap:
            player = self._player()
            laps_to_rain = self.scn.rain_start_lap - player.lap
            minutes = max(0.0, laps_to_rain * self.ref_lap / 60.0)
            for m in (5, 10, 15, 30):
                will_rain = m >= minutes
                forecast.append(ForecastSample(m, "light_rain" if will_rain else "overcast",
                                               rain_percentage=80.0 if will_rain else 20.0, air_temp_c=19.0,
                                               track_temp_c=24.0))
        else:
            forecast = [ForecastSample(m, "clear", 5.0, 24.0, 36.0) for m in (5, 10, 15, 30)]
        track_temp = 36.0 - 12.0 * self.wetness
        return Weather(condition=cond, air_temp_c=24.0 - 5 * self.rain, track_temp_c=track_temp,
                       rain_intensity=self.rain, track_wetness=self.wetness, forecast=forecast)

    def _xy(self, s_lap: float) -> tuple[float, float]:
        """Plausible Streckenkarte: Kurvenrichtungen integriert zu einer geschlossenen Linie."""
        if not hasattr(self, "_xy_cache"):
            self._build_xy()
        n = len(self._xy_cache)
        i = int((s_lap % self.track.length_m) / self.ds) % n if s_lap >= 0 else 0
        return self._xy_cache[i]

    def _build_xy(self) -> None:
        n = len(self.profile)
        heading = 0.0
        x = z = 0.0
        pts: list[tuple[float, float]] = []
        # Gesamtdrehung auf 2π normieren, damit die Linie schließt
        turn = [0.0] * n
        for c in self.track.corners:
            i0 = int(c.start_m / self.ds)
            i1 = int((c.start_m + c.length_m) / self.ds)
            for i in range(i0, i1):
                turn[i % n] += c.direction * (1.0 + 120.0 / max(60.0, c.min_speed_kmh))
        total = sum(turn)
        scale = (2 * math.pi) / total if total else 0.0
        for i in range(n):
            heading += turn[i] * scale
            x += math.cos(heading) * self.ds
            z += math.sin(heading) * self.ds
            pts.append((x, z))
        # Schließfehler linear verteilen
        ex, ez = pts[-1]
        self._xy_cache = [(px - ex * k / n, pz - ez * k / n) for k, (px, pz) in enumerate(pts)]

    def _player_state(self, car: SimCar) -> PlayerState:
        s_lap = max(0.0, self.lap_distance(car))
        n = len(self.profile)
        i = int(s_lap / self.ds) % n
        v_now = self.profile[i]
        v_next = self.profile[(i + 2) % n]
        corner = self.track.corner_at(s_lap)
        braking = v_next < v_now - 0.4 and car.speed > 5
        accel = v_next > v_now + 0.05
        if car.pit_state is PitState.PIT_BOX or car.speed < 0.5:
            throttle, brake = 0.0, 0.0 if car.speed > 0 else 0.3
        elif braking:
            throttle, brake = 0.0, min(1.0, 0.35 + (v_now - v_next) / 2.5)
        elif accel:
            throttle, brake = min(1.0, 0.65 + (v_next - v_now) * 1.5), 0.0
        elif corner:
            throttle, brake = 0.55, 0.0
        else:
            throttle, brake = 1.0, 0.0
        if self.sc is not SafetyCar.NONE:
            throttle *= 0.5
        steer = 0.0
        if corner:
            steer = corner.direction * min(1.0, 22.0 / max(20.0, corner.min_speed_kmh * self.cd.corner_scale))
        lo, hi = self.cd.tyre_window
        mid = (lo + hi) / 2
        load = abs(steer) * 10 + brake * 6
        hot = 14.0 if self.scn.hot_tyres else 0.0
        cool = -18.0 * self.wetness if car.compound not in ("inter", "wet") else 0.0
        sc_cool = -15.0 if self.sc is not SafetyCar.NONE else 0.0
        wheels: list[WheelState] = []
        rng = random.Random(self.seq * 31 + car.idx)
        for w in range(4):
            side_bias = (2.5 if (w % 2 == 0) else -1.0) * (1 if steer > 0 else 0.4)
            surf = mid + load * (1.1 if w < 2 else 0.8) + side_bias + hot * (1.3 if w == 0 else 1.0) + cool + sc_cool
            surf += rng.uniform(-1.5, 1.5)
            core = mid - 2 + hot * 0.8 + cool * 0.6 + sc_cool * 0.5
            slip = 0.0
            if brake > 0.9 and w < 2 and rng.random() < 0.012:
                slip = -0.35  # kurzes Blockieren
            if throttle > 0.9 and car.speed < 30 and w >= 2 and rng.random() < 0.03:
                slip = 0.3
            wheels.append(WheelState(
                temp_surface_c=surf,
                temp_inner_c=surf + 3,
                temp_middle_c=surf,
                temp_outer_c=surf - 3,
                temp_core_c=core,
                pressure_kpa=self.cd.pressure_kpa + (core - mid) * 0.35,
                wear_pct=car.wear[w],
                brake_temp_c=380 + brake * 450 + rng.uniform(-20, 20),
                slip_ratio=slip,
            ))
        damage = Damage(front_wing_left=car.damage_front_wing, front_wing_right=car.damage_front_wing * 0.4,
                        rear_wing=0.0, floor=0.0, engine=car.laps_completed * 0.4, gearbox=car.laps_completed * 0.3)
        if self.cd is GT:
            damage = Damage(aero=car.damage_front_wing, body=car.damage_front_wing * 0.5, suspension=0.0)
        ers = None
        if self.cd.has_ers:
            ers = 100.0 * (0.5 + 0.45 * math.sin(s_lap / self.track.length_m * 2 * math.pi * 2))
        drs_zone = any(a <= s_lap < b for a, b in self.track.drs_zones)
        ahead = self._car_ahead_on_road(car)
        drs_avail = bool(self.cd.has_drs and car.laps_completed >= 2 and self.sc is SafetyCar.NONE and ahead
                         and 0 < ahead.dist - car.dist < car.speed * 1.0 + 1)
        gear = max(1, min(8, int(car.speed / 11.5) + 1)) if car.speed > 0.5 else 0
        return PlayerState(
            speed_ms=car.speed,
            gear=gear,
            rpm=4000 + (car.speed % 11.5) / 11.5 * 7500,
            max_rpm=12000,
            throttle=throttle,
            brake=brake,
            steering=steer,
            fuel_l=car.fuel_l,
            fuel_capacity_l=self.cd.fuel_capacity_l,
            wheels=wheels,
            tyre_compound=car.compound,
            tyre_age_laps=car.tyre_age,
            damage=damage,
            ers_store_pct=ers,
            ers_mode="medium" if ers is not None else None,
            drs_available=drs_avail,
            drs_active=drs_avail and drs_zone,
            pit_limiter=car.pit_state is not PitState.ON_TRACK,
            yaw_rate=steer * car.speed / 60.0,
            lat_accel=steer * car.speed * car.speed / 300.0,
            long_accel=(-brake * self.cd.brake_decel) if brake else throttle * self.cd.accel_low * 0.5,
        )

    def run(self, max_steps: int | None = None) -> Iterator[TelemetryFrame]:
        steps = 0
        while True:
            self.step()
            yield self.frame()
            steps += 1
            if self.phase is SessionPhase.FINISHED and steps > 10:
                for _ in range(int(self.hz * 3)):
                    self.step()
                    yield self.frame()
                return
            if max_steps is not None and steps >= max_steps:
                return


# ------------------------------------------------------------------ Adapter


class SimulatorAdapter(SimAdapter):
    sim_id = SimId.SIMULATOR
    key = "simulator"
    display_name = "PIT WALL Simulator"

    def __init__(self, config: Config, scenario: str | None = None, time_scale: float | None = None,
                 laps: int | None = None, seed: int | None = None):
        super().__init__(config)
        sc = config.section("simulator")
        self.sim = RaceSimulator(
            seed=int(seed if seed is not None else sc.get("seed", 7)),
            n_cars=int(sc.get("cars", 20)),
            laps=int(laps if laps is not None else sc.get("laps", 20)),
            scenario=scenario or str(sc.get("scenario", "default")),
            vehicle_class=str(sc.get("vehicle_class", "formula")) if not scenario else None,
            hz=float(sc.get("hz", 20)),
        )
        self.time_scale = float(time_scale if time_scale is not None else sc.get("time_scale", 1.0))
        self.capabilities = self.sim.capabilities()
        self.loop_forever = True

    def command(self, name: str, **kwargs: Any) -> bool:
        if name == "box":
            self.sim.request_player_pit(kwargs.get("compound"))
            return True
        return False

    async def frames(self) -> AsyncIterator[TelemetryFrame]:  # type: ignore[override]
        self.running = True
        self.status_text = "Simulation läuft"
        period = self.sim.dt
        next_t = time.monotonic()
        while self.running:
            for frame in self.sim.run():
                if not self.running:
                    return
                self.last_packet_t = time.monotonic()
                yield frame
                if self.time_scale > 0:
                    next_t += period / self.time_scale
                    delay = next_t - time.monotonic()
                    if delay > 0:
                        await asyncio.sleep(delay)
                    elif delay < -1.0:
                        next_t = time.monotonic()
                else:
                    if frame.seq % 200 == 0:
                        await asyncio.sleep(0)
            if not self.loop_forever:
                return
            # neues Rennen
            self.sim = RaceSimulator(seed=self.sim.seed + 1, n_cars=self.sim.n_cars, laps=self.sim.total_laps,
                                     scenario=self.sim.scn.name, hz=self.sim.hz)

    @classmethod
    async def detect(cls, config: Config, timeout: float = 1.0) -> bool:
        return False
