# Assetto Corsa (2014) – Telemetrie-Schnittstellen

Stand der Recherche: 2026-10-28. Gilt für das originale **Assetto Corsa** (Kunos, letzte Version 1.16.x).
ACC steht in [`acc.md`](acc.md), AC EVO ebenfalls dort.

Kurz zusammengefasst: AC hat **zwei** offizielle Telemetriewege:

1. **Shared Memory** (Windows Named File Mapping, nur auf demselben Rechner), drei Seiten: Physics, Graphics und Static.
2. **UDP Remote Telemetry** auf Port **9996**, gedacht für Tablets und Smartphones im LAN. Liefert ausschließlich das *fokussierte / eigene* Auto.

Daten zu **anderen Autos** liefert keine der beiden Schnittstellen (siehe Abschnitt 3). Dafür braucht man eine
In-Game-Python-App (`ac`-Modul) oder auf dem Dedicated Server das UDP-Server-Plugin.

Legende: **UNVERIFIED** = nicht gegen eine Primärquelle von Kunos geprüft, weil das Kunos-Forum, Scribd und
Google Docs aus der Recherche-Umgebung nicht erreichbar waren. Alle Offsets und Größen unten wurden **rechnerisch**
geprüft: mit eigenem Layout-Rechner, gegengeprüft mit `ctypes` (`_pack_ = 4`, wchar als `c_uint16`) und `struct.calcsize`.

---

## 1. Shared Memory

### 1.1 Grundlagen

| Seite | Map-Name (Win32) | Python `mmap`-Tagname | Struct | Größe (berechnet) |
|---|---|---|---|---|
| Physics | `Local\acpmf_physics` | `"Local\\acpmf_physics"` (oder `"acpmf_physics"`) | `SPageFilePhysics` | 580 B |
| Graphics | `Local\acpmf_graphics` | `"Local\\acpmf_graphics"` | `SPageFileGraphic` | 296 B |
| Static | `Local\acpmf_static` | `"Local\\acpmf_static"` | `SPageFileStatic` | 684 B |

- Layout: `#pragma pack(4)`, Little Endian. `int` ist 32 Bit, `float` ist IEEE-754 32 Bit.
- `wchar_t` ist unter Windows **2 Bytes (UTF-16LE)**. Strings haben feste Länge und sind NUL-terminiert. In Python liest man sie mit
  `raw.decode("utf-16-le").split("\x00", 1)[0]`.
  **Achtung:** `ctypes.c_wchar` ist auf Linux/macOS 4 Bytes groß. Für Tests unter Linux deshalb `c_uint16 * n` bzw. `struct` mit `"{2n}s"` verwenden.
- `wchar_t[33]` belegt 66 Bytes. Dadurch entstehen 2 Byte Padding vor dem nächsten 4-Byte-Feld. In den Tabellen ist das explizit als `(Padding)` eingetragen.
- Python unter Windows: `mmap.mmap(-1, size, tagname="Local\\acpmf_physics", access=mmap.ACCESS_READ)`.
  Empfehlung: **großzügig mappen** (z. B. 4096 B) und nur den bekannten Präfix parsen. Ältere oder neuere AC-Builds
  können längere oder kürzere Seiten haben, und Mods wie CSP erweitern sie unter Umständen. Die meisten Tools mappen 2048 B pro Seite.
- Aktualisierung: Die Physics-Seite wird pro Physikschritt aktualisiert (AC simuliert mit 333 Hz, **UNVERIFIED**), die Graphics-Seite pro Grafikframe, die Static-Seite einmal beim Laden der Session.
  Die Konsistenz prüft man über `packetId`: Physics/Graphics kopieren, dann prüfen, ob `packetId` noch gleich ist. Torn reads sind möglich.
- Speicher existiert nur, solange AC läuft (bzw. seit AC einmal gestartet wurde). Wenn `status == AC_OFF` ist, sind die Daten ungültig.
- Die Versionshinweise „seit 1.x“ beziehen sich auf die **Shared-Memory-/AC-Version** und stammen aus den Kommentaren in
  `mdjarv/assettocorsasharedmemory` sowie `simapi/acdata.h`. Diese spiegeln die Kunos-Referenz wider.
- Feldreihenfolge und Größe basieren auf dem verbreiteten `sim_info.py` („updated to AC 1.14.3“, u. a. in `albertowd/live-telemetry`),
  gegengeprüft mit CrewChief (`ACSData.cs`), `mdjarv` (C#) und `simapi` (C).

> **Offene Frage AC 1.16 (UNVERIFIED):** Ob das finale AC 1.16 im Physics-Struct nach `localVelocity` weitere Felder
> anhängt (in ACC folgen dort `P2PActivation`, `P2PStatus`, `currentMaxRpm`, `mz[4]`, `fx[4]`, `fy[4]`, `slipRatio[4]`, …),
> konnte nicht gegen die Kunos-PDF geprüft werden. `simapi/acdata.h` führt diese Felder in einem gemeinsamen AC/ACC-Header.
> CrewChief, `sim_info.py` (1.14.3) und `mdjarv` führen sie für AC nicht. **Die Tabellen unten enden deshalb am sicher belegten Präfix.**
> Weitere Felder wären nur angehängt und verschieben keine Offsets.

### 1.2 `SPageFilePhysics` (`acpmf_physics`)

Wird pro Physikschritt aktualisiert und betrifft nur das Spielerauto. Reihenfolge der Räder: FL, FR, RL, RR.

| Offset | C-Typ / Feld | Bytes | `struct` | Hinweis |
|---:|---|---:|---|---|
| 0 | `int packetId` | 4 | `i` |  |
| 4 | `float gas` | 4 | `f` | 0..1 |
| 8 | `float brake` | 4 | `f` | 0..1 |
| 12 | `float fuel` | 4 | `f` | Liter |
| 16 | `int gear` | 4 | `i` | 0=R, 1=N, 2=1. Gang |
| 20 | `int rpms` | 4 | `i` |  |
| 24 | `float steerAngle` | 4 | `f` |  |
| 28 | `float speedKmh` | 4 | `f` |  |
| 32 | `float velocity[3]` | 12 | `3f` | Weltkoordinaten |
| 44 | `float accG[3]` | 12 | `3f` |  |
| 56 | `float wheelSlip[4]` | 16 | `4f` | FL,FR,RL,RR |
| 72 | `float wheelLoad[4]` | 16 | `4f` | N |
| 88 | `float wheelsPressure[4]` | 16 | `4f` | psi |
| 104 | `float wheelAngularSpeed[4]` | 16 | `4f` | rad/s |
| 120 | `float tyreWear[4]` | 16 | `4f` |  |
| 136 | `float tyreDirtyLevel[4]` | 16 | `4f` |  |
| 152 | `float tyreCoreTemperature[4]` | 16 | `4f` | °C |
| 168 | `float camberRAD[4]` | 16 | `4f` |  |
| 184 | `float suspensionTravel[4]` | 16 | `4f` |  |
| 200 | `float drs` | 4 | `f` |  |
| 204 | `float tc` | 4 | `f` |  |
| 208 | `float heading` | 4 | `f` | rad |
| 212 | `float pitch` | 4 | `f` |  |
| 216 | `float roll` | 4 | `f` |  |
| 220 | `float cgHeight` | 4 | `f` |  |
| 224 | `float carDamage[5]` | 20 | `5f` | front, rear, left, right, centre |
| 244 | `int numberOfTyresOut` | 4 | `i` |  |
| 248 | `int pitLimiterOn` | 4 | `i` |  |
| 252 | `float abs` | 4 | `f` |  |
| 256 | `float kersCharge` | 4 | `f` |  |
| 260 | `float kersInput` | 4 | `f` |  |
| 264 | `int autoShifterOn` | 4 | `i` |  |
| 268 | `float rideHeight[2]` | 8 | `2f` | front, rear |
| 276 | `float turboBoost` | 4 | `f` | seit 1.5 |
| 280 | `float ballast` | 4 | `f` | seit 1.5 |
| 284 | `float airDensity` | 4 | `f` | seit 1.5 |
| 288 | `float airTemp` | 4 | `f` | seit 1.6 |
| 292 | `float roadTemp` | 4 | `f` | seit 1.6 |
| 296 | `float localAngularVel[3]` | 12 | `3f` | seit 1.6 |
| 308 | `float finalFF` | 4 | `f` | seit 1.6 |
| 312 | `float performanceMeter` | 4 | `f` | seit 1.7 |
| 316 | `int engineBrake` | 4 | `i` | seit 1.7 |
| 320 | `int ersRecoveryLevel` | 4 | `i` | seit 1.7 |
| 324 | `int ersPowerLevel` | 4 | `i` | seit 1.7 |
| 328 | `int ersHeatCharging` | 4 | `i` | seit 1.7 |
| 332 | `int ersIsCharging` | 4 | `i` | seit 1.7 |
| 336 | `float kersCurrentKJ` | 4 | `f` | seit 1.7 |
| 340 | `int drsAvailable` | 4 | `i` | seit 1.7 |
| 344 | `int drsEnabled` | 4 | `i` | seit 1.7 |
| 348 | `float brakeTemp[4]` | 16 | `4f` | seit 1.7 |
| 364 | `float clutch` | 4 | `f` | seit 1.10 |
| 368 | `float tyreTempI[4]` | 16 | `4f` | seit 1.10 (inner) |
| 384 | `float tyreTempM[4]` | 16 | `4f` | seit 1.10 (middle) |
| 400 | `float tyreTempO[4]` | 16 | `4f` | seit 1.10 (outer) |
| 416 | `int isAIControlled` | 4 | `i` | seit 1.10.2 |
| 420 | `float tyreContactPoint[4][3]` | 48 | `12f` | seit 1.11, [Rad][x,y,z] |
| 468 | `float tyreContactNormal[4][3]` | 48 | `12f` | seit 1.11 |
| 516 | `float tyreContactHeading[4][3]` | 48 | `12f` | seit 1.11 |
| 564 | `float brakeBias` | 4 | `f` | seit 1.11 |
| 568 | `float localVelocity[3]` | 12 | `3f` | seit 1.12 |

**Gesamtgröße `SPageFilePhysics`: 580 Bytes.** Rechnung: 8 Skalare (32) + 2×`[3]` (24) + 9×`[4]` (144) + 6 Skalare (24)
+ `[5]` (20) + 6 Skalare (24) + `[2]` (8) + 5 Skalare (20) + `[3]` (12) + 10 Skalare (40) + `[4]` (16) + 1 (4) + 3×`[4]` (48) + 1 (4)
+ 3×`[4][3]` (144) + 1 (4) + `[3]` (12) = 580.

```python
SPAGEFILEPHYSICS_FMT = "<ifffiiff3f3f4f4f4f4f4f4f4f4f4fffffff5fiifffi2ffffff3fffiiiiifii4ff4f4f4fi12f12f12ff3f"
assert struct.calcsize(SPAGEFILEPHYSICS_FMT) == 580
```

### 1.3 `SPageFileGraphic` (`acpmf_graphics`)

| Offset | C-Typ / Feld | Bytes | `struct` | Hinweis |
|---:|---|---:|---|---|
| 0 | `int packetId` | 4 | `i` |  |
| 4 | `AC_STATUS status` | 4 | `i` | Enum, int32 |
| 8 | `AC_SESSION_TYPE session` | 4 | `i` | Enum, int32 |
| 12 | `wchar_t currentTime[15]` | 30 | `30s` | formatiert, z. B. "1:23:456" |
| 42 | `wchar_t lastTime[15]` | 30 | `30s` |  |
| 72 | `wchar_t bestTime[15]` | 30 | `30s` |  |
| 102 | `wchar_t split[15]` | 30 | `30s` |  |
| 132 | `int completedLaps` | 4 | `i` |  |
| 136 | `int position` | 4 | `i` |  |
| 140 | `int iCurrentTime` | 4 | `i` | ms |
| 144 | `int iLastTime` | 4 | `i` | ms |
| 148 | `int iBestTime` | 4 | `i` | ms |
| 152 | `float sessionTimeLeft` | 4 | `f` | ms (UNVERIFIED Einheit) |
| 156 | `float distanceTraveled` | 4 | `f` | m |
| 160 | `int isInPit` | 4 | `i` |  |
| 164 | `int currentSectorIndex` | 4 | `i` |  |
| 168 | `int lastSectorTime` | 4 | `i` | ms |
| 172 | `int numberOfLaps` | 4 | `i` | Rundenzahl der Session |
| 176 | `wchar_t tyreCompound[33]` | 66 | `66s` |  |
| 242 | (Padding) | 2 | `2x` | Alignment auf 4 Byte |
| 244 | `float replayTimeMultiplier` | 4 | `f` |  |
| 248 | `float normalizedCarPosition` | 4 | `f` | 0..1 auf dem Track-Spline |
| 252 | `float carCoordinates[3]` | 12 | `3f` | **nur Spielerauto** (x,y,z) |
| 264 | `float penaltyTime` | 4 | `f` |  |
| 268 | `AC_FLAG_TYPE flag` | 4 | `i` | Enum, int32 |
| 272 | `int idealLineOn` | 4 | `i` |  |
| 276 | `int isInPitLane` | 4 | `i` | seit 1.5 |
| 280 | `float surfaceGrip` | 4 | `f` | seit 1.5 |
| 284 | `int mandatoryPitDone` | 4 | `i` | seit 1.13 |
| 288 | `float windSpeed` | 4 | `f` | Version UNVERIFIED (in `sim_info.py` 1.14.3 enthalten) |
| 292 | `float windDirection` | 4 | `f` | Version UNVERIFIED |

**Gesamtgröße `SPageFileGraphic`: 296 Bytes.** Rechnung: 3×4 + 4×30 + 11×4 (=176) + 66 + 2 Pad (=244) + 2×4 + 12
+ 7×4 (=292) + 4 = 296. CrewChief und `mdjarv` enden bei `mandatoryPitDone` (288 B). Die beiden Wind-Felder sind angehängt.

```python
SPAGEFILEGRAPHIC_FMT = "<iii30s30s30s30siiiiiffiiii66s2xff3ffiiififf"
assert struct.calcsize(SPAGEFILEGRAPHIC_FMT) == 296
```

> **Korrektur zur Aufgabenstellung:** `carCoordinates[60][3]`, `carID[60]`, `playerCarID` und `activeCars` gibt es im
> **originalen AC nicht**. Diese Felder gehören zu ACC (und AC EVO hat ein eigenes `car_coordinates[60][3]`). In AC steht an
> Offset 252 nur `float carCoordinates[3]`, die Position des Spielerautos. Belege: CrewChief, `sim_info.py` 1.14.3, `mdjarv`
> und `simapi` (dort ist `CarID[60]` für AC sogar explizit auskommentiert).

### 1.4 `SPageFileStatic` (`acpmf_static`)

| Offset | C-Typ / Feld | Bytes | `struct` | Hinweis |
|---:|---|---:|---|---|
| 0 | `wchar_t smVersion[15]` | 30 | `30s` | Shared-Memory-Version, z. B. "1.7" |
| 30 | `wchar_t acVersion[15]` | 30 | `30s` | AC-Version |
| 60 | `int numberOfSessions` | 4 | `i` |  |
| 64 | `int numCars` | 4 | `i` | Anzahl Autos in der Session (**nur die Zahl**) |
| 68 | `wchar_t carModel[33]` | 66 | `66s` | Ordnername des Autos |
| 134 | `wchar_t track[33]` | 66 | `66s` |  |
| 200 | `wchar_t playerName[33]` | 66 | `66s` |  |
| 266 | `wchar_t playerSurname[33]` | 66 | `66s` |  |
| 332 | `wchar_t playerNick[33]` | 66 | `66s` |  |
| 398 | (Padding) | 2 | `2x` | Alignment auf 4 Byte |
| 400 | `int sectorCount` | 4 | `i` |  |
| 404 | `float maxTorque` | 4 | `f` |  |
| 408 | `float maxPower` | 4 | `f` |  |
| 412 | `int maxRpm` | 4 | `i` |  |
| 416 | `float maxFuel` | 4 | `f` |  |
| 420 | `float suspensionMaxTravel[4]` | 16 | `4f` |  |
| 436 | `float tyreRadius[4]` | 16 | `4f` |  |
| 452 | `float maxTurboBoost` | 4 | `f` | seit 1.5 |
| 456 | `float deprecated_1` | 4 | `f` | vor 1.6: airTemp (jetzt in Physics) |
| 460 | `float deprecated_2` | 4 | `f` | vor 1.6: roadTemp (jetzt in Physics) |
| 464 | `int penaltiesEnabled` | 4 | `i` |  |
| 468 | `float aidFuelRate` | 4 | `f` |  |
| 472 | `float aidTireRate` | 4 | `f` |  |
| 476 | `float aidMechanicalDamage` | 4 | `f` |  |
| 480 | `int aidAllowTyreBlankets` | 4 | `i` |  |
| 484 | `float aidStability` | 4 | `f` |  |
| 488 | `int aidAutoClutch` | 4 | `i` |  |
| 492 | `int aidAutoBlip` | 4 | `i` |  |
| 496 | `int hasDRS` | 4 | `i` | seit 1.7.1 |
| 500 | `int hasERS` | 4 | `i` | seit 1.7.1 |
| 504 | `int hasKERS` | 4 | `i` | seit 1.7.1 |
| 508 | `float kersMaxJ` | 4 | `f` | seit 1.7.1 |
| 512 | `int engineBrakeSettingsCount` | 4 | `i` | seit 1.7.1 |
| 516 | `int ersPowerControllerCount` | 4 | `i` | seit 1.7.1 |
| 520 | `float trackSPlineLength` | 4 | `f` | seit 1.7.2, Meter |
| 524 | `wchar_t trackConfiguration[33]` | 66 | `66s` | seit 1.7.2. **Länge umstritten, siehe unten** |
| 590 | (Padding) | 2 | `2x` | Alignment auf 4 Byte |
| 592 | `float ersMaxJ` | 4 | `f` | seit 1.10.2 |
| 596 | `int isTimedRace` | 4 | `i` | seit 1.13 |
| 600 | `int hasExtraLap` | 4 | `i` | seit 1.13 |
| 604 | `wchar_t carSkin[33]` | 66 | `66s` | seit 1.13 |
| 670 | (Padding) | 2 | `2x` | Alignment auf 4 Byte |
| 672 | `int reversedGridPositions` | 4 | `i` | seit 1.13 |
| 676 | `int PitWindowStart` | 4 | `i` | seit 1.13 |
| 680 | `int PitWindowEnd` | 4 | `i` | seit 1.13 |

**Gesamtgröße `SPageFileStatic`: 684 Bytes.** Rechnung: 60 + 8 + 5×66 + 2 Pad (=400) + 5×4 + 2×16 (=452) + 18×4 (=524)
+ 66 + 2 Pad (=592) + 3×4 (=604) + 66 + 2 Pad (=672) + 3×4 = 684.

```python
SPAGEFILESTATIC_FMT = "<30s30sii66s66s66s66s66s2xiffif4f4ffffifffifiiiiifiif66s2xfii66s2xiii"
assert struct.calcsize(SPAGEFILESTATIC_FMT) == 684
```

> **`trackConfiguration`: UNVERIFIED, ob `[33]` oder `[15]`.** `sim_info.py` (1.14.3) und CrewChief verwenden `[33]`,
> `mdjarv` (C#, Stand 2017) und `simapi` verwenden `[15]`. Mit `[15]` (30 B + 2 Pad) verschieben sich alle folgenden Felder
> um **−36 Bytes**: `ersMaxJ` liegt dann bei 556, die Gesamtgröße beträgt 648 B. Empfehlung für den Parser: Standard `[33]` (Mehrheit,
> darunter CrewChief, das im Alltag mit AC gut erprobt ist), dazu ein Plausibilitätscheck. `isTimedRace`/`hasExtraLap` müssen 0 oder 1 sein,
> `carSkin` muss gültiges UTF-16 sein. Schlägt der Check fehl, auf das `[15]`-Layout zurückfallen.

### 1.5 Enums (alle `int32`)

```text
AC_STATUS:        AC_OFF=0, AC_REPLAY=1, AC_LIVE=2, AC_PAUSE=3
AC_SESSION_TYPE:  AC_UNKNOWN=-1, AC_PRACTICE=0, AC_QUALIFY=1, AC_RACE=2, AC_HOTLAP=3,
                  AC_TIME_ATTACK=4, AC_DRIFT=5, AC_DRAG=6
AC_FLAG_TYPE:     AC_NO_FLAG=0, AC_BLUE_FLAG=1, AC_YELLOW_FLAG=2, AC_BLACK_FLAG=3,
                  AC_WHITE_FLAG=4, AC_CHECKERED_FLAG=5, AC_PENALTY_FLAG=6
```

Alle vier geprüften Implementierungen (CrewChief, `sim_info.py`, `mdjarv`, `simapi`) stimmen hier überein.

### 1.6 Minimalbeispiel (Python, Windows)

```python
import mmap, struct

def read_page(tag: str, size: int) -> bytes:
    m = mmap.mmap(-1, 4096, tagname=tag, access=mmap.ACCESS_READ)
    try:
        return m[:size]
    finally:
        m.close()

def wstr(b: bytes) -> str:
    return b.decode("utf-16-le", errors="replace").split("\x00", 1)[0]

raw = read_page("Local\\acpmf_graphics", 296)
packet_id, status, session = struct.unpack_from("<iii", raw, 0)
current_time = wstr(raw[12:42])
car_xyz = struct.unpack_from("<3f", raw, 252)
```

---

## 2. UDP Remote Telemetry (Port 9996)

Kunos hat das Protokoll für Begleit-Apps (iPhone, iPad, Android) entwickelt. Die offizielle Spezifikation heißt „AC Remote Telemetry
Documentation“ (PDF bzw. Google Doc von Kunos). Die Layouts unten stammen aus `rickwest/ac-remote-telemetry-client`
(Offsets dort kommentiert) und wurden mit `struct.calcsize` geprüft.

### 2.1 Ablauf, Richtung, Netzwerk

- **AC ist der Server.** Das Spiel lauscht auf **UDP 9996**. Der **Client startet** die Kommunikation, indem er einen `Handshaker` an
  `<AC-PC-IP>:9996` schickt. AC antwortet an die Absenderadresse und den Absenderport des Clients.
- Funktioniert **lokal (127.0.0.1) und im LAN**, dafür wurde es gebaut (Tablet-Apps). Über das Internet bzw. durch NAT ist es nicht vorgesehen.
  Die Windows-Firewall muss eingehendes UDP 9996 für AC erlauben.
- Ablauf:
  1. Client → AC: `Handshaker{operationId = HANDSHAKE (0)}`
  2. AC → Client: `HandshakerResponse` (408 B)
  3. Client → AC: `Handshaker{operationId = SUBSCRIBE_UPDATE (1)}` **oder** `SUBSCRIBE_SPOT (2)`
  4. AC → Client: Bei `SUBSCRIBE_UPDATE` kommt ein kontinuierlicher Strom von `RTCarInfo` (328 B). Bei `SUBSCRIBE_SPOT` kommen nur „Spot-Events“
     als `RTLap` (212 B), z. B. am Rundenende.
  5. Client → AC: `Handshaker{operationId = DISMISS (3)}` beendet die Verbindung.
- Pakettyp erkennen: Es gibt **keinen Typ-Header**. Man unterscheidet die Pakete nur an der **Datagrammlänge** (408 / 328 / 212), so macht es auch die Referenz-Implementierung.
- Daten betreffen **nur das Auto des lokalen Spielers bzw. das fokussierte Auto** (UNVERIFIED, welches von beiden im Replay oder bei Kamerawechsel).
  `RTLap` enthält `carIdentifierNumber` und Fahrer- und Autonamen. Ob AC Spot-Events auch für andere Autos sendet, ist **UNVERIFIED**.
- Senderate von `RTCarInfo`: an die Physik bzw. Grafik gekoppelt, hoch (**UNVERIFIED**, keine Primärquelle).
- Anzahl gleichzeitiger Clients: **UNVERIFIED**.

### 2.2 `Handshaker` (Client → AC), 12 Bytes

| Offset | Typ | Feld | Hinweis |
|---:|---|---|---|
| 0 | `int32` | `identifier` | Gerätetyp: eIPhoneDevice=0, eIPadDevice=1, eAndroidPhone=2, eAndroidTablet=3 (laut Kunos derzeit ungenutzt) |
| 4 | `int32` | `version` | 1 (laut Kunos derzeit ungenutzt) |
| 8 | `int32` | `operationId` | HANDSHAKE=0, SUBSCRIBE_UPDATE=1, SUBSCRIBE_SPOT=2, DISMISS=3 |

```python
HANDSHAKER_FMT = "<iii"            # calcsize = 12
```

### 2.3 `HandshakerResponse` (AC → Client), 408 Bytes

| Offset | Typ | Feld | Bytes |
|---:|---|---|---:|
| 0 | `wchar_t[50]` | `carName` | 100 |
| 100 | `wchar_t[50]` | `driverName` | 100 |
| 200 | `int32` | `identifier` | 4 |
| 204 | `int32` | `version` | 4 |
| 208 | `wchar_t[50]` | `trackName` | 100 |
| 308 | `wchar_t[50]` | `trackConfig` | 100 |

Rechnung: 100 + 100 + 4 + 4 + 100 + 100 = **408**.

Die Strings sind UTF-16LE. In der Praxis enthalten sie oft ein `%`-Zeichen als Terminator und danach Datenmüll.
Deshalb bis zum ersten `\x00` **oder** `%` abschneiden (verbreitete Beobachtung, **UNVERIFIED**).

```python
HANDSHAKER_RESPONSE_FMT = "<100s100sii100s100s"   # calcsize = 408
```

### 2.4 `RTCarInfo` (AC → Client bei SUBSCRIBE_UPDATE), 328 Bytes

Natürliches C-Alignment. Ein `char` am Anfang und 6 `bool` (je 1 Byte) erzeugen Padding.

| Offset | Typ | Feld | Bytes | Hinweis |
|---:|---|---|---:|---|
| 0 | `char` | `identifier` | 1 | laut Kunos-Doku ein Zeichen; manche Clients lesen 4 B als UTF-16 |
| 1 | – | (Padding) | 3 |  |
| 4 | `int32` | `size` | 4 | Größe des Pakets |
| 8 | `float` | `speed_Kmh` | 4 |  |
| 12 | `float` | `speed_Mph` | 4 |  |
| 16 | `float` | `speed_Ms` | 4 |  |
| 20 | `bool` | `isAbsEnabled` | 1 |  |
| 21 | `bool` | `isAbsInAction` | 1 |  |
| 22 | `bool` | `isTcInAction` | 1 |  |
| 23 | `bool` | `isTcEnabled` | 1 |  |
| 24 | `bool` | `isInPit` | 1 |  |
| 25 | `bool` | `isEngineLimiterOn` | 1 |  |
| 26 | – | (Padding) | 2 |  |
| 28 | `float` | `accG_vertical` | 4 |  |
| 32 | `float` | `accG_horizontal` | 4 |  |
| 36 | `float` | `accG_frontal` | 4 |  |
| 40 | `int32` | `lapTime` | 4 | ms |
| 44 | `int32` | `lastLap` | 4 | ms |
| 48 | `int32` | `bestLap` | 4 | ms |
| 52 | `int32` | `lapCount` | 4 |  |
| 56 | `float` | `gas` | 4 |  |
| 60 | `float` | `brake` | 4 |  |
| 64 | `float` | `clutch` | 4 |  |
| 68 | `float` | `engineRPM` | 4 |  |
| 72 | `float` | `steer` | 4 |  |
| 76 | `int32` | `gear` | 4 |  |
| 80 | `float` | `cgHeight` | 4 |  |
| 84 | `float[4]` | `wheelAngularSpeed` | 16 |  |
| 100 | `float[4]` | `slipAngle` | 16 |  |
| 116 | `float[4]` | `slipAngle_ContactPatch` | 16 |  |
| 132 | `float[4]` | `slipRatio` | 16 |  |
| 148 | `float[4]` | `tyreSlip` | 16 |  |
| 164 | `float[4]` | `ndSlip` | 16 |  |
| 180 | `float[4]` | `load` | 16 |  |
| 196 | `float[4]` | `Dy` | 16 |  |
| 212 | `float[4]` | `Mz` | 16 |  |
| 228 | `float[4]` | `tyreDirtyLevel` | 16 |  |
| 244 | `float[4]` | `camberRAD` | 16 |  |
| 260 | `float[4]` | `tyreRadius` | 16 |  |
| 276 | `float[4]` | `tyreLoadedRadius` | 16 |  |
| 292 | `float[4]` | `suspensionHeight` | 16 |  |
| 308 | `float` | `carPositionNormalized` | 4 | 0..1 |
| 312 | `float` | `carSlope` | 4 |  |
| 316 | `float[3]` | `carCoordinates` | 12 | x,y,z |

Rechnung: 4 + 4 + 12 + 6 + 2 + 12 + 16 + 20 + 4 + 4 (= 84) + 14×16 (= 308) + 4 + 4 + 12 = **328**.

```python
RTCARINFO_FMT = "<c3xi3f6?2x3f4i5fif56f2f3f"     # calcsize = 328
```

### 2.5 `RTLap` (AC → Client bei SUBSCRIBE_SPOT), 212 Bytes

| Offset | Typ | Feld | Bytes |
|---:|---|---|---:|
| 0 | `int32` | `carIdentifierNumber` | 4 |
| 4 | `int32` | `lap` | 4 |
| 8 | `wchar_t[50]` | `driverName` | 100 |
| 108 | `wchar_t[50]` | `carName` | 100 |
| 208 | `int32` | `time` | 4 (ms) |

Rechnung: 4 + 4 + 100 + 100 + 4 = **212**.

```python
RTLAP_FMT = "<ii100s100si"                       # calcsize = 212
```

---

## 3. Daten über andere Autos (wichtig für den Race Engineer)

| Quelle | Andere Autos? | Details |
|---|---|---|
| Shared Memory Physics | nein | nur Spielerauto |
| Shared Memory Graphics | **nein** | `carCoordinates[3]` = nur Spieler; `position` = eigene Position. Kein `carID[]`/`activeCars` (das gibt es erst in ACC) |
| Shared Memory Static | nur Anzahl | `numCars` = Anzahl Autos der Session, keine Namen oder Positionen |
| UDP 9996 `RTCarInfo` | nein | nur Spieler- bzw. fokussiertes Auto |
| UDP 9996 `RTLap` | eingeschränkt, UNVERIFIED | trägt `carIdentifierNumber` + Namen, aber nur Runden-Events |
| **In-Game-Python-App** (`ac`-Modul, `apps/python/<app>/`) | **ja** | `ac.getCarsCount()`, `ac.getCarState(carId, acsys.CS.WorldPosition / SpeedKMH / NormalizedSplinePosition / LapCount / BestLap / LastLap …)`, `ac.getDriverName(carId)`, `ac.getCarLeaderboardPosition(carId)`, `ac.isCarInPitline(carId)` … (Funktionsnamen nach Kunos „AC Python Documentation“, **UNVERIFIED** im Detail). Die App muss die Daten selbst exportieren (z. B. per UDP oder eigenem Shared Memory). |
| **CrewChief-Muster** | ja | CrewChief liefert die App `CrewChiefEx` mit, die über das `ac`-Modul ein **eigenes** Shared Memory `Local\acpmf_crewchief` füllt: `SPageFileCrewChief{numVehicles, focusVehicle, serverName[512], acsVehicleInfo vehicle[64], …}` mit `acsVehicleInfo{carId, driverName[64], carModel[64], speedMS, bestLapMS, lapCount, currentLapInvalid, currentLapTimeMS, lastLapTimeMS, worldPosition, isCarInPitline, isCarInPit, carLeaderboardPosition, carRealTimeLeaderboardPosition, spLineLength, isConnected, suspensionDamage[4], engineLifeLeft, tyreInflation[4]}`. Das ist **kein** Kunos-Standard, aber ein gutes Vorbild für eine eigene Chef-App. |
| Dedicated-Server-UDP-Plugin (`acServer`, ACSP-Protokoll) | ja (nur Multiplayer) | Serverseitig: Car-Updates aller Fahrer, Lap-Completed, Kollisionen. Nur relevant, wenn man den Server kontrolliert. Details **UNVERIFIED**, nicht Teil dieser Recherche. |

**Konsequenz für Chef:** Für Gaps, Positionen und Verkehr im originalen AC braucht man eine kleine In-Game-Python-App,
die als Bridge dient (Python 3.3 embedded in AC). Shared Memory und UDP 9996 genügen nur für Telemetrie des eigenen Autos.

---

## 4. Implementierungshinweise für Chef

- **Plattform:** Shared Memory gibt es nur unter Windows, und zwar im selben Prozess-Namespace bzw. derselben Session wie AC. Unter Linux/Proton liegt es
  nicht automatisch in `/dev/shm`. `simapi` bzw. `simshmbridge` lösen das mit einer Bridge (**UNVERIFIED**, nicht getestet).
- **Torn reads:** `packetId` vor und nach dem Kopieren vergleichen.
- **Status prüfen:** Nur wenn `graphics.status == AC_LIVE (2)` ist, sind die Daten Live-Daten. `AC_REPLAY` vom Engineer-Logik-Pfad trennen.
- **Strings:** `wchar_t` immer explizit als UTF-16LE dekodieren, niemals `ctypes.c_wchar` plattformunabhängig verwenden.
- **Tests:** Offsets oben als Golden Values in Unit-Tests übernehmen (`struct.calcsize(FMT) == 580/296/684`, `328/212/408/12`).

---

## Quellen

| Quelle | URL | Lizenz / Status | Verwendet für |
|---|---|---|---|
| Kunos „Shared Memory Reference“ (Forum-Thread, offizielle Referenz) | https://www.assettocorsa.net/forum/index.php?threads/shared-memory-reference.3352/ | Kunos, proprietär; **aus der Recherche-Umgebung nicht erreichbar** | Ursprung der Struct-Definitionen |
| Kunos „ACSharedMemoryDocumentation“ (PDF-Kopie) | https://www.scribd.com/document/629251108/ACSharedMemoryDocumentation | Kunos, proprietär; **nicht erreichbar** | – |
| Kunos „AC Remote Telemetry Documentation“ (PDF-Kopie / Google Doc) | https://www.scribd.com/document/629251050/ACRemoteTelemetryDocumentation · https://docs.google.com/document/d/1KfkZiIluXZ6mMhLWfDX1qAGbvhGRC3ZUzjVIt5FQpp4/pub | Kunos, proprietär; **nicht erreichbar** | – |
| CrewChiefV4 – `CrewChiefV4/ACS/ACSData.cs` (+ Plugin `CrewChiefEx`) | https://github.com/mrbelowski/CrewChiefV4 | Lizenz im Repo nicht geprüft (**UNVERIFIED**); nur als Referenz gelesen | Struct-Layouts AC, `trackConfiguration[33]`, Muster für Daten anderer Autos |
| albertowd/live-telemetry – `apps/python/LiveTelemetry/lib/sim_info.py` („updated to AC 1.14.3“) | https://github.com/albertowd/live-telemetry | Lizenz nicht geprüft (**UNVERIFIED**) | ctypes-Layout inkl. `windSpeed`/`windDirection` |
| mdjarv/assettocorsasharedmemory (C#) | https://github.com/mdjarv/assettocorsasharedmemory | MIT | „since 1.x“-Versionskommentare, Enums |
| Spacefreak18/simapi – `include/acdata.h` | https://github.com/Spacefreak18/simapi | LGPL-3.0 | C-Header AC/ACC (Abgleich, `CarID[60]` für AC auskommentiert) |
| rickwest/ac-remote-telemetry-client (JS) | https://github.com/rickwest/ac-remote-telemetry-client | MIT | UDP-9996-Layouts mit Offsets, Größen 408/328/212, Operation-IDs |
| PyPI `acudpclient` | https://pypi.org/project/acudpclient/ | Lizenz nicht angegeben | Hinweis auf AC-Server-UDP-Client (nicht ausgewertet) |

Eigene Verifikation: Die Offsets und Gesamtgrößen in Abschnitt 1 stammen aus einem Layout-Rechner (pack 4, wchar = 2 B) und wurden
mit `ctypes.Structure(_pack_=4)` gegengeprüft. Alle `struct`-Formatstrings wurden mit `struct.calcsize` geprüft (580 / 296 / 684 / 12 / 408 / 328 / 212).
