"""Baut kompakte JSON-Schnappschüsse für Dashboard und Overlay."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..core.frame import WHEELS, Capability, CarStatus, TelemetryFrame

if TYPE_CHECKING:
    from ..core.state import RaceState
    from ..core.track import TrackModel
    from ..strategy.engine import StrategySnapshot


def _r(v: float | None, n: int = 1) -> float | None:
    return None if v is None else round(float(v), n)


def timing_rows(frame: TelemetryFrame, state: RaceState) -> list[dict[str, Any]]:
    me = frame.me
    rows: list[dict[str, Any]] = []
    order = frame.by_position()
    leader = order[0] if order else None
    gap_leader_me = state.gap_to(leader.idx) if (leader and me and leader.idx != me.idx) else 0.0
    prev_gap_leader: float | None = 0.0
    for car in order:
        if car.status is CarStatus.INACTIVE:
            continue
        gap_leader: float | None
        if car.gap_to_leader_s is not None and frame.has(Capability.NATIVE_GAPS):
            gap_leader = car.gap_to_leader_s
        elif me is not None and car.idx == me.idx:
            gap_leader = gap_leader_me
        else:
            g = state.gap_to(car.idx)
            gap_leader = None if (g is None or gap_leader_me is None) else gap_leader_me - g
        interval = None
        if gap_leader is not None and prev_gap_leader is not None and car is not leader:
            interval = gap_leader - prev_gap_leader
        if car.gap_to_ahead_s is not None and frame.has(Capability.NATIVE_GAPS):
            interval = car.gap_to_ahead_s
        prev_gap_leader = gap_leader
        rows.append({
            "idx": car.idx,
            "pos": car.position,
            "name": car.driver_name or f"#{car.car_number or car.idx}",
            "team": car.team,
            "color": car.team_color,
            "num": car.car_number,
            "cls": car.car_class,
            "lap": car.lap,
            "gap": _r(gap_leader, 3) if car is not leader else 0.0,
            "int": _r(interval, 3),
            "last": _r(car.last_lap_s, 3),
            "best": _r(car.best_lap_s, 3),
            "tyre": car.tyre_compound,
            "age": car.tyre_age_laps,
            "stops": car.pit_stops,
            "pit": car.in_pit,
            "status": str(car.status),
            "me": car.is_player,
            "mate": car.is_teammate,
            "pen": _r(car.penalties_s, 0) if car.penalties_s else None,
        })
    return rows


def player_block(frame: TelemetryFrame) -> dict[str, Any]:
    p = frame.player
    me = frame.me
    wheels = []
    for i, w in enumerate(p.wheels[:4]):
        wheels.append({
            "pos": WHEELS[i],
            "t": _r(w.surface_temp()),
            "ti": _r(w.temp_inner_c),
            "tm": _r(w.temp_middle_c),
            "to": _r(w.temp_outer_c),
            "core": _r(w.temp_core_c),
            "p": _r(w.pressure_kpa),
            "wear": _r(w.wear_pct),
            "brake": _r(w.brake_temp_c, 0),
            "dmg": _r(w.damage_pct),
            "puncture": w.puncture,
        })
    dmg = {k: _r(getattr(p.damage, k), 0) for k in p.damage.__slots__ if getattr(p.damage, k) is not None}
    out: dict[str, Any] = {
        "speed": _r(p.speed_ms * 3.6, 0),
        "gear": p.gear,
        "rpm": _r(p.rpm, 0),
        "max_rpm": _r(p.max_rpm, 0),
        "throttle": _r(p.throttle, 2),
        "brake": _r(p.brake, 2),
        "clutch": _r(p.clutch, 2),
        "steer": _r(p.steering, 2),
        "fuel": _r(p.fuel_l, 2),
        "fuel_cap": _r(p.fuel_capacity_l, 1),
        "tyre": p.tyre_compound,
        "tyre_age": p.tyre_age_laps,
        "wheels": wheels,
        "damage": dmg,
        "ers": _r(p.ers_store_pct, 0),
        "ers_mode": p.ers_mode,
        "drs": p.drs_active,
        "drs_avail": p.drs_available,
        "limiter": p.pit_limiter,
        "incidents": p.incidents,
        "warnings": p.warnings,
        "lap_invalid": p.lap_invalid,
    }
    if me is not None:
        out.update({
            "pos": me.position,
            "lap": me.lap,
            "lap_pct": _r(me.lap_pct, 4),
            "dist": _r(me.lap_distance_m, 0),
            "cur": _r(me.current_lap_s, 3),
            "last": _r(me.last_lap_s, 3),
            "best": _r(me.best_lap_s, 3),
            "sector": me.sector,
            "sectors": [_r(x, 3) for x in me.sector_times_s],
            "pit_state": str(me.pit_state),
        })
    if p.truck is not None:
        t = p.truck
        out["truck"] = {k: getattr(t, k) for k in t.__slots__}
    return out


def session_block(frame: TelemetryFrame) -> dict[str, Any]:
    s = frame.session
    w = s.weather
    return {
        "sim": str(frame.sim),
        "track": s.track_name,
        "track_len": _r(s.track_length_m, 0),
        "type": str(s.session_type),
        "phase": str(s.phase),
        "total_laps": s.total_laps,
        "time_left": _r(s.time_remaining_s, 0),
        "session_time": _r(s.session_time_s, 1),
        "cars": s.num_cars,
        "class": str(s.vehicle_class),
        "car": s.car_name,
        "flag": str(s.flag),
        "sc": str(s.safety_car),
        "yellow_sectors": s.yellow_sectors,
        "weather": {
            "cond": w.condition,
            "air": _r(w.air_temp_c),
            "track": _r(w.track_temp_c),
            "rain": _r(w.rain_intensity, 2),
            "wet": _r(w.track_wetness, 2),
            "forecast": [{"min": f.minutes_ahead, "cond": f.condition, "rain": f.rain_percentage}
                         for f in w.forecast[:6]],
        },
        "lights": s.start_lights,
    }


def map_block(frame: TelemetryFrame, track: TrackModel | None) -> dict[str, Any]:
    cars = []
    for c in frame.cars:
        if c.x is None or c.z is None or c.status is CarStatus.INACTIVE:
            continue
        cars.append({"idx": c.idx, "x": round(c.x, 1), "z": round(c.z, 1), "pos": c.position, "me": c.is_player,
                     "color": c.team_color, "pit": c.in_pit})
    # Fallback ohne XY: Position auf einem Kreis aus lap_pct
    if not cars and frame.has(Capability.LAP_DISTANCE):
        import math

        for c in frame.cars:
            if c.lap_pct is None:
                continue
            a = c.lap_pct * 2 * math.pi
            cars.append({"idx": c.idx, "x": round(math.cos(a) * 100, 1), "z": round(math.sin(a) * 100, 1),
                         "pos": c.position, "me": c.is_player, "color": c.team_color, "pit": c.in_pit, "ring": True})
    return {"cars": cars}


def build_snapshot(frame: TelemetryFrame | None, state: RaceState, strategy: StrategySnapshot | None,
                   track: TrackModel | None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    if frame is None:
        snap: dict[str, Any] = {"type": "snapshot", "live": False}
        if extra:
            snap.update(extra)
        return snap
    snap = {
        "type": "snapshot",
        "live": True,
        "t": round(frame.t, 2),
        "caps": sorted(str(c) for c in frame.capabilities),
        "session": session_block(frame),
        "player": player_block(frame),
        "timing": timing_rows(frame, state),
        "map": map_block(frame, track),
        "strategy": strategy.to_dict() if strategy else None,
        "history": state.snapshot(),
    }
    if extra:
        snap.update(extra)
    return snap
