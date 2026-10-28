"""ACC: Broadcasting-Parser und Shared-Memory-Decoder."""

from __future__ import annotations

from pitwall.adapters.acc import ACCDecoder, BroadcastState, register_message
from pitwall.adapters.base import RawPacket
from pitwall.core.frame import Capability, Flag, PitState, SessionPhase, SessionType
from tests.fixtures import acc_builder as b


def test_register_message_matches_reference_bytes():
    # Referenz aus der Protokoll-Doku (acbc-Testvektor)
    assert register_message("Your name", "asd", 250, "").hex(" ") == \
        "01 04 09 00 59 6f 75 72 20 6e 61 6d 65 03 00 61 73 64 fa 00 00 00 00 00"


def _bc_session(dec: ACCDecoder, t: float = 1.0) -> None:
    for msg in (b.registration(), b.track_data(7), b.entry_list(7, [1, 2]),
                b.entry_car(1, "Pit Wall Racing", 7, "Kai", "Vögele"),
                b.entry_car(2, "Rivals", 12, "Max", "Muster", cup=1),
                b.car_update(1, 2, 4, 0.50), b.car_update(2, 1, 4, 0.52)):
        dec.feed(RawPacket("accb", msg, t))


def test_broadcast_only_frame():
    dec = ACCDecoder()
    _bc_session(dec)
    f = dec.feed(RawPacket("accb", b.realtime(), 1.1))
    assert f is not None
    assert f.session.track_name == "Spa" and f.session.track_length_m == 7004.0
    assert f.session.session_type is SessionType.RACE and f.session.phase is SessionPhase.RUNNING
    assert f.player_idx == 1
    me = f.me
    assert me.driver_name == "Kai Vögele" and me.car_number == "7" and me.position == 2 and me.lap == 5
    assert me.last_lap_s == 121.0 and me.last_sector_times_s == [40.1, 41.2, 39.7]
    rival = f.car(2)
    assert rival.class_position == 1 and rival.car_class == "Pro-Am"
    assert Capability.OPPONENTS in f.capabilities and Capability.FUEL not in f.capabilities
    assert abs(f.session.time_remaining_s - 1800.0) < 1e-6


def test_pit_detection_counts_stops():
    st = BroadcastState()
    st.handle(b.car_update(2, 1, 4, 0.9), 1.0)
    st.handle(b.car_update(2, 1, 4, 0.95, location=3), 2.0)
    st.handle(b.car_update(2, 1, 4, 0.97, location=2, kmh=0), 3.0)
    st.handle(b.car_update(2, 1, 5, 0.02, location=4), 4.0)
    st.handle(b.car_update(2, 1, 5, 0.05, location=1), 5.0)
    assert st.cars[2].pit_stops == 1


def test_shared_memory_merge():
    dec = ACCDecoder()
    _bc_session(dec)
    dec.feed(RawPacket("accb", b.realtime(), 1.0))
    dec.feed(RawPacket("static", b.static(), 1.0))
    dec.feed(RawPacket("physics", b.physics(), 1.01))
    f = dec.feed(RawPacket("graphics", b.graphics(GlobalYellow2=1), 1.02))
    assert f is not None
    p = f.player
    assert p.fuel_l == 55.0 and p.fuel_capacity_l == 120.0 and p.gear == 4
    assert abs(p.wheels[0].pressure_kpa - 27.6 * 6.894757) < 1e-3
    assert p.wheels[0].surface_temp() == 82.0
    assert f.me.position == 3 and f.me.x == 10.0 and f.me.z == 20.0 and f.me.sector == 2
    assert f.car(2).x == 30.0
    assert f.session.flag is Flag.YELLOW and f.session.yellow_sectors == [2]
    assert f.session.weather.forecast[1].condition == "light_rain"
    assert abs(f.raw_extra["fuel_per_lap_game"] - 2.8) < 1e-6
    assert Capability.FUEL in f.capabilities and Capability.OPPONENTS in f.capabilities
    # Broadcasting-Realtime erzeugt keinen zweiten Frame, solange SHM aktiv ist
    assert dec.feed(RawPacket("accb", b.realtime(), 1.05)) is None


def test_pitbox_and_lights_out():
    dec = ACCDecoder()
    _bc_session(dec)
    f = dec.feed(RawPacket("accb", b.realtime(phase=3), 1.0))
    assert f.session.phase is SessionPhase.FORMATION
    f = dec.feed(RawPacket("accb", b.realtime(phase=5), 1.2))
    assert any(e.kind.value == "lights_out" for e in f.events)
    dec.feed(RawPacket("accb", b.car_update(2, 1, 4, 0.1, location=2, kmh=0), 1.3))
    f = dec.feed(RawPacket("accb", b.realtime(), 1.4))
    assert f.car(2).pit_state is PitState.PIT_BOX


def test_malformed_datagram_is_dropped():
    dec = ACCDecoder()
    assert dec.safe_feed(RawPacket("accb", bytes([3, 1]), 1.0)) is None
    assert dec.errors == 1


def test_ac_and_acc_share_map_names_but_are_told_apart():
    from pitwall.adapters.assetto_corsa import ACDecoder, looks_like_acc

    acc_static = b.static() + bytes(4096 - 820)
    acc_physics = b.physics(currentMaxRpm=9250.0) + bytes(4096 - 800)
    assert looks_like_acc(acc_static) is True
    assert looks_like_acc(None, acc_physics) is True
    ac = ACDecoder()
    ac.feed(RawPacket("static", acc_static, 1.0))
    ac.feed(RawPacket("physics", acc_physics, 1.0))
    assert ac.feed(RawPacket("graphics", b.graphics() + bytes(4096 - 1588), 1.0)) is None
    # AC-Static (684 Bytes, Rest leer) → ACC-Decoder ignoriert das Shared Memory
    ac_static = bytearray(4096)
    ac_static[:30] = "1.7".encode("utf-16-le").ljust(30, b"\0")
    ac_static[68:68 + 20] = "ks_bmw_m3".encode("utf-16-le")
    assert looks_like_acc(bytes(ac_static)) is False
    dec = ACCDecoder()
    dec.feed(RawPacket("static", bytes(ac_static), 1.0))
    dec.feed(RawPacket("physics", b.physics(), 1.0))
    assert dec.feed(RawPacket("graphics", b.graphics(), 1.0)) is None
