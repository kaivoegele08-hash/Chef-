"""rF2-/LMU-Decoder gegen synthetische Maps."""

from __future__ import annotations

from pitwall.adapters.base import RawPacket
from pitwall.adapters.rfactor2 import RF2Decoder, RF2Splitter
from pitwall.core.frame import (
    Capability,
    CarStatus,
    Flag,
    PitState,
    SafetyCar,
    SessionPhase,
    SessionType,
    SimId,
)
from tests.fixtures.rf2_builder import (
    lmu_map,
    scoring_info,
    scoring_map,
    telem_block,
    telemetry,
    telemetry_map,
    vehicle,
)


def _field():
    return [vehicle(0, 1, 5, 3000.0), vehicle(1, 2, 5, 2900.0, player=True), vehicle(2, 3, 5, 2800.0),
            vehicle(3, 4, 4, 2000.0, inPits=True, pitState=3, lapsBehindLeader=1)]


def _feed(dec, sp, pkts):
    frame = None
    for p in pkts:
        for sub in sp.split(p):
            frame = dec.feed(sub) or frame
    return frame


def test_rf2_plugin_frame():
    dec, sp = RF2Decoder(), RF2Splitter()
    sc = RawPacket("rf2_scoring_raw", scoring_map(scoring_info(), _field()), 1.0)
    te = RawPacket("rf2_telemetry_raw", telemetry_map([telem_block(telemetry(0, elapsedTime=899.9)),
                                                       telem_block(telemetry(1))]), 1.01)
    f = _feed(dec, sp, [sc, te])
    assert f is not None and f.sim is SimId.RF2
    assert f.player_idx == 1
    s = f.session
    assert s.track_length_m == 7004.0 and s.session_type is SessionType.RACE and s.total_laps == 30
    assert s.phase is SessionPhase.RUNNING and s.flag is Flag.GREEN
    me = f.me
    assert me.position == 2 and me.laps_completed == 5 and me.lap == 6 and me.sector == 2
    assert me.car_number == "5" and me.gap_to_leader_s == 1.5
    assert me.last_sector_times_s == [40.0, 42.0, 44.0 + 1]
    p = f.player
    assert abs(p.speed_ms - 60.0) < 1e-6 and p.gear == 5 and p.fuel_l == 40.0 and p.fuel_capacity_l == 90.0
    fl = p.wheels[0]
    assert abs(fl.temp_outer_c - 90.0) < 1e-6 and abs(fl.temp_inner_c - 100.0) < 1e-6
    assert abs(fl.wear_pct - 10.0) < 1e-6 and fl.pressure_kpa == 170.0
    assert p.tyre_compound == "medium"
    assert Capability.NATIVE_GAPS in f.capabilities and Capability.FUEL in f.capabilities
    lapped = f.car(3)
    assert lapped.pit_state is PitState.PIT_BOX and lapped.gap_to_leader_s is None
    assert "virtual_energy_pct" not in f.raw_extra  # nur LMU


def test_torn_read_dropped_and_unchanged_skipped():
    dec, sp = RF2Decoder(), RF2Splitter()
    sc = scoring_map(scoring_info(), _field())
    assert _feed(dec, sp, [RawPacket("rf2_scoring_raw", sc, 1.0)]) is not None  # ohne Telemetrie → Scoring-Frame
    torn = telemetry_map([telem_block(telemetry(1))], torn=True)
    assert sp.split(RawPacket("rf2_telemetry_raw", torn, 1.1)) == []
    assert sp.torn == 1
    assert sp.split(RawPacket("rf2_scoring_raw", sc, 1.2)) == []  # unverändert


def test_full_course_yellow_and_sector_flags():
    dec, sp = RF2Decoder(), RF2Splitter()
    sc = RawPacket("rf2_scoring_raw", scoring_map(scoring_info(gamePhase=6, yellowFlagState=4), _field()), 1.0)
    f = _feed(dec, sp, [sc])
    assert f.session.safety_car is SafetyCar.SC and f.session.flag is Flag.YELLOW
    sc = RawPacket("rf2_scoring_raw", scoring_map(scoring_info(gamePhase=6, yellowFlagState=6, currentET=901.0),
                                                  _field(), version=9), 2.0)
    assert _feed(dec, sp, [sc]).session.safety_car is SafetyCar.ENDING
    sc = RawPacket("rf2_scoring_raw", scoring_map(scoring_info(sectorFlag=(0, 1, 0), currentET=902.0), _field(),
                                                  version=11), 3.0)
    f = _feed(dec, sp, [sc])
    assert f.session.yellow_sectors == [1] and f.session.flag is Flag.YELLOW


def test_lmu_data_frame_and_priority():
    dec, sp = RF2Decoder(lmu=True), RF2Splitter()
    veh = _field()
    blocks = [telem_block(telemetry(i)) for i in range(4)]
    m = lmu_map(scoring_info(sessionTimeRemaining=0.0), veh, blocks, player_idx=1)
    f = _feed(dec, sp, [RawPacket("lmu_data", m, 1.0)])
    assert f is not None and f.sim is SimId.LMU and f.player_idx == 1
    assert abs(f.raw_extra["virtual_energy_pct"] - 55.0) < 1e-3
    assert abs(f.player.ers_store_pct - 40.0) < 1e-3
    assert Capability.ERS in f.capabilities
    # Plugin-Daten werden ignoriert, solange LMU_Data kommt
    assert sp.split(RawPacket("rf2_scoring_raw", scoring_map(scoring_info(), veh), 1.5)) == []


def test_finish_status_and_session_change():
    dec, sp = RF2Decoder(), RF2Splitter()
    veh = _field()
    veh[2]["finishStatus"] = 2
    f = _feed(dec, sp, [RawPacket("rf2_scoring_raw", scoring_map(scoring_info(), veh), 1.0)])
    assert f.car(2).status is CarStatus.DNF
    uid = f.session.session_uid
    f = _feed(dec, sp, [RawPacket("rf2_scoring_raw", scoring_map(scoring_info(session=5, currentET=10.0), veh,
                                                                 version=20), 5.0)])
    assert f.session.session_type is SessionType.QUALIFYING and f.session.session_uid != uid
