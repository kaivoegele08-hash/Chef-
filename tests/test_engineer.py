import io

from pitwall.dryrun import simulate
from pitwall.engineer.director import DirectorInput, RadioDirector
from pitwall.engineer.events import Priority, RadioEvent
from pitwall.engineer.phrasebook import Phrasebook


def test_phrasebooks_have_same_keys():
    de, en = Phrasebook("de"), Phrasebook("en")
    assert set(de.keys()) == set(en.keys())


def test_render_display_and_spoken():
    from pitwall.engineer.speech import Speaker

    sp = Speaker("de")
    pb = Phrasebook("de", seed=3)
    disp, spoken = pb.render("gap.ahead", {"gap": sp.seconds(0.4), "name": sp.name("Max Weber")})
    assert "0,4" in disp and "null Komma vier" in spoken and "Weber" in spoken


def _ev(key, prio, t=0.0, **kw):
    return RadioEvent(key=key, priority=prio, category=kw.pop("category", "gaps"), data=kw, created_t=t, ttl_s=10)


def test_director_holds_info_in_braking_zone_but_not_critical():
    d = RadioDirector(Phrasebook("de"))
    d.submit(_ev("fuel.ok", Priority.INFO, surplus="1"))
    braking = DirectorInput(t=0.1, lap=3, brake=0.9, throttle=0.0, steering=0.0)
    assert d.tick(braking) is None
    d.submit(_ev("strategy.box_now", Priority.CRITICAL, t=0.1))
    utt = d.tick(DirectorInput(t=0.2, lap=3, brake=0.9))
    assert utt is not None and utt.priority is Priority.CRITICAL
    # nach Ende der Durchsage auf der Geraden: INFO kommt
    straight = DirectorInput(t=utt.start_t + utt.duration_s + 0.1, lap=3, brake=0.0, throttle=1.0)
    utt2 = d.tick(straight)
    assert utt2 is not None and utt2.event.key == "fuel.ok"


def test_director_expiry_and_rationing():
    d = RadioDirector(Phrasebook("de"), max_info_per_lap=2)
    for i in range(5):
        d.submit(_ev("perf.push", Priority.INFO, t=0.0, category=f"c{i}"))
    t = 0.0
    spoken = 0
    for _ in range(200):
        t += 0.5
        if d.tick(DirectorInput(t=t, lap=1, throttle=1.0)):
            spoken += 1
    assert spoken <= 2
    d.submit(_ev("perf.push", Priority.INFO, t=t))
    assert d.tick(DirectorInput(t=t + 30, lap=2, brake=0.9)) is None  # verfallen
    assert d.stats.dropped_expired >= 1


def test_director_silent_mode_only_critical():
    d = RadioDirector(Phrasebook("de"), mode="silent")
    d.submit(_ev("gap.ahead", Priority.INFO, gap="1", name="X"))
    d.submit(_ev("flags.sc_deployed", Priority.CRITICAL))
    utt = d.tick(DirectorInput(t=0.1, lap=1, throttle=1.0))
    assert utt.event.key == "flags.sc_deployed"
    assert d.tick(DirectorInput(t=10, lap=1, throttle=1.0)) is None


def test_director_push_lap_silence():
    d = RadioDirector(Phrasebook("de"))
    d.submit(_ev("gap.ahead", Priority.INFO, gap="1", name="X"))
    assert d.tick(DirectorInput(t=0.1, lap=1, throttle=1.0, push_lap=True)) is None


def test_director_interrupt_chatter():
    d = RadioDirector(Phrasebook("de"))
    d.submit(_ev("perf.consistent", Priority.CHATTER, category="perf"))
    u1 = d.tick(DirectorInput(t=0.0, lap=5, throttle=1.0))
    assert u1 is not None
    d.submit(RadioEvent("flags.sc_deployed", Priority.CRITICAL, "flags", created_t=0.5, interrupt=True))
    u2 = d.tick(DirectorInput(t=0.5, lap=5, throttle=1.0))
    assert u2 is not None and u2.priority is Priority.CRITICAL and u1.interrupted


def test_dry_run_race_is_plausible_and_not_spammy(cfg):
    out = io.StringIO()
    r = simulate(cfg, "default", laps=20, out=out)
    keys = r.keys()
    assert "race.lights_out" in keys
    assert any(k.startswith("race.start_") for k in keys)
    assert any(k.startswith("strategy.box") for k in keys)
    assert any(k.startswith("race.finished") for k in keys)
    assert keys.count("strategy.box_this_lap") <= 2
    # nicht spammig: im Schnitt höchstens ~3 Funksprüche pro Runde
    assert len(r.utterances) / max(1, r.laps) < 3.5
    # Briefing vor Lights out
    assert keys.index("strategy.briefing") < keys.index("race.lights_out")


def test_scenario_undercut(cfg):
    r = simulate(cfg, "undercut", laps=20)
    assert any(k in ("strategy.rival_pitted_behind", "strategy.rival_pitted_ahead", "strategy.undercut_chance")
               for k in r.keys()) or "strategy.box_this_lap" in r.keys()


def test_scenario_sc_window(cfg):
    r = simulate(cfg, "sc_window", laps=20)
    keys = r.keys()
    assert "flags.sc_deployed" in keys
    assert "strategy.box_under_sc" in keys or "strategy.stay_out_sc" in keys
    assert "flags.green" in keys


def test_scenario_fuel_critical(cfg):
    r = simulate(cfg, "fuel_critical", laps=20)
    assert any(k in ("fuel.critical", "fuel.save") for k in r.keys())


def test_scenario_rain(cfg):
    r = simulate(cfg, "rain", laps=20)
    keys = r.keys()
    assert "weather.rain_soon" in keys
    assert any(k.startswith("weather.crossover") for k in keys)


def test_scenario_flashback_and_restart(cfg):
    r = simulate(cfg, "flashback", laps=8)
    assert r.state.flashback_count >= 1
    r2 = simulate(cfg, "restart", laps=6)
    assert r2.state.reset_count >= 1
    assert r2.keys().count("race.lights_out") == 2


def test_scenario_quali_push_lap_silence(cfg):
    r = simulate(cfg, "quali", laps=5)
    keys = r.keys()
    assert "quali.outlap" in keys or "quali.push" in keys
    assert any(k.startswith("quali.result") for k in keys)
    # während Push-Runden nur Erlaubtes
    for u in r.utterances:
        if u.event.key.startswith("gap."):
            raise AssertionError("Gap-Infos im Quali")


def test_english_engineer(cfg_en):
    r = simulate(cfg_en, "default", laps=4)
    assert any("Lights out" in t or "go" in t.lower() for t in r.texts())
