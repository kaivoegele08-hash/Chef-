"""Gran Turismo 7 (PS5) – UDP "Simulator Interface".

* PC sendet Heartbeat an PS5:33739, Daten kommen auf PC:33740 (60 Hz).
* Pakete sind Salsa20-verschlüsselt (Schlüssel/Nonce siehe ``docs/protocols/gt7.md``).
* Keine Gegnerdaten, kein Verschleiß: der Engineer fokussiert Sprit, Reifen-
  temperaturen, Rundenzeiten, Konstanz und Coaching.
* Keine Strecken-ID: Streckenlänge wird aus der ersten Runde gelernt, die
  Strecke über die Start/Ziel-Position wiedererkannt.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import socket
import struct
import time
from typing import TYPE_CHECKING, Any

from ..core.frame import (
    Capability,
    CarState,
    Flag,
    GameEvent,
    GameEventKind,
    PitState,
    PlayerState,
    SessionInfo,
    SessionPhase,
    SessionType,
    SimId,
    TelemetryFrame,
    VehicleClass,
    WheelState,
)
from .base import Decoder, RawPacket, TransportAdapter, UdpSource

if TYPE_CHECKING:
    from ..config import Config

log = logging.getLogger(__name__)

KEY = b"Simulator Interface Packet GT7 ver 0.0"[:32]
MAGIC = 0x47375330
XOR_BY_SIZE = {296: 0xDEADBEAF, 316: 0xDEADBEEF, 344: 0x55FABB4F, 368: 0xDEADBEEF}
SEND_PORT = 33739
RECV_PORT = 33740

# Layout (nach dem Entschlüsseln), eigene Definition nach docs/protocols/gt7.md §3.6
FMT_A = "<i3f3f4f3fff4x7f4fihhiiihhhhhHBBBB3ff4f4f4f8fffff8fi"
FMT_B = FMT_A + "5f"
FMT_TILDE = FMT_B + "4B4fff"
FMT_C = FMT_TILDE + "4si3f4s"
STRUCTS = {296: struct.Struct(FMT_A), 316: struct.Struct(FMT_B), 344: struct.Struct(FMT_TILDE),
           368: struct.Struct(FMT_C)}
assert {k: v.size for k, v in STRUCTS.items()} == {296: 296, 316: 316, 344: 344, 368: 368}

# Feld-Indizes im entpackten Tupel
I_POS, I_VEL, I_ROT, I_ANGVEL = 1, 4, 7, 11
I_BODY, I_RPM, I_GAS, I_GASCAP, I_SPEED = 14, 15, 16, 17, 18
I_TYRE_T = 23
I_PACKET, I_LAP, I_TOTAL, I_BEST, I_LAST, I_TOD = 27, 28, 29, 30, 31, 32
I_STARTPOS, I_NUMCARS, I_MINRPM, I_MAXRPM, I_MAXSPEED, I_FLAGS = 33, 34, 35, 36, 37, 38
I_GEAR, I_THROTTLE, I_BRAKE = 39, 40, 41
I_WHEEL_RPS, I_RADIUS, I_SUSP = 47, 51, 55
I_CLUTCH = 67
I_CARCODE = 79
I_B_WHEELROT, I_B_SWAY, I_B_HEAVE, I_B_SURGE = 80, 82, 83, 84
I_C_CURLAP = 96
I_C_CATEGORY = 100

FLAG_ON_TRACK, FLAG_PAUSED, FLAG_LOADING = 0x1, 0x2, 0x4
FLAG_ASM, FLAG_TCS = 0x400, 0x800

CAPS = frozenset({
    Capability.POSITION, Capability.LAP_TIMES, Capability.FUEL, Capability.TYRE_TEMPS, Capability.INPUTS,
    Capability.TRACK_POSITION_XY, Capability.TOTAL_LAPS, Capability.WHEEL_SLIP, Capability.YAW_RATE,
    Capability.LAP_DISTANCE,
})


# ------------------------------------------------------------------ Salsa20

def _salsa20_xor_py(data: bytes, key: bytes, nonce: bytes) -> bytes:
    """Reines Python (Fallback ohne pycryptodome), Salsa20/20, 64-Bit-Nonce."""
    m = 0xFFFFFFFF
    k = struct.unpack("<8I", key)
    n = struct.unpack("<2I", nonce)

    def rotl(v: int, c: int) -> int:
        return ((v << c) & m) | (v >> (32 - c))

    out = bytearray(len(data))
    for blk in range((len(data) + 63) // 64):
        s = [0x61707865, k[0], k[1], k[2], k[3], 0x3320646E, n[0], n[1], blk & m, blk >> 32, 0x79622D32,
             k[4], k[5], k[6], k[7], 0x6B206574]
        x = s[:]
        for _ in range(10):
            for a, b, c, d in ((0, 4, 8, 12), (5, 9, 13, 1), (10, 14, 2, 6), (15, 3, 7, 11),
                               (0, 1, 2, 3), (5, 6, 7, 4), (10, 11, 8, 9), (15, 12, 13, 14)):
                x[b] ^= rotl((x[a] + x[d]) & m, 7)
                x[c] ^= rotl((x[b] + x[a]) & m, 9)
                x[d] ^= rotl((x[c] + x[b]) & m, 13)
                x[a] ^= rotl((x[d] + x[c]) & m, 18)
        ks = struct.pack("<16I", *((x[i] + s[i]) & m for i in range(16)))
        base = blk * 64
        chunk = data[base:base + 64]
        for i, byte in enumerate(chunk):
            out[base + i] = byte ^ ks[i]
    return bytes(out)


try:
    from Crypto.Cipher import Salsa20 as _Salsa20

    def salsa20_xor(data: bytes, key: bytes, nonce: bytes) -> bytes:
        return _Salsa20.new(key=key, nonce=nonce).decrypt(data)

    SALSA_BACKEND = "pycryptodome"
except ImportError:  # pragma: no cover - abhängig von der Installation
    salsa20_xor = _salsa20_xor_py
    SALSA_BACKEND = "python"


def decrypt(datagram: bytes) -> bytes | None:
    xor = XOR_BY_SIZE.get(len(datagram))
    if xor is None:
        return None
    iv1 = struct.unpack_from("<I", datagram, 0x40)[0]
    nonce = struct.pack("<II", iv1 ^ xor, iv1)
    plain = salsa20_xor(datagram, KEY, nonce)
    if struct.unpack_from("<I", plain, 0)[0] != MAGIC:
        return None
    return plain


def encrypt(plain: bytes, seed: int) -> bytes:
    """Gegenstück für Tests/Fixtures: verschlüsselt und setzt den Seed bei 0x40."""
    xor = XOR_BY_SIZE[len(plain)]
    nonce = struct.pack("<II", seed ^ xor, seed)
    cipher = bytearray(salsa20_xor(plain, KEY, nonce))
    cipher[0x40:0x44] = struct.pack("<I", seed)
    return bytes(cipher)


# ------------------------------------------------------------------ Decoder

class GT7Decoder(Decoder):
    sim = SimId.GT7

    def __init__(self) -> None:
        super().__init__()
        self.reset()

    def reset(self) -> None:
        self.last_packet_id: int | None = None
        self.lap: int | None = None
        self.lap_start_packet: int | None = None
        self.lap_dist = 0.0
        self.track_length: float | None = None
        self.track_key = "gt7_unknown"
        self.sf_pos: tuple[float, float] | None = None
        self.started = False
        self.prev_lap_count: int | None = None
        self.prev_t: float | None = None
        self.session_n = 1
        self.events: list[GameEvent] = []
        self.best_lap: float | None = None
        self.max_tyre_age = 0
        self.bad = 0
        self.format_size = 0
        self.chequered = False

    def feed(self, packet: RawPacket) -> TelemetryFrame | None:
        plain = decrypt(packet.data)
        if plain is None:
            self.bad += 1
            if self.bad in (1, 50):
                log.warning("GT7: Paket nicht entschlüsselbar (Länge %d)", len(packet.data))
            return None
        self.format_size = len(plain)
        v = STRUCTS[len(plain)].unpack(plain)
        pid = v[I_PACKET]
        if self.last_packet_id is not None:
            if pid <= self.last_packet_id and self.last_packet_id - pid < 600:
                return None  # Duplikat/alt
            if pid < self.last_packet_id - 600:
                log.info("GT7: Paketzähler neu gestartet")
        dt = 1.0 / 60.0 if self.last_packet_id is None else max(1, pid - self.last_packet_id) / 60.0
        dt = min(dt, 0.5)
        self.last_packet_id = pid
        return self._frame(v, packet.t, dt)

    def _frame(self, v: tuple[Any, ...], t: float, dt: float) -> TelemetryFrame:
        flags = v[I_FLAGS]
        on_track = bool(flags & FLAG_ON_TRACK)
        paused = bool(flags & FLAG_PAUSED)
        loading = bool(flags & FLAG_LOADING)
        lap_count = v[I_LAP] if v[I_LAP] not in (-1, 0xFFFF) else 0
        total = v[I_TOTAL] if v[I_TOTAL] > 0 else None
        speed = float(v[I_SPEED])
        x, _y, z = v[I_POS:I_POS + 3]
        events: list[GameEvent] = []

        # Rennneustart: Runde fällt zurück auf 0/1
        if self.prev_lap_count is not None and lap_count < self.prev_lap_count and lap_count <= 1:
            events.append(GameEvent(GameEventKind.SESSION_STARTED, data={"reason": "lap_reset"}))
            keep_len, keep_key, keep_sf = self.track_length, self.track_key, self.sf_pos
            self.reset()
            self.track_length, self.track_key, self.sf_pos = keep_len, keep_key, keep_sf
            self.session_n += 1
        # Start (Runde 0 → 1)
        if not self.started and lap_count >= 1 and (self.prev_lap_count or 0) == 0 and total:
            self.started = True
            events.append(GameEvent(GameEventKind.LIGHTS_OUT))
        if lap_count >= 1:
            self.started = True

        # Rundenwechsel → Distanz/Länge/Strecke lernen
        if self.lap is None:
            self.lap = lap_count
            self.lap_start_packet = v[I_PACKET]
        elif lap_count != self.lap:
            if lap_count == self.lap + 1 and self.lap >= 1 and self.lap_dist > 500:
                if self.track_length is None:
                    self.track_length = self.lap_dist
                else:
                    self.track_length = 0.8 * self.track_length + 0.2 * self.lap_dist
            if self.sf_pos is None and lap_count >= 1:
                self.sf_pos = (x, z)
                self.track_key = f"gt7_{int(round(x / 40.0))}_{int(round(z / 40.0))}"
            self.lap = lap_count
            self.lap_start_packet = v[I_PACKET]
            self.lap_dist = 0.0
        if on_track and not paused:
            self.lap_dist += speed * dt
        self.prev_lap_count = lap_count

        cur_lap = ((v[I_PACKET] - (self.lap_start_packet or v[I_PACKET])) / 60.0) if lap_count >= 1 else None
        if len(v) > I_C_CURLAP and v[I_C_CURLAP] >= 0:
            cur_lap = v[I_C_CURLAP] / 1000.0
        last = v[I_LAST] / 1000.0 if v[I_LAST] > 0 else None
        best = v[I_BEST] / 1000.0 if v[I_BEST] > 0 else None

        # Phase
        if loading:
            phase = SessionPhase.MENU
        elif paused:
            phase = SessionPhase.PAUSED
        elif total and not self.started:
            phase = SessionPhase.GRID
        elif total and lap_count > total:
            phase = SessionPhase.CHECKERED
        elif on_track:
            phase = SessionPhase.RUNNING
        else:
            phase = SessionPhase.MENU
        if total and lap_count > total and not self.chequered:
            self.chequered = True
            events.append(GameEvent(GameEventKind.CHEQUERED_FLAG))

        length = self.track_length
        lap_dist = self.lap_dist if lap_count >= 1 else None
        if length and lap_dist is not None:
            lap_dist = min(lap_dist, length)
        position = v[I_STARTPOS] if v[I_STARTPOS] > 0 else None
        me = CarState(
            idx=0, driver_name="Du", is_player=True, position=position, lap=max(lap_count, 0),
            laps_completed=max(0, lap_count - 1), lap_distance_m=lap_dist,
            lap_pct=(lap_dist / length) if (length and lap_dist is not None) else None,
            current_lap_s=cur_lap, last_lap_s=last, best_lap_s=best,
            pit_state=PitState.ON_TRACK if on_track else PitState.GARAGE, x=x, z=z, speed_ms=speed,
        )
        player = self._player(v, speed, flags)
        caps = set(CAPS)
        if position is not None:
            caps.add(Capability.POSITION)
        if not v[I_GASCAP]:
            caps.discard(Capability.FUEL)
        session = SessionInfo(
            sim=SimId.GT7,
            track_name="Gran Turismo 7",
            track_id=self.track_key,
            track_length_m=length,
            session_type=SessionType.RACE if total else SessionType.PRACTICE,
            phase=phase,
            session_uid=f"gt7-{self.session_n}",
            total_laps=total,
            session_time_s=0.0,
            num_cars=max(1, v[I_NUMCARS]) if v[I_NUMCARS] > 0 else 0,
            vehicle_class=_class(v[I_C_CATEGORY]) if len(v) > I_C_CATEGORY else VehicleClass.GT,
            car_name=f"GT7-Auto {v[I_CARCODE]}",
            flag=Flag.GREEN if phase is SessionPhase.RUNNING else Flag.NONE,
            paused=paused,
        )
        events, self.events = self.events + events, []
        return TelemetryFrame(seq=self.next_seq(), t=t, sim=SimId.GT7, capabilities=frozenset(caps),
                              session=session, player=player, player_idx=0, cars=[me], events=events,
                              raw_extra={"packet_id": v[I_PACKET], "car_code": v[I_CARCODE],
                                         "format": self.format_size})

    def _player(self, v: tuple[Any, ...], speed: float, flags: int) -> PlayerState:
        gear = v[I_GEAR] & 0x0F
        if gear == 0:
            gear = -1
        elif gear == 15:
            gear = 0
        wheels = []
        for i in range(4):
            rps = float(v[I_WHEEL_RPS + i])
            radius = float(v[I_RADIUS + i])
            wheel_speed = abs(rps) * radius
            slip = None
            if speed > 3.0 and radius > 0:
                slip = max(-1.0, min(1.0, (wheel_speed - speed) / speed))
            wheels.append(WheelState(temp_surface_c=float(v[I_TYRE_T + i]), slip_ratio=slip))
        steering = 0.0
        lat = long = None
        if len(v) > I_B_SURGE:
            steering = max(-1.0, min(1.0, float(v[I_B_WHEELROT]) / math.pi))
            lat = float(v[I_B_SWAY])
            long = float(v[I_B_SURGE])
        gas_cap = float(v[I_GASCAP]) or None
        return PlayerState(
            speed_ms=speed,
            gear=gear,
            rpm=float(v[I_RPM]),
            max_rpm=float(v[I_MAXRPM]) or None,
            throttle=v[I_THROTTLE] / 255.0,
            brake=v[I_BRAKE] / 255.0,
            clutch=float(v[I_CLUTCH]),
            steering=steering,
            fuel_l=float(v[I_GAS]) if gas_cap else None,
            fuel_capacity_l=gas_cap,
            wheels=wheels,
            tc_active=bool(flags & FLAG_TCS),
            abs_active=False,
            yaw_rate=float(v[I_ANGVEL + 1]),
            lat_accel=lat,
            long_accel=long,
        )


def _class(raw: bytes) -> VehicleClass:
    cat = raw.split(b"\0", 1)[0].decode("ascii", errors="ignore")
    if cat in ("GR1",):
        return VehicleClass.PROTOTYPE
    if cat in ("GR3", "GR4", "GRB"):
        return VehicleClass.GT
    if cat in ("GRN", ""):
        return VehicleClass.ROAD
    return VehicleClass.GT


# ------------------------------------------------------------------ Adapter

class GT7Adapter(TransportAdapter):
    sim_id = SimId.GT7
    key = "gt7"
    display_name = "Gran Turismo 7"
    capabilities = CAPS

    def __init__(self, config: Config, ps5_ip: str | None = None):
        sec = config.section("gt7")
        self.ps5_ip = (ps5_ip or str(sec.get("ps5_ip", ""))).strip()
        self.heartbeat_byte = str(sec.get("heartbeat", "~"))[:1] or "~"
        self.interval = float(sec.get("heartbeat_interval_s", 1.0))
        src = UdpSource("0.0.0.0", RECV_PORT, "udp", on_datagram=self._on_datagram)
        super().__init__(config, src, GT7Decoder())
        self._hb_task: asyncio.Task[None] | None = None
        self._last_rx: float | None = None
        self._fallback_done = False
        self.hint = ("Keine Daten von GT7 – PS5-IP prüfen (Einstellungen → PS5 suchen), Heartbeat läuft an "
                     f"Port {SEND_PORT}, Port {RECV_PORT}/udp in der Firewall offen? "
                     f"(sudo firewall-cmd --add-port={RECV_PORT}/udp)")

    def _on_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        self._last_rx = time.monotonic()
        if not self.ps5_ip and len(data) in XOR_BY_SIZE:
            self.ps5_ip = addr[0]
            log.info("GT7: PS5 gefunden unter %s", addr[0])
            self.config.set("gt7.ps5_ip", addr[0])

    async def start(self) -> None:
        await super().start()
        sock = self.source.transport.get_extra_info("socket") if self.source.transport else None  # type: ignore[attr-defined]
        if sock is not None:
            with contextlib.suppress(OSError):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self._hb_task = asyncio.create_task(self._heartbeat(), name="gt7-heartbeat")
        self.status_text = f"Heartbeat an {self.ps5_ip or 'Broadcast'}"

    async def stop(self) -> None:
        if self._hb_task:
            self._hb_task.cancel()
        await super().stop()

    async def _heartbeat(self) -> None:
        src: UdpSource = self.source  # type: ignore[assignment]
        started = time.monotonic()
        while True:
            target = self.ps5_ip or "255.255.255.255"
            src.send(self.heartbeat_byte.encode(), (target, SEND_PORT))
            # Fallback auf Format A, wenn "~"/"B"/"C" nach 6 s nichts liefert (z. B. Replays/Sport-Modus)
            if (not self._fallback_done and self._last_rx is None and time.monotonic() - started > 6
                    and self.heartbeat_byte != "A" and self.ps5_ip):
                log.info("GT7: keine Daten mit Heartbeat '%s' – versuche 'A'", self.heartbeat_byte)
                self.heartbeat_byte = "A"
                self._fallback_done = True
            # Wenn länger nichts kam, schneller pingen
            await asyncio.sleep(self.interval if self._last_rx and time.monotonic() - self._last_rx < 2 else 0.5)

    @classmethod
    async def detect(cls, config: Config, timeout: float = 1.0) -> bool:
        return False  # GT7 sendet nur auf Heartbeat – Erkennung erfolgt über die laufende Verbindung


async def discover_ps5(timeout: float = 2.5) -> str | None:
    """PlayStation Device Discovery (UDP 9302) + Heartbeat-Broadcast als Fallback."""
    loop = asyncio.get_running_loop()
    fut: asyncio.Future[str] = loop.create_future()

    class _P(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
            if not fut.done() and (data.startswith(b"HTTP/1.1") or len(data) in XOR_BY_SIZE):
                fut.set_result(addr[0])

    transport, _ = await loop.create_datagram_endpoint(_P, local_addr=("0.0.0.0", 0), allow_broadcast=True)
    try:
        transport.sendto(b"SRCH * HTTP/1.1\ndevice-discovery-protocol-version:00030010", ("255.255.255.255", 9302))
        transport.sendto(b"A", ("255.255.255.255", SEND_PORT))
        return await asyncio.wait_for(fut, timeout)
    except (TimeoutError, OSError):
        return None
    finally:
        transport.close()
