"""Euro Truck Simulator 2 / American Truck Simulator über das SCS-Telemetry-Plugin (Layout Rev 12).

* Linux nativ: ``libscs-telemetry.so`` (truckermudgeon/scs-sdk-plugin, MIT) → ``/dev/shm/SCSTelemetry``
  (alxwk-Fork: ``/dev/shm/SCS/SCSTelemetry``).
* Windows / Proton: RenCloud-DLL → ``Local\\SCSTelemetry`` (unter Proton über ``pitwall-relay.exe --sim ets2``).

Offsets: ``docs/protocols/ets2.md`` (gegen ``offsetof`` verifiziert). Event-Flags ab Rev 11 sind Toggles.
"""

from __future__ import annotations

import logging
import struct
from typing import TYPE_CHECKING, Any

from ..core.frame import (
    Capability,
    CarState,
    Damage,
    GameEvent,
    GameEventKind,
    PlayerState,
    SessionInfo,
    SessionPhase,
    SessionType,
    SimId,
    TelemetryFrame,
    TruckState,
    VehicleClass,
)
from .base import Decoder, RawPacket, TransportAdapter
from .shm import MapSpec, ShmSource, cstr

if TYPE_CHECKING:
    from ..config import Config

log = logging.getLogger(__name__)

MAP_NAME = "Local\\SCSTelemetry"
MAP_SIZE = 32 * 1024
LAYOUT_SIZE = 21600
PLUGIN_REVISION = 12

CAPS = frozenset({Capability.FUEL, Capability.DAMAGE, Capability.INPUTS, Capability.SPEED_LIMIT,
                  Capability.TRUCK_JOB, Capability.TRUCK_FATIGUE, Capability.TRUCK_NAVIGATION,
                  Capability.TRACK_POSITION_XY})

# Toggle-Flags (Offset → Event)
TOGGLES = {4302: GameEventKind.JOB_CANCELLED, 4303: GameEventKind.JOB_DELIVERED, 4304: GameEventKind.FINE,
           4305: GameEventKind.TOLLGATE, 4306: GameEventKind.FERRY}


def _u32(b: bytes, o: int) -> int:
    return int(struct.unpack_from("<I", b, o)[0])


def _i32(b: bytes, o: int) -> int:
    return int(struct.unpack_from("<i", b, o)[0])


def _f(b: bytes, o: int) -> float:
    return float(struct.unpack_from("<f", b, o)[0])


def _i64(b: bytes, o: int) -> int:
    return int(struct.unpack_from("<q", b, o)[0])


class ETS2Decoder(Decoder):
    sim = SimId.ETS2

    def __init__(self) -> None:
        super().__init__()
        self.prev_toggles: dict[int, bool] | None = None
        self.prev_refuel = False
        self.prev_time = -1
        self.rev_warned = False
        self.job_n = 0
        self.prev_job_key: tuple[str, str, int] | None = None

    def feed(self, packet: RawPacket) -> TelemetryFrame | None:
        b = packet.data
        if len(b) < LAYOUT_SIZE or not b[0]:
            return None
        rev = _u32(b, 40)
        if rev != PLUGIN_REVISION:
            if not self.rev_warned:
                self.rev_warned = True
                log.warning("SCS-Telemetry-Plugin Rev %d wird nicht unterstützt (erwartet %d) – bitte "
                            "truckermudgeon/scs-sdk-plugin v1.12.x verwenden", rev, PLUGIN_REVISION)
            return None
        game = _u32(b, 52)
        sim = SimId.ATS if game == 2 else SimId.ETS2
        self.sim = sim
        paused = bool(b[4])
        t_us = int(struct.unpack_from("<Q", b, 8)[0])
        if t_us == self.prev_time and not paused:
            return None  # unverändert
        self.prev_time = t_us
        events = self._events(b)

        time_abs = _u32(b, 64)
        delivery_abs = _u32(b, 88)
        planned_km = _u32(b, 100)
        rest_stop = _i32(b, 500)
        gear = _i32(b, 508)
        scale = _f(b, 700)
        fuel_cap = _f(b, 704)
        rpm_max = _f(b, 740)
        cargo_mass = _f(b, 748)
        speed, rpm = _f(b, 948), _f(b, 952)
        steer, throttle, brake, clutch = (_f(b, 956 + 4 * i) for i in range(4))
        cruise = _f(b, 988)
        fuel, fuel_avg, fuel_range = _f(b, 1000), _f(b, 1004), _f(b, 1008)
        wear = [_f(b, 1036 + 4 * i) for i in range(5)]  # engine, transmission, cabin, chassis, wheels
        odo, nav_dist, nav_time, limit = (_f(b, 1056 + 4 * i) for i in range(4))
        cargo_damage = _f(b, 1468)
        on_job = bool(b[4300])
        cargo = cstr(b[2620:2684])
        dest_city, dest_company = cstr(b[2748:2812]), cstr(b[2876:2940])
        src_city = cstr(b[3004:3068])
        income = int(struct.unpack_from("<Q", b, 4000)[0])
        trailer_attached = bool(b[6080])
        trailer_wear = [_f(b, 6152 + 4 * i) for i in range(4)]  # cargo, chassis, wheels, body

        job_key = (cargo, dest_city, delivery_abs)
        if on_job and job_key != self.prev_job_key and cargo:
            self.job_n += 1
        self.prev_job_key = job_key if on_job else None

        truck = TruckState(
            speed_limit_kmh=limit * 3.6 if limit > 0.5 else None, cruise_control_kmh=cruise * 3.6 if cruise > 0 else None,
            odometer_km=odo, fuel_range_km=fuel_range if fuel_range > 0 else None,
            fuel_avg_consumption_l_per_km=fuel_avg or None, nav_distance_m=nav_dist if nav_dist > 0 else None,
            nav_time_s=nav_time if nav_time > 0 else None, game_time_min=time_abs, job_active=on_job,
            job_cargo=cargo, job_source_city=src_city, job_destination_city=dest_city,
            job_destination_company=dest_company, job_income=income or None,
            job_delivery_time_min=delivery_abs if on_job and delivery_abs else None,
            job_planned_distance_km=float(planned_km) if planned_km else None,
            job_cargo_mass_kg=cargo_mass or None, next_rest_stop_min=rest_stop if rest_stop else None,
            parking_brake=bool(b[1566]), engine_on=bool(b[1576]), lights_on=bool(b[1583] or b[1584]),
            trailer_attached=trailer_attached, fuel_warning=bool(b[1570]),
        )
        damage = Damage(engine=wear[0] * 100, transmission=wear[1] * 100, cabin=wear[2] * 100,
                        chassis=wear[3] * 100, wheels=wear[4] * 100,
                        trailer=max(trailer_wear[1:]) * 100 if trailer_attached else None,
                        cargo=cargo_damage * 100 if on_job else None)
        player = PlayerState(speed_ms=abs(speed), gear=gear, rpm=rpm, max_rpm=rpm_max or None, throttle=throttle,
                             brake=brake, clutch=clutch, steering=max(-1.0, min(1.0, steer)), fuel_l=fuel,
                             fuel_capacity_l=fuel_cap or None, damage=damage, truck=truck)
        x, _y, z = struct.unpack_from("<3d", b, 2200)
        me = CarState(idx=0, driver_name="Du", is_player=True, x=float(x), z=float(z), speed_ms=abs(speed))
        session = SessionInfo(
            sim=sim, track_name="USA" if sim is SimId.ATS else "Europa", track_id=str(sim),
            session_type=SessionType.FREE_DRIVE, phase=SessionPhase.PAUSED if paused else SessionPhase.RUNNING,
            session_uid=f"{sim}-job{self.job_n}", session_time_s=t_us / 1e6, num_cars=1,
            vehicle_class=VehicleClass.TRUCK, car_name=f"{cstr(b[2364:2428])} {cstr(b[2492:2556])}".strip(),
            paused=paused,
        )
        extra: dict[str, Any] = {
            "time_scale": scale or None, "special_job": bool(b[1565]), "cargo_loaded": bool(b[1564]),
            "fine_offence": cstr(b[3436:3468]), "fine_amount": _i64(b, 4216),
            "delivered_revenue": _i64(b, 4208), "cancel_penalty": _i64(b, 4200),
            "toll_amount": _i64(b, 4224), "ferry_amount": _i64(b, 4232), "air_pressure_warning": bool(b[1568]),
            "oil_pressure_warning": bool(b[1572]), "water_temp_warning": bool(b[1573]),
            "refuel_amount_l": _f(b, 1464),
        }
        return TelemetryFrame(seq=self.next_seq(), t=packet.t, sim=sim, capabilities=CAPS, session=session,
                              player=player, player_idx=0, cars=[me], events=events, raw_extra=extra)

    def _events(self, b: bytes) -> list[GameEvent]:
        toggles = {off: bool(b[off]) for off in TOGGLES}
        events: list[GameEvent] = []
        if self.prev_toggles is not None:
            for off, kind in TOGGLES.items():
                if toggles[off] != self.prev_toggles[off]:
                    data: dict[str, Any] = {}
                    if kind is GameEventKind.FINE:
                        data = {"offence": cstr(b[3436:3468]), "amount": _i64(b, 4216)}
                    elif kind is GameEventKind.JOB_DELIVERED:
                        data = {"revenue": _i64(b, 4208), "xp": _i32(b, 640), "cargo_damage": _f(b, 1456),
                                "distance_km": _f(b, 1460)}
                    elif kind is GameEventKind.JOB_CANCELLED:
                        data = {"penalty": _i64(b, 4200)}
                    elif kind is GameEventKind.TOLLGATE:
                        data = {"amount": _i64(b, 4224)}
                    elif kind is GameEventKind.FERRY:
                        data = {"amount": _i64(b, 4232), "from": cstr(b[3468:3532]), "to": cstr(b[3532:3596])}
                    events.append(GameEvent(kind, data=data))
        self.prev_toggles = toggles
        refuel = bool(b[4308])
        if refuel and not self.prev_refuel:
            events.append(GameEvent(GameEventKind.REFUEL))
        self.prev_refuel = refuel
        return events


class ETS2Adapter(TransportAdapter):
    sim_id = SimId.ETS2
    key = "ets2"
    display_name = "Euro Truck Simulator 2 / ATS"
    capabilities = CAPS

    def __init__(self, config: Config):
        sec = config.section("ets2")
        shm_cfg = config.section("shm")
        hz = float(sec.get("poll_hz", 10))
        src = ShmSource([MapSpec(MAP_NAME, "scs", MAP_SIZE, hz)], hz, str(shm_cfg.get("mirror_dir", "/dev/shm")),
                        str(shm_cfg.get("prefix", "pitwall-")))
        super().__init__(config, src, ETS2Decoder())
        self.hint = ("Keine Daten von ETS2/ATS. Ist libscs-telemetry.so (Linux) bzw. die SCS-Telemetry-DLL "
                     "installiert und der SDK-Dialog beim Start bestätigt? Siehe docs/setup-ets2.md.")
