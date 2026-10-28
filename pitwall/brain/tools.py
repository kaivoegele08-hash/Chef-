"""Tools für Claude: liefern ausschließlich exakt berechnete Werte (keine Schätzungen des Modells).

Jedes Tool gibt kompaktes JSON zurück. Fehlt ein Wert, steht dort explizit
``null`` bzw. ein ``"unavailable"``-Grund – das Modell soll dann ehrlich sagen,
dass es keine Daten hat.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from ..core.frame import WHEELS, Capability
from ..strategy.plans import _evaluate, plan_race

if TYPE_CHECKING:
    from ..engineer.engineer import Engineer

ToolFn = Callable[["Engineer", dict[str, Any]], dict[str, Any]]


def _r(v: float | None, n: int = 2) -> float | None:
    return None if v is None else round(float(v), n)


def _no(reason: str) -> dict[str, Any]:
    return {"available": False, "reason": reason}


def get_session_overview(eng: Engineer, _: dict[str, Any]) -> dict[str, Any]:
    f = eng.frame
    if f is None:
        return _no("Keine Telemetrie")
    s = f.session
    me = f.me
    return {
        "sim": str(f.sim),
        "track": s.track_name,
        "session_type": str(s.session_type),
        "phase": str(s.phase),
        "vehicle_class": str(s.vehicle_class),
        "car": s.car_name,
        "lap": me.lap if me else None,
        "total_laps": s.total_laps,
        "time_remaining_s": _r(s.time_remaining_s, 0),
        "position": me.position if me else None,
        "cars": s.num_cars,
        "flag": str(s.flag),
        "safety_car": str(s.safety_car),
        "capabilities": sorted(str(c) for c in f.capabilities),
        "pit_stops_made": eng.state.pit_stops_made if eng.state else 0,
        "start_position": eng.state.start_position if eng.state else None,
    }


def get_gaps(eng: Engineer, _: dict[str, Any]) -> dict[str, Any]:
    f = eng.frame
    if f is None or not f.has(Capability.OPPONENTS):
        return _no("Diese Sim liefert keine Gegnerdaten")
    snap = eng.snapshot
    out: dict[str, Any] = {
        "ahead": snap.ahead.to_dict() if snap.ahead else None,
        "behind": snap.behind.to_dict() if snap.behind else None,
        "note": "gap_s: + = vor mir, - = hinter mir; delta_per_lap_s: + = Abstand wächst",
    }
    near = [g.to_dict() for g in snap.grid if g.gap_s is not None and abs(g.gap_s) < 15]
    out["nearby"] = near[:8]
    return out


def get_fuel_status(eng: Engineer, _: dict[str, Any]) -> dict[str, Any]:
    f = eng.frame
    if f is None or not f.has(Capability.FUEL) or eng.snapshot.fuel is None:
        return _no("Keine Spritdaten")
    return eng.snapshot.fuel.to_dict()


def get_tyre_status(eng: Engineer, _: dict[str, Any]) -> dict[str, Any]:
    f = eng.frame
    if f is None:
        return _no("Keine Telemetrie")
    p = f.player
    snap = eng.snapshot
    wheels = {}
    for i, w in enumerate(p.wheels[:4]):
        wheels[WHEELS[i]] = {"surface_c": _r(w.surface_temp(), 1), "core_c": _r(w.temp_core_c, 1),
                             "pressure_kpa": _r(w.pressure_kpa, 1), "wear_pct": _r(w.wear_pct, 1),
                             "brake_c": _r(w.brake_temp_c, 0)}
    deg = snap.degradation
    return {
        "compound": p.tyre_compound,
        "age_laps": p.tyre_age_laps,
        "wheels": wheels,
        "temp_window_c": snap.temps.window if snap.temps else None,
        "temp_states": snap.temps.states if snap.temps else None,
        "wear_projection": snap.wear.to_dict() if snap.wear else None,
        "degradation_s_per_lap_fuel_corrected": _r(deg.deg_s_per_lap, 3) if deg else None,
        "degradation_raw_s_per_lap": _r(deg.raw_trend_s_per_lap, 3) if deg else None,
        "degradation_laps_used": deg.laps_used if deg else 0,
        "available": {"temps": f.has(Capability.TYRE_TEMPS), "wear": f.has(Capability.TYRE_WEAR),
                      "pressure": f.has(Capability.TYRE_PRESSURE)},
    }


def get_pit_plan(eng: Engineer, _: dict[str, Any]) -> dict[str, Any]:
    snap = eng.snapshot
    if snap.plans is None:
        return _no("Kein Rennen mit Rundenzahl oder noch keine Daten")
    out = snap.plans.to_dict()
    out.update({
        "next_stop_lap": snap.next_stop_lap,
        "window": snap.window,
        "window_open": snap.window_open,
        "box_this_lap": snap.box_this_lap,
        "box_under_sc": snap.box_under_sc,
        "sc_gain_s": _r(snap.sc_gain_s, 1),
        "pit_loss_s": _r(snap.pit_loss_s, 1),
        "pit_loss_now_s": _r(snap.pit_loss_effective_s, 1),
        "rejoin_if_box_now": snap.rejoin.to_dict() if snap.rejoin else None,
        "undercut_threat": snap.undercut_threat.to_dict() if snap.undercut_threat else None,
        "undercut_chance": snap.undercut_chance.to_dict() if snap.undercut_chance else None,
        "rivals_still_to_stop": [_name(eng, i) for i in snap.rivals_to_stop][:10],
        "used_compounds": snap.used_compounds,
        "compound_models": snap.compound_models,
    })
    return out


def compare_strategies(eng: Engineer, args: dict[str, Any]) -> dict[str, Any]:
    """Rennzeit für eine gewünschte Stoppzahl / Mischungsfolge / Stopp-Runde rechnen."""
    f = eng.frame
    snap = eng.snapshot
    if f is None or f.me is None or not f.session.total_laps or snap.plans is None:
        return _no("Kein Rennen mit Rundenzahl")
    from ..strategy.plans import CompoundModel

    models = {k: CompoundModel(k, v["pace"], v["deg"], 25, max_laps=int(v["max_laps"]))
              for k, v in snap.compound_models.items()}
    me = f.me
    compound = f.player.tyre_compound or "medium"
    age = f.player.tyre_age_laps or 0
    loss = snap.pit_loss_effective_s or snap.pit_loss_s or 22.0
    stops = args.get("stops")
    pit_laps = args.get("pit_laps")
    compounds = args.get("compounds")
    result: dict[str, Any] = {"baseline_plan_a": snap.plans.plan_a.to_dict() if snap.plans.plan_a else None}
    if pit_laps and compounds:
        seq = [compound, *compounds]
        t = _evaluate(list(pit_laps), seq, me.lap, f.session.total_laps - me.lap + 1, age, models, loss)
        result["requested"] = {"pit_laps": pit_laps, "compounds": seq,
                               "total_time_s": None if t is None else round(t, 1),
                               "delta_to_plan_a_s": None if t is None or not snap.plans.plan_a
                               else round(t - snap.plans.plan_a.total_time_s, 1)}
    elif stops is not None:
        res = plan_race(me.lap, f.session.total_laps, compound, age, models, loss, max_stops=int(stops),
                        used_compounds=set(snap.used_compounds))
        best = res.by_stops.get(int(stops))
        result["requested"] = best.to_dict() if best else None
        if best and snap.plans.plan_a:
            result["requested"]["delta_to_plan_a_s"] = round(best.total_time_s - snap.plans.plan_a.total_time_s, 1)
    result["note"] = "total_time_s ist relativ (nur Differenzen sind aussagekräftig)"
    return result


def get_rival(eng: Engineer, args: dict[str, Any]) -> dict[str, Any]:
    f = eng.frame
    if f is None or not f.has(Capability.OPPONENTS):
        return _no("Keine Gegnerdaten")
    query = str(args.get("name", "")).lower().strip()
    pos = args.get("position")
    car = None
    for c in f.cars:
        if pos is not None and c.position == int(pos):
            car = c
            break
        if query and query in (c.driver_name or "").lower():
            car = c
            break
    if car is None:
        return _no(f"Kein Fahrer '{query or pos}' gefunden")
    hist = eng.state.opponents.get(car.idx) if eng.state else None
    g = eng.state.gap_to(car.idx) if eng.state else None
    return {
        "name": car.driver_name,
        "team": car.team,
        "position": car.position,
        "gap_to_me_s": _r(g, 2),
        "last_lap_s": _r(car.last_lap_s, 3),
        "best_lap_s": _r(car.best_lap_s, 3),
        "recent_laps_s": [round(x, 3) for x in (hist.recent_laps(5) if hist else [])],
        "compound": car.tyre_compound if f.has(Capability.OPPONENT_TYRES) else None,
        "tyre_age_laps": car.tyre_age_laps if f.has(Capability.OPPONENT_TYRES) else None,
        "pit_stops": car.pit_stops,
        "pit_laps": hist.pit_laps if hist else [],
        "in_pit": car.in_pit,
        "teammate": car.is_teammate,
    }


def get_standings(eng: Engineer, args: dict[str, Any]) -> dict[str, Any]:
    f = eng.frame
    if f is None or not f.has(Capability.OPPONENTS):
        return _no("Keine Gegnerdaten")
    n = int(args.get("top_n", 10))
    rows = []
    for c in f.by_position()[:n]:
        rows.append({"pos": c.position, "name": c.driver_name, "gap_to_me_s": _r(eng.state.gap_to(c.idx), 1)
                     if eng.state and not c.is_player else 0.0, "compound": c.tyre_compound, "age": c.tyre_age_laps,
                     "stops": c.pit_stops, "me": c.is_player})
    return {"standings": rows}


def get_weather(eng: Engineer, _: dict[str, Any]) -> dict[str, Any]:
    f = eng.frame
    if f is None or not f.has(Capability.WEATHER):
        return _no("Keine Wetterdaten")
    w = f.session.weather
    adv = eng.snapshot.weather
    return {
        "condition": w.condition,
        "air_c": _r(w.air_temp_c, 1),
        "track_c": _r(w.track_temp_c, 1),
        "rain_intensity": _r(w.rain_intensity, 2),
        "track_wetness": _r(w.track_wetness, 2),
        "forecast": [{"minutes": x.minutes_ahead, "condition": x.condition, "rain_pct": x.rain_percentage}
                     for x in w.forecast[:8]] if f.has(Capability.WEATHER_FORECAST) else None,
        "tyre_recommendation": adv.to_dict() if adv else None,
    }


def get_last_laps(eng: Engineer, args: dict[str, Any]) -> dict[str, Any]:
    if eng.state is None:
        return _no("Keine Daten")
    n = max(1, min(20, int(args.get("n", 5))))
    laps = [lap.to_dict() for lap in eng.state.laps[-n:]]
    return {"laps": laps, "best_lap_s": _r(eng.state.best_lap_s, 3), "best_sectors": eng.state.best_sectors}


def get_damage(eng: Engineer, _: dict[str, Any]) -> dict[str, Any]:
    f = eng.frame
    if f is None or not f.has(Capability.DAMAGE):
        return _no("Keine Schadensdaten")
    d = f.player.damage
    return {k: _r(getattr(d, k), 0) for k in d.__slots__ if getattr(d, k) is not None}  # type: ignore[attr-defined]


def get_corner_analysis(eng: Engineer, args: dict[str, Any]) -> dict[str, Any]:
    coach = getattr(eng, "coach", None)
    if coach is None:
        return _no("Coaching noch nicht verfügbar (zu wenige Runden)")
    return coach.analysis(limit=int(args.get("limit", 5)))  # type: ignore[no-any-return]


def remember_driver_note(eng: Engineer, args: dict[str, Any]) -> dict[str, Any]:
    text = str(args.get("text", "")).strip()
    if not text:
        return {"ok": False}
    f = eng.frame
    lap = f.me.lap if f and f.me else 0
    eng.memory.note(f.t if f else 0.0, lap, text)
    if eng.long_memory is not None and f is not None:
        eng.long_memory.add_note(str(f.sim), f.session.track_id or f.session.track_name, f.session.car_name, text,
                                 kind=str(args.get("kind", "note")))
    return {"ok": True}


def get_driver_history(eng: Engineer, _: dict[str, Any]) -> dict[str, Any]:
    f = eng.frame
    if eng.long_memory is None or f is None:
        return _no("Kein Langzeitgedächtnis")
    return eng.long_memory.summary(str(f.sim), f.session.track_id or f.session.track_name,  # type: ignore[no-any-return]
                                   f.session.car_name)


def get_truck_job(eng: Engineer, _: dict[str, Any]) -> dict[str, Any]:
    """ETS2/ATS: Auftrag, Zeitplan (Spielzeit), Pausenbedarf, Sprit, Tempolimit, Schäden."""
    f = eng.frame
    tr = f.player.truck if f else None
    if f is None or tr is None:
        return _no("Kein Truck-Spiel aktiv")
    p = f.player
    eta_min = (tr.nav_time_s or 0) / 60.0 if tr.nav_time_s else None
    remaining = (tr.job_delivery_time_min - tr.game_time_min) if (
        tr.job_delivery_time_min is not None and tr.game_time_min is not None) else None
    scale = f.raw_extra.get("time_scale") or None
    d = p.damage
    return {
        "game": str(f.sim), "truck": f.session.car_name, "game_clock_min": tr.game_time_min,
        "job": {"active": tr.job_active, "cargo": tr.job_cargo, "from": tr.job_source_city,
                "to": f"{tr.job_destination_company}, {tr.job_destination_city}".strip(", "),
                "income": tr.job_income, "planned_km": tr.job_planned_distance_km,
                "cargo_mass_kg": _r(tr.job_cargo_mass_kg, 0), "cargo_damage_pct": _r(d.cargo, 1)},
        "schedule_game_minutes": {"deadline_in": remaining, "eta_in": _r(eta_min, 0),
                                  "margin": _r(remaining - eta_min, 0) if remaining is not None and eta_min else None},
        "eta_real_minutes": _r(eta_min / scale, 0) if eta_min and scale else None,
        "nav_distance_km": _r((tr.nav_distance_m or 0) / 1000, 1) if tr.nav_distance_m else None,
        "rest_needed_in_game_min": tr.next_rest_stop_min,
        "fuel": {"litres": _r(p.fuel_l, 0), "capacity_l": _r(p.fuel_capacity_l, 0), "range_km": _r(tr.fuel_range_km, 0),
                 "avg_l_per_100km": _r((tr.fuel_avg_consumption_l_per_km or 0) * 100, 1) or None,
                 "warning": tr.fuel_warning},
        "speed_kmh": _r(p.speed_ms * 3.6, 0), "speed_limit_kmh": _r(tr.speed_limit_kmh, 0),
        "cruise_control_kmh": _r(tr.cruise_control_kmh, 0),
        "wear_pct": {k: _r(getattr(d, k), 1) for k in ("engine", "transmission", "cabin", "chassis", "wheels",
                                                        "trailer")},
        "note": "Deadline/ETA in Spielminuten (Spielzeit läuft komprimiert).",
    }


def _name(eng: Engineer, idx: int) -> str:
    f = eng.frame
    car = f.car(idx) if f else None
    return car.driver_name if car else str(idx)


TOOLS: dict[str, tuple[ToolFn, str, dict[str, Any]]] = {
    "get_session_overview": (get_session_overview, "Sessionüberblick: Sim, Strecke, Typ, Runde, Position, Flaggen, "
                             "verfügbare Datenfelder (capabilities).", {}),
    "get_gaps": (get_gaps, "Abstände zu Vorder-/Hintermann und Autos in der Nähe inkl. Trend pro Runde, DRS-Prognose "
                 "und Reifenoffset.", {}),
    "get_fuel_status": (get_fuel_status, "Sprit: Menge, Verbrauch/Runde, Runden im Tank, Bedarf bis Ziel, "
                        "Überschuss/Defizit, Sparziel, Lift-and-Coast-Empfehlung.", {}),
    "get_tyre_status": (get_tyre_status, "Reifen: Mischung, Alter, Temperaturen/Fenster, Druck, Verschleiß und "
                        "Hochrechnung, sprit-korrigierte Degradation.", {}),
    "get_pit_plan": (get_pit_plan, "Strategie: Plan A/B, Stopp-Runden, Boxenfenster, Boxenverlust, "
                     "Wiedereinstiegsposition, Undercut-Bewertung, SC-Stopp-Gewinn.", {}),
    "compare_strategies": (compare_strategies, "Vergleicht eine Strategie mit Plan A: entweder 'stops' (Anzahl) oder "
                           "'pit_laps' + 'compounds' (Mischungen nach jedem Stopp).",
                           {"stops": {"type": "integer", "minimum": 0, "maximum": 3},
                            "pit_laps": {"type": "array", "items": {"type": "integer"}},
                            "compounds": {"type": "array", "items": {"type": "string"}}}),
    "get_rival": (get_rival, "Daten zu einem Gegner per Name (Teilstring) oder Position.",
                  {"name": {"type": "string"}, "position": {"type": "integer"}}),
    "get_standings": (get_standings, "Aktuelle Reihenfolge (Top N) mit Abstand zu mir, Reifen, Stopps.",
                      {"top_n": {"type": "integer", "minimum": 1, "maximum": 30}}),
    "get_weather": (get_weather, "Wetter, Streckentemperatur, Nässe, Prognose und Reifenempfehlung.", {}),
    "get_last_laps": (get_last_laps, "Meine letzten n Runden (Zeit, Sprit, Reifen, Sektoren, gültig/SC/Box).",
                      {"n": {"type": "integer", "minimum": 1, "maximum": 20}}),
    "get_damage": (get_damage, "Schäden am eigenen Auto in Prozent.", {}),
    "get_corner_analysis": (get_corner_analysis, "Fahrcoaching: Kurven mit Zeitverlust und Ursache (Bremspunkt, "
                            "Minimalspeed, Gas, Blockieren) gegenüber der Bestrunde.",
                            {"limit": {"type": "integer", "minimum": 1, "maximum": 10}}),
    "remember_driver_note": (remember_driver_note, "Merkt sich eine Aussage des Fahrers zum Auto/zur Balance "
                             "(für dieses Rennen und langfristig).",
                             {"text": {"type": "string"}, "kind": {"type": "string",
                                                                   "enum": ["balance", "note", "setup"]}}),
    "get_truck_job": (get_truck_job, "Nur ETS2/ATS: Auftrag (Ladung, Ziel, Lohn), Zeitplan gegen Deadline, "
                      "Pausenbedarf, Sprit/Reichweite, Tempolimit, Verschleiß.", {}),
    "get_driver_history": (get_driver_history, "Langzeitwissen für Sim/Strecke/Auto: typischer Verbrauch, "
                           "Boxenverlust, beste Zeiten, frühere Notizen, Schwachkurven.", {}),
}


def tool_definitions(cache_last: bool = True) -> list[dict[str, Any]]:
    defs: list[dict[str, Any]] = []
    for name in sorted(TOOLS):
        _, desc, props = TOOLS[name]
        schema: dict[str, Any] = {"type": "object", "properties": props, "additionalProperties": False}
        defs.append({"name": name, "description": desc, "input_schema": schema})
    if cache_last and defs:
        defs[-1]["cache_control"] = {"type": "ephemeral"}
    return defs


def run_tool(eng: Engineer, name: str, args: dict[str, Any] | None) -> str:
    entry = TOOLS.get(name)
    if entry is None:
        return json.dumps({"error": f"unknown tool {name}"})
    fn = entry[0]
    try:
        result = fn(eng, args or {})
    except Exception as exc:
        result = {"error": f"{type(exc).__name__}: {exc}"}
    return json.dumps(result, ensure_ascii=False, separators=(",", ":"), default=str)
