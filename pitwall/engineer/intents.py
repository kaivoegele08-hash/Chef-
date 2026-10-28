"""Instant Intents: häufige Fragen ohne LLM direkt beantworten (< 300 ms).

Ablauf: Transkript normalisieren → Intent per Schlüsselwort-Mustern erkennen →
Antwort aus den exakt berechneten Werten + Phrasebook. Alles, was hier nicht
eindeutig passt, geht an das LLM (falls verfügbar).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..core.frame import Capability
from .describe import condition_name, damage_part, delta_text, describe_plan
from .speech import Spoken

if TYPE_CHECKING:
    from .engineer import Engineer


def normalize(text: str) -> str:
    t = text.lower().strip()
    t = unicodedata.normalize("NFC", t)
    t = re.sub(r"[^\wäöüß ]+", " ", t)
    return " ".join(t.split())


# (Intent, Muster). Reihenfolge = Priorität. Muster sind Regex auf normalisiertem Text.
INTENT_PATTERNS: list[tuple[str, list[str]]] = [
    ("radio_silent", [r"\bfunk (bitte )?(aus|stumm)\b", r"\bkeine (durchsagen|infos)\b", r"\bradio (off|silence)\b",
                      r"\bstumm\b", r"\bsilence\b"]),
    ("radio_quiet", [r"\bfunk (bitte )?ruhig", r"\bruhe bitte\b", r"\bweniger funk\b", r"\bnur (das )?wichtige",
                     r"\bquiet\b", r"\bless (radio|talk)\b", r"\bonly important\b"]),
    ("radio_chatty", [r"\bmehr infos?\b", r"\bgesprächig", r"\bmore (info|updates)\b", r"\bchatty\b"]),
    ("radio_normal", [r"\bfunk (wieder )?normal\b", r"\bnormal(er)? funk\b", r"\bradio normal\b"]),
    ("mic_check", [r"\bfunk ?check\b", r"\bradio check\b", r"\bhörst du mich\b", r"\bcan you hear me\b"]),
    ("box_request", [r"\bich komme? (jetzt )?rein\b", r"\bkomme (in die )?box\b", r"\bbox(en)? (diese|die) runde\b",
                     r"\bich box\w*\b", r"\bbox box\b", r"\bboxing\b", r"\bcoming in\b", r"\bbox this lap\b"]),
    ("rejoin", [r"\bwo komme? ich raus\b", r"\brauskommen\b", r"\bnach dem stopp\b", r"\brejoin\b",
                r"\bwhere (would|will) i come out\b"]),
    ("gap_ahead", [r"\bnach vorne?\b", r"\bvordermann\b", r"\bvor mir\b", r"\bauto vor\b", r"\bgap ahead\b",
                   r"\bcar (in front|ahead)\b", r"\bto the car ahead\b", r"\babstand vorne\b"]),
    ("gap_behind", [r"\bnach hinten\b", r"\bhintermann\b", r"\bhinter mir\b", r"\bgap behind\b",
                    r"\bcar behind\b", r"\babstand hinten\b"]),
    ("fuel", [r"\bsprit\b", r"\bbenzin\b", r"\btank\w*\b", r"\bfuel\b", r"\bverbrauch\b"]),
    ("tyres", [r"\breifen\b", r"\btyres?\b", r"\btires?\b", r"\bgummis?\b"]),
    ("pit_plan", [r"\bboxenstopp\b", r"\bwann (soll ich )?(an die |in die )?box\b", r"\bstrategie\b",
                  r"\bplan\b", r"\bboxenfenster\b", r"\bpit (window|stop|plan)\b", r"\bwhen .*pit\b",
                  r"\bstrategy\b", r"\bstopp\b"]),
    ("laps_left", [r"\bwie ?viele runden\b", r"\brunden (noch|übrig)\b", r"\bnoch runden\b",
                   r"\blaps (left|remaining|to go)\b", r"\bhow many laps\b"]),
    ("time_left", [r"\bwie lange noch\b", r"\bzeit (noch|übrig)\b", r"\brestzeit\b", r"\btime (left|remaining)\b"]),
    ("position", [r"\bwelche position\b", r"\bwelcher platz\b", r"\bauf welchem platz\b", r"\bposition\b",
                  r"\bwhat position\b", r"\bwhere am i\b"]),
    ("last_lap", [r"\bletzte runden ?zeit\b", r"\bwie war (die|meine) runde\b", r"\brundenzeit\b",
                  r"\blast lap( time)?\b", r"\blap time\b"]),
    ("best_lap", [r"\bbeste runde\b", r"\bbestzeit\b", r"\bbest lap\b", r"\bfastest lap\b"]),
    ("weather", [r"\bwetter\b", r"\bregen\b", r"\bregnet\b", r"\bweather\b", r"\brain\b"]),
    ("damage", [r"\bschaden\b", r"\bbeschädigt\b", r"\bflügel\b", r"\bdamage\w*\b", r"\bfront wing\b"]),
    ("coaching", [r"\bwo verliere ich\b", r"\bwo (kann|könnte) ich\b.*\b(zeit|schneller)\b", r"\bcoaching\b",
                  r"\bwhere am i losing\b", r"\bwhere can i (gain|find)\b"]),
]

BALANCE_WORDS = [r"untersteuer", r"übersteuer", r"balance", r"\bheck\b", r"\bfront\b", r"understeer", r"oversteer",
                 r"\bneutral\b", r"\bgut\b", r"\bloose\b", r"\btight\b", r"\bunruhig\b", r"\brutscht\b", r"\bgrip\b"]


@dataclass
class Intent:
    name: str
    text: str
    args: dict[str, Any] = field(default_factory=dict)


def classify(text: str, driver_names: list[str] | None = None, awaiting_balance: bool = False) -> Intent | None:
    norm = normalize(text)
    if not norm:
        return None
    if awaiting_balance and any(re.search(p, norm) for p in BALANCE_WORDS):
        return Intent("balance_note", text)
    # Rivale beim Namen genannt?
    for name in driver_names or []:
        last = normalize(name).split()[-1] if name else ""
        if len(last) >= 3 and re.search(rf"\b{re.escape(last)}\b", norm):
            return Intent("rival", text, {"name": name})
    for intent, patterns in INTENT_PATTERNS:
        for p in patterns:
            if re.search(p, norm):
                return Intent(intent, text)
    return None


def answer(intent: Intent, eng: Engineer) -> tuple[str, dict[str, Any]] | tuple[None, dict[str, Any]] | None:
    """(Phrasebook-Schlüssel, Werte) oder None = kann nicht beantworten (→ LLM)."""
    f = eng.frame
    st = eng.state
    sp = eng.speaker
    snap = eng.snapshot
    name = intent.name
    de = sp.lang == "de"

    if name.startswith("radio_"):
        mode = {"radio_silent": "silent", "radio_quiet": "important", "radio_chatty": "chatty",
                "radio_normal": "normal"}[name]
        eng.director.set_mode(mode)
        label = {"silent": ("Funk aus, nur noch Kritisches", "Radio off, critical only"),
                 "important": ("Nur noch Wichtiges", "Only important calls"),
                 "chatty": ("Mehr Infos ab jetzt", "More updates from now"),
                 "normal": ("Funk normal", "Normal radio")}[mode]
        return "answer.radio_mode", {"mode": sp.text(label[0] if de else label[1])}
    if name == "mic_check":
        return "system.mic_check", {}
    if f is None or st is None:
        return "answer.no_data", {}
    me = f.me
    if name == "balance_note":
        lap = me.lap if me else 0
        eng.memory.note(f.t, lap, intent.text)
        eng.memory.awaiting_balance = False
        if eng.long_memory is not None:
            eng.long_memory.add_note(str(f.sim), f.session.track_id or f.session.track_name, f.session.car_name,
                                     intent.text, kind="balance")
        return "balance.ack", {"note": sp.text("Wir schauen uns das an." if de else "We'll look at it.")}
    if me is None:
        return "answer.no_data", {}

    if name in ("gap_ahead", "gap_behind"):
        if not f.has(Capability.OPPONENTS):
            return "answer.no_data", {}
        info = snap.ahead if name == "gap_ahead" else snap.behind
        if info is None or info.gap_s is None:
            car = st.car_ahead() if name == "gap_ahead" else st.car_behind()
            if car is None:
                return ("answer.gap_none_ahead" if name == "gap_ahead" else "answer.gap_none_behind"), {}
            return "answer.no_data", {}
        trend = Spoken("", "")
        if info.delta_per_lap_s is not None and abs(info.delta_per_lap_s) >= 0.05:
            d = sp.seconds(abs(info.delta_per_lap_s))
            if info.delta_per_lap_s < 0:
                trend = Spoken(f"{'Abstand schrumpft' if de else 'Closing'} {d.display} {'pro Runde' if de else 'a lap'}.",
                               f"{'Abstand schrumpft' if de else 'Closing'} {d.spoken} {'pro Runde' if de else 'a lap'}.")
            else:
                trend = Spoken(f"{'Abstand wächst' if de else 'Growing'} {d.display} {'pro Runde' if de else 'a lap'}.",
                               f"{'Abstand wächst' if de else 'Growing'} {d.spoken} {'pro Runde' if de else 'a lap'}.")
        key = "answer.gap_ahead" if name == "gap_ahead" else "answer.gap_behind"
        return key, {"gap": sp.seconds(abs(info.gap_s)), "name": sp.name(info.name), "trend": trend}

    if name == "position":
        if me.position is None:
            return "answer.no_data", {}
        return "answer.position", {"pos": sp.position(me.position)}

    if name == "laps_left":
        total = f.session.total_laps
        if total:
            return "answer.laps_left", {"laps": sp.n_laps(max(0, total - me.lap + 1))}
        if f.session.time_remaining_s is not None:
            return "answer.time_left", {"minutes": sp.minutes(f.session.time_remaining_s / 60)}
        return "answer.no_data", {}

    if name == "time_left":
        if f.session.time_remaining_s is None:
            if f.session.total_laps:
                return "answer.laps_left", {"laps": sp.n_laps(max(0, f.session.total_laps - me.lap + 1))}
            return "answer.no_data", {}
        return "answer.time_left", {"minutes": sp.minutes(f.session.time_remaining_s / 60)}

    if name == "fuel":
        fs = snap.fuel
        if not f.has(Capability.FUEL) or fs is None or fs.fuel_l is None:
            return "answer.no_data", {}
        if fs.laps_in_tank is None:
            return "answer.fuel_simple", {"litres": sp.fuel(fs.fuel_l, f.player.fuel_unit)}
        if fs.surplus_laps is None:
            surplus = Spoken("Bedarf noch unbekannt" if de else "target unknown",
                             "Bedarf noch unbekannt" if de else "target unknown")
        elif fs.surplus_laps >= 0:
            v = sp.laps(fs.surplus_laps, 1)
            surplus = Spoken(f"{v.display} {'Runden Reserve' if de else 'laps margin'}",
                             f"{v.spoken} {'Runden Reserve' if de else 'laps margin'}")
        else:
            v = sp.laps(-fs.surplus_laps, 1)
            t = sp.fuel(fs.target_per_lap_l or 0, f.player.fuel_unit, 2)
            surplus = Spoken(f"{v.display} {'Runden zu wenig, Ziel' if de else 'laps short, target'} {t.display}",
                             f"{v.spoken} {'Runden zu wenig, Ziel' if de else 'laps short, target'} {t.spoken}")
        return "answer.fuel", {"laps": _laps_float(sp, fs.laps_in_tank), "surplus_text": surplus}

    if name == "tyres":
        p = f.player
        if not (f.has(Capability.TYRE_COMPOUND) or f.has(Capability.TYRE_TEMPS) or f.has(Capability.TYRE_WEAR)):
            return "answer.no_data", {}
        age = p.tyre_age_laps if p.tyre_age_laps is not None else me.tyre_age_laps
        wear_text = Spoken("", "")
        if snap.wear and snap.wear.limiting_wheel and snap.wear.laps_to_limit is not None:
            idx = ["FL", "FR", "RL", "RR"].index(snap.wear.limiting_wheel)
            w = sp.percent(snap.wear.wear_now[idx] or 0)
            wh = sp.wheel(snap.wear.limiting_wheel)
            n = sp.n_laps(max(1, int(snap.wear.laps_to_limit)))
            wear_text = Spoken(f"{wh.display} {w.display}, {'noch etwa' if de else 'about'} {n.display}. ",
                               f"{wh.spoken} {w.spoken}, {'noch etwa' if de else 'about'} {n.spoken}. ")
        temp_text = Spoken("", "")
        if snap.temps and snap.temps.window:
            states = snap.temps.states
            if all(s == "ok" for s in states):
                temp_text = Spoken("Temperaturen im Fenster." if de else "Temps in the window.",
                                   "Temperaturen im Fenster." if de else "Temps in the window.")
            elif snap.temps.worst_wheel and snap.temps.worst_delta:
                wh = sp.wheel(snap.temps.worst_wheel)
                d = sp.temp(abs(snap.temps.worst_delta))
                word = ("zu heiß" if snap.temps.worst_delta > 0 else "zu kalt") if de else \
                    ("too hot" if snap.temps.worst_delta > 0 else "too cold")
                temp_text = Spoken(f"{wh.display} {d.display} {word}.", f"{wh.spoken} {d.spoken} {word}.")
        return "answer.tyres", {"compound": sp.compound(p.tyre_compound or me.tyre_compound),
                                "age": sp.n_laps(age or 0), "wear_text": wear_text, "temp_text": temp_text}

    if name == "pit_plan":
        plans = snap.plans
        if plans is None or plans.plan_a is None:
            if not f.session.session_type.is_race:
                return None  # außerhalb des Rennens → LLM/allgemein
            return "answer.no_data", {}
        a = plans.plan_a
        if a.stops == 0:
            return "answer.pit_none", {}
        window = Spoken("", "")
        if snap.window:
            lo, hi = sp.int_(snap.window[0]), sp.int_(snap.window[1])
            window = Spoken(f"{'Fenster Runde' if de else 'Window laps'} {lo.display}–{hi.display}.",
                            f"{'Fenster Runde' if de else 'Window laps'} {lo.spoken} {'bis' if de else 'to'} {hi.spoken}.")
        return "answer.pit_plan", {"plan_a": describe_plan(a, sp), "window_text": window}

    if name == "rejoin":
        r = snap.rejoin
        if r is None or r.position is None:
            return "answer.no_data", {}
        traffic = Spoken("", "")
        if r.ahead is not None and r.ahead.gap_after_s < 1.5:
            n = sp.name(r.ahead.name)
            traffic = Spoken(f", {'direkt hinter' if de else 'right behind'} {n.display}",
                             f", {'direkt hinter' if de else 'right behind'} {n.spoken}")
        return "answer.rejoin", {"pos": sp.position(r.position), "traffic": traffic}

    if name == "box_request":
        eng.flags["driver_box_request_lap"] = me.lap
        compound = None
        if snap.plans and snap.plans.plan_a and len(snap.plans.plan_a.compounds) > 1:
            compound = snap.plans.plan_a.compounds[1]
        if snap.weather and snap.weather.recommendation in ("inters", "wets"):
            compound = "inter" if snap.weather.recommendation == "inters" else "wet"
        eng.flags["box_compound"] = compound
        if compound:
            return "answer.box_ack", {"compound": sp.compound(compound)}
        return "answer.box_sim_none", {}

    if name == "last_lap":
        if me.last_lap_s is None:
            return "answer.no_data", {}
        best = st.best_lap_s
        delta = None if best is None else me.last_lap_s - best
        return "answer.last_lap", {"time": sp.laptime(me.last_lap_s, 3 if f.session.session_type.is_qualifying else 1),
                                   "delta_text": delta_text(delta, sp)}

    if name == "best_lap":
        best = st.best_lap_s or me.best_lap_s
        if best is None:
            return "answer.no_data", {}
        return "answer.best_lap", {"time": sp.laptime(best, 3)}

    if name == "weather":
        if not f.has(Capability.WEATHER):
            return "answer.no_data", {}
        w = f.session.weather
        forecast = Spoken("", "")
        adv = snap.weather
        if adv and adv.rain_in_min is not None:
            m = sp.minutes(adv.rain_in_min)
            forecast = Spoken(f"{'Regen in etwa' if de else 'Rain in about'} {m.display}.",
                              f"{'Regen in etwa' if de else 'Rain in about'} {m.spoken}.")
        elif f.has(Capability.WEATHER_FORECAST):
            forecast = Spoken("Kein Regen erwartet." if de else "No rain expected.",
                              "Kein Regen erwartet." if de else "No rain expected.")
        unknown = Spoken("?", "unbekannt" if de else "unknown")
        return "answer.weather", {"condition": condition_name(w.condition, sp),
                                  "track_temp": sp.temp(w.track_temp_c) if w.track_temp_c is not None else unknown,
                                  "air_temp": sp.temp(w.air_temp_c) if w.air_temp_c is not None else unknown,
                                  "forecast": forecast}

    if name == "damage":
        if not f.has(Capability.DAMAGE):
            return "answer.no_data", {}
        dmg = f.player.damage
        parts = []
        for fld in dmg.__slots__:  # type: ignore[attr-defined]
            val = getattr(dmg, fld)
            if val is not None and val >= 5 and fld not in ("engine", "gearbox"):
                parts.append((fld, val))
        if not parts:
            return "answer.damage_none", {}
        parts.sort(key=lambda x: -x[1])
        disp = []
        spoken = []
        for fld, val in parts[:3]:
            pn = damage_part(fld, sp)
            pc = sp.percent(val)
            disp.append(f"{pn.display} {pc.display}")
            spoken.append(f"{pn.spoken} {pc.spoken}")
        prefix = "Schaden: " if de else "Damage: "
        return "answer.damage", {"damage_text": Spoken(prefix + ", ".join(disp) + ".", prefix + ", ".join(spoken) + ".")}

    if name == "rival":
        target = intent.args.get("name")
        car = next((c for c in f.cars if c.driver_name == target), None)
        if car is None:
            return "answer.no_data", {}
        g = st.gap_to(car.idx)
        gap_sp = Spoken("?", "unbekannt" if de else "unknown")
        if g is not None:
            gs = sp.seconds(abs(g))
            word = ("vor dir" if g > 0 else "hinter dir") if de else ("ahead" if g > 0 else "behind")
            gap_sp = Spoken(f"{gs.display} {word}", f"{gs.spoken} {word}")
        tyre = Spoken("", "")
        if car.tyre_compound and f.has(Capability.OPPONENT_TYRES):
            c = sp.compound(car.tyre_compound)
            age = sp.n_laps(car.tyre_age_laps or 0)
            word = "auf" if de else "on"
            alt = "alt" if de else "old"
            tyre = Spoken(f"{word} {c.display}, {age.display} {alt}.", f"{word} {c.spoken}, {age.spoken} {alt}.")
            if car.pit_stops == 0 and de:
                tyre = Spoken(tyre.display + " Noch kein Stopp.", tyre.spoken + " Noch kein Stopp.")
            elif car.pit_stops == 0:
                tyre = Spoken(tyre.display + " Not stopped yet.", tyre.spoken + " Not stopped yet.")
        return "answer.rival", {"name": sp.name(car.driver_name), "pos": sp.position(car.position), "gap": gap_sp,
                                "tyre_text": tyre}

    if name == "coaching":
        coach = getattr(eng, "coach", None)
        if coach is None:
            return None
        text = coach.summary_text(sp)
        if text is None:
            return None
        return "answer.corner", {"text": text}
    return None


def _laps_float(sp: Any, value: float) -> Spoken:
    if value >= 10:
        return sp.n_laps(int(value))  # type: ignore[no-any-return]
    d = sp.decimal(value, 1)
    unit = "Runden" if sp.lang == "de" else "laps"
    return Spoken(f"{d.display} {unit}", f"{d.spoken} {unit}")
