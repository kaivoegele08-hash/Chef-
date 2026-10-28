import struct

import pytest

from pitwall.adapters.base import RawPacket
from pitwall.adapters.f1 import F1Decoder
from pitwall.adapters.f1_packets import LAYOUTS
from pitwall.core.frame import (
    Capability,
    CarStatus,
    GameEventKind,
    PitState,
    SafetyCar,
    SessionPhase,
    SessionType,
    SimId,
)
from tests.fixtures import f1_builder as B

PLAYER = 3


def cars(n=22):
    out = []
    for i in range(n):
        out.append({"pos": i + 1, "lap": 5, "dist": 3000.0 - i * 40, "total": 4 * 5793 + 3000.0 - i * 40,
                    "last_ms": 82_345 + i * 100, "cur_ms": 40_000, "s1_ms": 28_123, "sector": 1,
                    "front_ms": 0 if i == 0 else 600, "leader_ms": i * 600})
    out[5]["pit"] = 1
    return out


def feed_all(fmt, extra=()):
    d = F1Decoder()
    names = [f"Fahrer {i}" for i in range(B.ncars(fmt))]
    teams = [i // 2 for i in range(B.ncars(fmt))]
    pkts = [B.session(fmt, player=PLAYER), B.participants(fmt, names, teams, player=PLAYER),
            B.telemetry(fmt, PLAYER), B.status(fmt, PLAYER), B.damage(fmt, fl_wing=35, player=PLAYER),
            B.motion(fmt, [(float(i * 10), 0.0, float(i)) for i in range(B.ncars(fmt))], player=PLAYER), *extra,
            B.lap_data(fmt, cars(B.ncars(fmt)), player=PLAYER)]
    frame = None
    for p in pkts:
        assert len(p) == LAYOUTS[fmt].sizes[p[6]], f"Builder-Paket {p[6]} falsche Größe"
        out = d.feed(RawPacket("udp", p, 1.0))
        frame = out or frame
    return d, frame


@pytest.mark.parametrize("fmt", [2024, 2025, 2026])
def test_full_frame(fmt):
    _, f = feed_all(fmt)
    assert f is not None
    assert f.sim is (SimId.F1_24 if fmt == 2024 else SimId.F1_25)
    assert f.player_idx == PLAYER
    assert len(f.cars) == B.ncars(fmt)
    me = f.me
    assert me.position == 4 and me.lap == 5 and me.driver_name == "Fahrer 3"
    assert me.last_lap_s == pytest.approx(82.645)
    assert me.gap_to_leader_s == pytest.approx(1.8)
    assert me.tyre_compound == "medium" and me.tyre_age_laps == 7
    assert f.car(5).pit_state is PitState.PIT_LANE
    assert f.car(2).is_teammate  # Team 1 = Index 2,3
    s = f.session
    assert s.track_name == "Monza" and s.track_id == "monza" and s.track_length_m == 5793
    assert s.session_type is SessionType.RACE and s.total_laps == 20
    assert s.weather.condition == "light_rain" and s.weather.air_temp_c == 22
    assert s.yellow_sectors == [2]  # Zone bei 50 % = 2896 m → Sektor 2
    p = f.player
    assert p.speed_ms == pytest.approx(287 / 3.6)
    assert p.gear == 7 and p.fuel_l == pytest.approx(42.5) and p.fuel_unit == "kg"
    # Radreihenfolge F1 RL,RR,FL,FR → FL,FR,RL,RR
    assert [w.temp_surface_c for w in p.wheels] == [101, 102, 95, 96]
    assert [w.wear_pct for w in p.wheels] == pytest.approx([14.0, 15.0, 10.0, 11.0])
    assert p.wheels[0].pressure_kpa == pytest.approx(23.5 * 6.894757)
    assert p.damage.front_wing_left == 35
    assert p.ers_store_pct == pytest.approx(50.0)
    assert f.has(Capability.OPPONENTS) and f.has(Capability.NATIVE_GAPS)
    assert me.x == 30.0


def test_wrong_size_is_rejected_not_crashing():
    d = F1Decoder()
    bad = B.lap_data(2025, cars())[:-1]
    assert d.safe_feed(RawPacket("udp", bad, 0.0)) is None
    assert d.errors == 1
    assert d.safe_feed(RawPacket("udp", b"\x00" * 10, 0.0)) is None


def test_unknown_format_ignored():
    d = F1Decoder()
    pkt = bytearray(B.lap_data(2025, cars()))
    pkt[0:2] = struct.pack("<H", 2023)
    assert d.feed(RawPacket("udp", bytes(pkt), 0.0)) is None


def test_events_lights_flashback_sc_penalty():
    fmt = 2025
    extra = [B.event(fmt, "STLG", bytes([3]), player=PLAYER)]
    d, f = feed_all(fmt, extra)
    assert any(e.kind is GameEventKind.START_LIGHTS for e in f.events)
    d.feed(RawPacket("udp", B.event(fmt, "LGOT", player=PLAYER), 2.0))
    d.feed(RawPacket("udp", B.event(fmt, "FLBK", struct.pack("<If", 100, 55.5), player=PLAYER), 2.0))
    d.feed(RawPacket("udp", B.event(fmt, "SCAR", bytes([1, 1]), player=PLAYER), 2.0))
    d.feed(RawPacket("udp", B.event(fmt, "PENA", bytes([4, 7, PLAYER, 0, 5, 5, 0]), player=PLAYER), 2.0))
    d.feed(RawPacket("udp", B.session(fmt, sc=1, player=PLAYER), 2.0))
    f2 = d.feed(RawPacket("udp", B.lap_data(fmt, cars(), player=PLAYER), 2.0))
    kinds = [e.kind for e in f2.events]
    assert GameEventKind.LIGHTS_OUT in kinds and GameEventKind.FLASHBACK in kinds
    fb = next(e for e in f2.events if e.kind is GameEventKind.FLASHBACK)
    assert fb.data["session_time"] == pytest.approx(55.5)
    pen = next(e for e in f2.events if e.kind is GameEventKind.PENALTY)
    assert pen.car_idx == PLAYER and pen.data["penalty_type"] == "time" and pen.data["time"] == 5
    assert f2.session.safety_car is SafetyCar.ENDING
    assert f2.session.phase is SessionPhase.RUNNING


def test_new_session_uid_resets():
    d, f = feed_all(2025)
    f2 = d.feed(RawPacket("udp", B.lap_data(2025, cars(), uid=99, player=PLAYER), 3.0))
    assert any(e.kind is GameEventKind.SESSION_STARTED for e in f2.events)
    assert f2.session.session_uid == "f1-99"


def test_inactive_slots_skipped():
    d = F1Decoder()
    c = cars(10)
    f = d.feed(RawPacket("udp", B.lap_data(2025, c, player=PLAYER), 0.0))
    assert len(f.cars) == 10
    assert all(car.status is CarStatus.ACTIVE for car in f.cars)


def test_engineer_runs_on_f1_frames(cfg):
    from pitwall.core.state import RaceState
    from pitwall.engineer.engineer import Engineer

    d, f = feed_all(2025)
    st = RaceState()
    eng = Engineer(cfg)
    eng.process(f, st, st.update(f))
    assert eng.snapshot.fuel is not None


def test_raw_recording_replays_through_decoder(tmp_path, cfg):
    from pitwall.adapters.base import RawPacket
    from pitwall.adapters.replay import iter_frames
    from pitwall.core.recorder import Recorder

    path = tmp_path / "f1.pwrec"
    rec = Recorder(path, "f1_25", "f1")
    names = [f"Fahrer {i}" for i in range(22)]
    pkts = [B.session(2025, player=PLAYER), B.participants(2025, names, [i // 2 for i in range(22)], player=PLAYER),
            B.telemetry(2025, PLAYER), B.status(2025, PLAYER), B.lap_data(2025, cars(), player=PLAYER)]
    for k, p in enumerate(pkts * 3):
        rec.write_raw(RawPacket("udp", p, 100.0 + k * 0.05))
    rec.close()
    header, it = iter_frames(path, cfg)
    frames = [f for _, f in it]
    assert header["adapter"] == "f1"
    assert len(frames) == 3 and frames[-1].me.driver_name == "Fahrer 3"
