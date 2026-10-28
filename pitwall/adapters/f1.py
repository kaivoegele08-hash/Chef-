"""EA SPORTS F1 24 / F1 25 (inkl. 2026 Season Pack) – UDP-Telemetrie.

PC oder PS5: Im Spiel unter Telemetrie-Einstellungen UDP an, IP = dieser PC,
Port 20777, Senderate 20–30 Hz, UDP-Format 2024/2025/2026 (wird erkannt).
Details und Quellen: ``docs/protocols/f1.md``, Einrichtung: ``docs/setup-f1.md``.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from ..core.frame import (
    Capability,
    CarState,
    CarStatus,
    Damage,
    Flag,
    ForecastSample,
    GameEvent,
    GameEventKind,
    PitState,
    PlayerState,
    SafetyCar,
    SessionInfo,
    SessionPhase,
    SessionType,
    SimId,
    TelemetryFrame,
    VehicleClass,
    Weather,
    WheelState,
)
from . import f1_packets as P
from .base import Decoder, RawPacket, TransportAdapter, UdpSource

if TYPE_CHECKING:
    from ..config import Config

log = logging.getLogger(__name__)

# F1-Radreihenfolge RL, RR, FL, FR → unsere FL, FR, RL, RR
_WHEEL_MAP = (2, 3, 0, 1)

CAPS = frozenset({
    Capability.POSITION, Capability.LAP_TIMES, Capability.SECTOR_TIMES, Capability.LAP_DISTANCE,
    Capability.OPPONENTS, Capability.OPPONENT_LAP_TIMES, Capability.OPPONENT_TYRES, Capability.OPPONENT_PIT,
    Capability.NATIVE_GAPS, Capability.TEAMMATE, Capability.FUEL, Capability.TYRE_TEMPS, Capability.TYRE_PRESSURE,
    Capability.TYRE_WEAR, Capability.TYRE_COMPOUND, Capability.TYRE_AGE, Capability.BRAKE_TEMPS, Capability.DAMAGE,
    Capability.ERS, Capability.DRS, Capability.INPUTS, Capability.TRACK_POSITION_XY, Capability.FLAGS,
    Capability.SECTOR_FLAGS, Capability.SAFETY_CAR, Capability.PIT_STATUS, Capability.PENALTIES,
    Capability.WEATHER, Capability.WEATHER_FORECAST, Capability.SESSION_TIME, Capability.TOTAL_LAPS,
    Capability.START_LIGHTS, Capability.FLASHBACK, Capability.PIT_WINDOW, Capability.WHEEL_SLIP,
    Capability.YAW_RATE,
})

_RESULT = {0: CarStatus.INACTIVE, 1: CarStatus.INACTIVE, 2: CarStatus.ACTIVE, 3: CarStatus.FINISHED,
           4: CarStatus.DNF, 5: CarStatus.DSQ, 6: CarStatus.NOT_CLASSIFIED, 7: CarStatus.RETIRED}


def _name(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("utf-8", errors="replace").strip()


class F1Decoder(Decoder):
    sim = SimId.F1_25

    def __init__(self) -> None:
        super().__init__()
        self.layout: P.Layout | None = None
        self.bad_format_warned: set[int] = set()
        self._reset_session(0)

    def _reset_session(self, uid: int) -> None:
        self.session_uid = uid
        self.player_idx = 0
        self.session: dict[str, Any] = {}
        self.marshal: list[tuple[float, int]] = []
        self.forecast: list[tuple[int, int, int, int, int, int]] = []
        self.laps: dict[int, tuple[Any, ...]] = {}
        self.parts: dict[int, tuple[Any, ...]] = {}
        self.num_active = 0
        self.status: dict[int, tuple[Any, ...]] = {}
        self.tele: dict[int, tuple[Any, ...]] = {}
        self.damage: tuple[Any, ...] | None = None
        self.motion: dict[int, tuple[float, float, float]] = {}
        self.player_motion: tuple[Any, ...] | None = None
        self.motion_ex: tuple[float, ...] | None = None
        self.best_laps: dict[int, float] = {}
        self.last_sectors: dict[int, list[float | None]] = {}
        self.prev_lap_num: dict[int, int] = {}
        self.prev_s12: dict[int, tuple[float | None, float | None]] = {}
        self.events: list[GameEvent] = []
        self.lights = 0
        self.lights_out = False
        self.chequered = False
        self.session_ended = False
        self.sc_returning = False
        self.session_time = 0.0
        self.paused = False
        self.game_year = 25
        self.fmt = 2025

    # ----------------------------------------------------------- Parsen
    def feed(self, packet: RawPacket) -> TelemetryFrame | None:
        data = packet.data
        if len(data) < P.HEADER.size:
            return None
        (fmt, year, _maj, _min, _pver, pid, uid, stime, _frame, _oframe, player, _sec) = P.HEADER.unpack_from(data)
        layout = P.LAYOUTS.get(fmt)
        if layout is None:
            if fmt not in self.bad_format_warned:
                self.bad_format_warned.add(fmt)
                log.warning("F1: unbekanntes UDP-Format %s – bitte im Spiel 2024, 2025 oder 2026 wählen", fmt)
            return None
        expected = layout.sizes.get(pid)
        if expected is None or len(data) != expected:
            if expected is not None:
                raise ValueError(f"Paket {pid} hat {len(data)} statt {expected} Bytes (Format {fmt})")
            return None
        if uid != self.session_uid and uid != 0:
            if self.session_uid:
                self.events.append(GameEvent(GameEventKind.SESSION_STARTED, data={"reason": "new_session_uid"}))
            pending = self.events
            self._reset_session(uid)
            self.events = pending
        self.layout = layout
        self.fmt = fmt
        self.game_year = year
        self.sim = SimId.F1_24 if year == 24 else SimId.F1_25
        self.player_idx = player if player < layout.num_cars else 0
        self.session_time = stime
        handler = _HANDLERS.get(pid)
        if handler is not None:
            handler(self, data, layout)
        if pid == P.PacketId.LAP_DATA:
            return self._build(packet.t)
        return None

    def _session(self, d: bytes, lay: P.Layout) -> None:
        head = P.SESSION_HEAD.unpack_from(d, 29)
        (weather, track_temp, air_temp, total_laps, track_len, stype, track_id, formula, time_left, duration,
         pit_limit, paused, _spec, _spec_idx, _sli, n_zones) = head
        self.marshal = [P.MARSHAL.unpack_from(d, 48 + i * 5) for i in range(min(21, n_zones))]
        sc_status, _net, n_fc = struct.unpack_from("<BBB", d, 153)
        self.forecast = [P.FORECAST.unpack_from(d, 156 + i * 8) for i in range(min(64, n_fc))]
        ideal, latest, rejoin = struct.unpack_from("<BBB", d, 682)
        s2, s3 = struct.unpack_from("<ff", d, 745)
        self.paused = bool(paused)
        self.session.update(weather=weather, track_temp=track_temp, air_temp=air_temp, total_laps=total_laps,
                            track_len=track_len, stype=stype, track_id=track_id, formula=formula,
                            time_left=time_left, duration=duration, pit_limit=pit_limit, sc=sc_status,
                            ideal=ideal, latest=latest, rejoin=rejoin, s2=s2, s3=s3)

    def _lap_data(self, d: bytes, lay: P.Layout) -> None:
        st = lay.lap
        for i in range(lay.num_cars):
            row = st.unpack_from(d, 29 + i * st.size)
            lap_num = row[14]
            cur = self.prev_s12.get(i, (None, None))
            prev_num = self.prev_lap_num.get(i)
            if prev_num is not None and lap_num == prev_num + 1:
                s1, s2 = cur
                last = row[0] / 1000.0 if row[0] else None
                s3 = (last - s1 - s2) if (last and s1 and s2) else None
                self.last_sectors[i] = [s1, s2, s3]
            if prev_num is not None and lap_num != prev_num:
                cur = (None, None)
            self.prev_lap_num[i] = lap_num
            s1 = P.ms(row[3], row[2]) if (row[2] or row[3]) else cur[0]
            s2 = P.ms(row[5], row[4]) if (row[4] or row[5]) else cur[1]
            self.prev_s12[i] = (s1, s2)
            self.laps[i] = row

    def _participants(self, d: bytes, lay: P.Layout) -> None:
        self.num_active = d[29]
        st = lay.participant
        for i in range(lay.num_cars):
            self.parts[i] = st.unpack_from(d, 30 + i * st.size)

    def _telemetry(self, d: bytes, lay: P.Layout) -> None:
        st = lay.telemetry
        for i in range(lay.num_cars):
            self.tele[i] = st.unpack_from(d, 29 + i * st.size)

    def _status(self, d: bytes, lay: P.Layout) -> None:
        st = lay.status
        for i in range(lay.num_cars):
            self.status[i] = st.unpack_from(d, 29 + i * st.size)

    def _damage(self, d: bytes, lay: P.Layout) -> None:
        st = lay.damage
        self.damage = st.unpack_from(d, 29 + self.player_idx * st.size)

    def _motion(self, d: bytes, lay: P.Layout) -> None:
        st = lay.motion
        for i in range(lay.num_cars):
            row = st.unpack_from(d, 29 + i * st.size)
            self.motion[i] = (row[0], row[1], row[2])
            if i == self.player_idx:
                self.player_motion = row

    def _motion_ex(self, d: bytes, lay: P.Layout) -> None:
        n = (len(d) - 29) // 4
        self.motion_ex = struct.unpack_from(f"<{n}f", d, 29)

    def _history(self, d: bytes, lay: P.Layout) -> None:
        car, _num_laps, _stints, best_lap_num = struct.unpack_from("<BBBB", d, 29)
        if 0 < best_lap_num <= 100:
            row = P.LAP_HISTORY.unpack_from(d, 36 + (best_lap_num - 1) * 14)
            if row[0] > 0:
                self.best_laps[car] = row[0] / 1000.0

    def _event(self, d: bytes, lay: P.Layout) -> None:
        code = d[29:33].decode("ascii", errors="replace")
        ev: GameEvent | None = None
        off = 33
        if code == "SSTA":
            self.lights_out = False
            self.chequered = False
            self.session_ended = False
            ev = GameEvent(GameEventKind.SESSION_STARTED)
        elif code == "SEND":
            self.session_ended = True
            ev = GameEvent(GameEventKind.SESSION_ENDED)
        elif code == "FTLP":
            idx, lap_time = struct.unpack_from("<Bf", d, off)
            ev = GameEvent(GameEventKind.FASTEST_LAP, idx, {"lap_time": lap_time})
        elif code == "RTMT":
            idx = d[off]
            ev = GameEvent(GameEventKind.RETIREMENT, idx, {"reason": d[off + 1] if self.fmt >= 2025 else None})
        elif code == "DRSE":
            ev = GameEvent(GameEventKind.DRS_ENABLED)
        elif code == "DRSD":
            ev = GameEvent(GameEventKind.DRS_DISABLED)
        elif code == "TMPT":
            ev = GameEvent(GameEventKind.TEAMMATE_IN_PITS, d[off])
        elif code == "CHQF":
            self.chequered = True
            ev = GameEvent(GameEventKind.CHEQUERED_FLAG)
        elif code == "RCWN":
            ev = GameEvent(GameEventKind.RACE_WINNER, d[off])
        elif code == "PENA":
            ptype, infr, idx, other, secs, lap, places = struct.unpack_from("<7B", d, off)
            ev = GameEvent(GameEventKind.PENALTY, idx, {
                "penalty_type": P.PENALTY_TYPES.get(ptype, str(ptype)), "time": secs, "lap": lap,
                "infringement": "track_limits" if infr in P.CORNER_CUTTING_INFRINGEMENTS else str(infr),
                "other": other, "places_gained": places})
        elif code == "SPTP":
            idx, speed = struct.unpack_from("<Bf", d, off)
            ev = GameEvent(GameEventKind.SPEED_TRAP, idx, {"speed_kmh": speed})
        elif code == "STLG":
            self.lights = d[off]
            ev = GameEvent(GameEventKind.START_LIGHTS, data={"num_lights": self.lights})
        elif code == "LGOT":
            self.lights_out = True
            self.lights = 0
            ev = GameEvent(GameEventKind.LIGHTS_OUT)
        elif code == "DTSV":
            ev = GameEvent(GameEventKind.DRIVE_THROUGH_SERVED, d[off])
        elif code == "SGSV":
            ev = GameEvent(GameEventKind.STOP_GO_SERVED, d[off])
        elif code == "FLBK":
            frame_id, sess_time = struct.unpack_from("<If", d, off)
            ev = GameEvent(GameEventKind.FLASHBACK, data={"frame_id": frame_id, "session_time": sess_time})
        elif code == "BUTN":
            (status,) = struct.unpack_from("<I", d, off)
            ev = GameEvent(GameEventKind.BUTTON, data={"status": status})
        elif code == "RDFL":
            ev = GameEvent(GameEventKind.RED_FLAG)
        elif code == "OVTK":
            a, b = struct.unpack_from("<BB", d, off)
            ev = GameEvent(GameEventKind.OVERTAKE, a, {"overtaken": b})
        elif code == "SCAR":
            sc_type, sc_event = struct.unpack_from("<BB", d, off)
            kind = {0: "none", 1: "sc", 2: "vsc", 3: "formation"}.get(sc_type, "sc")
            what = {0: "deployed", 1: "returning", 2: "returned", 3: "resume"}.get(sc_event, "deployed")
            self.sc_returning = what == "returning"
            ev = GameEvent(GameEventKind.SAFETY_CAR, data={"type": kind, "event": what})
        elif code == "COLL":
            a, b = struct.unpack_from("<BB", d, off)
            ev = GameEvent(GameEventKind.COLLISION, a, {"other": b})
        if ev is not None and code != "BUTN":
            self.events.append(ev)

    # ------------------------------------------------------------ Frame
    def _build(self, t: float) -> TelemetryFrame | None:
        lay = self.layout
        if lay is None or not self.laps:
            return None
        s = self.session
        pidx = self.player_idx
        player_team = self.parts.get(pidx, (0, 0, 0, -1))[3]
        cars: list[CarState] = []
        for i in range(lay.num_cars):
            lap = self.laps.get(i)
            if lap is None:
                continue
            result = _RESULT.get(lap[26], CarStatus.ACTIVE)
            if result is CarStatus.INACTIVE and i != pidx:
                continue
            part = self.parts.get(i)
            team_id = part[3] if part else -1
            team_name, color = P.TEAMS.get(team_id, ("", ""))
            status = self.status.get(i)
            compound = None
            age = None
            if status is not None:
                vis = status[14]
                compound = (P.VISUAL_COMPOUND_F2 if s.get("formula") == 2 else P.VISUAL_COMPOUND_F1).get(vis)
                age = status[15]
            pit_state = {0: PitState.ON_TRACK, 1: PitState.PIT_LANE, 2: PitState.PIT_BOX}.get(lap[15], PitState.ON_TRACK)
            if lap[25] == 0:
                pit_state = PitState.GARAGE
            delta_front = P.ms(lap[7], lap[6]) if (lap[6] or lap[7]) else None
            delta_leader = P.ms(lap[9], lap[8]) if (lap[8] or lap[9]) else None
            if lap[13] == 1:
                delta_leader, delta_front = 0.0, None
            sect = [P.ms(lap[3], lap[2]) if (lap[2] or lap[3]) else None,
                    P.ms(lap[5], lap[4]) if (lap[4] or lap[5]) else None, None]
            pos = self.motion.get(i)
            tele = self.tele.get(i)
            name = _name(part[7]) if part else ""
            car = CarState(
                idx=i,
                driver_name=name or f"Auto {i + 1}",
                team=team_name,
                team_color=color,
                car_number=str(part[5]) if part else "",
                position=lap[13] or None,
                lap=lap[14],
                laps_completed=max(0, lap[14] - 1),
                lap_distance_m=lap[10],
                lap_pct=(lap[10] / s["track_len"]) if s.get("track_len") and lap[10] >= 0 else None,
                total_distance_m=lap[11] if lap[11] > 0 or lap[14] > 1 else None,
                sector=lap[17] + 1,
                current_lap_s=lap[1] / 1000.0,
                last_lap_s=lap[0] / 1000.0 if lap[0] else None,
                best_lap_s=self.best_laps.get(i),
                sector_times_s=sect,
                last_sector_times_s=self.last_sectors.get(i, []),
                gap_to_leader_s=delta_leader,
                gap_to_ahead_s=delta_front,
                pit_state=pit_state,
                pit_stops=lap[16],
                tyre_compound=compound,
                tyre_age_laps=age,
                status=result,
                penalties_s=float(lap[19]),
                is_player=i == pidx,
                is_teammate=i != pidx and team_id == player_team and team_id >= 0,
                x=pos[0] if pos else None,
                z=pos[2] if pos else None,
                speed_ms=(tele[0] / 3.6) if tele else None,
            )
            cars.append(car)
        session = self._session_info(t)
        player = self._player_state()
        events, self.events = self.events, []
        return TelemetryFrame(seq=self.next_seq(), t=t, sim=self.sim, capabilities=CAPS, session=session,
                              player=player, player_idx=pidx, cars=cars, events=events,
                              raw_extra={"packet_format": self.fmt,
                                         "pit_rejoin_position": s.get("rejoin") or None})

    def _session_info(self, t: float) -> SessionInfo:
        s = self.session
        stype = SessionType(P.SESSION_TYPES.get(s.get("stype", 0), "unknown"))
        sc = {0: SafetyCar.NONE, 1: SafetyCar.SC, 2: SafetyCar.VSC, 3: SafetyCar.FORMATION}.get(s.get("sc", 0),
                                                                                                SafetyCar.NONE)
        if sc in (SafetyCar.SC, SafetyCar.VSC) and self.sc_returning:
            sc = SafetyCar.ENDING
        if sc is SafetyCar.NONE:
            self.sc_returning = False
        me = self.laps.get(self.player_idx)
        phase = SessionPhase.RUNNING
        if self.session_ended:
            phase = SessionPhase.FINISHED
        elif self.chequered:
            phase = SessionPhase.CHECKERED
        elif stype is SessionType.RACE and not self.lights_out:
            if sc is SafetyCar.FORMATION:
                phase = SessionPhase.FORMATION
            elif me is not None and me[14] <= 1 and (me[11] <= 5 or self.lights > 0):
                phase = SessionPhase.GRID
        if self.paused:
            phase = SessionPhase.PAUSED
        if me is not None and me[25] == 0 and phase is SessionPhase.RUNNING:
            phase = SessionPhase.GARAGE
        track_len = float(s["track_len"]) if s.get("track_len") else None
        # Gelb pro Sektor aus den Marshal-Zonen
        yellow: set[int] = set()
        flag = Flag.GREEN if phase is SessionPhase.RUNNING else Flag.NONE
        s2 = s.get("s2") or 0
        s3 = s.get("s3") or 0
        for start, zflag in self.marshal:
            if zflag == 3 and track_len:
                d = start * track_len
                yellow.add(1 if d < s2 else 2 if d < s3 else 3)
            elif zflag == 4:
                flag = Flag.RED
        status = self.status.get(self.player_idx)
        if status is not None:
            fia = status[16]
            if fia == 2:
                flag = Flag.BLUE
            elif fia == 3:
                flag = Flag.YELLOW
        if self.chequered:
            flag = Flag.CHEQUERED
        weather_code = s.get("weather", 0)
        forecasts = []
        for fc in self.forecast:
            fstype, offset, fweather, ftt, _ftc, fat, _fac, rain = fc
            if fstype != s.get("stype"):
                continue
            forecasts.append(ForecastSample(float(offset), P.WEATHER.get(fweather, "unknown"), float(rain),
                                            float(fat), float(ftt)))
        rain_now = P.RAIN_INTENSITY.get(weather_code, 0.0)
        weather = Weather(condition=P.WEATHER.get(weather_code, "unknown"), air_temp_c=s.get("air_temp"),
                          track_temp_c=s.get("track_temp"), rain_intensity=rain_now,
                          track_wetness=None, forecast=forecasts)
        uid = f"f1-{self.session_uid}"
        track_id = s.get("track_id", -1)
        return SessionInfo(
            sim=self.sim,
            track_name=P.TRACKS.get(track_id, f"Strecke {track_id}"),
            track_id=P.TRACK_KEYS.get(track_id, f"track{track_id}"),
            track_length_m=track_len,
            session_type=stype,
            phase=phase,
            session_uid=uid,
            total_laps=s.get("total_laps") if stype is SessionType.RACE else None,
            duration_s=float(s["duration"]) if s.get("duration") else None,
            time_remaining_s=float(s["time_left"]) if s.get("time_left") and stype is not SessionType.RACE else None,
            session_time_s=self.session_time,
            num_cars=self.num_active or len(self.laps),
            vehicle_class=VehicleClass.FORMULA,
            car_name={0: "F1", 1: "F1 Classic", 2: "F2"}.get(s.get("formula", 0), "F1"),
            flag=flag,
            safety_car=sc,
            yellow_sectors=sorted(yellow),
            weather=weather,
            pit_speed_limit_kmh=float(s["pit_limit"]) if s.get("pit_limit") else None,
            pit_window_ideal_lap=s.get("ideal") or None,
            pit_window_latest_lap=s.get("latest") or None,
            start_lights=self.lights if not self.lights_out else None,
            paused=self.paused,
        )

    def _player_state(self) -> PlayerState:
        i = self.player_idx
        tele = self.tele.get(i)
        st = self.status.get(i)
        lap = self.laps.get(i)
        dmg = self.damage
        p = PlayerState(fuel_unit="kg")
        if tele is not None:
            p.speed_ms = tele[0] / 3.6
            p.throttle = tele[1]
            p.steering = tele[2]
            p.brake = tele[3]
            p.clutch = tele[4] / 100.0
            p.gear = tele[5]
            p.rpm = float(tele[6])
            p.drs_active = bool(tele[7])
            brakes = tele[10:14]
            surf = tele[14:18]
            inner = tele[18:22]
            press = tele[23:27]
            wheels = []
            for ours in range(4):
                f1i = _WHEEL_MAP[ours]
                wheels.append(WheelState(temp_surface_c=float(surf[f1i]), temp_core_c=float(inner[f1i]),
                                         pressure_kpa=float(press[f1i]) * 6.894757,
                                         brake_temp_c=float(brakes[f1i])))
            p.wheels = wheels
        if st is not None:
            p.fuel_l = st[5]
            p.fuel_capacity_l = st[6] or None
            p.fuel_remaining_laps_game = st[7]
            p.max_rpm = float(st[8]) or None
            p.drs_available = bool(st[11])
            p.pit_limiter = bool(st[4])
            p.tc_active = bool(st[0])
            p.abs_active = bool(st[1])
            vis = st[14]
            p.tyre_compound = (P.VISUAL_COMPOUND_F2 if self.session.get("formula") == 2
                               else P.VISUAL_COMPOUND_F1).get(vis)
            p.tyre_age_laps = st[15]
            p.ers_store_pct = min(100.0, st[19] / 4_000_000.0 * 100.0)
            p.ers_mode = P.ERS_MODES.get(st[20])
            deployed = st[24] if self.fmt >= 2026 else st[23]
            p.ers_deployed_this_lap_pct = min(100.0, deployed / 4_000_000.0 * 100.0)
        if dmg is not None:
            wear = dmg[0:4]
            tyre_dmg = dmg[4:8]
            base = 16 if self.fmt >= 2025 else 12
            for ours in range(4):
                f1i = _WHEEL_MAP[ours]
                if ours < len(p.wheels):
                    p.wheels[ours].wear_pct = float(wear[f1i])
                    p.wheels[ours].damage_pct = float(tyre_dmg[f1i])
                    p.wheels[ours].puncture = tyre_dmg[f1i] >= 95
            p.damage = Damage(front_wing_left=float(dmg[base]), front_wing_right=float(dmg[base + 1]),
                              rear_wing=float(dmg[base + 2]), floor=float(dmg[base + 3]),
                              diffuser=float(dmg[base + 4]), sidepod=float(dmg[base + 5]),
                              gearbox=float(dmg[base + 8]), engine=float(dmg[base + 9]))
        if lap is not None:
            p.lap_invalid = bool(lap[18])
            p.warnings = lap[20]
        if self.player_motion is not None:
            m = self.player_motion
            if self.fmt >= 2026:
                p.lat_accel = m[12] / 1000.0 * 9.81
                p.long_accel = m[13] / 1000.0 * 9.81
            else:
                p.lat_accel = m[12] * 9.81
                p.long_accel = m[13] * 9.81
        if self.motion_ex is not None and len(self.motion_ex) >= 37:
            slip = self.motion_ex[16:20]  # wheelSlipRatio (RL, RR, FL, FR)
            for ours in range(4):
                if ours < len(p.wheels):
                    p.wheels[ours].slip_ratio = float(slip[_WHEEL_MAP[ours]])
            p.yaw_rate = float(self.motion_ex[37])  # angularVelocityY (Hochachse)
        return p


_HANDLERS = {
    P.PacketId.SESSION: F1Decoder._session,
    P.PacketId.LAP_DATA: F1Decoder._lap_data,
    P.PacketId.PARTICIPANTS: F1Decoder._participants,
    P.PacketId.CAR_TELEMETRY: F1Decoder._telemetry,
    P.PacketId.CAR_STATUS: F1Decoder._status,
    P.PacketId.CAR_DAMAGE: F1Decoder._damage,
    P.PacketId.MOTION: F1Decoder._motion,
    P.PacketId.MOTION_EX: F1Decoder._motion_ex,
    P.PacketId.SESSION_HISTORY: F1Decoder._history,
    P.PacketId.EVENT: F1Decoder._event,
}


class F1Adapter(TransportAdapter):
    sim_id = SimId.F1_25
    key = "f1"
    display_name = "EA SPORTS F1 24/25"
    capabilities = CAPS

    def __init__(self, config: Config, port: int | None = None, bind: str | None = None):
        sec = config.section("f1")
        self.port = int(port or sec.get("port", 20777))
        host = str(bind or sec.get("bind", "0.0.0.0"))
        self.forward = [_parse_addr(a) for a in sec.get("forward", [])]
        self._fwd_sock: socket.socket | None = None
        if self.forward:
            self._fwd_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        src = UdpSource(host, self.port, "udp", on_datagram=self._forward if self.forward else None)
        super().__init__(config, src, F1Decoder())
        self.hint = (f"Keine Daten auf UDP {self.port}. Im Spiel: Einstellungen → Telemetrie → UDP an, IP dieses "
                     f"PCs, Port {self.port}. Linux-Firewall: sudo firewall-cmd --add-port={self.port}/udp")

    def _forward(self, data: bytes, addr: tuple[str, int]) -> None:
        if self._fwd_sock is None:
            return
        for target in self.forward:
            try:
                self._fwd_sock.sendto(data, target)
            except OSError:
                pass

    async def frames(self) -> AsyncIterator[TelemetryFrame]:  # type: ignore[override]
        async for frame in super().frames():
            self.sim_id = frame.sim
            self.display_name = "EA SPORTS F1 24" if frame.sim is SimId.F1_24 else "EA SPORTS F1 25"
            yield frame

    @classmethod
    async def detect(cls, config: Config, timeout: float = 1.0) -> bool:
        port = int(config.get("f1.port", 20777))
        return await udp_probe(port, timeout, lambda d: len(d) >= 29 and d[0:2] in (b"\xe8\x07", b"\xe9\x07",
                                                                                     b"\xea\x07"))


def _parse_addr(s: str) -> tuple[str, int]:
    host, _, port = s.rpartition(":")
    return host or "127.0.0.1", int(port)


async def udp_probe(port: int, timeout: float, accept: Any) -> bool:
    """Lauscht kurz auf einem UDP-Port (für Auto-Erkennung/doctor)."""
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[bool] = loop.create_future()

    class _P(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
            if not fut.done() and accept(data):
                fut.set_result(True)

    try:
        transport, _ = await loop.create_datagram_endpoint(_P, local_addr=("0.0.0.0", port), reuse_port=True)
    except OSError:
        return False
    try:
        return await asyncio.wait_for(fut, timeout)
    except TimeoutError:
        return False
    finally:
        transport.close()

