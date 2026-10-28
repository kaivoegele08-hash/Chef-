from pitwall.adapters.simulator import FORMULA, PITWALL_RING, RaceSimulator, build_profile, profile_lap_time
from pitwall.core.frame import Capability, SessionPhase
from pitwall.core.state import RaceState, StateEventKind


def run(scenario="default", laps=6, n_cars=20):
    sim = RaceSimulator(laps=laps, scenario=scenario, n_cars=n_cars)
    st = RaceState()
    kinds: dict = {}
    frames = 0
    for f in sim.run():
        frames += 1
        for e in st.update(f):
            kinds.setdefault(e.kind, []).append(e)
    return sim, st, kinds, frames


def test_profile_plausible():
    prof = build_profile(PITWALL_RING, FORMULA)
    lap = profile_lap_time(prof)
    assert 80 < lap < 110
    assert max(prof) * 3.6 <= 331


def test_deterministic_and_complete():
    _, st1, k1, n1 = run(laps=4)
    _, st2, k2, n2 = run(laps=4)
    assert n1 == n2
    assert [l.time_s for l in st1.laps] == [l.time_s for l in st2.laps]
    assert len(k1[StateEventKind.LAP_COMPLETED]) == 4
    assert StateEventKind.LIGHTS_OUT in k1 and StateEventKind.CHEQUERED in k1


def test_twenty_cars_and_capabilities():
    sim = RaceSimulator(laps=2)
    frames = list(sim.run(max_steps=200))
    f = frames[-1]
    assert len(f.cars) == 20
    assert f.has(Capability.OPPONENTS) and f.has(Capability.FUEL)
    assert sorted(c.position for c in f.cars) == list(range(1, 21))


def test_lights_out_only_after_grid():
    sim = RaceSimulator(laps=2)
    st = RaceState()
    lights_out_t = None
    for f in sim.run(max_steps=200):
        for e in st.update(f):
            if e.kind is StateEventKind.LIGHTS_OUT:
                lights_out_t = f.session.session_time_s
                assert f.session.phase is SessionPhase.RUNNING
    assert lights_out_t is not None and lights_out_t >= 5.9


def test_gaps_time_based():
    sim = RaceSimulator(laps=3)
    st = RaceState()
    for f in sim.run(max_steps=20 * 140):
        st.update(f)
    ahead = st.car_ahead()
    assert ahead is not None
    g = st.gap_to(ahead.idx)
    assert g is not None and 0 < g < 20
    behind = st.car_behind()
    gb = st.gap_to(behind.idx)
    assert gb is not None and gb < 0


def test_flashback_rolls_back():
    _, st, kinds, _ = run("flashback", laps=6)
    assert StateEventKind.FLASHBACK in kinds
    laps = [l.lap for l in st.laps]
    assert laps == sorted(set(laps)), "keine doppelten Runden nach Flashback"


def test_restart_resets():
    _, st, kinds, _ = run("restart", laps=5)
    assert StateEventKind.SESSION_RESET in kinds
    assert st.laps[0].lap == 1
    assert len(st.laps) == 5


def test_pit_loss_learned():
    _, st, _, _ = run("default", laps=12)
    assert st.pit_stops_made == 1
    assert st.pit_loss_samples and 8 < st.pit_loss_samples[0] < 30
