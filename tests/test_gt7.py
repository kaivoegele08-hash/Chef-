import os

import pytest

from pitwall.adapters.base import RawPacket
from pitwall.adapters.gt7 import GT7Decoder, _salsa20_xor_py, decrypt, encrypt, salsa20_xor
from pitwall.core.frame import Capability, GameEventKind, SessionPhase, SessionType, VehicleClass
from tests.fixtures import gt7_builder as B


def test_pure_python_salsa_matches_backend():
    data = os.urandom(344)
    key = b"Simulator Interface Packet GT7 v"
    nonce = os.urandom(8)
    assert _salsa20_xor_py(data, key, nonce) == salsa20_xor(data, key, nonce)


@pytest.mark.parametrize("size", [296, 316, 344, 368])
def test_roundtrip_all_formats(size):
    plain = B.packet(size)
    cipher = encrypt(plain, seed=0x12345678)
    assert cipher != plain
    out = decrypt(cipher)
    assert out is not None and out[:4] == plain[:4] and out[0x44:] == plain[0x44:]


def test_wrong_xor_is_rejected():
    plain = B.packet(296)
    cipher = encrypt(plain, seed=7)
    assert decrypt(cipher + b"x" * 20) is None  # 316 Bytes → falsche Konstante/Magic


def feed(d, **kw):
    return d.feed(RawPacket("udp", encrypt(B.packet(**kw), seed=kw.get("packet_id", 1) * 7919), 0.0))


def test_decode_values():
    d = GT7Decoder()
    f = feed(d, size=368, category=b"GR3\0", cur_lap_ms=12345)
    assert f is not None
    p = f.player
    assert p.speed_ms == pytest.approx(50.0) and p.rpm == pytest.approx(7000.0)
    assert p.fuel_l == pytest.approx(55.0) and p.fuel_capacity_l == pytest.approx(100.0)
    assert [w.temp_surface_c for w in p.wheels] == [80.0, 81.0, 75.0, 76.0]
    assert p.gear == 4 and p.throttle == pytest.approx(1.0)
    assert p.steering == pytest.approx(0.2 / 3.141592653589793, rel=1e-4)
    assert abs(p.wheels[0].slip_ratio) < 1e-3
    me = f.me
    assert me.lap == 2 and me.last_lap_s == pytest.approx(96.456) and me.best_lap_s == pytest.approx(95.123)
    assert me.position == 3 and me.current_lap_s == pytest.approx(12.345)
    assert f.session.session_type is SessionType.RACE and f.session.total_laps == 10
    assert f.session.vehicle_class is VehicleClass.GT
    assert not f.has(Capability.OPPONENTS) and f.has(Capability.FUEL)


def test_duplicates_dropped_and_lockup_detected():
    d = GT7Decoder()
    assert feed(d, packet_id=10) is not None
    assert feed(d, packet_id=10) is None
    f = feed(d, packet_id=11, brake=255, wheel_rps=[10.0, 10.0, 150.0, 150.0])
    assert f.player.wheels[0].slip_ratio < -0.5


def test_track_length_learned_and_start_event():
    d = GT7Decoder()
    pid = 1
    feed(d, packet_id=pid, lap=0, total=5, num_cars=12, speed=0.0)
    f = feed(d, packet_id=pid + 1, lap=0, total=5)
    assert f.session.phase is SessionPhase.GRID
    pid += 2
    f = feed(d, packet_id=pid, lap=1, total=5)
    assert any(e.kind is GameEventKind.LIGHTS_OUT for e in f.events)
    # eine Runde mit 50 m/s über 60 s = 3000 m
    for _i in range(3600):
        pid += 1
        feed(d, packet_id=pid, lap=1, total=5)
    pid += 1
    f = feed(d, packet_id=pid, lap=2, total=5, pos=(400.0, 0.0, 400.0))
    assert f.session.track_length_m == pytest.approx(3000, rel=0.01)
    assert f.session.track_id.startswith("gt7_")


def test_engineer_on_gt7_frames(cfg):
    from pitwall.core.state import RaceState
    from pitwall.engineer.engineer import Engineer
    from pitwall.engineer.intents import answer, classify

    d = GT7Decoder()
    st = RaceState()
    eng = Engineer(cfg)
    pid = 1
    for lap in (1, 2, 3):
        for _ in range(600):
            pid += 1
            f = feed(d, packet_id=pid, lap=lap, total=10, gas=60 - lap * 3.0, last_ms=95000 + lap * 100)
            eng.process(f, st, st.update(f))
    key, _ = answer(classify("Abstand nach vorne?"), eng)
    assert key == "answer.no_data"  # GT7 hat keine Gegnerdaten
    key, _ = answer(classify("Wie viel Sprit?"), eng)
    assert key == "answer.fuel"
