# iRacing – irsdk Shared-Memory-Telemetrie

Stand der Recherche: 2026-10-28. Primärquelle ist der offizielle Header `irsdk_defines.h` und
`irsdk_utils.cpp` (Copyright 2013 iRacing.com Motorsport Simulations, BSD-3-Clause). Abgeglichen
mit `kutu/pyirsdk` 1.3.7 (MIT) und `mpapenbr/goirsdk` (MIT, inkl. `variables.md`, einem Dump der
Var-Header einer aktuellen Sim-Version).

Markierung: **UNVERIFIED** heißt, dass es keine Primärquelle gab, dass Quellen sich widersprechen
oder dass die Angabe aus Erfahrung bzw. Community-Wissen stammt. Solche Angaben vor dem
Implementieren gegen einen echten Speicher-Dump oder eine `.ibt`-Datei prüfen.

> Grundprinzip: Die Var-Header beschreiben sich selbst. Ein Parser darf **keine** festen Offsets für
> Telemetrievariablen hart codieren. Er liest die Header beim Verbinden und sucht Variablen über den
> Namen. Fest sind nur die Strukturen `irsdk_header`, `irsdk_varBuf` und `irsdk_varHeader`.

---

## 1. Windows-Objekte, Größen, Broadcast-Messages

### 1.1 Namen und Konstanten (aus `irsdk_defines.h`)

| Konstante | Wert |
|---|---|
| `IRSDK_MEMMAPFILENAME` | `"Local\\IRSDKMemMapFileName"` (C-String, im Code also `Local\IRSDKMemMapFileName`) |
| `IRSDK_DATAVALIDEVENTNAME` | `"Local\\IRSDKDataValidEvent"` |
| `IRSDK_BROADCASTMSGNAME` | `"IRSDK_BROADCASTMSG"` |
| `IRSDK_MAX_BUFS` | `4` (Kommentar im Header: „3 for now“, `numBuf` liegt in der Praxis bei 3) |
| `IRSDK_MAX_STRING` | `32` |
| `IRSDK_MAX_DESC` | `64` |
| `IRSDK_UNLIMITED_LAPS` | `32767` |
| `IRSDK_UNLIMITED_TIME` | `604800.0f` (7 Tage in s) |
| `IRSDK_VER` | `2` (Header-Version) |
| `irsdk_stConnected` | `1` (Bit in `irsdk_header.status`) |

**Map-Größe:** Der offizielle Client öffnet die Map mit `OpenFileMapping(FILE_MAP_READ, FALSE, name)` und
`MapViewOfFile(h, FILE_MAP_READ, 0, 0, 0)`, also mit Länge 0 für die ganze Map. Eine Größe ist nicht
definiert. pyirsdk und goirsdk verwenden beide `MEMMAPFILESIZE = 1164 * 1024` (= 1 191 936 Bytes).
**UNVERIFIED**, ob iRacing die Map exakt so groß anlegt. Robust ist es, die Größe aus
`varBuf[i].bufOffset + bufLen` (Maximum über alle Buffers) und `sessionInfoOffset + sessionInfoLen` zu
validieren. In Python unter Windows: `mmap.mmap(-1, 1164*1024, tagname="Local\\IRSDKMemMapFileName", access=mmap.ACCESS_READ)`.
Die pyirsdk-Variante `mmap.mmap(0, ...)` funktioniert ebenfalls.

**Data-Valid-Event:** `OpenEventW(SYNCHRONIZE /*0x00100000*/, FALSE, "Local\\IRSDKDataValidEvent")`, danach
`WaitForSingleObject(h, timeout_ms)`. iRacing signalisiert das Event nach jeder geschriebenen Zeile,
also mit `tickRate` = 60 Hz. pyirsdk wartet 32 ms (ca. 2 Ticks).

**Sim-Status per HTTP (nur pyirsdk):** `http://127.0.0.1:32034/get_sim_status?object=simStatus`. Die
Antwort enthält `running:1`, wenn die Sim läuft. Das ist nicht Teil des offiziellen Headers.
**UNVERIFIED**, ob der Endpunkt unter Proton von Linux aus erreichbar ist. Wine teilt den
Netzwerk-Stack mit dem Host, deshalb ist es wahrscheinlich.

### 1.2 Broadcast-Messages (Fernsteuerung, z. B. Boxenbefehle)

Versand laut `irsdk_utils.cpp`:

```c
msgId = RegisterWindowMessage("IRSDK_BROADCASTMSG");
SendNotifyMessage(HWND_BROADCAST /*0xFFFF*/, msgId, MAKELONG(msg, var1), var2);
// 3-Parameter-Variante: var2 = MAKELONG(var2, var3)   (var1..var3 je 16 Bit signed)
// Float-Variante:       var2 = (int)(f * 65536.0f)     (16.16 Fixed Point)
```

Packing: `wParam = (msg & 0xFFFF) | ((var1 & 0xFFFF) << 16)`, `lParam = var2` (32 Bit) bzw.
`(var2 & 0xFFFF) | ((var3 & 0xFFFF) << 16)`. Python (pyirsdk, MIT, sinngemäß):
`ctypes.windll.user32.SendNotifyMessageW(0xFFFF, RegisterWindowMessageW("IRSDK_BROADCASTMSG"), msg | var1 << 16, var2 | var3 << 16)`.
Kamera- und Replay-Befehle wirken nur außerhalb des Autos, Pit-Befehle nur im Auto.

`irsdk_BroadcastMsg` (wParam low word):

| Wert | Name | Parameter |
|---|---|---|
| 0 | `irsdk_BroadcastCamSwitchPos` | car position, group, camera |
| 1 | `irsdk_BroadcastCamSwitchNum` | driver # (padCarNum), group, camera |
| 2 | `irsdk_BroadcastCamSetState` | `irsdk_CameraState` |
| 3 | `irsdk_BroadcastReplaySetPlaySpeed` | speed, slowMotion |
| 4 | `irskd_BroadcastReplaySetPlayPosition` (Tippfehler im Original) | `irsdk_RpyPosMode`, Frame (high, low) |
| 5 | `irsdk_BroadcastReplaySearch` | `irsdk_RpySrchMode` |
| 6 | `irsdk_BroadcastReplaySetState` | `irsdk_RpyStateMode` |
| 7 | `irsdk_BroadcastReloadTextures` | `irsdk_ReloadTexturesMode`, carIdx |
| 8 | `irsdk_BroadcastChatComand` | `irsdk_ChatCommandMode`, subCommand |
| **9** | **`irsdk_BroadcastPitCommand`** | **`irsdk_PitCommandMode`, parameter (32 Bit in lParam)** |
| 10 | `irsdk_BroadcastTelemCommand` | `irsdk_TelemCommandMode` |
| 11 | `irsdk_BroadcastFFBCommand` | `irsdk_FFBCommandMode`, float (16.16) |
| 12 | `irsdk_BroadcastReplaySearchSessionTime` | sessionNum, sessionTimeMS |
| 13 | `irsdk_BroadcastVideoCapture` | `irsdk_VideoCaptureMode`. Neuer als der 2013er Header, belegt durch pyirsdk und goirsdk |

`irsdk_PitCommandMode` (wParam high word = var1, Parameter in lParam):

| Wert | Name | pyirsdk | Bedeutung / Parameter |
|---|---|---|---|
| 0 | `irsdk_PitCommand_Clear` | `clear` | alle Pit-Checkboxen löschen |
| 1 | `irsdk_PitCommand_WS` | `ws` | Windschutzscheibe (Tear-off) |
| 2 | `irsdk_PitCommand_Fuel` | `fuel` | tanken. Parameter: Liter (int), `0` übernimmt die bestehende Menge |
| 3 | `irsdk_PitCommand_LF` | `lf` | Reifen LF wechseln. Parameter: Druck in kPa, `0` übernimmt den bestehenden |
| 4 | `irsdk_PitCommand_RF` | `rf` | RF |
| 5 | `irsdk_PitCommand_LR` | `lr` | LR |
| 6 | `irsdk_PitCommand_RR` | `rr` | RR |
| 7 | `irsdk_PitCommand_ClearTires` | `clear_tires` | Reifen-Checkboxen löschen |
| 8 | `irsdk_PitCommand_FR` | `fr` | Fast Repair anfordern |
| 9 | `irsdk_PitCommand_ClearWS` | `clear_ws` | Tear-off abwählen |
| 10 | `irsdk_PitCommand_ClearFR` | `clear_fr` | Fast Repair abwählen |
| 11 | `irsdk_PitCommand_ClearFuel` | `clear_fuel` | Tanken abwählen |
| 12 | `irsdk_PitCommand_TC` | `tc` | Reifenmischung wechseln. Parameter: Compound-Index. Nicht im 2013er Header, belegt durch pyirsdk. Exakter C-Name **UNVERIFIED** |

Beispiel „20 l tanken“: `wParam = 9 | (2 << 16)`, `lParam = 20`.

Unter Proton muss der Sender **innerhalb desselben Wine-Prefix/Wineservers** laufen wie iRacing, weil
`HWND_BROADCAST` nur Fenster dieses Wineservers erreicht. Ein natives Linux-Programm kann keine
Pit-Befehle senden. Nötig ist ein kleiner Helfer (`.exe` oder Windows-Python) im Prefix. **UNVERIFIED**,
in der Praxis nicht getestet.

Weitere Enums (Werte 0…n in Reihenfolge):
- `irsdk_ChatCommandMode`: Macro(0, 1–15), BeginChat, Reply, Cancel
- `irsdk_TelemCommandMode`: Stop, Start, Restart
- `irsdk_RpySrchMode`: ToStart, ToEnd, PrevSession, NextSession, PrevLap, NextLap, PrevFrame, NextFrame, PrevIncident, NextIncident
- `irsdk_RpyPosMode`: Begin, Current, End
- `irsdk_csMode`: FocusAtIncident = −3, FocusAtLeader = −2, FocusAtExiting = −1, FocusAtDriver = 0 (+ Nummer)
- `irsdk_padCarNum(num, zero)`: `num + 1000 * (stellen(num) + zero)`. Codiert führende Nullen, z. B. #001.

---

## 2. `irsdk_header` (Offset 0 der Map, Little Endian, alle Felder `int32`)

| Offset | Feld | Bedeutung |
|---|---|---|
| 0 | `int ver` | API-Header-Version (`IRSDK_VER` = 2) |
| 4 | `int status` | Bitfeld `irsdk_StatusField`. Bit `irsdk_stConnected = 1` ist gesetzt, wenn die Sim aktiv ist |
| 8 | `int tickRate` | Ticks/s (60, für Disk-Logging bis 360) |
| 12 | `int sessionInfoUpdate` | wird bei jeder YAML-Änderung erhöht |
| 16 | `int sessionInfoLen` | Länge des YAML-Strings in Bytes (Puffergröße, Rest mit NUL gefüllt) |
| 20 | `int sessionInfoOffset` | Offset des YAML-Strings ab Map-Beginn |
| 24 | `int numVars` | Anzahl Einträge im Var-Header-Array |
| 28 | `int varHeaderOffset` | Offset von `irsdk_varHeader[numVars]` |
| 32 | `int numBuf` | Anzahl genutzter Buffers (≤ `IRSDK_MAX_BUFS`, praktisch 3) |
| 36 | `int bufLen` | Länge einer Datenzeile in Bytes |
| 40 | `int pad1[2]` | 16-Byte-Alignment (siehe Hinweis unten) |
| 48 | `irsdk_varBuf varBuf[4]` | je 16 Bytes → Offsets 48, 64, 80, 96 |
| 112 | – | Ende des Headers (in `.ibt` folgt hier `irsdk_diskSubHeader`) |

`irsdk_varBuf` (16 Bytes):

| Offset | Feld | Bedeutung |
|---|---|---|
| +0 | `int tickCount` | Tick der Zeile, wird **nach** dem Schreiben gesetzt |
| +4 | `int bufOffset` | Offset der Datenzeile ab Map-Beginn |
| +8 | `int pad[2]` | Alignment |

> **Hinweis (UNVERIFIED, neuere SDK-Version):** pyirsdk 1.3.7 interpretiert die Padding-Felder neu:
> Header-Offset 40 = `cur_buf_tick_count` („stashed copy of the current tick_count“), Offset 44
> (1 Byte) = `cur_buf` („index of the most recently written buffer“), `varBuf[i]+8` =
> `tick_count_begin` („updated BEFORE write starts, for torn read detection“). Das deutet auf eine
> neuere offizielle `irsdk_defines.h` hin, die ich nicht gefunden habe. Der Parser soll diese Felder
> nur optional nutzen und immer auf den Algorithmus aus Abschnitt 4 zurückfallen können.

`irsdk_diskSubHeader` (nur `.ibt`, Offset 112): `time_t sessionStartDate` (8 Byte), `double sessionStartTime`,
`double sessionEndTime`, `int sessionLapCount`, `int sessionRecordCount`. In `.ibt` liegen die Zeilen
hintereinander ab `varBuf[0].bufOffset`, im Abstand `bufLen`. Das passt gut für Replay und Tests.

Python-`struct`: `HEADER = struct.Struct("<10i2i")`, danach `VARBUF = struct.Struct("<4i")` × 4 ab Offset 48.

---

## 3. `irsdk_varHeader` (144 Bytes, Array ab `varHeaderOffset`)

| Offset | Feld | Typ |
|---|---|---|
| 0 | `type` | `int32` (`irsdk_VarType`) |
| 4 | `offset` | `int32`, Offset innerhalb der Datenzeile |
| 8 | `count` | `int32`, Array-Länge (1 = Skalar) |
| 12 | `countAsTime` | `bool` (1 Byte) |
| 13 | `pad[3]` | Alignment |
| 16 | `name[32]` | `char[IRSDK_MAX_STRING]`, NUL-terminiert |
| 48 | `desc[64]` | `char[IRSDK_MAX_DESC]` |
| 112 | `unit[32]` | `char[IRSDK_MAX_STRING]`, z. B. `"m/s"`, `"%"`, `"irsdk_Flags"` |
| 144 | – | Größe gesamt |

Python: `struct.Struct("<iii?3x32s64s32s")` (= 144 Bytes). Strings mit `.split(b"\0",1)[0].decode("latin-1")` lesen.

`irsdk_VarType` und Größen (`irsdk_VarTypeBytes`):

| Wert | Name | Bytes | `struct` |
|---|---|---|---|
| 0 | `irsdk_char` | 1 | `c` / `B` |
| 1 | `irsdk_bool` | 1 | `?` |
| 2 | `irsdk_int` | 4 | `i` |
| 3 | `irsdk_bitField` | 4 | `I` |
| 4 | `irsdk_float` | 4 | `f` |
| 5 | `irsdk_double` | 8 | `d` |

Bytes je Variable = `irsdk_VarTypeBytes[type] * count`. Die Var-Liste kann sich je nach Auto und Session
ändern. Laut Header bleibt sie während einer Session fest. Bei einem Reconnect (z. B. `ver`, `numVars` oder
`varHeaderOffset` geändert) muss sie neu eingelesen werden.

Gute Praxis: einmal pro Verbindung einen `numpy.dtype` oder eine Offset-Tabelle für die benötigten
Variablen bauen. Fehlende Variablen (andere Autos oder Versionen) als `None` behandeln, statt einen
Fehler zu werfen.

---

## 4. Algorithmus: neueste Zeile sicher lesen

Offizielle Logik (`irsdk_getNewData`, sinngemäß):

1. Ist `status & irsdk_stConnected == 0`, gibt es keine Daten. `lastTickCount = INT_MAX` setzen.
2. `latest` = Index mit dem **höchsten** `varBuf[i].tickCount` über `i < numBuf`.
3. Ist `varBuf[latest].tickCount > lastTickCount`, gibt es neue Daten. Bis zu **2 Versuche**:
   - `t = varBuf[latest].tickCount`
   - `memcpy(local, map + varBuf[latest].bufOffset, bufLen)`
   - Ist `varBuf[latest].tickCount == t` noch gleich, ist die Kopie konsistent: `lastTickCount = t`, Ende.
   - Sonst wurde der Buffer während des Kopierens überschrieben: neuer Versuch, danach `False`.
4. Ist `tickCount < lastTickCount`, hat die Sim neu gestartet bzw. neu verbunden: `lastTickCount = tickCount`,
   kein neuer Datensatz.
5. `irsdk_waitForDataReady(timeout)`: zuerst `getNewData`, sonst `WaitForSingleObject(event, timeout)`,
   danach erneut `getNewData`.
6. `irsdk_isConnected()`: `status & stConnected` **und** letzter gültiger Datensatz < 30 s alt.

Python-Skizze (eigener Code, nicht kopiert):

```python
def read_latest(mm, hdr, last_tick):
    ticks = [VARBUF.unpack_from(mm, 48 + 16*i)[0] for i in range(hdr.num_buf)]
    idx = max(range(hdr.num_buf), key=ticks.__getitem__)
    tick = ticks[idx]
    if tick < last_tick:                 # Neustart/Reconnect
        return None, tick
    if tick == last_tick:                # nichts Neues
        return None, last_tick
    for _ in range(2):
        t, off = VARBUF.unpack_from(mm, 48 + 16*idx)[:2]
        data = bytes(mm[off:off + hdr.buf_len])           # Kopie
        if VARBUF.unpack_from(mm, 48 + 16*idx)[0] == t:   # unverändert -> konsistent
            return data, t
    return None, last_tick               # während des Kopierens überschrieben
```

Alternative von pyirsdk: Ohne „Freeze“ liest pyirsdk direkt aus dem **zweitneuesten** Buffer, um
halb geschriebene Daten zu vermeiden. Mit `freeze_var_buffer_latest()` kopiert es den neuesten Buffer
nach dem Event. Für uns gilt: Event abwarten → kopieren → Tick prüfen. Danach nur noch auf der Kopie
arbeiten.

Session-Info: `sessionInfoUpdate` mit dem letzten Wert vergleichen. Nur bei einer Änderung YAML neu
lesen und parsen. Zur Sicherheit `sessionInfoUpdate` vor und nach dem Kopieren vergleichen
(**UNVERIFIED**, aber billig). Das Parsen dauert mehrere ms, deshalb in einem Worker-Thread parsen,
nicht im 60-Hz-Pfad.

---

## 5. Session-Info-YAML

### 5.1 Encoding und Format

- Der String steht bei `map[sessionInfoOffset : sessionInfoOffset + sessionInfoLen]` und ist mit NUL-Bytes aufgefüllt.
  Er beginnt mit `---` und endet mit `...`. Einrückung: 1 Leerzeichen pro Ebene.
- Historisch **Windows-1252 / ISO-8859-1**. pyirsdk erkennt eine UTF-8-Variante am Präfix
  `b"---\nWeekendInfo:\n Encoding: UTF8"`. Fehlt das Präfix, dekodiert es als `cp1252`. **UNVERIFIED**, seit
  welcher Sim-Version UTF-8 ausgegeben wird.
- In cp1252 undefinierte Bytes `0x81 0x8D 0x8F 0x90 0x9D` ersetzt pyirsdk vor dem Dekodieren durch Leerzeichen
  und entfernt nicht druckbare Zeichen.
- Zahlen mit Einheiten werden als Strings ausgegeben: `TrackLength: 5.79 km`, `TrackPitSpeedLimit: 80.00 kph`,
  `SessionTime: 1800.0000 sec` oder `unlimited`, `SessionLaps: 25` oder `unlimited`, `WindSpeed: 8.05 km/h`,
  `TrackSurfaceTemp: 31.24 C`, `CarClassMaxFuelPct: 1.000 %` (Wert ist trotz `%` ein Anteil 0–1).
  Diese Werte mit einer eigenen Regex `^(-?[\d.]+)\s*(\S+)?$` zerlegen.

### 5.2 Bekannte YAML-Quirks (Sanitizing nötig)

- **Nicht escapte Namen:** `UserName`, `TeamName`, `AbbrevName`, `Initials`, `DriverSetupName` können `:`, `#`,
  `"`, `'`, `&`, `*`, `{`, `[`, führende Kommas usw. enthalten. pyirsdk setzt diese Werte vor dem Parsen per Regex
  in doppelte Anführungszeichen und escapet `"` und `\`. Gleiches gilt für alle Werte, die mit `,` beginnen.
- **Timestamps:** YAML 1.1 würde manche Werte als Datum interpretieren. pyirsdk entfernt den impliziten
  Timestamp-Resolver aus dem `SafeLoader`.
- **Typunsicherheit:** `CarNumber` (z. B. `"007"` vs. `7`) immer als String behandeln. `TeamName` oder `UserName`
  kann wie eine Zahl, `yes`/`no` oder `null` aussehen. Deshalb Werte möglichst als String laden und die Typen
  selbst konvertieren. `ResultsPositions` kann `null` sein (vor Sessionstart).
- Sehr großer YAML-Block bei vollen Feldern: pyirsdk schneidet per Regex nur die benötigte Top-Level-Sektion
  (`\nDriverInfo:\n` … `\n\n`) heraus. Das lohnt sich auch bei uns.
- Empfehlung: PyYAML `CSafeLoader` (falls vorhanden), Sanitizing vorher, und Fehler abfangen. Bei einem
  Parse-Fehler den letzten guten Stand behalten.

### 5.3 Schlüsselstruktur (Auswahl, für den Renningenieur relevant)

Die Feldnamen stammen aus Community-Dumps und pyirsdk-Nutzung. Vollständigkeit und Typen sind
**UNVERIFIED** und müssen mit einem echten Dump abgeglichen werden.

```yaml
WeekendInfo:
 TrackName: spa 2024 up          # interner Name
 TrackID: 163                    # int
 TrackLength: 7.00 km            # String mit Einheit
 TrackDisplayName: Circuit de Spa-Francorchamps
 TrackDisplayShortName: Spa
 TrackConfigName: Grand Prix
 TrackCity / TrackCountry / TrackNumTurns
 TrackPitSpeedLimit: 80.00 kph
 TrackType: road course
 TrackSurfaceTemp / TrackAirTemp / TrackWindVel / TrackWindDir / TrackRelativeHumidity
 SeriesID / SeasonID / SessionID / SubSessionID / LeagueID
 Official: 1
 EventType: Race                 # Test / Practice / Race ...
 Category: Road
 SimMode: full
 TeamRacing: 0
 NumCarClasses / NumCarTypes
 WeekendOptions:
  NumStarters: 0
  StartingGrid: single file
  QualifyScoring: best lap
  CourseCautions: off
  StandingStart: 0
  Restarts: single file
  WeatherType: Constant / Realistic
  Skies / WindDirection / WindSpeed / WeatherTemp / RelativeHumidity / FogLevel
  Unofficial / CommercialMode / NightMode / IsFixedSetup / StrictLapsChecking
  HardcoreLevel / NumJokerLaps
  IncidentLimit: unlimited | 17
  FastRepairsLimit: unlimited | 1
  GreenWhiteCheckeredLimit
 TelemetryOptions:
  TelemetryDiskFile: ""

SessionInfo:
 Sessions:
 - SessionNum: 0                 # entspricht Telemetrie SessionNum
   SessionLaps: unlimited        # oder Zahl
   SessionTime: 1800.0000 sec    # oder unlimited
   SessionNumLapsToAvg: 5
   SessionType: Practice         # Practice | Open Qualify | Lone Qualify | Warmup | Race | Offline Testing
   SessionName: PRACTICE
   SessionTrackRubberState: moderate usage
   ResultsPositions:             # null möglich
   - Position: 1                 # 1-basiert
     ClassPosition: 0            # ACHTUNG 0-basiert (UNVERIFIED, Community-Erfahrung)
     CarIdx: 12
     Lap / Time / FastestLap / FastestTime / LastTime
     LapsLed / LapsComplete / LapsDriven / Incidents
     ReasonOutId / ReasonOutStr
   ResultsFastestLap: [ {CarIdx, FastestLap, FastestTime} ]
   ResultsNumCautionFlags / ResultsNumCautionLaps / ResultsNumLeadChanges
   ResultsLapsComplete / ResultsOfficial

QualifyResultsInfo:              # optional
 Results: [ {Position, ClassPosition, CarIdx, FastestLap, FastestTime} ]

DriverInfo:
 DriverCarIdx: 5                 # = Telemetrie PlayerCarIdx
 DriverUserID / PaceCarIdx
 DriverCarIdleRPM / DriverCarRedLine
 DriverCarSLFirstRPM / DriverCarSLShiftRPM / DriverCarSLLastRPM / DriverCarSLBlinkRPM
 DriverCarFuelKgPerLtr: 0.750
 DriverCarFuelMaxLtr: 120.000    # Tankgröße in l
 DriverCarMaxFuelPct: 1.000      # erlaubter Füllgrad (BoP) als Anteil 0–1
 DriverCarGearNumForward
 DriverCarEstLapTime
 DriverPitTrkPct                 # Lage der eigenen Box auf der Runde (0–1)
 DriverSetupName / DriverSetupIsModified / DriverSetupPassedTech
 DriverIncidentCount
 Drivers:
 - CarIdx: 0
   UserName: Max Mustermann
   AbbrevName / Initials / UserID / TeamID
   TeamName: Team XY
   CarNumber: "7"                # String, kann führende Nullen haben
   CarNumberRaw: 7
   CarPath / CarID
   CarScreenName: Porsche 911 GT3 R (992)
   CarScreenNameShort
   CarClassID: 4083
   CarClassShortName / CarClassRelSpeed / CarClassColor: 0xffda59
   CarClassMaxFuelPct: 1.000 %   # BoP-Füllgrad
   CarClassWeightPenalty: 0.000 kg
   CarClassPowerAdjust / CarClassEstLapTime
   CarIsPaceCar: 0               # 1 = Safety Car -> ausfiltern
   CarIsAI: 0
   CarIsElectric: 0
   IsSpectator: 0                # 1 = Zuschauer -> ausfiltern
   IRating / LicLevel / LicSubLevel / LicString / LicColor
   CurDriverIncidentCount / TeamIncidentCount
   ClubName / DivisionName

SplitTimeInfo:
 Sectors:
 - SectorNum: 0
   SectorStartPct: 0.000000      # Sektorstart als Anteil der Runde (für LapDistPct)
 - SectorNum: 1
   SectorStartPct: 0.330000

CarSetup: ...                    # Setup-Baum, autoabhängig
CameraInfo: ...
RadioInfo: ...
```

Abfrage-Syntax der Original-SDK (`irsdk_getSessionInfoStrValue`, nicht nötig) ist pfadbasiert, z. B.
`DriverInfo:Drivers:CarIdx:{5}UserName:`. Wir parsen das YAML vollständig.

---

## 6. Telemetrievariablen

Namen, Typen, Einheiten und Beschreibungen stammen aus `goirsdk/variables.md` (Dump realer Var-Header) und
sind damit gut belegt. Laufzeit-Typ und `count` trotzdem immer aus dem Var-Header nehmen. Die Einheit
`%` bedeutet einen **Anteil 0–1**, nicht 0–100. Arrays `[64]` werden über `CarIdx` (0–63) indiziert:
**Max. 64 Autos**. Ungenutzte Slots enthalten typischerweise −1 (z. B. `CarIdxLapDistPct`,
`CarIdxTrackSurface` = `NotInWorld`) oder 0. Das ist **UNVERIFIED**, deshalb gegen die `Drivers[]`-Liste filtern.

### 6.1 Session

| Name | Typ | Einheit | Bedeutung |
|---|---|---|---|
| `SessionTime` | double | s | Sekunden seit Sessionstart |
| `SessionTick` | int | | Update-Zähler |
| `SessionNum` | int | | Index in `SessionInfo.Sessions[]` |
| `SessionState` | int | `irsdk_SessionState` | siehe Enum |
| `SessionFlags` | bitfield | `irsdk_Flags` | siehe Bitfeld |
| `SessionUniqueID` | int | | Session-ID |
| `SessionTimeRemain` | double | s | verbleibende Zeit (`IRSDK_UNLIMITED_TIME` = unbegrenzt, **UNVERIFIED** ob exakt 604800) |
| `SessionTimeTotal` | double | s | Gesamtdauer |
| `SessionLapsRemain` | int | | alt, besser `SessionLapsRemainEx` |
| `SessionLapsRemainEx` | int | | verbleibende Runden (32767 = unbegrenzt) |
| `SessionLapsTotal` | int | | Gesamt-Runden |

`irsdk_SessionState`: 0 `Invalid`, 1 `GetInCar`, 2 `Warmup`, 3 `ParadeLaps`, 4 `Racing`, 5 `Checkered`, 6 `CoolDown`.

`irsdk_Flags` (`SessionFlags`, `CarIdxSessionFlags[64]`):

| Bit | Name | | Bit | Name |
|---|---|---|---|---|
| `0x00000001` | `checkered` | | `0x00010000` | `black` |
| `0x00000002` | `white` | | `0x00020000` | `disqualify` |
| `0x00000004` | `green` | | `0x00040000` | `servicible` (Service erlaubt, keine Flagge) |
| `0x00000008` | `yellow` | | `0x00080000` | `furled` |
| `0x00000010` | `red` | | `0x00100000` | `repair` (Meatball) |
| `0x00000020` | `blue` | | `0x00200000` | `dqScoringInvalid` (nur pyirsdk, neuer, **UNVERIFIED** Name) |
| `0x00000040` | `debris` | | `0x10000000` | `startHidden` |
| `0x00000080` | `crossed` | | `0x20000000` | `startReady` |
| `0x00000100` | `yellowWaving` | | `0x40000000` | `startSet` |
| `0x00000200` | `oneLapToGreen` | | `0x80000000` | `startGo` |
| `0x00000400` | `greenHeld` | | | |
| `0x00000800` | `tenToGo` | | | |
| `0x00001000` | `fiveToGo` | | | |
| `0x00002000` | `randomWaving` | | | |
| `0x00004000` | `caution` | | | |
| `0x00008000` | `cautionWaving` | | | |

Für „Lights out“ reicht der Wechsel von `startSet` auf `startGo` bzw. `green` bei `SessionState == Racing`.
Die genaue Abfolge bei Rolling Starts ist **UNVERIFIED**. Dafür gibt es `PaceMode` und `CarIdxPaceFlags`.

### 6.2 Spieler-Auto

| Name | Typ | Einheit | Bedeutung |
|---|---|---|---|
| `PlayerCarIdx` | int | | eigener CarIdx |
| `PlayerCarPosition` | int | | Gesamtposition |
| `PlayerCarClassPosition` | int | | Klassenposition |
| `PlayerTrackSurface` | int | `irsdk_TrkLoc` | Ort (Pitstall, Strecke …) |
| `PlayerTrackSurfaceMaterial` | int | `irsdk_TrkSurf` | Untergrund |
| `PlayerCarMyIncidentCount` | int | | eigene Incidents |
| `PlayerCarTeamIncidentCount` | int | | Team-Incidents |
| `PlayerCarDriverIncidentCount` | int | | Incidents des aktuellen Team-Fahrers |
| `PlayerCarInPitStall` | bool | | korrekt in der Box |
| `PlayerCarPitSvStatus` | int | `irsdk_PitSvStatus` | Pit-Service-Status |
| `PlayerTireCompound` | int | | aktuelle Mischung |
| `PlayerFastRepairsUsed` | int | | genutzte Fast Repairs |
| `OnPitRoad` | bool | | in der Boxengasse zwischen den Pylonen |
| `IsOnTrack` | bool | | Auto auf der Strecke, Physik läuft, Spieler im Auto |
| `IsOnTrackCar` | bool | | Auto auf der Strecke (auch ohne Spieler-Fokus) |
| `Lap` | int | | begonnene Runden |
| `LapCompleted` | int | | beendete Runden |
| `LapDist` | float | m | Meter seit S/F |
| `LapDistPct` | float | % | Anteil der Runde 0–1 |
| `LapCurrentLapTime` | float | s | Schätzung der laufenden Runde (F3-Box) |
| `LapLastLapTime` | float | s | letzte Runde (≤ 0 = ungültig/keine, **UNVERIFIED**) |
| `LapBestLapTime` | float | s | beste Runde |
| `LapDeltaToBestLap` | float | s | Delta zur eigenen Bestrunde (`_OK`-bool prüfen) |
| `LapDeltaToSessionBestLap` | float | s | Delta zur Session-Bestrunde |
| `Speed` | float | m/s | GPS-Geschwindigkeit |
| `RPM` | float | revs/min | Drehzahl |
| `Gear` | int | | −1 = R, 0 = N, 1..n |
| `Throttle` | float | % | Gas 0–1 |
| `Brake` | float | % | Bremse 0–1 (`BrakeRaw` ungefiltert) |
| `Clutch` | float | % | **0 = ausgekuppelt, 1 = eingekuppelt** (invertiert gegenüber Pedalweg) |
| `SteeringWheelAngle` | float | rad | Lenkwinkel |
| `FuelLevel` | float | l | Spritmenge |
| `FuelLevelPct` | float | % | Füllgrad 0–1 |
| `FuelUsePerHour` | float | **kg/h** | Momentanverbrauch, über `DriverCarFuelKgPerLtr` in l/h umrechnen |
| `YawRate` | float | rad/s | Gierrate |
| `LatAccel` | float | m/s² | Querbeschleunigung (inkl. Gravitation) |
| `LongAccel` | float | m/s² | Längsbeschleunigung (inkl. Gravitation) |
| `VelocityX` / `VelocityY` / `VelocityZ` | float | m/s | Geschwindigkeit im Fahrzeug-Koordinatensystem |
| `EngineWarnings` | bitfield | `irsdk_EngineWarnings` | Warnlampen |
| `CarLeftRight` | int | `irsdk_CarLeftRight` | Spotter (0 Off, 1 Clear, 2 CarLeft, 3 CarRight, 4 LeftRight, 5 2CarsLeft, 6 2CarsRight) |
| `PaceMode` | int | `irsdk_PaceMode` | 0 SingleFileStart, 1 DoubleFileStart, 2 SingleFileRestart, 3 DoubleFileRestart, 4 NotPacing |

Viele Fahrdynamik-Werte gibt es zusätzlich als `_ST`-Variante (`float[6]`, 360 Hz in 6 Sub-Samples pro Tick).

### 6.3 Reifen (Präfix `LF`, `RF`, `LR`, `RR`)

| Name | Typ | Einheit | Bedeutung |
|---|---|---|---|
| `LFtempCL` / `LFtempCM` / `LFtempCR` | float | °C | Karkassentemperatur links/mitte/rechts |
| `LFwearL` / `LFwearM` / `LFwearR` | float | % | verbleibendes Profil 0–1 |
| `LFcoldPressure` | float | kPa | Kaltdruck laut Garage |
| `LFTiresUsed` / `LFTiresAvailable` | int | | Reifensätze (255 = unbegrenzt) |
| `LFbrakeLinePress` | float | bar | Bremsleitungsdruck |

**Wichtige Einschränkung:** In der Live-Telemetrie werden Temperatur und Verschleiß i. d. R. **nur im Stand
in der Box aktualisiert**, weil sie von der Crew gemessen werden. Während der Fahrt bleiben sie konstant.
Das ist **UNVERIFIED** (Community-Erfahrung), sollte aber eingeplant werden: Reifenmodell über
Rundenzeiten und Sprit schätzen statt über Live-Temperaturen.

`LFpressure` (Heißdruck), `LFtempL/M/R` (Oberflächentemperatur), `LFspeed`, `LFrideHeight` usw. sind laut
`goirsdk/variables.md` **nicht** in der Live-Map enthalten. Sie gibt es nach Community-Angaben nur im
Disk-Logging (`.ibt`). **UNVERIFIED**.

### 6.4 Alle Autos (`CarIdx…`, jeweils `[64]`)

| Name | Typ | Einheit | Bedeutung |
|---|---|---|---|
| `CarIdxLapDistPct` | float[64] | % | Rundenanteil 0–1 (−1 = nicht in der Welt) |
| `CarIdxPosition` | int[64] | | Gesamtposition (0 = keine) |
| `CarIdxClassPosition` | int[64] | | Klassenposition |
| `CarIdxClass` | int[64] | | Klassen-ID |
| `CarIdxLap` | int[64] | | begonnene Runden |
| `CarIdxLapCompleted` | int[64] | | beendete Runden |
| `CarIdxLastLapTime` | float[64] | s | letzte Runde |
| `CarIdxBestLapTime` | float[64] | s | Bestrunde |
| `CarIdxBestLapNum` | int[64] | | Runde der Bestzeit |
| `CarIdxOnPitRoad` | bool[64] | | in der Boxengasse |
| `CarIdxTrackSurface` | int[64] | `irsdk_TrkLoc` | Ort |
| `CarIdxTrackSurfaceMaterial` | int[64] | `irsdk_TrkSurf` | Untergrund |
| `CarIdxF2Time` | float[64] | s | Rennen: Rückstand zum Führenden, sonst Bestrundenzeit. **UNVERIFIED** wie zuverlässig, Community nutzt oft eigene Abstandsrechnung |
| `CarIdxEstTime` | float[64] | s | geschätzte Zeit bis zur aktuellen Position auf der Runde (Basis für Live-Abstände) |
| `CarIdxTireCompound` | int[64] | | aktuelle Reifenmischung (Index; Mapping über Session-Info **UNVERIFIED**) |
| `CarIdxQualTireCompound` / `…Locked` | int/bool[64] | | Quali-Mischung |
| `CarIdxFastRepairsUsed` | int[64] | | genutzte Fast Repairs |
| `CarIdxSessionFlags` | bitfield[64] | `irsdk_Flags` | Flaggen je Auto |
| `CarIdxGear` / `CarIdxRPM` / `CarIdxSteer` | int/float[64] | | Gang, Drehzahl, Lenkung |
| `CarIdxPaceLine` / `CarIdxPaceRow` / `CarIdxPaceFlags` | int/bitfield[64] | | Pacing (`irsdk_PaceFlags`: 0x1 EndOfLine, 0x2 FreePass, 0x4 WavedAround) |

`irsdk_TrkLoc`: −1 `NotInWorld`, 0 `OffTrack`, 1 `InPitStall`, 2 `AproachingPits` (sic), 3 `OnTrack`.
`irsdk_TrkSurf`: −1 NotInWorld, 0 Undefined, 1–4 Asphalt1–4, 5–6 Concrete1–2, 7–8 RacingDirt1–2,
9–10 Paint1–2, 11–14 Rumble1–4, 15–18 Grass1–4, 19–22 Dirt1–4, 23 Sand, 24–25 Gravel1–2, 26 Grasscrete, 27 Astroturf.

### 6.5 Box und Pit-Service

| Name | Typ | Einheit | Bedeutung |
|---|---|---|---|
| `PitSvFlags` | bitfield | `irsdk_PitSvFlags` | gesetzte Pit-Checkboxen |
| `PitSvFuel` | float | l oder kWh | geplante Tankmenge |
| `PitSvLFP` / `PitSvRFP` / `PitSvLRP` / `PitSvRRP` | float | kPa | geplanter Reifendruck |
| `PitSvTireCompound` | int | | geplante Mischung |
| `PitRepairLeft` | float | s | verbleibende Pflicht-Reparaturzeit |
| `PitOptRepairLeft` | float | s | verbleibende optionale Reparaturzeit |
| `PitstopActive` | bool | | Pit-Service läuft gerade |
| `PitsOpen` | bool | | Boxenstopp erlaubt |
| `FastRepairAvailable` / `FastRepairUsed` | int | | Fast Repairs (255 = unbegrenzt) |
| `dpFuelFill` | float | | Flag „tanken“ (als float) |
| `dpFuelAddKg` | float | kg | geplante Tankmenge in kg |
| `dpFuelAutoFillEnabled` / `dpFuelAutoFillActive` | float | | Auto-Fill |
| `dpTireChange` | float | | alle Reifen wechseln |
| `dpLFTireColdPress` (…RF/LR/RR) | float | **Pa** | Druckanpassung |
| `dpWindshieldTearoff` / `dpFastRepair` | float | | Tear-off, Fast Repair |
| `dpWingFront` / `dpWingRear` | float | | Flügelanpassung (autoabhängig) |

`irsdk_PitSvFlags`: `0x01` LFTireChange, `0x02` RFTireChange, `0x04` LRTireChange, `0x08` RRTireChange,
`0x10` FuelFill, `0x20` WindshieldTearoff, `0x40` FastRepair.
`irsdk_PitSvStatus`: 0 None, 1 InProgress, 2 Complete, 100 TooFarLeft, 101 TooFarRight, 102 TooFarForward,
103 TooFarBack, 104 BadAngle, 105 CantFixThat.

`irsdk_EngineWarnings` (`EngineWarnings`):

| Bit | Name | Quelle |
|---|---|---|
| `0x0001` | `waterTempWarning` | Header 2013 |
| `0x0002` | `fuelPressureWarning` | Header 2013 |
| `0x0004` | `oilPressureWarning` | Header 2013 |
| `0x0008` | `engineStalled` | Header 2013 |
| `0x0010` | **`pitSpeedLimiter`** | Header 2013 |
| `0x0020` | `revLimiterActive` | Header 2013 |
| `0x0040` | `oilTempWarning` | pyirsdk (neuer) |
| `0x0080` | `mandRepNeeded` (Pflichtreparatur nötig) | pyirsdk (neuer) |
| `0x0100` | `optRepNeeded` (optionale Reparatur möglich) | pyirsdk (neuer) |

### 6.6 Umwelt und Wetter

| Name | Typ | Einheit | Bedeutung |
|---|---|---|---|
| `TrackTempCrew` | float | °C | Streckentemperatur (Crew-Messung) |
| `TrackTemp` | float | °C | veraltet, gleich `TrackTempCrew` |
| `AirTemp` | float | °C | Lufttemperatur an S/F |
| `AirDensity` / `AirPressure` | float | kg/m³ / Pa | Luftdichte, -druck |
| `WeatherDeclaredWet` | bool | | Rennleitung erlaubt Regenreifen |
| `Precipitation` | float | % | Niederschlag 0–1 |
| `TrackWetness` | int | `irsdk_TrackWetness` | 0 Unknown, 1 Dry, 2 MostlyDry, 3 VeryLightlyWet, 4 LightlyWet, 5 ModeratelyWet, 6 VeryWet, 7 ExtremelyWet |

---

## 7. Linux / Proton (Kurzfassung, Details in eigenem Dokument)

- iRacing nutzt unter Windows benannte File-Mappings (`Local\…`). Unter Wine/Proton existieren diese nur
  **innerhalb des Wineservers**, ein natives Linux-Programm sieht sie nicht (Wine-Bug 54015, „Accessing shared
  memory from Linux“).
- **Variante A (am einfachsten):** Unseren Reader als Windows-Python/`.exe` im selben Proton-Prefix starten,
  z. B. über `protontricks-launch` oder einen Launch-Wrapper. Der Reader liest dann normal per irsdk und
  leitet die Daten per UDP oder WebSocket an das native Backend weiter. Damit funktionieren auch das
  Data-Valid-Event und Pit-Broadcasts.
- **Variante B (Bridge nach `/dev/shm`):**
  - `Spacefreak18/simshmbridge`: legt die Map vorher auf Unix-Seite an und mappt sie per Helper-`.exe` in den
    Prefix. Unterstützt laut README **ac, pcars2, rf2**, iRacing ist dort nicht dokumentiert.
  - `srounce/winecarte` (GPL-2.0): spiegelt Win32-Mappings nach `/dev/shm` (`winecarte-run %command%`). Ob
    iRacing auf der Liste unterstützter Spiele steht: **UNVERIFIED**.
  - `poljar/shm-bridge`: ähnliches Prinzip.
  - Bei einer Bridge fehlt das Data-Valid-Event. Dann auf `tickCount` pollen (z. B. alle 5 ms).
- Hinweis aus der Suche: ein Go-Port (`0xkalle/irsdk-go`) nennt Kompatibilität mit iRacing unter
  Wine/CrossOver. Die iRacing-EOS/Anti-Cheat-Lage unter Proton ändert sich und muss separat geprüft werden.

---

## 8. Umsetzungs-Empfehlungen für PIT WALL

1. Parser in reinem Python mit `mmap` + `struct`. Keine Abhängigkeit von pyirsdk nötig, MIT erlaubt aber die
   Nutzung mit Lizenzvermerk. Abstraktion: `Source` (Windows-mmap | `/dev/shm`-Datei | `.ibt`-Replay | Dump-Datei).
2. `.ibt`-Dateien und pyirsdks `--dump` (roher Map-Dump) als Test-Fixtures nutzen. Beide haben dasselbe Layout
   wie die Live-Map.
3. Var-Lookup nach Namen und einmal pro Verbindung cachen. Fehlende Variablen tolerieren.
4. YAML asynchron parsen, mit Sanitizing (Abschnitt 5.2). Werte mit Einheiten selbst zerlegen.
5. Abstände: `CarIdxEstTime` + `CarIdxLapDistPct` + `CarIdxLapCompleted` statt `CarIdxF2Time` allein.
6. Pit-Befehle (Broadcast) nur als optionale Funktion, die nur im Wine-Prefix bzw. unter Windows läuft.

---

## Quellen

| Quelle | URL | Lizenz | Nutzung |
|---|---|---|---|
| iRacing SDK (offiziell, `irsdk_defines.h`, `irsdk_utils.cpp`, Doku), Forum-Thread „iRacing SDK“ | https://forums.iracing.com/discussion/62/iracing-sdk (URL **UNVERIFIED**, Login nötig) | BSD-3-Clause (Copyright 2013 iRacing.com Motorsport Simulations, LLC) | Primärquelle für Strukturen und Enums |
| Kopie `irsdk_defines.h` / `irsdk_utils.cpp` (2013er Stand) | https://github.com/vipoo/irsdk (`irsdk_defines.h`, `irsdk_utils.cpp`) | BSD-3-Clause (iRacing-Header) | gelesen und hier zitiert |
| Kopie `irsdk_defines.h` in node-irsdk | https://github.com/apihlaja/node-irsdk/blob/master/src/cpp/irsdk/irsdk_defines.h | Header BSD-3-Clause, node-irsdk selbst MIT (**UNVERIFIED**) | Abgleich |
| kutu/pyirsdk 1.3.7 (`irsdk.py`) | https://github.com/kutu/pyirsdk | **MIT** (Copyright 2014 Mihail Latyshov) | Abgleich neuer Enums, Header-Felder, YAML-Sanitizing |
| mpapenbr/goirsdk (`irsdk/types.go`, `variables.md`) | https://github.com/mpapenbr/goirsdk | **MIT** (Copyright 2023 Markus Papenbrock) | Variablenliste mit Typen und Einheiten |
| Nick Thissen iRacingSdkWrapper (C#) | https://github.com/NickThissen/iRacingSdkWrapper | **GPL-3.0**, keinen Code übernehmen | nur Referenz |
| IRSDKSharper (C#, mherbold) | https://github.com/mherbold/IRSDKSharper | Lizenz **UNVERIFIED** | nicht genutzt, aktuelle Referenz-Implementierung |
| simshmbridge | https://github.com/Spacefreak18/simshmbridge | Lizenz **UNVERIFIED** (keine LICENSE-Datei auf `master` gefunden) | Linux-Bridge-Konzept |
| winecarte | https://github.com/srounce/winecarte | GPL-2.0 | Linux-Bridge-Konzept |
| shm-bridge | https://github.com/poljar/shm-bridge | **UNVERIFIED** | Linux-Bridge-Konzept |
| WineHQ Bug 54015 | https://bugs.winehq.org/show_bug.cgi?id=54015 | – | Hintergrund Shared Memory unter Wine |
| irsdk-go (Wine/CrossOver-kompatibel) | https://github.com/0xkalle/irsdk-go | **UNVERIFIED** | Hinweis Linux |
