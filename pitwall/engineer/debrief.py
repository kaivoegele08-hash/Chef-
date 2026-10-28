"""Debrief nach der Session: Stints, Sprit, beste Runden, Konstanz, Schwachkurven, Strategie-Rückblick."""

from __future__ import annotations

import statistics
from typing import TYPE_CHECKING, Any

from ..core.state import RaceState
from .speech import Speaker

if TYPE_CHECKING:
    from .coaching import Coach
    from .engineer import Engineer


def _fmt(sp: Speaker, t: float | None) -> str:
    return sp.laptime(t, 3).display if t else "–"


def build_debrief(state: RaceState, eng: Engineer, coach: Coach | None, lang: str = "de") -> tuple[str, dict[str, Any]]:
    sp = Speaker(lang)
    de = lang == "de"
    f = eng.frame
    me = f.me if f else None
    clean = [lap.time_s for lap in state.laps if lap.clean and lap.time_s]
    data: dict[str, Any] = {
        "session_type": str(f.session.session_type) if f else None,
        "track": f.session.track_name if f else None,
        "sim": str(f.sim) if f else None,
        "laps": len(state.laps),
        "best_lap_s": state.best_lap_s,
        "best_lap_no": next((lap.lap for lap in state.laps if lap.time_s == state.best_lap_s), None),
        "start_position": state.start_position,
        "result_position": me.position if me else None,
        "pit_stops": state.pit_stops_made,
        "pit_loss_samples": [round(x, 1) for x in state.pit_loss_samples],
        "stints": [{"compound": s.compound, "start_lap": s.start_lap,
                    "end_lap": s.end_lap if s.end_lap is not None else (me.lap if me else s.start_lap)}
                   for s in state.stints],
        "fuel_avg_l": None,
        "consistency_s": round(statistics.pstdev(clean), 3) if len(clean) >= 3 else None,
        "median_lap_s": round(statistics.median(clean), 3) if clean else None,
        "weak_corners": coach.session_corner_summary()[:3] if coach else [],
        "radio_calls": eng.director.stats.spoken,
        "flashbacks": state.flashback_count,
    }
    fuels = [lap.fuel_used_l for lap in state.laps if lap.fuel_used_l and lap.clean]
    if fuels:
        data["fuel_avg_l"] = round(statistics.median(fuels), 3)

    lines: list[str] = []
    title = "Debrief"
    where = f" – {data['track']}" if data["track"] else ""
    lines.append(f"{title}{where} ({data['session_type'] or '?'})")
    if data["result_position"]:
        if data["start_position"] and data["session_type"] == "race":
            diff = data["start_position"] - data["result_position"]
            sign = f"+{diff}" if diff > 0 else str(diff)
            lines.append(f"Ergebnis: P{data['result_position']} (Start P{data['start_position']}, {sign})" if de else
                          f"Result: P{data['result_position']} (started P{data['start_position']}, {sign})")
        else:
            lines.append(f"Position: P{data['result_position']}")
    lines.append(f"{'Runden' if de else 'Laps'}: {data['laps']}, {'beste Runde' if de else 'best lap'} "
                 f"{_fmt(sp, data['best_lap_s'])}" + (f" ({'Runde' if de else 'lap'} {data['best_lap_no']})"
                                                       if data["best_lap_no"] else ""))
    if data["median_lap_s"]:
        lines.append(f"{'Typische Runde' if de else 'Typical lap'} {_fmt(sp, data['median_lap_s'])}, "
                     f"{'Konstanz' if de else 'consistency'} ±{data['consistency_s']} s"
                     if data["consistency_s"] is not None else "")
    if data["stints"]:
        parts = [f"{s['compound'] or '?'} R{s['start_lap']}–{s['end_lap']}" for s in data["stints"]]
        lines.append(f"Stints: {', '.join(parts)}")
    if data["pit_stops"]:
        loss = (f", {'Boxenverlust' if de else 'pit loss'} ~{statistics.median(state.pit_loss_samples):.1f} s"
                if state.pit_loss_samples else "")
        lines.append(f"{'Boxenstopps' if de else 'Pit stops'}: {data['pit_stops']}{loss}")
    if data["fuel_avg_l"]:
        lines.append(f"{'Sprit' if de else 'Fuel'}: {data['fuel_avg_l']:.2f} l/{'Runde' if de else 'lap'}")
    if data["weak_corners"]:
        from .coaching import CAUSE_TEXT_DE, CAUSE_TEXT_EN

        table = CAUSE_TEXT_DE if de else CAUSE_TEXT_EN
        wc = "; ".join(f"{c['name']} {c['loss_s']:.2f} s {table.get(c['cause'], '')}".strip()
                       for c in data["weak_corners"])
        lines.append(f"{'Schwachkurven' if de else 'Weak corners'}: {wc}")
    plans = eng.snapshot.plans
    if plans and plans.plan_a:
        lines.append(f"{'Strategie zuletzt' if de else 'Latest strategy'}: Plan A {plans.plan_a.label}")
    notes = [n.text for n in eng.memory.notes]
    if notes:
        lines.append(f"{'Deine Notizen' if de else 'Your notes'}: " + " | ".join(notes[-4:]))
    text = "\n".join(line for line in lines if line)
    return text, data
