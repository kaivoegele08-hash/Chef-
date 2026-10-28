"""Normalisiertes Telemetrie-Datenmodell.

Jeder Sim-Adapter übersetzt seine Rohdaten in einen :class:`TelemetryFrame`.
Alles dahinter (State, Strategie, Detektoren, Dashboard, LLM-Tools) kennt nur
dieses Modell – nie die Rohformate.

Einheiten intern (SI): Meter, Sekunden, m/s, °C, kPa, Liter, Prozent 0..100
für Verschleiß/Schaden, 0..1 für Pedale und Anteile. Umrechnung (km/h, psi, …)
nur in der Ausgabe-Schicht.

Radreihenfolge überall: FL, FR, RL, RR (Index 0..3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

WHEELS = ("FL", "FR", "RL", "RR")


class SimId(StrEnum):
    F1_24 = "f1_24"
    F1_25 = "f1_25"
    GT7 = "gt7"
    AC = "assetto_corsa"
    ACC = "acc"
    IRACING = "iracing"
    RF2 = "rfactor2"
    LMU = "lmu"
    ETS2 = "ets2"
    ATS = "ats"
    SIMULATOR = "simulator"
    REPLAY = "replay"
    UNKNOWN = "unknown"


SIM_DISPLAY_NAMES: dict[SimId, str] = {
    SimId.F1_24: "EA SPORTS F1 24",
    SimId.F1_25: "EA SPORTS F1 25",
    SimId.GT7: "Gran Turismo 7",
    SimId.AC: "Assetto Corsa",
    SimId.ACC: "Assetto Corsa Competizione",
    SimId.IRACING: "iRacing",
    SimId.RF2: "rFactor 2",
    SimId.LMU: "Le Mans Ultimate",
    SimId.ETS2: "Euro Truck Simulator 2",
    SimId.ATS: "American Truck Simulator",
    SimId.SIMULATOR: "PIT WALL Simulator",
    SimId.REPLAY: "Replay",
    SimId.UNKNOWN: "Unbekannt",
}


class Capability(StrEnum):
    """Welche Datenfelder ein Adapter tatsächlich liefert."""

    POSITION = "position"  # eigene Rennposition
    LAP_TIMES = "lap_times"
    SECTOR_TIMES = "sector_times"
    LAP_DISTANCE = "lap_distance"  # Rundendistanz/Spline-Position
    OPPONENTS = "opponents"  # Liste anderer Autos mit Position/Distanz
    OPPONENT_LAP_TIMES = "opponent_lap_times"
    OPPONENT_TYRES = "opponent_tyres"
    OPPONENT_PIT = "opponent_pit"
    NATIVE_GAPS = "native_gaps"  # Sim liefert Abstände selbst
    TEAMMATE = "teammate"
    FUEL = "fuel"
    TYRE_TEMPS = "tyre_temps"
    TYRE_PRESSURE = "tyre_pressure"
    TYRE_WEAR = "tyre_wear"
    TYRE_COMPOUND = "tyre_compound"
    TYRE_AGE = "tyre_age"
    BRAKE_TEMPS = "brake_temps"
    DAMAGE = "damage"
    ERS = "ers"
    DRS = "drs"
    INPUTS = "inputs"  # Gas/Bremse/Lenkung
    WHEEL_SLIP = "wheel_slip"
    YAW_RATE = "yaw_rate"
    TRACK_POSITION_XY = "track_position_xy"
    FLAGS = "flags"
    SECTOR_FLAGS = "sector_flags"
    SAFETY_CAR = "safety_car"
    PIT_STATUS = "pit_status"
    PENALTIES = "penalties"
    INCIDENTS = "incidents"
    WEATHER = "weather"
    WEATHER_FORECAST = "weather_forecast"
    SESSION_TIME = "session_time"
    TOTAL_LAPS = "total_laps"
    START_LIGHTS = "start_lights"
    FLASHBACK = "flashback"
    PIT_WINDOW = "pit_window"
    # Truck-Modus
    TRUCK_JOB = "truck_job"
    TRUCK_FATIGUE = "truck_fatigue"
    TRUCK_NAVIGATION = "truck_navigation"
    SPEED_LIMIT = "speed_limit"


class SessionType(StrEnum):
    PRACTICE = "practice"
    QUALIFYING = "qualifying"
    RACE = "race"
    TIME_TRIAL = "time_trial"
    WARMUP = "warmup"
    HOTLAP = "hotlap"
    FREE_DRIVE = "free_drive"  # z. B. ETS2, GT7-Freies Fahren
    UNKNOWN = "unknown"

    @property
    def is_race(self) -> bool:
        return self is SessionType.RACE

    @property
    def is_qualifying(self) -> bool:
        return self in (SessionType.QUALIFYING, SessionType.HOTLAP, SessionType.TIME_TRIAL)


class SessionPhase(StrEnum):
    MENU = "menu"
    GARAGE = "garage"
    FORMATION = "formation"  # Einführungsrunde
    GRID = "grid"  # in der Startaufstellung, Ampel noch nicht aus
    RUNNING = "running"
    CHECKERED = "checkered"  # Zielflagge gefallen, Auslaufrunde
    FINISHED = "finished"
    PAUSED = "paused"
    UNKNOWN = "unknown"


class Flag(StrEnum):
    NONE = "none"
    GREEN = "green"
    YELLOW = "yellow"
    DOUBLE_YELLOW = "double_yellow"
    BLUE = "blue"
    WHITE = "white"
    RED = "red"
    CHEQUERED = "chequered"
    BLACK = "black"
    MEATBALL = "meatball"  # Schwarz-Orange: Schaden reparieren
    BLACK_WHITE = "black_white"  # Verwarnung (Unsportlichkeit/Track Limits)


class SafetyCar(StrEnum):
    NONE = "none"
    SC = "sc"
    VSC = "vsc"
    FCY = "fcy"  # Full Course Yellow
    FORMATION = "formation"
    ENDING = "ending"  # SC/VSC kommt diese Runde rein


class VehicleClass(StrEnum):
    FORMULA = "formula"
    GT = "gt"
    PROTOTYPE = "prototype"
    TOURING = "touring"
    ROAD = "road"
    OVAL = "oval"
    TRUCK = "truck"
    UNKNOWN = "unknown"

    @property
    def has_ers(self) -> bool:
        return self in (VehicleClass.FORMULA, VehicleClass.PROTOTYPE)

    @property
    def has_drs(self) -> bool:
        return self is VehicleClass.FORMULA


class PitState(StrEnum):
    ON_TRACK = "on_track"
    PIT_LANE = "pit_lane"
    PIT_BOX = "pit_box"  # steht in der Box
    GARAGE = "garage"
    OFF_TRACK = "off_track"


class CarStatus(StrEnum):
    ACTIVE = "active"
    FINISHED = "finished"
    DNF = "dnf"
    DSQ = "dsq"
    RETIRED = "retired"
    NOT_CLASSIFIED = "nc"
    INACTIVE = "inactive"


class GameEventKind(StrEnum):
    """Spiel-native Ereignisse, die Adapter direkt melden (wo verfügbar)."""

    SESSION_STARTED = "session_started"
    SESSION_ENDED = "session_ended"
    LIGHTS_OUT = "lights_out"
    START_LIGHTS = "start_lights"  # data: num_lights
    FASTEST_LAP = "fastest_lap"  # data: car_idx, lap_time
    PENALTY = "penalty"  # data: car_idx, penalty_type, infringement, time
    FLASHBACK = "flashback"  # data: frame_id, session_time
    SAFETY_CAR = "safety_car"  # data: type, event (deployed/returning/returned/resume)
    RED_FLAG = "red_flag"
    CHEQUERED_FLAG = "chequered_flag"
    RACE_WINNER = "race_winner"
    RETIREMENT = "retirement"
    OVERTAKE = "overtake"  # data: overtaker, overtaken
    COLLISION = "collision"
    DRS_ENABLED = "drs_enabled"
    DRS_DISABLED = "drs_disabled"
    TEAMMATE_IN_PITS = "teammate_in_pits"
    DRIVE_THROUGH_SERVED = "drive_through_served"
    STOP_GO_SERVED = "stop_go_served"
    SPEED_TRAP = "speed_trap"
    TRACK_LIMITS = "track_limits"
    JOB_DELIVERED = "job_delivered"  # Truck
    JOB_CANCELLED = "job_cancelled"
    FINE = "fine"
    TOLLGATE = "tollgate"
    FERRY = "ferry"
    REFUEL = "refuel"
    BUTTON = "button"  # Lenkradtasten über Spiel (F1 BUTN)


@dataclass(slots=True)
class GameEvent:
    kind: GameEventKind
    car_idx: int | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class WheelState:
    temp_surface_c: float | None = None  # Oberflächentemperatur (Mittel)
    temp_inner_c: float | None = None
    temp_middle_c: float | None = None
    temp_outer_c: float | None = None
    temp_core_c: float | None = None  # Karkasse/innen
    pressure_kpa: float | None = None
    wear_pct: float | None = None  # 0 = neu, 100 = runter
    brake_temp_c: float | None = None
    damage_pct: float | None = None
    slip_ratio: float | None = None  # >0 Wheelspin, <0 Blockieren (normiert)
    slip_angle: float | None = None
    puncture: bool = False

    def surface_temp(self) -> float | None:
        """Repräsentative Oberflächentemperatur."""
        if self.temp_surface_c is not None:
            return self.temp_surface_c
        vals = [v for v in (self.temp_inner_c, self.temp_middle_c, self.temp_outer_c) if v is not None]
        return sum(vals) / len(vals) if vals else None


@dataclass(slots=True)
class ForecastSample:
    minutes_ahead: float
    condition: str  # clear, light_cloud, overcast, light_rain, heavy_rain, storm
    rain_percentage: float | None = None  # Regenwahrscheinlichkeit 0..100
    air_temp_c: float | None = None
    track_temp_c: float | None = None


@dataclass(slots=True)
class Weather:
    condition: str = "unknown"
    air_temp_c: float | None = None
    track_temp_c: float | None = None
    rain_intensity: float | None = None  # 0..1 aktuell
    track_wetness: float | None = None  # 0..1
    forecast: list[ForecastSample] = field(default_factory=list)

    @property
    def is_wet(self) -> bool:
        return (self.rain_intensity or 0.0) > 0.15 or (self.track_wetness or 0.0) > 0.25 or self.condition in (
            "light_rain",
            "heavy_rain",
            "storm",
        )


@dataclass(slots=True)
class SessionInfo:
    sim: SimId = SimId.UNKNOWN
    track_name: str = ""
    track_id: str = ""
    track_length_m: float | None = None
    session_type: SessionType = SessionType.UNKNOWN
    phase: SessionPhase = SessionPhase.UNKNOWN
    session_uid: str = ""  # wechselt bei neuer Session/Neustart (falls Sim das liefert)
    total_laps: int | None = None  # None bei Zeitrennen/unbekannt
    duration_s: float | None = None  # Sessionlänge bei Zeitrennen
    time_remaining_s: float | None = None
    session_time_s: float = 0.0  # Zeit seit Sessionstart (Spielzeit)
    num_cars: int = 0
    vehicle_class: VehicleClass = VehicleClass.UNKNOWN
    car_name: str = ""
    flag: Flag = Flag.NONE
    safety_car: SafetyCar = SafetyCar.NONE
    yellow_sectors: list[int] = field(default_factory=list)  # 1-basiert
    weather: Weather = field(default_factory=Weather)
    pit_speed_limit_kmh: float | None = None
    pit_window_ideal_lap: int | None = None
    pit_window_latest_lap: int | None = None
    start_lights: int | None = None  # Anzahl leuchtender Ampeln
    paused: bool = False


@dataclass(slots=True)
class CarState:
    """Ein Auto im Feld (auch der Spieler selbst erscheint in ``cars``)."""

    idx: int
    driver_name: str = ""
    team: str = ""
    car_number: str = ""
    car_class: str = ""
    team_color: str = ""  # #rrggbb falls bekannt
    position: int | None = None
    class_position: int | None = None
    lap: int = 0  # aktuelle Runde (1-basiert, 0 = vor Start)
    laps_completed: int = 0
    lap_distance_m: float | None = None
    lap_pct: float | None = None  # 0..1
    total_distance_m: float | None = None  # gefahrene Gesamtdistanz (für Gaps)
    sector: int | None = None  # 1..3
    current_lap_s: float | None = None
    last_lap_s: float | None = None
    best_lap_s: float | None = None
    sector_times_s: list[float | None] = field(default_factory=list)  # aktuelle Runde
    last_sector_times_s: list[float | None] = field(default_factory=list)
    gap_to_leader_s: float | None = None
    gap_to_ahead_s: float | None = None  # Intervall
    pit_state: PitState = PitState.ON_TRACK
    pit_stops: int = 0
    tyre_compound: str | None = None  # normalisiert: soft, medium, hard, inter, wet, ...
    tyre_age_laps: int | None = None
    status: CarStatus = CarStatus.ACTIVE
    penalties_s: float = 0.0
    is_player: bool = False
    is_teammate: bool = False
    x: float | None = None
    z: float | None = None  # horizontale Ebene x/z (y = Höhe)
    speed_ms: float | None = None

    @property
    def in_pit(self) -> bool:
        return self.pit_state in (PitState.PIT_LANE, PitState.PIT_BOX, PitState.GARAGE)


@dataclass(slots=True)
class Damage:
    """Schäden in Prozent 0..100 (nur Felder, die die Sim liefert, sind gesetzt)."""

    front_wing_left: float | None = None
    front_wing_right: float | None = None
    rear_wing: float | None = None
    floor: float | None = None
    diffuser: float | None = None
    sidepod: float | None = None
    aero: float | None = None  # zusammengefasst (GT/Prototyp)
    body: float | None = None
    suspension: float | None = None
    engine: float | None = None
    gearbox: float | None = None
    transmission: float | None = None
    # Truck-spezifisch
    cabin: float | None = None
    chassis: float | None = None
    wheels: float | None = None
    trailer: float | None = None
    cargo: float | None = None

    def worst(self) -> tuple[str, float] | None:
        best: tuple[str, float] | None = None
        for name in self.__slots__:
            val = getattr(self, name)
            if val is not None and (best is None or val > best[1]):
                best = (name, val)
        return best


@dataclass(slots=True)
class TruckState:
    """Zusatzdaten für ETS2/ATS (Disponenten-Modus)."""

    speed_limit_kmh: float | None = None
    cruise_control_kmh: float | None = None
    odometer_km: float | None = None
    fuel_range_km: float | None = None
    fuel_avg_consumption_l_per_km: float | None = None
    nav_distance_m: float | None = None
    nav_time_s: float | None = None  # Navigationszeit (Spielzeit)
    game_time_min: int | None = None  # Spielzeit in Minuten seit Spielbeginn
    job_active: bool = False
    job_cargo: str = ""
    job_source_city: str = ""
    job_destination_city: str = ""
    job_destination_company: str = ""
    job_income: int | None = None
    job_delivery_time_min: int | None = None  # absolute Spielzeit-Minute der Deadline
    job_planned_distance_km: float | None = None
    job_cargo_mass_kg: float | None = None
    next_rest_stop_min: int | None = None  # Minuten Spielzeit bis Müdigkeit
    parking_brake: bool = False
    engine_on: bool = True
    lights_on: bool = False
    trailer_attached: bool = False
    fuel_warning: bool = False


@dataclass(slots=True)
class PlayerState:
    """Detaildaten des eigenen Autos."""

    speed_ms: float = 0.0
    gear: int = 0  # -1 = R, 0 = N
    rpm: float = 0.0
    max_rpm: float | None = None
    throttle: float = 0.0  # 0..1
    brake: float = 0.0  # 0..1
    clutch: float = 0.0
    steering: float = 0.0  # -1 (links) .. 1 (rechts)
    fuel_l: float | None = None
    fuel_capacity_l: float | None = None
    fuel_unit: str = "l"  # "l" oder "kg" (F1-Spiele rechnen in kg)
    fuel_remaining_laps_game: float | None = None  # Schätzung des Spiels (F1)
    wheels: list[WheelState] = field(default_factory=lambda: [WheelState() for _ in range(4)])
    tyre_compound: str | None = None
    tyre_age_laps: int | None = None
    damage: Damage = field(default_factory=Damage)
    ers_store_pct: float | None = None  # 0..100
    ers_mode: str | None = None
    ers_deployed_this_lap_pct: float | None = None
    drs_available: bool = False
    drs_active: bool = False
    pit_limiter: bool = False
    abs_active: bool = False
    tc_active: bool = False
    yaw_rate: float | None = None  # rad/s
    lat_accel: float | None = None  # m/s²
    long_accel: float | None = None
    lap_invalid: bool = False
    incidents: int | None = None
    warnings: int | None = None  # Track-Limit-Verwarnungen
    pit_requested: bool = False
    off_track: bool = False
    truck: TruckState | None = None


@dataclass(slots=True)
class TelemetryFrame:
    """Ein konsistenter Schnappschuss der Lage."""

    seq: int
    t: float  # monotone Zeit in Sekunden (Quelle: Adapter; bei Replay/Simulator virtuell)
    sim: SimId
    capabilities: frozenset[Capability]
    session: SessionInfo
    player: PlayerState
    player_idx: int = 0
    cars: list[CarState] = field(default_factory=list)
    events: list[GameEvent] = field(default_factory=list)
    raw_extra: dict[str, Any] = field(default_factory=dict)

    def has(self, cap: Capability) -> bool:
        return cap in self.capabilities

    @property
    def me(self) -> CarState | None:
        for car in self.cars:
            if car.idx == self.player_idx:
                return car
        return None

    def car(self, idx: int) -> CarState | None:
        for car in self.cars:
            if car.idx == idx:
                return car
        return None

    def by_position(self) -> list[CarState]:
        return sorted(
            (c for c in self.cars if c.position is not None and c.status is not CarStatus.INACTIVE),
            key=lambda c: c.position or 999,
        )


def normalize_compound(name: str | None) -> str | None:
    """Vereinheitlicht Mischungsnamen verschiedener Sims."""
    if not name:
        return None
    n = name.strip().lower()
    table = {
        "s": "soft", "soft": "soft", "c5": "soft", "c4": "soft", "hypersoft": "soft", "supersoft": "soft",
        "ultrasoft": "soft", "racing soft": "soft", "rs": "soft",
        "m": "medium", "medium": "medium", "c3": "medium", "racing medium": "medium", "rm": "medium",
        "h": "hard", "hard": "hard", "c2": "hard", "c1": "hard", "c0": "hard", "racing hard": "hard", "rh": "hard",
        "i": "inter", "inter": "inter", "intermediate": "inter",
        "w": "wet", "wet": "wet", "full wet": "wet", "heavy wet": "wet", "rain": "wet", "wet tyres": "wet",
        "dry": "dry", "slick": "dry", "dry_compound": "dry",
    }
    return table.get(n, n)
