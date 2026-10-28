import math

from pitwall.core.frame import SafetyCar
from pitwall.core.state import LapRecord
from pitwall.strategy.fuel import fuel_status, laps_remaining, robust_mean
from pitwall.strategy.pit import PitLossModel, predict_rejoin
from pitwall.strategy.plans import CompoundModel, plan_race
from pitwall.strategy.tyres import fit_degradation, temperature_status


def laps_with(fuel_per_lap=2.0, n=6, start=100.0, base=90.0, deg=0.1, tpl=0.03):
    out = []
    fuel = start
    for i in range(1, n + 1):
        t = base + deg * i + fuel * tpl
        out.append(LapRecord(lap=i, time_s=t, fuel_start_l=fuel, fuel_end_l=fuel - fuel_per_lap, compound="medium",
                             tyre_age=i))
        fuel -= fuel_per_lap
    return out


def test_robust_mean_ignores_outlier():
    assert abs(robust_mean([2.0, 2.0, 2.1, 1.9, 6.0]) - 2.0) < 0.1


def test_fuel_status_exact():
    laps = laps_with(2.0, n=5)
    fs = fuel_status(laps, fuel_l=30.0, capacity_l=110, current_lap=6, lap_pct=0.0, total_laps=20,
                     time_remaining_s=None, lap_time_s=90, reserve_laps=0.5)
    assert math.isclose(fs.per_lap_l, 2.0, rel_tol=1e-6)
    assert math.isclose(fs.laps_to_go, 15.0)
    assert math.isclose(fs.needed_l, 31.0)
    assert math.isclose(fs.surplus_l, -1.0)
    assert fs.critical is True  # −0,5 Runden (Reserve nicht gedeckt)
    assert fs.save_per_lap_l > 0
    assert fs.lift_coast_m is not None


def test_laps_remaining_timed_race():
    # 10 min Rest, 90 s Runde, gerade an der Linie → 7 volle Runden (Runde nach Ablauf wird beendet)
    assert laps_remaining(5, 0.0, None, 600.0, 90.0) == 7
    assert laps_remaining(5, 0.5, 20, None, None) == 15.5


def test_degradation_fuel_corrected():
    laps = laps_with(2.0, n=8, deg=0.1)
    fit = fit_degradation(laps, time_per_litre=0.03)
    assert abs(fit.deg_s_per_lap - 0.1) < 1e-6
    # roh sieht man weniger Abbau, weil der Sprit leichter wird
    assert fit.raw_trend_s_per_lap < fit.deg_s_per_lap


def test_temperature_window():
    st = temperature_status([80, 95, 110, None], (85, 105))
    assert st.states == ["cold", "ok", "hot", "unknown"]
    assert st.worst_wheel == "FL"


def test_stint_time_closed_form_matches_loop():
    m = CompoundModel("soft", -0.5, 0.08, 10, 0.06)
    for start in (0, 5, 12):
        for n in (1, 7, 20):
            loop = sum(-0.5 + 0.08 * a + (0.06 * (a - 10) ** 2 if a > 10 else 0) for a in range(start + 1, start + n + 1))
            assert abs(m.stint_time(start, n) - loop) < 1e-9


def test_plan_race_prefers_one_stop_and_window():
    models = {"soft": CompoundModel("soft", -0.6, 0.09, 16), "medium": CompoundModel("medium", 0.0, 0.055, 26),
              "hard": CompoundModel("hard", 0.45, 0.035, 38)}
    res = plan_race(1, 30, "medium", 0, models, pit_loss_s=22.0, max_stops=2, two_compound_rule=True)
    assert res.plan_a is not None and res.plan_a.stops == 1
    assert res.plan_a.window and res.plan_a.window[0][0] <= res.plan_a.pit_laps[0] <= res.plan_a.window[0][1]
    # Zwei-Mischungen-Regel: nie nur medium
    assert len(set(res.plan_a.compounds)) >= 2


def test_plan_race_cheap_pitstop_means_more_stops():
    models = {"soft": CompoundModel("soft", -1.0, 0.3, 8), "hard": CompoundModel("hard", 0.5, 0.05, 40)}
    res = plan_race(1, 40, "soft", 0, models, pit_loss_s=3.0, max_stops=3)
    assert res.plan_a.stops >= 2


def test_rejoin_prediction():
    gaps = {1: 5.0, 2: 12.0, 3: 25.0, 4: -3.0, 5: -14.0}
    info = {i: {"name": f"D{i}", "position": i} for i in gaps}
    r = predict_rejoin(gaps, info, pit_loss_s=20.0)
    # nach dem Stopp: vor mir 1,2,3,4 (−3+20=17), 5 (−14+20=6) → alle vor mir → P6
    assert r.position == 6
    r2 = predict_rejoin(gaps, info, pit_loss_s=13.0)
    # 5: −14+13 = −1 → hinter mir; 4: 10 → vor mir
    assert r2.position == 5 and r2.behind.idx == 5 and abs(r2.behind.gap_after_s + 1.0) < 1e-9
    assert not r2.free_air


def test_pit_loss_model_sc_and_learning():
    m = PitLossModel(base_s=20.0, sc_factor=0.5)
    assert m.effective(SafetyCar.SC) == 10.0
    for v in (24.0, 24.0, 24.0):
        m.learn(v)
    assert abs(m.loss_s - 24.0) < 1e-9
