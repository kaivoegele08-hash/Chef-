import asyncio

from pitwall.adapters.simulator import RaceSimulator
from pitwall.core.state import RaceState
from pitwall.engineer.engineer import Engineer
from pitwall.engineer.intents import answer, classify


def test_classify_german_and_english():
    assert classify("Wie ist der Abstand nach vorne?").name == "gap_ahead"
    assert classify("wer ist hinter mir").name == "gap_behind"
    assert classify("Wie viel Sprit haben wir noch?").name == "fuel"
    assert classify("Wie viele Runden noch?").name == "laps_left"
    assert classify("Funk ruhig bitte").name == "radio_quiet"
    assert classify("Box box, ich komme rein").name == "box_request"
    assert classify("wo komme ich raus wenn ich jetzt stoppe").name == "rejoin"
    assert classify("what's the gap ahead").name == "gap_ahead"
    assert classify("how much fuel").name == "fuel"
    assert classify("Erzähl mir einen Witz") is None
    assert classify("wo ist Weber", ["Jonas Weber"]).name == "rival"
    assert classify("leichtes Untersteuern in langsamen Kurven", awaiting_balance=True).name == "balance_note"


def _engineer_mid_race(cfg):
    sim = RaceSimulator(laps=10)
    st = RaceState()
    eng = Engineer(cfg)
    for f in sim.run(max_steps=20 * 330):
        ev = st.update(f)
        eng.process(f, st, ev)
    return eng


def test_answers_use_computed_values(cfg):
    eng = _engineer_mid_race(cfg)
    key, data = answer(classify("Abstand nach vorne?"), eng)
    assert key == "answer.gap_ahead"
    assert "," in data["gap"].display
    key, data = answer(classify("Sprit?"), eng)
    assert key == "answer.fuel"
    key, data = answer(classify("Reifen?"), eng)
    assert key == "answer.tyres"
    key, data = answer(classify("Wie ist die Strategie?"), eng)
    assert key in ("answer.pit_plan", "answer.pit_none")
    key, data = answer(classify("Funk stumm"), eng)
    assert eng.director.mode == "silent"


def test_no_data_for_missing_capability(cfg):
    from pitwall.core.frame import Capability

    eng = _engineer_mid_race(cfg)
    eng.frame.capabilities = frozenset(c for c in eng.frame.capabilities if c is not Capability.OPPONENTS)
    key, _ = answer(classify("Abstand nach vorne?"), eng)
    assert key == "answer.no_data"


def test_app_question_flow_without_llm(cfg):
    from pitwall.app import PitWallApp

    async def run():
        app = PitWallApp(cfg, "simulator", {"time_scale": 0}, enable_voice=False, enable_brain=True, enable_ptt=False)
        sim = RaceSimulator(laps=5)
        for f in sim.run(max_steps=20 * 200):
            app.on_frame(f)
        r1 = await app.handle_question("Abstand nach hinten?")
        r2 = await app.handle_question("Erzähl mir einen Witz")
        return app, r1, r2

    app, r1, r2 = asyncio.run(run())
    assert r1["source"] == "intent"
    assert r2["source"] == "fallback"  # kein API-Key → Phrasebook
    assert any(m.get("role") == "driver" for m in app.radio_log)
