# Gran Turismo 7 – UDP „Simulator Interface“ (Telemetrie)

Stand der Recherche: 2026-10-28. Polyphony Digital veröffentlicht für dieses Protokoll keine offizielle Dokumentation. Alles hier stammt aus Reverse Engineering der Community, vor allem von Nenkai/PDTools. Wo Quellen sich widersprechen oder nur eine Quelle etwas behauptet, steht **UNVERIFIED**.

**Wie geprüft wurde.** Die Quelltexte von PDTools (C#), gt7dashboard, gt7telemetry, granturismo, gt7-udp (C++), gt7-telemetry-relay und gt-telem (PyPI) wurden direkt gelesen. Die Offsets wurden mit `struct.calcsize` nachgerechnet (siehe §3.6). Die Salsa20-Varianten wurden lokal gegeneinander getestet: `pycryptodome` und eine reine Python-Implementierung liefern identischen Keystream. Es gab **keinen** Mitschnitt von einer echten PS5. Die Inhalte gelten also nur so weit, wie die Quellen übereinstimmen.

---

## 1. Transport, Ports und Heartbeat

| Richtung | Protokoll | Port | Inhalt |
|---|---|---|---|
| PC → PS5 | UDP | **33739** (Ziel auf der PS5) | Heartbeat, 1 ASCII-Byte |
| PS5 → PC | UDP | **33740** (der PC bindet und lauscht hier) | verschlüsseltes Telemetriepaket |

- GT Sport und GT6 nutzen 33339 und 33340. Der Code von PDTools nennt diese Ports als `ReceivePortDefault` und `BindPortDefault`.
- Die PS5 sendet an die **Absenderadresse des Heartbeats**, also an die IP des PCs. Den Port 33740 erwarten alle Tools gleich. Ob die PS5 auch an den Quellport des Heartbeats sendet oder fest an 33740, ist **UNVERIFIED**. Alle Tools senden den Heartbeat deshalb über denselben Socket, der auf 33740 gebunden ist. Das sollten wir genauso machen.
- Die PS5 schickt **ein Paket pro Simulations-Tick, also 60 Hz**. PDTools kommentiert das mit „Will send a packet per tick – 60fps“. gt7dashboard rechnet Rundenzeiten mit `ticks * 1000/60` hoch.
- Man kann Telemetrie jederzeit starten, auch beim Booten des Spiels oder mitten im Rennen (PDTools).

### 1.1 Heartbeat-Varianten

Das Payload-Byte des Heartbeats legt das Paketformat fest:

| Heartbeat (ASCII) | Paketgröße (Bytes) | XOR-Konstante für die Nonce | Ab GT7-Version | Quellen |
|---|---|---|---|---|
| `"A"` (0x41) | **296** (0x128) | `0xDEADBEAF` | 1.00 | PDTools, gt7dashboard, gt7telemetry, gt7-udp, gt-telem |
| `"B"` (0x42) | **316** (0x13C) | `0xDEADBEEF` | 1.42 | PDTools, gt7-udp, gt-telem |
| `"~"` (0x7E) | **344** (0x158) | `0x55FABB4F` | 1.42 | PDTools, gt7-udp, gt-telem |
| `"C"` (0x43) | **368** (0x170) | `0xDEADBEEF` (wie B) | ca. 1.68 (**UNVERIFIED**) | gt7-udp, granturismo, gt7-telemetry-relay |

- Die Konstante für A ist wirklich `0xDEADBEAF` mit **A**F, kein Tippfehler. gt7dashboard kommentiert sie extra: „Notice DEADBEAF, not DEADBEEF“.
- Bei Packet C stimmen drei unabhängige Implementierungen überein: gt7-udp, granturismo und gt7-telemetry-relay. PDTools kennt C noch nicht. Ein Go-Projekt (`those-who-run/paddock`, Issue #30) nennt C noch „unconfirmed“. Laut gt7-telemetry-relay ist B ein Präfix von C, das mit demselben Keystream verschlüsselt ist: Kürzt man ein verschlüsseltes C-Paket auf 316 Bytes, erhält man ein gültiges B-Paket. Das passt zur gleichen XOR-Konstante. Status von C insgesamt: **wahrscheinlich, aber UNVERIFIED**.
- Das Paketformat erkennt man **an der Länge des Datagramms**. gt7-udp und PDTools prüfen die Länge genau so. Vor dem Entschlüsseln wählt man daraus die XOR-Konstante: 296 → A, 316 → B, 344 → ~, 368 → C.
- Laut README von gt7-udp gibt es Einschränkungen: B „not available in Sport Mode“, ~ „not available in Replays“. Das ist **UNVERIFIED**. Ein Fallback auf A muss also möglich sein.
- Aus PDTools: „Game might send a packet of 0x94 if not using 'A' type heartbeat?“. Das ist ein offenes TODO. 0x94 = 148 Bytes, **UNVERIFIED**.

### 1.2 Heartbeat-Intervall und Timeout

Die Quellen widersprechen sich. Hier ist, was die einzelnen Implementierungen tatsächlich tun:

| Quelle | Strategie |
|---|---|
| PDTools (Nenkai) | sendet alle **10 s** (`SendDelaySeconds = 10`) |
| granturismo, gt-telem | alle **10 s** |
| gt7dashboard, gt7telemetry | alle **100 empfangenen Pakete** (ca. 1,7 s) und zusätzlich nach einem Socket-Timeout von 10 s |
| gt7-telemetry-relay | Standard alle **1 s** |
| gt7-udp, README | „The console will expect a heartbeat every 1000 packets (around 16 seconds) or else the connection will cease.“ Ältere Fassungen sprachen laut Suchergebnis von 100 Paketen bzw. 1,6 s. |

Folgerung: Der tatsächliche Timeout auf der PS5 liegt **wahrscheinlich bei etwa 1000 Paketen bzw. 16 s**. Clients mit 10-s-Takt laufen stabil, also muss er über 10 s liegen. Das ist **UNVERIFIED**. Empfehlung für Chef: **alle 1–5 s einen Heartbeat senden**, zusätzlich sofort, wenn 1 s lang kein Paket kam. Heartbeats kosten nichts. Ein Heartbeat mit einem anderen Byte sollte das Format umschalten. Ob das sofort passiert, ist **UNVERIFIED**, also das Format nach der Paketlänge erkennen und nicht vom gesendeten Byte ausgehen.

Mehrere Clients: gt7-telemetry-relay empfiehlt, dass nur **ein** Prozess Heartbeats sendet und die anderen ihre eigene Heartbeat-Funktion abschalten. Ob die PS5 an mehrere Abonnenten gleichzeitig streamt, ist **UNVERIFIED**. Für Chef heißt das: Chef sollte selbst als Relay arbeiten und Pakete bei Bedarf weiterleiten.

---

## 2. Verschlüsselung (Salsa20/20)

### 2.1 Schlüssel

- Quellstring: `"Simulator Interface Packet GT7 ver 0.0"`, ASCII, **38 Bytes**. Lokal nachgezählt. Die Angabe „39 bytes“ im README von gt7-udp ist falsch.
- Salsa20 nimmt 32-Byte-Schlüssel, darum werden die **ersten 32 Bytes** verwendet:
  ```
  KEY = b"Simulator Interface Packet GT7 v"   # == b"Simulator Interface Packet GT7 ver 0.0"[:32]
  ```
  Der Konstruktor `Salsa20(byte[] key, int keyLength)` in PDTools kürzt mit `if (keyLength > 32) keyLength = 32` ebenfalls auf 32 Bytes.
- Variante: Standard-Salsa20 mit 20 Runden (Bernstein), 256-Bit-Schlüssel, **64-Bit-Nonce** und 64-Bit-Blockzähler ab 0.
- Zum Vergleich: GT Sport nutzt `"Simulator Interface Packet ver 0.0"[:32]` (gt-telem), GT6 einen eigenen Cryptor.

### 2.2 Nonce (IV)

1. Aus dem **verschlüsselten** Datagramm die 4 Bytes an Offset **0x40** als `uint32` lesen, **Little Endian**. Das ergibt `iv1`, den „Seed“. Dieser Bereich liegt im Klartext vor, der Sender überschreibt ihn nach der Verschlüsselung.
2. `iv2 = iv1 ^ XOR_KONSTANTE`, die Konstante hängt vom Paketformat ab (siehe §1.1).
3. Die 8-Byte-Nonce ist `struct.pack("<II", iv2, iv1)`: **zuerst iv2, dann iv1, beide Little Endian**.
4. Das **gesamte** Datagramm einschließlich Offset 0 und 0x40 mit dem Keystream ab Blockzähler 0 XOR-verknüpfen.
5. Nach dem Entschlüsseln ist der Inhalt an 0x40–0x43 Müll. PDTools überspringt ihn mit „Skip IV“. Den Seed muss man also **vorher** aus dem Ciphertext lesen.

### 2.3 Magic-Prüfung

- Die Bytes 0x00–0x03 nach dem Entschlüsseln, als `int32` Little Endian gelesen, ergeben `0x47375330`. Auf dem Draht steht `b"0S7G"`, als Big-Endian-Text gelesen „G7S0“.
- Stimmt die Magic nicht, wurde die falsche XOR-Konstante gewählt oder das Paket ist kaputt → verwerfen.
- Zum Vergleich: GT6 auf der PS3 sendet Big Endian mit der Magic `0x30533647` („G6S0“).

### 2.4 Python-Bibliotheken für Salsa20

| Paket (pip) | Salsa20? | Lizenz | Bemerkung |
|---|---|---|---|
| `pycryptodome` (`from Crypto.Cipher import Salsa20`) | **Ja** | BSD-2-Clause, Teile Public Domain bzw. Unlicense | `Salsa20.new(key=32 Bytes, nonce=8 Bytes).decrypt(buf)`. Von gt7dashboard, gt7telemetry und gt-telem genutzt. Lokal mit Version 4.2.0 getestet. Wheels mit C-Code für alle Plattformen. **Empfehlung.** |
| `pycryptodomex` (`from Cryptodome.Cipher import Salsa20`) | Ja | wie oben | dieselbe API, eigener Namespace und darum konfliktfrei neben PyCrypto |
| `cryptography` (pyca) | **Nein** | Apache-2.0 / BSD-3-Clause | Lokal mit Version 50.0.2 geprüft: `algorithms.ChaCha20` ist vorhanden, `algorithms.Salsa20` nicht. ChaCha20 ist mit Salsa20 **nicht** kompatibel. |
| `salsa20` (keybase/python-salsa20, ctypes auf NaCl) | theoretisch ja (`Salsa20_xor(msg, nonce, key)`) | BSD-3-Clause | **Unter Python ≥ 3.12 kaputt**: Version 0.3.0 macht `import imp`, und dieses Modul wurde entfernt (lokal reproduziert mit `ModuleNotFoundError`). Nicht verwenden. |
| `PyNaCl` | nur XSalsa20 (`crypto_stream_xsalsa20` bzw. SecretBox), kein reines Salsa20 mit 8-Byte-Nonce | Apache-2.0 | ungeeignet |
| reines Python | ja | eigener Code | Siehe §2.6. Lokal gemessen: **ca. 0,54 ms je 368-Byte-Paket** unter CPython 3.13. Das sind etwa 3 % CPU bei 60 Hz und damit als Fallback ohne Abhängigkeiten tauglich. granturismo (MIT) arbeitet genau so ganz ohne Abhängigkeiten. |

### 2.5 Referenzimplementierung (pycryptodome)

```python
import struct
from Crypto.Cipher import Salsa20

GT7_KEY = b"Simulator Interface Packet GT7 ver 0.0"[:32]
MAGIC_GT7 = 0x47375330  # b"0S7G" on the wire
XOR_BY_SIZE = {296: 0xDEADBEAF, 316: 0xDEADBEEF, 344: 0x55FABB4F, 368: 0xDEADBEEF}

def decrypt_gt7(datagram: bytes) -> bytes | None:
    xor = XOR_BY_SIZE.get(len(datagram))
    if xor is None:
        return None
    iv1 = struct.unpack_from("<I", datagram, 0x40)[0]
    nonce = struct.pack("<II", iv1 ^ xor, iv1)
    plain = Salsa20.new(key=GT7_KEY, nonce=nonce).decrypt(datagram)
    if struct.unpack_from("<I", plain, 0)[0] != MAGIC_GT7:
        return None
    return plain
```

### 2.6 Fallback in reinem Python (Skizze, lokal gegen pycryptodome verifiziert)

```python
import struct
_M = 0xFFFFFFFF
def _rotl(v, c): return ((v << c) & _M) | (v >> (32 - c))
def _qr(x, a, b, c, d):
    x[b] ^= _rotl((x[a] + x[d]) & _M, 7);  x[c] ^= _rotl((x[b] + x[a]) & _M, 9)
    x[d] ^= _rotl((x[c] + x[b]) & _M, 13); x[a] ^= _rotl((x[d] + x[c]) & _M, 18)
def _block(k, n, ctr):
    s = [0x61707865, *k[0:4], 0x3320646E, *n, ctr & _M, ctr >> 32,
         0x79622D32, *k[4:8], 0x6B206574]
    x = s[:]
    for _ in range(10):  # 20 rounds = 10 double rounds
        _qr(x, 0, 4, 8, 12); _qr(x, 5, 9, 13, 1); _qr(x, 10, 14, 2, 6); _qr(x, 15, 3, 7, 11)
        _qr(x, 0, 1, 2, 3);  _qr(x, 5, 6, 7, 4);  _qr(x, 10, 11, 8, 9); _qr(x, 15, 12, 13, 14)
    return struct.pack("<16I", *((a + b) & _M for a, b in zip(x, s)))
def salsa20_xor(data: bytes, key: bytes, nonce: bytes) -> bytes:
    k, n, out = struct.unpack("<8I", key), struct.unpack("<2I", nonce), bytearray()
    for i in range(0, len(data), 64):
        ks = _block(k, n, i // 64)
        out += bytes(a ^ b for a, b in zip(data[i:i + 64], ks))
    return bytes(out)
```

---

## 3. Paketlayout (nach dem Entschlüsseln)

Allgemein gilt: **Little Endian**, keine Ausrichtung, Struct-Präfix `<`. Typen: `f` = float32, `i` = int32, `I` = uint32, `h` = int16, `H` = uint16, `B` = uint8, `4s` = 4 Bytes ASCII. Feldnamen folgen PDTools, in Klammern Alternativnamen aus anderen Quellen.

### 3.1 Packet A (296 Bytes, Heartbeat `"A"`)

| Offset (hex/dez) | Größe | struct | Feld | Bedeutung / Einheit |
|---|---|---|---|---|
| 0x00 / 0 | 4 | `i` | `magic` | `0x47375330` („G7S0“) |
| 0x04 / 4 | 12 | `3f` | `position` x, y, z | Weltposition in m |
| 0x10 / 16 | 12 | `3f` | `velocity` x, y, z | Weltgeschwindigkeit in m/s je Achse |
| 0x1C / 28 | 12 | `3f` | `rotation` | PDTools liest 0x1C–0x2B als **Quaternion** (4 floats). gt7dashboard, gt7telemetry und gt7-udp lesen 0x1C–0x27 als pitch/yaw/roll, Bereich −1…1. granturismo nennt es „real part of the unit quaternion“. Werte im Bereich −1…1 sprechen für Quaternion-Komponenten (**UNVERIFIED**). |
| 0x28 / 40 | 4 | `f` | `orientation_relative_to_north` | Bei PDTools die 4. Quaternion-Komponente. gt7-udp: 1.0 = Norden … 0.0 = Süden. granturismo: „heading in radians“. Nach der Quaternion-Lesart ist das wohl w bzw. cos(yaw/2) (**UNVERIFIED**). |
| 0x2C / 44 | 12 | `3f` | `angular_velocity` x, y, z | Drehrate in rad/s |
| 0x38 / 56 | 4 | `f` | `body_height` | Karosseriehöhe in m. gt7dashboard rechnet ×1000 für mm. |
| 0x3C / 60 | 4 | `f` | `engine_rpm` | Motordrehzahl in 1/min |
| 0x40 / 64 | 4 | `4x` | `iv` / Seed | im Ciphertext der Nonce-Seed, nach dem Entschlüsseln bedeutungslos |
| 0x44 / 68 | 4 | `f` | `gas_level` | Kraftstoff in Litern (0…`gas_capacity`). Bei E-Autos Ladung, ändert sich durch Rekuperation. gt7telemetry zeigt es dann als kWh an (**UNVERIFIED**). |
| 0x48 / 72 | 4 | `f` | `gas_capacity` | Tankgröße. 100 bei den meisten Autos, 5 bei Karts, 0 bei E-Autos. |
| 0x4C / 76 | 4 | `f` | `speed` (`meters_per_second`) | Geschwindigkeit über Grund in m/s, ×3,6 = km/h |
| 0x50 / 80 | 4 | `f` | `turbo_boost` | Ladedruck mit Offset +1: Wert − 1 = Anzeige in ×100 kPa (bar). 1,0 entspricht 0. |
| 0x54 / 84 | 4 | `f` | `oil_pressure` | Öldruck in bar (ungefähr) |
| 0x58 / 88 | 4 | `f` | `water_temperature` | in °C, laut PDTools und gt7-udp **immer 85** |
| 0x5C / 92 | 4 | `f` | `oil_temperature` | in °C, laut PDTools und gt7-udp **immer 110**. Achtung: granturismo vertauscht in seinem PACKET.md 85 und 110, die Offsets stimmen aber. |
| 0x60 / 96 | 16 | `4f` | `tyre_surface_temp` FL, FR, RL, RR | Reifenoberflächentemperatur in °C |
| 0x70 / 112 | 4 | `i` | `packet_id` | laufende Paketnummer, zum Sortieren und Verwerfen von Duplikaten oder alten Paketen |
| 0x74 / 116 | 2 | `h` | `lap_count` | aktuelle Runde, siehe §4 |
| 0x76 / 118 | 2 | `h` | `laps_in_race` (`total_laps`) | Gesamtrunden des Rennens |
| 0x78 / 120 | 4 | `i` | `best_lap_time` | in ms, **−1** wenn nicht gesetzt |
| 0x7C / 124 | 4 | `i` | `last_lap_time` | in ms, **−1** wenn nicht gesetzt |
| 0x80 / 128 | 4 | `i` | `time_of_day` (`day_progression`) | Tageszeit auf der Strecke in ms seit Mitternacht. Läuft mit dem Zeitfaktor der Strecke, als Uhr fürs Rundentiming also ungeeignet. |
| 0x84 / 132 | 2 | `h` | `pre_race_start_position` (`race_start_position`) | PDTools und gt7-udp: Startposition bzw. Qualifying-Platz, −1 nach dem Start. granturismo hat dagegen gemessen, dass der Wert in einer Lobby **live die aktuelle Position** zeigt. gt7dashboard nennt ihn `current_position`. Das Verhalten hängt vom Modus ab (**UNVERIFIED**). |
| 0x86 / 134 | 2 | `h` | `num_cars_at_pre_race` | Anzahl Autos, −1 nach dem Start (laut PDTools). granturismo maß einen konstanten Wert (3) in einer Lobby. |
| 0x88 / 136 | 2 | `h` | `min_alert_rpm` | Drehzahl, ab der die Schaltanzeige bzw. das Rev-Limiter-Warnlicht anfängt |
| 0x8A / 138 | 2 | `h` | `max_alert_rpm` | obere Grenze der Warnung (gt7dashboard: „rpm rev limiter“) |
| 0x8C / 140 | 2 | `h` | `calculated_max_speed` | theoretische Höchstgeschwindigkeit mit der aktuellen Getriebeeinstellung. PDTools gibt km/h aus, granturismo schreibt m/s, wahrscheinlich ist km/h (**UNVERIFIED**). |
| 0x8E / 142 | 2 | `H` | `flags` | Bitfeld, siehe §3.2 |
| 0x90 / 144 | 1 | `B` | `gear` | **unteres Nibble** (`& 0x0F`) = aktueller Gang, 0 = Rückwärtsgang. **Oberes Nibble** (`>> 4`) = vorgeschlagener Gang, 15 = kein Vorschlag. granturismo behandelt auch 15 im unteren Nibble als Leerlauf (**UNVERIFIED**). |
| 0x91 / 145 | 1 | `B` | `throttle` | Gaspedal 0…255 |
| 0x92 / 146 | 1 | `B` | `brake` | Bremspedal 0…255 |
| 0x93 / 147 | 1 | `B` | `unknown_0x93` | Padding, immer 0 |
| 0x94 / 148 | 12 | `3f` | `road_plane` x, y, z | Normalenvektor der Fahrbahn unter dem Auto (Neigung bzw. Überhöhung) |
| 0xA0 / 160 | 4 | `f` | `road_plane_distance` | Abstand des Fahrzeugursprungs zur Fahrbahnebene. Entspricht am Boden etwa `body_height`. |
| 0xA4 / 164 | 16 | `4f` | `wheel_rps` FL, FR, RL, RR | Raddrehzahl in **rad/s**, trotz des Namens „RevPerSecond“. Umfangsgeschwindigkeit = `radius × rad/s`. ÷2π ergibt Umdrehungen pro Sekunde. |
| 0xB4 / 180 | 16 | `4f` | `tyre_radius` FL, FR, RL, RR | Reifenradius in m |
| 0xC4 / 196 | 16 | `4f` | `suspension_height` FL, FR, RL, RR | Federweg bzw. Höhe der Aufhängung. Einheit unklar, granturismo: 0 = ausgefedert, 1 = eingefedert (**UNVERIFIED**). |
| 0xD4 / 212 | 32 | `8f` | `unknown_0xD4[8]` | reserviert. PDTools: „server does not set that“, immer 0. |
| 0xF4 / 244 | 4 | `f` | `clutch_pedal` | Kupplungspedal 0,0…1,0 |
| 0xF8 / 248 | 4 | `f` | `clutch_engagement` | Einkupplungsgrad 0,0…1,0 |
| 0xFC / 252 | 4 | `f` | `rpm_from_clutch_to_gearbox` | Drehzahl am Getriebeeingang. Bei geschlossener Kupplung und eingelegtem Gang gleich `engine_rpm`. |
| 0x100 / 256 | 4 | `f` | `transmission_top_speed` | Top-Speed-Einstellung des Getriebes, ausgedrückt als Übersetzungsverhältnis |
| 0x104 / 260 | 32 | `8f` | `gear_ratios[8]` | Übersetzungen der Gänge 1–8, unbenutzte sind 0. PDTools liest nur 7 und behandelt den 8. als „empty_or_gearRatio8“. |
| 0x124 / 292 | 4 | `i` | `car_code` (`car_id`) | interne Fahrzeug-ID. **Fehler im Spiel**: Bei Autos mit 9 oder mehr Gängen kopiert GT7 die Übersetzungen ohne Grenzprüfung, sodass `car_code` überschrieben wird. Beispiel aus PDTools: Lexus LC500 mit 10 Gängen. |
| **Ende 0x128 / 296** | | | | |

### 3.2 `flags` (uint16 an 0x8E)

| Bit | Maske | Name (PDTools) | Bedeutung |
|---|---|---|---|
| 0 | 0x0001 | `CarOnTrack` | Auto steht auf der Strecke oder in der Box, die Daten gelten. gt7dashboard nutzt das als `in_race`. |
| 1 | 0x0002 | `Paused` | Simulation pausiert. In Online-Modi pausiert das Pausemenü die Simulation **nicht**. |
| 2 | 0x0004 | `LoadingOrProcessing` | Laden oder Nachbearbeitung, Werte unzuverlässig |
| 3 | 0x0008 | `InGear` | Gang eingelegt. Laut PDTools „needs more investigation“, granturismo: False beim Schalten und im Leerlauf. |
| 4 | 0x0010 | `HasTurbo` | Auto hat Turbolader |
| 5 | 0x0020 | `RevLimiterBlinkAlertActive` | Drehzahlwarnung blinkt |
| 6 | 0x0040 | `HandBrakeActive` | Handbremse gezogen |
| 7 | 0x0080 | `LightsActive` | Licht an |
| 8 | 0x0100 | `HighBeamActive` | Fernlicht |
| 9 | 0x0200 | `LowBeamActive` | Abblendlicht |
| 10 | 0x0400 | `ASMActive` | ASM (Stabilitätskontrolle) greift ein |
| 11 | 0x0800 | `TCSActive` | Traktionskontrolle greift ein |
| 12–15 | | – | unbenutzt, immer 0 |

### 3.3 Zusatzfelder von Packet B (316 Bytes, Heartbeat `"B"`)

B = A (296 Bytes) + 20 Bytes:

| Offset | Größe | struct | Feld | Bedeutung |
|---|---|---|---|---|
| 0x128 / 296 | 4 | `f` | `wheel_rotation` | Lenkradwinkel in rad |
| 0x12C / 300 | 4 | `f` | `filler_float_fb` / `steering_angular_velocity` | PDTools: „FillerFloatFB“, Bedeutung offen. gt7-udp: Winkelgeschwindigkeit des Lenkrads in rad/s (**UNVERIFIED**). |
| 0x130 / 304 | 4 | `f` | `sway` | Querbeschleunigung (X). Einheit wahrscheinlich m/s² (**UNVERIFIED**). |
| 0x134 / 308 | 4 | `f` | `heave` | Vertikalbeschleunigung (Y), Einheit wie oben (**UNVERIFIED**) |
| 0x138 / 312 | 4 | `f` | `surge` | Längsbeschleunigung (Z), Einheit wie oben (**UNVERIFIED**) |
| **Ende 0x13C / 316** | | | | |

### 3.4 Zusatzfelder von Packet „~“ (344 Bytes, Heartbeat `"~"`)

„~“ = B (316 Bytes) + 28 Bytes. Laut gt7-udp wird dieser Bereich noch erforscht.

| Offset | Größe | struct | Feld | Bedeutung |
|---|---|---|---|---|
| 0x13C / 316 | 1 | `B` | `throttle_filtered` | gefiltertes Gas 0…255, also das, was nach den Fahrhilfen beim Auto ankommt (**UNVERIFIED**) |
| 0x13D / 317 | 1 | `B` | `brake_filtered` | gefilterte Bremse 0…255 (**UNVERIFIED**) |
| 0x13E / 318 | 1 | `B` | `unknown_0x13E` | PDTools: `Unk3` mit dem Kommentar „4 = electric“, Debug-Name „CarType (?)“ (**UNVERIFIED**) |
| 0x13F / 319 | 1 | `B` | `unknown_0x13F` | PDTools: `NoGasConsumption`, also wohl ein Flag für „kein Spritverbrauch“ (**UNVERIFIED**) |
| 0x140 / 320 | 16 | `4f` | `torque_vectors[4]` | PDTools: `Unk5` (Vector4). gt7-udp: Torque Vectoring je Rad, positiv = Antrieb, negativ = Bremsen bzw. Rekuperation (**UNVERIFIED**) |
| 0x150 / 336 | 4 | `f` | `energy_recovery` | aktuell rekuperierte Energie (Einheit offen, **UNVERIFIED**) |
| 0x154 / 340 | 4 | `f` | `unknown_0x154` | unbekannt |
| **Ende 0x158 / 344** | | | | |

### 3.5 Zusatzfelder von Packet C (368 Bytes, Heartbeat `"C"`, **UNVERIFIED**)

C = „~“ (344 Bytes) + 24 Bytes. Belegt durch die Header-Datei von gt7-udp (`struct PacketC : public PacketTilda`) und den Parser von granturismo. Das README von gt7-udp schreibt noch `public PacketB`. Das ergäbe aber 340 statt 368 Bytes und ist daher veraltet.

| Offset | Größe | struct | Feld | Bedeutung |
|---|---|---|---|---|
| 0x158 / 344 | 4 | `4s` | `surface_type[4]` | Untergrund je Rad (FL, FR, RL, RR) als ASCII: `T` Asphalt, `C` Curb, `D` Dirt, `G` Gras, `S` Sand, `s` Schnee |
| 0x15C / 348 | 4 | `i` | `current_lap_time` | **laufende Rundenzeit in ms**, −1 ohne gezeitete Runde |
| 0x160 / 352 | 8 | `2f` | `wheel_steering_angle` FL, FR | Lenkwinkel der Vorderräder in rad |
| 0x168 / 360 | 4 | `f` | `wheel_base` | Radstand in m, „dynamisch“, links vorn bis links hinten |
| 0x16C / 364 | 4 | `4s` | `car_category` | NUL-terminiert, z. B. `GR3\0`, `GR4\0`, `GRB\0`, `GRN\0`, `GRX\0`. Bei Straßenautos leer. |
| **Ende 0x170 / 368** | | | | |

### 3.6 Struct-Formate und Größenprüfung

```python
FMT_A = ("<i3f3f4f3fff4x"   # 0x00 magic .. 0x40 iv           -> 68 B  (0x44)
         "7f4f"            # gas..oil temp, 4 tyre temps     -> +44 = 112 (0x70)
         "ihhiii"          # packet_id .. time_of_day        -> +20 = 132 (0x84)
         "hhhhhH"          # start pos .. flags              -> +12 = 144 (0x90)
         "BBBB"            # gear, throttle, brake, pad      -> +4  = 148 (0x94)
         "3ff"             # road plane + distance           -> +16 = 164 (0xA4)
         "4f4f4f"          # wheel rps, radius, suspension   -> +48 = 212 (0xD4)
         "8f"              # unknown                         -> +32 = 244 (0xF4)
         "ffff"            # clutch, engage, rpm, top speed  -> +16 = 260 (0x104)
         "8fi")            # gear ratios, car code           -> +36 = 296 (0x128)
FMT_B     = FMT_A + "5f"            # +20 = 316 (0x13C)
FMT_TILDE = FMT_B + "4B4fff"        # +4+16+4+4 = +28 = 344 (0x158)
FMT_C     = FMT_TILDE + "4si3f4s"   # +4+4+12+4 = +24 = 368 (0x170)
```

Im Rechenschritt bestätigt: `struct.calcsize` liefert 296, 316, 344 und 368 und stimmt damit mit allen Quellen überein. Die Summe für A im Einzelnen: 4 + 12 + 12 + 16 + 12 + 4 + 4 + 4 + 28 + 16 + 4 + 2 + 2 + 4 + 4 + 4 + 2·5 + 2 + 4·1 + 12 + 4 + 16·3 + 32 + 4·4 + 32 + 4 = **296**.

Die Offsets aller Quellen stimmen paarweise überein: PDTools, gt7dashboard, gt7telemetry, gt7-udp und granturismo, Letzteres für A und C. Die einzigen Unterschiede liegen in der **Deutung** von 0x1C–0x2B, 0x84 und 0x8C, nicht im Layout.

---

## 4. Bekannte Einschränkungen

- **Keine Gegnerdaten.** Es gibt nur Daten zum eigenen Auto: keine Positionen, Abstände oder Rundenzeiten der anderen. Ausnahme ist 0x84 als eigene Position, je nach Modus (siehe oben). Nenkai im README von PDTools: „This is **NOT** a telemetry source, quite some stuff is missing“. Gedacht ist es für Motion-Rigs.
- **Kein Reifenverschleiß** und kein Reifendruck, keine Bremstemperaturen, kein Schaden. Vorhanden sind nur Oberflächentemperaturen.
- **Wasser- und Öltemperatur sind konstant** (85 bzw. 110) und damit nutzlos.
- **Keine Strecken-ID.** Die Strecke lässt sich nur heuristisch aus den Positionsdaten ableiten, z. B. mit `gt7trackdetect.py` aus gt7telemetry, das sind Bounding-Boxes in einer CSV-Datei.
- **Keine Fahrzeugnamen**, nur `car_code`. Die Zuordnung braucht eine Tabelle, z. B. die Liste im README von gt7-udp oder `cars.json` in granturismo.
- **Laufende Rundenzeit fehlt in A, B und ~.** Ableiten lässt sie sich so:
  - beim Wechsel von `lap_count` den Paketzähler zurücksetzen, dann `ticks × 1000/60` ms. gt7dashboard korrigiert bei jedem Rundenwechsel mit `last_lap_time` nach.
  - alternativ per Wanduhr, ungenauer wegen Netz-Jitter.
  - Bei Pause (Flag Bit 1) nicht weiterzählen.
  - Nur Packet C liefert `current_lap_time` direkt (**UNVERIFIED**).
- **Verhalten von `lap_count`**: Vor dem Start oder außerhalb eines Rennens ist der Wert 0 oder −1 (0xFFFF). granturismo behandelt 0xFFFF als None, gt7dashboard prüft `> 0`. Beim Überqueren der Ziellinie wird er hochgezählt, und `last_lap_time` bzw. `best_lap_time` werden im selben Paket aktualisiert. Die erste Runde ab dem Start ist Runde 1. Das genaue Verhalten bei Out-Laps, Zeitfahren und Rückspulen ist **UNVERIFIED**. In Zeitfahren ist `laps_in_race` vermutlich 0 (**UNVERIFIED**).
- **Replays** senden ebenfalls Telemetrie. gt7dashboard hat dafür `always_record_data`. Das Format ~ ist dabei angeblich nicht verfügbar (**UNVERIFIED**).
- **60 Hz fest** über UDP ohne Bestätigung. Verlorene Pakete erkennt man an Lücken in `packet_id`, alte Pakete an `packet_id` ≤ dem zuletzt gesehenen Wert. Bei einem neuen Heartbeat-Zyklus oder Reconnect kann der Zähler neu beginnen. gt7dashboard setzt seinen Vergleichswert nach einem Timeout auf 0 zurück.
- `calculated_max_speed` und `car_code` können bei Spezialfällen falsch sein, z. B. bei Autos mit 9 oder mehr Gängen.

---

## 5. Praktische Einrichtung

- **Im Spiel ist keine Einstellung nötig.** Es gibt keinen Menüschalter, der Stream startet allein durch den Heartbeat. Keine der untersuchten Quellen nennt eine Spieleinstellung.
- **IP der PS5**: Die PS5 nimmt Heartbeats nur per Unicast an ihre IP sicher an. Die IP findet man auf der PS5 unter *Einstellungen → Netzwerk → Verbindungsstatus anzeigen*, oder in der Client-Liste des Routers. Eine **DHCP-Reservierung** im Router ist empfehlenswert.
- **Automatische Erkennung**, zwei Wege:
  1. **PlayStation Device Discovery Protocol (DDP)**, so macht es gt-telem. UDP-Broadcast an Port **9302** (PS5) mit dem Payload `b"SRCH * HTTP/1.1\ndevice-discovery-protocol-version:00030010"`. Die PS5 antwortet mit einem HTTP-ähnlichen Text: `HTTP/1.1 200 Ok` bei eingeschaltetem Gerät, Statuscode **620** im Standby, dazu `host-type: PS5` usw. Die Absenderadresse der Antwort ist die IP der PS5. Die PS4 nutzt bei DDP üblicherweise Port 987. Das ist allgemeines Wissen über das PS-Remote-Play-Protokoll und **UNVERIFIED** im GT7-Kontext. Der DDP-Weg findet die Konsole, nicht das Spiel.
  2. **Heartbeat als Broadcast**, so macht es gt7dashboard, wenn keine IP gesetzt ist: `SO_BROADCAST` aktivieren und den Heartbeat an `255.255.255.255:33739` senden. Die PS5 antwortet mit Telemetrie an den PC, und `recvfrom()` liefert die IP der PS5, die man dann für Unicast-Heartbeats übernimmt. Ob jedes Netz bzw. jeder Router das zuverlässig durchlässt, ist **UNVERIFIED**. In Docker gehen Broadcasts standardmäßig nicht raus (README von gt7dashboard), dort braucht es `--network host` oder eine feste IP.
- **Firewall**: Auf dem PC **eingehendes UDP an Port 33740** zulassen. Unter Windows fragt die Defender-Firewall beim ersten `bind()`, dabei „Privates Netzwerk“ erlauben. Für DDP zusätzlich eingehende UDP-Antworten von Port 9302 zulassen. Ausgehend sind 33739 und, für Discovery, 9302 nötig.
- **Netzwerk**: PC und PS5 im selben Subnetz bzw. LAN. Bei Gast-WLANs oder WLAN mit Client-Isolation blockiert der Router. Über VPN oder NAT hinweg funktioniert es nur mit Portweiterleitung, sinnvoll ist das nicht.
- **Ein Abonnent**: Andere Tools wie SimHub oder gt7dashboard nicht gleichzeitig Heartbeats senden lassen. Wenn Chef selbst als Relay arbeitet und die Rohpakete an lokale Ports weiterleitet, wie gt7-telemetry-relay, können andere Tools parallel laufen.
- **Socket-Empfehlung**: einen UDP-Socket auf `0.0.0.0:33740` binden, `SO_REUSEADDR` setzen, Empfangspuffer ≥ 512 Bytes (C hat 368 Bytes, granturismo nimmt 0x200), Timeout ca. 1 s. Heartbeat **über denselben Socket** senden. Format an der Länge erkennen, dann entschlüsseln und die Magic prüfen.
- **Wahl des Formats für Chef**: zuerst `"~"` anfordern, das hat PDTools als Standard. Alternativ `"C"` für Untergrund und laufende Rundenzeit, da aber **UNVERIFIED**. Bei ausbleibenden Paketen oder unerwarteten Längen auf `"A"` zurückfallen.

---

## 6. Quellen

| Quelle | URL | Lizenz | Genutzt für |
|---|---|---|---|
| Nenkai/PDTools – `PDTools.SimulatorInterface` (`SimulatorInterfaceClient.cs`, `SimulatorPacket.cs`), `PDTools.Crypto/SimulationInterface/SimulatorInterfaceCryptorGT7.cs`, `Salsa20.cs` | https://github.com/Nenkai/PDTools | MIT | Primärquelle: Ports, Heartbeats A/B/~, XOR-Konstanten, Paketgrößen, Layout, Flags, Kürzen des Schlüssels |
| snipem/gt7dashboard – `gt7dashboard/gt7communication.py` | https://github.com/snipem/gt7dashboard | GPL-3.0 | Python-Referenz, Heartbeat alle 100 Pakete, Broadcast-Discovery, Rundenzeit über Ticks/60 |
| Bornhall/gt7telemetry – `gt7telemetry.py`, `gt7trackdetect.py` | https://github.com/Bornhall/gt7telemetry | GPL-3.0 | Python-Referenz (pycryptodome), Offsets, Track-Erkennung |
| chrshdl/granturismo – `PACKET.md`, `src/granturismo/security/*`, `model/packet.py`, `intake/feed.py` | https://github.com/chrshdl/granturismo | MIT | Python ohne Abhängigkeiten (eigenes Salsa20), Packet C mit Heartbeat `"C"`, Messungen zu 0x84 und 0x86 |
| MacManley/gt7-udp (Arduino/ESP32, C++) – `README.md`, `src/GT7UDPParser.{h,cpp}` | https://github.com/MacManley/gt7-udp | MIT | Strukturen A/B/~/C, XOR je Format inkl. C, Untergrundcodes, Aussage zum Timeout |
| ThrottleGeist/gt7-telemetry-relay | https://github.com/ThrottleGeist/gt7-telemetry-relay | MIT | Größen A/B/C, XOR für C, Betrieb als Relay mit nur einem Abonnenten |
| RaceCrewAI gt-telem (PyPI `gt-telem`, Version 1.2.1) – `net/crypto.py`, `net/device_discover.py` | https://pypi.org/project/gt-telem/ · https://github.com/RaceCrewAI | GPL-3.0 | XOR A/B/~, Heartbeat alle 10 s, DDP-Discovery (Port 9302, `SRCH`) |
| those-who-run/salsa PR #4, those-who-run/paddock Issues #30/#31 (Go) | https://github.com/those-who-run/salsa/pull/4 · https://github.com/those-who-run/paddock/issues/30 | nicht geprüft | XOR-Zuordnung A/B/~ bestätigt, C dort noch „unconfirmed“ |
| SHWotever/SimHub Issue #2254 | https://github.com/SHWotever/SimHub/issues/2254 | – (Issue-Text) | Hinweise auf B/~/C, surfaceType |
| GTPlanet-Thread „Overview of GT7 Telemetry Software“ | https://www.gtplanet.net/forum/threads/overview-of-gt7-telemetry-software.418011/ | – (Forum) | Historischer Kontext, Heartbeat-Verhalten |
| pycryptodome (`Crypto.Cipher.Salsa20`) | https://pypi.org/project/pycryptodome/ | BSD-2-Clause, Teile Public Domain/Unlicense | Empfohlene Salsa20-Implementierung |
| pyca/cryptography | https://pypi.org/project/cryptography/ | Apache-2.0 OR BSD-3-Clause | geprüft: **kein** Salsa20 |
| keybase/python-salsa20 (PyPI `salsa20` 0.3.0) | https://github.com/keybase/python-salsa20 | BSD-3-Clause | geprüft: unter Python ≥ 3.12 nicht importierbar (`imp`) |

**Hinweis zu den Lizenzen für Chef:** Code aus GPL-3.0-Projekten (gt7dashboard, gt7telemetry, gt-telem) **nicht kopieren**, falls Chef nicht GPL-lizenziert ist. Protokollfakten wie Offsets, Konstanten und Ports sind nicht urheberrechtlich geschützt und dürfen frei verwendet werden. MIT-Quellen (PDTools, granturismo, gt7-udp, relay) dürfen mit Lizenzhinweis übernommen werden. Die Codeschnipsel in diesem Dokument sind eigenständig geschrieben.
