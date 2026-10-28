"""Hilfen, um berechnete Werte als Funk-Halbsätze zu beschreiben (Anzeige + Sprache)."""

from __future__ import annotations

from ..strategy.plans import Plan
from .speech import Speaker, Spoken


def join(parts: list[Spoken], sep: str = " ") -> Spoken:
    return Spoken(sep.join(p.display for p in parts if p.display),
                  sep.join(p.spoken for p in parts if p.spoken))


def describe_plan(plan: Plan | None, sp: Speaker) -> Spoken:
    de = sp.lang == "de"
    if plan is None:
        return Spoken("kein Plan" if de else "no plan", "kein Plan" if de else "no plan")
    if plan.stops == 0:
        return Spoken("durchfahren, kein Stopp" if de else "no stop, run to the end",
                      "durchfahren, kein Stopp" if de else "no stop, run to the end")
    count_disp = {1: "ein Stopp", 2: "zwei Stopps", 3: "drei Stopps"} if de else \
        {1: "one stop", 2: "two stops", 3: "three stops"}
    head = count_disp.get(plan.stops, f"{plan.stops} Stopps" if de else f"{plan.stops} stops")
    disp_parts: list[str] = []
    spoken_parts: list[str] = []
    for lap, comp in zip(plan.pit_laps, plan.compounds[1:], strict=False):
        c = sp.compound(comp)
        lap_s = sp.int_(lap)
        if de:
            disp_parts.append(f"Runde {lap_s.display} auf {c.display}")
            spoken_parts.append(f"Runde {lap_s.spoken} auf {c.spoken}")
        else:
            disp_parts.append(f"lap {lap_s.display} onto {c.display}")
            spoken_parts.append(f"lap {lap_s.spoken} onto {c.spoken}")
    conj = " und " if de else " and "
    return Spoken(head + ", " + conj.join(disp_parts), head + ", " + conj.join(spoken_parts))


DAMAGE_NAMES_DE = {
    "front_wing_left": "Frontflügel links", "front_wing_right": "Frontflügel rechts", "rear_wing": "Heckflügel",
    "floor": "Unterboden", "diffuser": "Diffusor", "sidepod": "Seitenkasten", "aero": "Aerodynamik",
    "body": "Karosserie", "suspension": "Aufhängung", "engine": "Motor", "gearbox": "Getriebe",
    "transmission": "Antrieb", "cabin": "Kabine", "chassis": "Chassis", "wheels": "Rädern",
    "trailer": "Anhänger", "cargo": "Fracht",
}
DAMAGE_NAMES_EN = {
    "front_wing_left": "front wing left", "front_wing_right": "front wing right", "rear_wing": "rear wing",
    "floor": "floor", "diffuser": "diffuser", "sidepod": "sidepod", "aero": "aero", "body": "bodywork",
    "suspension": "suspension", "engine": "engine", "gearbox": "gearbox", "transmission": "transmission",
    "cabin": "cabin", "chassis": "chassis", "wheels": "wheels", "trailer": "trailer", "cargo": "cargo",
}


def damage_part(name: str, sp: Speaker) -> Spoken:
    table = DAMAGE_NAMES_DE if sp.lang == "de" else DAMAGE_NAMES_EN
    val = table.get(name, name)
    return Spoken(val, val)


def delta_text(delta: float | None, sp: Speaker) -> Spoken:
    """Delta zur Bestzeit als Halbsatz."""
    de = sp.lang == "de"
    if delta is None:
        return Spoken("", "")
    d = sp.decimal(abs(delta), 1 if abs(delta) >= 1 else 2)
    if abs(delta) < 0.005:
        return Spoken("Genau deine Bestzeit." if de else "Matching your best.",
                      "Genau deine Bestzeit." if de else "Matching your best.")
    if delta < 0:
        return Spoken(f"{d.display} schneller als deine Beste." if de else f"{d.display} faster than your best.",
                      f"{d.spoken} schneller als deine Beste." if de else f"{d.spoken} faster than your best.")
    return Spoken(f"{d.display} hinter deiner Besten." if de else f"{d.display} off your best.",
                  f"{d.spoken} hinter deiner Besten." if de else f"{d.spoken} off your best.")


def condition_name(cond: str, sp: Speaker) -> Spoken:
    de = {"clear": "Trocken, klar", "light_cloud": "Leicht bewölkt", "overcast": "Bewölkt",
          "light_rain": "Leichter Regen", "heavy_rain": "Starker Regen", "storm": "Gewitter", "unknown": "Wetter unbekannt"}
    en = {"clear": "Dry and clear", "light_cloud": "Light cloud", "overcast": "Overcast", "light_rain": "Light rain",
          "heavy_rain": "Heavy rain", "storm": "Storm", "unknown": "Weather unknown"}
    val = (de if sp.lang == "de" else en).get(cond, cond)
    return Spoken(val, val)
