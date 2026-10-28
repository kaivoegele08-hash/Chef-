"""App-Kette ohne Netzwerk: Simulator → App → Coaching, Langzeitgedächtnis, Debrief."""

from __future__ import annotations

from pitwall.adapters.simulator import RaceSimulator
from pitwall.app import PitWallApp


def _race(app: PitWallApp, seed: int, laps: int = 8) -> None:
    sim = RaceSimulator(seed=seed, n_cars=12, laps=laps, scenario="default", hz=10)
    for frame in sim.run():
        app.on_frame(frame)
        if app.engineer.flags.get("box_compound") and not getattr(sim, "_pitted", False):
            pass


def test_full_session_feeds_coach_memory_and_debrief(cfg):
    app = PitWallApp(cfg, "simulator", enable_voice=False, enable_brain=False, enable_ptt=False)
    _race(app, seed=3)
    app._finish_session()
    # Coaching: Strecke gelernt, Kurvenanalyse vorhanden
    assert app.engineer.track is not None and app.engineer.track.ready
    analysis = app.coach.analysis()
    assert analysis.get("reference_lap") is not None, analysis
    assert analysis["session_weak_corners"], analysis
    # Langzeitgedächtnis: Session mit Runden gespeichert
    sessions = app.long_memory.sessions()
    assert sessions and sessions[0]["laps"] >= 6
    laps = app.long_memory.session_laps(sessions[0]["id"])
    assert len(laps) >= 6 and any(lap["fuel_used"] for lap in laps)
    f = app.frame
    s = f.session
    summary = app.long_memory.summary(str(f.sim), s.track_id or s.track_name, s.car_name)
    assert summary["fuel_per_lap_l"] and summary["fuel_per_lap_l"] > 0.5
    # Debrief erzeugt und gespeichert
    assert app.debrief_text and len(app.debrief_text) > 80
    assert app.long_memory.debriefs()


def test_second_session_uses_learned_fuel(cfg):
    app = PitWallApp(cfg, "simulator", enable_voice=False, enable_brain=False, enable_ptt=False)
    _race(app, seed=3)
    app._finish_session()
    learned = app.long_memory.fuel_per_lap("simulator", app.frame.session.track_id, app.frame.session.car_name)
    app2 = PitWallApp(cfg, "simulator", enable_voice=False, enable_brain=False, enable_ptt=False)
    sim = RaceSimulator(seed=4, n_cars=12, laps=8, scenario="default", hz=10)
    frames = sim.run()
    for _ in range(60):  # vor Ende der ersten Runde
        app2.on_frame(next(frames))
    fuel = app2.engineer.snapshot.fuel
    assert learned is not None and fuel is not None
    assert fuel.per_lap_source in ("history", "memory", "learned"), fuel.per_lap_source
