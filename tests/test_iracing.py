"""iRacing-Decoder gegen eine synthetische irsdk-Map."""

from __future__ import annotations

import struct

from pitwall.adapters.base import RawPacket
from pitwall.adapters.iracing import (
    IRacingDecoder,
    IRacingSplitter,
    parse_session_yaml,
    sanitize_yaml,
)
from pitwall.core.frame import Flag, PitState, SafetyCar, SessionPhase, SessionType, VehicleClass
from tests.fixtures.irsdk_builder import YAML, IrsdkMap, race_values


def _feed_map(dec: IRacingDecoder, data: bytes, t: float, splitter: IRacingSplitter | None = None):
    sp = splitter or IRacingSplitter()
    frame = None
    for pkt in sp.split(data, t):
        frame = dec.feed(pkt) or frame
    return frame


def test_yaml_sanitizing_handles_unescaped_names():
    info = parse_session_yaml(YAML.encode("cp1252"))
    drivers = info["DriverInfo"]["Drivers"]
    assert drivers[1]["UserName"] == "Kai Vögele"
    assert drivers[1]["TeamName"] == 'Team: #1 "Pit" Wall'
    assert drivers[1]["CarNumber"] == "007"
    assert drivers[2]["UserName"] == "Max O'Neil, Jr."
    assert drivers[3]["UserName"] == "yes"  # nicht als bool
    assert "CarSetup" not in info  # nicht benötigte (hier kaputte) Sektion ignoriert
    assert 'UserName: "Kai' in sanitize_yaml(YAML)


def test_race_frame_basics():
    m = IrsdkMap().set(**race_values())
    dec = IRacingDecoder()
    f = _feed_map(dec, m.step(), 1.0)
    assert f is not None
    s = f.session
    assert s.track_name == "Circuit de Spa-Francorchamps"
    assert s.track_length_m == 7000.0
    assert s.session_type is SessionType.RACE
    assert s.phase is SessionPhase.RUNNING
    assert s.total_laps == 20 and s.time_remaining_s is None
    assert s.pit_speed_limit_kmh == 80.0
    assert s.flag is Flag.GREEN and s.safety_car is SafetyCar.NONE
    assert s.vehicle_class is VehicleClass.GT
    assert f.player_idx == 1
    me = f.me
    assert me is not None and me.is_player and me.position == 2 and me.lap == 3
    assert me.driver_name == "Kai Vögele" and me.car_number == "007" and me.team_color == "#ffda59"
    assert me.sector == 2
    # Pace Car (0) und Zuschauer (5) ausgefiltert
    assert sorted(c.idx for c in f.cars) == [1, 2, 3]
    p = f.player
    assert p.fuel_l == 60.0 and p.fuel_capacity_l == 100.0
    assert p.gear == 4 and p.clutch == 0.0 and p.incidents == 2
    # Reifenwerte auf der Strecke nicht live
    assert p.wheels[0].temp_middle_c is None
    assert f.raw_extra["tyres_measured"][0]["middle"] == 85.0
    leader = f.car(2)
    assert leader is not None and leader.position == 1 and leader.tyre_compound == "dry"
    assert leader.total_distance_m is not None and me.total_distance_m is not None
    assert leader.total_distance_m > me.total_distance_m


def test_tyres_in_pit_stall_and_wear():
    m = IrsdkMap().set(**race_values(PlayerCarInPitStall=True, PlayerTrackSurface=1, OnPitRoad=True))
    f = _feed_map(IRacingDecoder(), m.step(), 1.0)
    assert f.me.pit_state is PitState.PIT_BOX
    w = f.player.wheels[0]
    # linkes Vorderrad: CL = außen, CR = innen
    assert w.temp_outer_c == 80.0 and w.temp_inner_c == 90.0
    assert abs(w.wear_pct - 10.0) < 1e-3


def test_caution_and_lights_out_events():
    m = IrsdkMap().set(**race_values(SessionState=3, SessionFlags=0))
    dec = IRacingDecoder()
    sp = IRacingSplitter()
    f = _feed_map(dec, m.step(), 1.0, sp)
    assert f.session.phase is SessionPhase.FORMATION
    m.set(SessionState=4, SessionFlags=0x4 | 0x80000000)
    f = _feed_map(dec, m.step(), 1.1, sp)
    assert any(e.kind.value == "lights_out" for e in f.events)
    m.set(SessionFlags=0x4000)
    f = _feed_map(dec, m.step(), 1.2, sp)
    assert f.session.safety_car is SafetyCar.SC
    assert any(e.kind.value == "safety_car" for e in f.events)
    m.set(SessionFlags=0x4000 | 0x200)
    f = _feed_map(dec, m.step(), 1.3, sp)
    assert f.session.safety_car is SafetyCar.ENDING


def test_splitter_only_emits_new_rows_and_disconnect():
    m = IrsdkMap().set(**race_values())
    sp = IRacingSplitter()
    data = m.step()
    chans = [p.channel for p in sp.split(data, 0.0)]
    assert chans == ["irsdk_vars", "irsdk_yaml", "irsdk_row"]
    assert [p.channel for p in sp.split(data, 0.1)] == []  # gleicher Tick
    assert [p.channel for p in sp.split(m.step(), 0.2)] == ["irsdk_row"]
    m.status = 0
    assert sp.split(m.step(), 0.3) == []


def test_missing_vars_tolerated_and_raw_map_channel():
    vars_ = {k: v for k, v in IrsdkMap().vars.items() if not k.startswith(("LF", "CarIdxTire", "Fuel"))}
    m = IrsdkMap(vars_).set(**race_values())
    dec = IRacingDecoder()
    f = dec.feed(RawPacket("irsdk", m.step(), 1.0))
    assert f is not None and f.player.fuel_l is None and f.me.position == 2


def test_yaml_parse_error_keeps_last_state():
    m = IrsdkMap().set(**race_values())
    dec = IRacingDecoder()
    sp = IRacingSplitter()
    _feed_map(dec, m.step(), 1.0, sp)
    dec.feed(RawPacket("irsdk_yaml", struct.pack("<i", 99) + b"WeekendInfo:\n TrackName: [\n  - : :", 5.0))
    f = dec.feed(RawPacket("irsdk_row", struct.pack("<i", 1000) + m.rows[m.buf_index], 6.0))
    assert f.session.track_name == "Circuit de Spa-Francorchamps"
