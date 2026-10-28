"""ETS2/ATS: Decoder + Disponenten-Detektoren über die ganze Kette."""

from __future__ import annotations

from pitwall.adapters.base import RawPacket
from pitwall.adapters.ets2 import ETS2Decoder
from pitwall.core.frame import GameEventKind, SimId, VehicleClass
from pitwall.core.state import RaceState
from pitwall.engineer.engineer import Engineer
from tests.fixtures.scs_builder import ScsMap


def test_decoder_fields_and_toggles():
    m = ScsMap().drive().job()
    dec = ETS2Decoder()
    f = dec.feed(RawPacket("scs", m.snapshot(), 1.0))
    assert f is not None and f.sim is SimId.ETS2 and f.session.vehicle_class is VehicleClass.TRUCK
    tr = f.player.truck
    assert tr.job_cargo == "Bagger" and tr.job_destination_city == "Berlin" and tr.job_income == 12450
    assert abs(tr.speed_limit_kmh - 90.0) < 1e-3 and tr.job_delivery_time_min == 1200
    assert f.events == []
    m.i64(4216, 250).s(3436, "speeding", 32).toggle(4304)
    f = dec.feed(RawPacket("scs", m.snapshot(), 1.1))
    assert [e.kind for e in f.events] == [GameEventKind.FINE] and f.events[0].data["amount"] == 250
    m.toggle(4304)  # Toggle zurück = nächstes Event
    f = dec.feed(RawPacket("scs", m.snapshot(), 1.2))
    assert [e.kind for e in f.events] == [GameEventKind.FINE]


def test_wrong_revision_and_inactive_rejected():
    m = ScsMap().drive()
    m.u32(40, 11)
    assert ETS2Decoder().feed(RawPacket("scs", m.snapshot(), 1.0)) is None
    m = ScsMap(game=2).drive()
    m.b[0] = 0
    assert ETS2Decoder().feed(RawPacket("scs", m.snapshot(), 1.0)) is None


def _run(cfg, m: ScsMap, steps, dec, eng, state, t0):
    said = []
    t = t0
    for mutate in steps:
        mutate(m)
        for _ in range(30):
            t += 5.0
            f = dec.feed(RawPacket("scs", m.snapshot(), t))
            utt = eng.process(f, state, state.update(f))
            if utt:
                said.append((utt.event.key, utt.text))
    return said, t


def test_dispatcher_radio_chain(cfg):
    m = ScsMap().drive()
    dec, eng, state = ETS2Decoder(), Engineer(cfg), RaceState()
    said, _ = _run(cfg, m, [
        lambda m: m.job(),
        lambda m: m.drive(speed_ms=30.0, limit_ms=22.2),  # 108 statt 80
        lambda m: m.drive(rng=120.0),
        lambda m: m.drive(rest=45),
        lambda m: (m.i64(4216, 150), m.toggle(4304)),
        lambda m: m.f32(1036, 0.3),  # Motor 30 %
        lambda m: (m.i64(4208, 12450), m.toggle(4303), m.flag(4300, False)),
    ], dec, eng, state, 0.0)
    keys = [k for k, _ in said]
    for k in ("truck.job_start", "truck.speed_limit", "truck.fuel_low", "truck.rest_soon", "truck.fine",
              "truck.damage", "truck.delivered"):
        assert k in keys, (k, said)
    text = dict(said)
    assert "Bagger" in text["truck.job_start"] and "Berlin" in text["truck.job_start"]
    assert "20:00" in text["truck.job_start"]
    assert "12450 €" in text["truck.delivered"]
    assert "Motor" in text["truck.damage"]


def test_deadline_tight(cfg):
    m = ScsMap().drive(game_min=8 * 60, nav_s=10 * 3600).job(deadline_min=12 * 60)  # 10 h Fahrt, 4 h übrig
    dec, eng, state = ETS2Decoder(), Engineer(cfg), RaceState()
    said, _ = _run(cfg, m, [lambda m: None] * 8, dec, eng, state, 0.0)
    assert "truck.deadline_tight" in [k for k, _ in said]


def test_truck_intents_and_tool(cfg):
    import json

    from pitwall.brain.tools import run_tool
    from pitwall.engineer.intents import answer, classify

    m = ScsMap().drive(rest=95).job()
    dec, eng, state = ETS2Decoder(), Engineer(cfg), RaceState()
    f = dec.feed(RawPacket("scs", m.snapshot(), 1.0))
    eng.process(f, state, state.update(f))
    key, data = answer(classify("Wann muss ich Pause machen?"), eng)
    assert key == "truck.answer_rest" and data["minutes"].display == "95 min"
    key, data = answer(classify("Wie weit ist es noch?"), eng)
    assert key == "truck.distance_left" and data["distance"].display == "400 km"
    key, data = answer(classify("Wie viel Sprit habe ich?"), eng)
    assert key == "truck.answer_fuel"
    assert answer(classify("Wie ist der Schaden?"), eng)[0] == "truck.answer_no_damage"
    job = json.loads(run_tool(eng, "get_truck_job", {}))
    assert job["job"]["cargo"] == "Bagger" and job["schedule_game_minutes"]["deadline_in"] == 720
    assert job["speed_limit_kmh"] == 90
