# Assetto Corsa Competizione (ACC) – Telemetrie-Schnittstellen (+ AC EVO)

Stand der Recherche: 2026-10-28. Für das originale AC (2014) siehe [`assetto_corsa.md`](assetto_corsa.md).

ACC bietet **zwei** offizielle Schnittstellen:

1. **Shared Memory.** Gleiche Map-Namen wie AC, aber erweiterte Structs. Nur lokal unter Windows. Liefert vor allem das eigene Auto,
   dazu **Weltpositionen aller Autos** (`carCoordinates[60][3]` + `carID[60]`).
2. **Broadcasting API** über UDP: ein Protokoll mit variabler Länge für Entry List, Timing und Positionen **aller** Autos, Wetter und Session-Phase.
   Funktioniert lokal und im LAN. **Der Client registriert sich bei ACC.**

Legende: **UNVERIFIED** = nicht gegen eine Primärquelle geprüft. Primärquelle für das Shared Memory ist die offizielle Kunos-PDF
**„ACC Shared Memory v1.8.12“** (`ACCSharedMemoryDocumentationV1.8.12.pdf`, im Repo `rrennoir/PyAccSharedMemory` mitgeliefert und hier
vollständig ausgewertet). Die Offsets wurden mit eigenem Layout-Rechner bestimmt und mit `ctypes` (`_pack_=4`) und `struct.calcsize` gegengeprüft.

---

## 1. Shared Memory

### 1.1 Grundlagen

| Seite | Map-Name | Struct | Größe (berechnet) | Gegenprobe |
|---|---|---|---|---|
| Physics | `Local\acpmf_physics` | `SPageFilePhysics` | **800 B** | = Map-Größe in PyAccSharedMemory (800) |
| Graphics | `Local\acpmf_graphics` | `SPageFileGraphic` | **1588 B** | = Map-Größe in PyAccSharedMemory (1588) |
| Static | `Local\acpmf_static` | `SPageFileStatic` | **820 B** (siehe Hinweis zu `trackConfiguration`) | PyAccSharedMemory mappt 784 B, liest aber sequentiell 820 B |

- Layout: `#pragma pack(4)`, Little Endian, `int` = int32, `float` = float32, `wchar_t` = **2 Bytes UTF-16LE** (Windows).
  Enums sind int32. Hinter `wchar_t[33]` und `wchar_t[15]` entsteht Padding, wenn ein 4-Byte-Feld folgt. Es ist in den Tabellen als `(Padding)` eingetragen.
- Die PDF unterscheidet drei Kategorien: „working as intended“, „not used by ACC“ und „new / reworked since last build“. Felder mit dem Vermerk **nicht genutzt** liefern in ACC 0 oder Datenmüll,
  ihr Platz bleibt aber im Layout erhalten (Kompatibilität mit AC).
- Update-Raten: Die PDF sagt „Physics: change at each graphic step“ (sic) und „Graphics: updated at each graphical step“. Die Static-Seite wird beim Instanzstart geschrieben.
- ACC-Version: Die PDF trägt v1.8.12. Ob ACC 1.9.x/1.10.x das Layout noch verändert hat, ist **UNVERIFIED**. Keine der geprüften Quellen
  kennt spätere Felder. Empfehlung: 4096 B mappen und nur den bekannten Präfix lesen.
- Python: `mmap.mmap(-1, 4096, tagname="Local\\acpmf_graphics", access=mmap.ACCESS_READ)`. Strings mit
  `b.decode("utf-16-le").split("\x00",1)[0]` dekodieren (nicht `ctypes.c_wchar`, das ist unter Linux 4 Bytes groß).
- Konsistenz: `packetId` vor und nach dem Kopieren vergleichen.

### 1.2 `SPageFilePhysics` (800 B)

Offsets 0–579 sind **byte-identisch mit AC** (siehe `assetto_corsa.md`). ACC hängt ab 580 weitere Felder an. Räder: FL, FR, RL, RR.

| Offset | C-Typ / Feld | Bytes | `struct` | Hinweis (PDF v1.8.12) |
|---:|---|---:|---|---|
| 0 | `int packetId` | 4 | `i` |  |
| 4 | `float gas` | 4 | `f` | 0..1 |
| 8 | `float brake` | 4 | `f` | 0..1 |
| 12 | `float fuel` | 4 | `f` | PDF: „kg“ (sic), praktisch Liter |
| 16 | `int gear` | 4 | `i` | 0=R, 1=N, 2=1. Gang |
| 20 | `int rpm` | 4 | `i` |  |
| 24 | `float steerAngle` | 4 | `f` | -1..1 |
| 28 | `float speedKmh` | 4 | `f` |  |
| 32 | `float velocity[3]` | 12 | `3f` | global |
| 44 | `float accG[3]` | 12 | `3f` | global |
| 56 | `float wheelSlip[4]` | 16 | `4f` |  |
| 72 | `float wheelLoad[4]` | 16 | `4f` |  |
| 88 | `float wheelPressure[4]` | 16 | `4f` | psi |
| 104 | `float wheelAngularSpeed[4]` | 16 | `4f` | rad/s |
| 120 | `float tyreWear[4]` | 16 | `4f` |  |
| 136 | `float tyreDirtyLevel[4]` | 16 | `4f` |  |
| 152 | `float TyreCoreTemp[4]` | 16 | `4f` | Kerntemperatur |
| 168 | `float camberRAD[4]` | 16 | `4f` |  |
| 184 | `float suspensionTravel[4]` | 16 | `4f` |  |
| 200 | `float drs` | 4 | `f` |  |
| 204 | `float tc` | 4 | `f` | TC in action |
| 208 | `float heading` | 4 | `f` | Yaw |
| 212 | `float pitch` | 4 | `f` |  |
| 216 | `float roll` | 4 | `f` |  |
| 220 | `float cgHeight` | 4 | `f` |  |
| 224 | `float carDamage[5]` | 20 | `5f` | front 0, rear 1, left 2, right 3, centre 4 |
| 244 | `int numberOfTyresOut` | 4 | `i` |  |
| 248 | `int pitLimiterOn` | 4 | `i` |  |
| 252 | `float abs` | 4 | `f` | ABS in action |
| 256 | `float kersCharge` | 4 | `f` | nicht genutzt |
| 260 | `float kersInput` | 4 | `f` | nicht genutzt |
| 264 | `int autoshifterOn` | 4 | `i` |  |
| 268 | `float rideHeight[2]` | 8 | `2f` | front, rear |
| 276 | `float turboBoost` | 4 | `f` |  |
| 280 | `float ballast` | 4 | `f` | nicht implementiert |
| 284 | `float airDensity` | 4 | `f` |  |
| 288 | `float airTemp` | 4 | `f` |  |
| 292 | `float roadTemp` | 4 | `f` |  |
| 296 | `float localAngularVel[3]` | 12 | `3f` |  |
| 308 | `float finalFF` | 4 | `f` |  |
| 312 | `float performanceMeter` | 4 | `f` | nicht genutzt |
| 316 | `int engineBrake` | 4 | `i` | nicht genutzt |
| 320 | `int ersRecoveryLevel` | 4 | `i` | nicht genutzt |
| 324 | `int ersPowerLevel` | 4 | `i` | nicht genutzt |
| 328 | `int ersHeatCharging` | 4 | `i` | nicht genutzt |
| 332 | `int ersIsCharging` | 4 | `i` | nicht genutzt |
| 336 | `float kersCurrentKJ` | 4 | `f` | nicht genutzt |
| 340 | `int drsAvailable` | 4 | `i` | nicht genutzt |
| 344 | `int drsEnabled` | 4 | `i` | nicht genutzt |
| 348 | `float brakeTemp[4]` | 16 | `4f` | Bremsscheiben |
| 364 | `float clutch` | 4 | `f` |  |
| 368 | `float tyreTempI[4]` | 16 | `4f` | nicht gezeigt |
| 384 | `float tyreTempM[4]` | 16 | `4f` | nicht gezeigt |
| 400 | `float tyreTempO[4]` | 16 | `4f` | nicht gezeigt |
| 416 | `int isAIControlled` | 4 | `i` |  |
| 420 | `float tyreContactPoint[4][3]` | 48 | `12f` |  |
| 468 | `float tyreContactNormal[4][3]` | 48 | `12f` |  |
| 516 | `float tyreContactHeading[4][3]` | 48 | `12f` |  |
| 564 | `float brakeBias` | 4 | `f` | Anzeige-Offset je Auto (PDF Appendix 4) |
| 568 | `float localVelocity[3]` | 12 | `3f` |  |
| 580 | `int P2PActivation` | 4 | `i` | nicht genutzt |
| 584 | `int P2PStatus` | 4 | `i` | nicht genutzt |
| 588 | `float currentMaxRpm` | 4 | `f` | PDF: float (PyAccSharedMemory liest int) |
| 592 | `float mz[4]` | 16 | `4f` | nicht gezeigt |
| 608 | `float fx[4]` | 16 | `4f` | nicht gezeigt |
| 624 | `float fy[4]` | 16 | `4f` | nicht gezeigt |
| 640 | `float slipRatio[4]` | 16 | `4f` |  |
| 656 | `float slipAngle[4]` | 16 | `4f` |  |
| 672 | `int tcinAction` | 4 | `i` |  |
| 676 | `int absInAction` | 4 | `i` |  |
| 680 | `float suspensionDamage[4]` | 16 | `4f` |  |
| 696 | `float tyreTemp[4]` | 16 | `4f` | Kerntemperatur |
| 712 | `float waterTemp` | 4 | `f` |  |
| 716 | `float brakePressure[4]` | 16 | `4f` | Dash-Koeffizient je Auto (PDF Appendix 3) |
| 732 | `int frontBrakeCompound` | 4 | `i` |  |
| 736 | `int rearBrakeCompound` | 4 | `i` |  |
| 740 | `float padLife[4]` | 16 | `4f` | Belagverschleiß |
| 756 | `float discLife[4]` | 16 | `4f` | Scheibenverschleiß |
| 772 | `int ignitionOn` | 4 | `i` |  |
| 776 | `int starterEngineOn` | 4 | `i` |  |
| 780 | `int isEngineRunning` | 4 | `i` |  |
| 784 | `float kerbVibration` | 4 | `f` |  |
| 788 | `float slipVibrations` | 4 | `f` |  |
| 792 | `float gVibrations` | 4 | `f` |  |
| 796 | `float absVibrations` | 4 | `f` |  |

**Gesamt: 800 B.** Rechnung: 580 (AC-Präfix) + 3×4 (592) + 5×16 (672) + 2×4 (680) + 2×16 (712) + 4 (716) + 16 (732)
+ 2×4 (740) + 2×16 (772) + 7×4 (800).

```python
ACC_PHYSICS_FMT = "<ifffiiff3f3f4f4f4f4f4f4f4f4f4fffffff5fiifffi2ffffff3fffiiiiifii4ff4f4f4fi12f12f12ff3fiif4f4f4f4f4fii4f4ff4fii4f4fiiiffff"
assert struct.calcsize(ACC_PHYSICS_FMT) == 800
```

### 1.3 `SPageFileGraphic` (1588 B)

PDF: „mostly refer to player's car **except for carCoordinates and carID, which refer to the cars currently on track**.“

| Offset | C-Typ / Feld | Bytes | `struct` | Hinweis |
|---:|---|---:|---|---|
| 0 | `int packetId` | 4 | `i` |  |
| 4 | `ACC_STATUS status` | 4 | `i` |  |
| 8 | `ACC_SESSION_TYPE session` | 4 | `i` |  |
| 12 | `wchar_t currentTime[15]` | 30 | `30s` |  |
| 42 | `wchar_t lastTime[15]` | 30 | `30s` |  |
| 72 | `wchar_t bestTime[15]` | 30 | `30s` |  |
| 102 | `wchar_t split[15]` | 30 | `30s` |  |
| 132 | `int completedLaps` | 4 | `i` |  |
| 136 | `int position` | 4 | `i` | eigene Position |
| 140 | `int iCurrentTime` | 4 | `i` | ms |
| 144 | `int iLastTime` | 4 | `i` | ms |
| 148 | `int iBestTime` | 4 | `i` | ms |
| 152 | `float sessionTimeLeft` | 4 | `f` | ms (UNVERIFIED Einheit) |
| 156 | `float distanceTraveled` | 4 | `f` | im aktuellen Stint |
| 160 | `int isInPit` | 4 | `i` |  |
| 164 | `int currentSectorIndex` | 4 | `i` |  |
| 168 | `int lastSectorTime` | 4 | `i` | ms |
| 172 | `int numberOfLaps` | 4 | `i` |  |
| 176 | `wchar_t tyreCompound[33]` | 66 | `66s` |  |
| 242 | (Padding) | 2 | `2x` |  |
| 244 | `float replayTimeMultiplier` | 4 | `f` | nicht genutzt |
| 248 | `float normalizedCarPosition` | 4 | `f` | 0..1 Spline |
| 252 | `int activeCars` | 4 | `i` | Anzahl Autos auf der Strecke |
| 256 | `float carCoordinates[60][3]` | 720 | `180f` | **alle Autos**, `[i][x,y,z]` |
| 976 | `int carID[60]` | 240 | `60i` | `carID[i]` gehört zu `carCoordinates[i]` |
| 1216 | `int playerCarID` | 4 | `i` | Spieler-ID (zum Abgleich mit `carID[]`) |
| 1220 | `float penaltyTime` | 4 | `f` |  |
| 1224 | `ACC_FLAG_TYPE flag` | 4 | `i` |  |
| 1228 | `ACC_PENALTY_TYPE penalty` | 4 | `i` | Werte außerhalb des Enums kommen vor (PyAcc fängt das ab) |
| 1232 | `int idealLineOn` | 4 | `i` |  |
| 1236 | `int isInPitLane` | 4 | `i` |  |
| 1240 | `float surfaceGrip` | 4 | `f` |  |
| 1244 | `int mandatoryPitDone` | 4 | `i` |  |
| 1248 | `float windSpeed` | 4 | `f` | m/s |
| 1252 | `float windDirection` | 4 | `f` | rad |
| 1256 | `int isSetupMenuVisible` | 4 | `i` |  |
| 1260 | `int mainDisplayIndex` | 4 | `i` | PDF Appendix 1 |
| 1264 | `int secondaryDisplayIndex` | 4 | `i` | PDF schreibt `secondaryDisplyIndex` |
| 1268 | `int TC` | 4 | `i` | Stufe |
| 1272 | `int TCCUT` | 4 | `i` |  |
| 1276 | `int EngineMap` | 4 | `i` |  |
| 1280 | `int ABS` | 4 | `i` | Stufe |
| 1284 | `float fuelXLap` | 4 | `f` | Ø Verbrauch/Runde (l) |
| 1288 | `int rainLights` | 4 | `i` |  |
| 1292 | `int flashingLights` | 4 | `i` |  |
| 1296 | `int lightsStage` | 4 | `i` |  |
| 1300 | `float exhaustTemperature` | 4 | `f` |  |
| 1304 | `int wiperLV` | 4 | `i` |  |
| 1308 | `int driverStintTotalTimeLeft` | 4 | `i` | ms |
| 1312 | `int driverStintTimeLeft` | 4 | `i` | ms |
| 1316 | `int rainTyres` | 4 | `i` |  |
| 1320 | `int sessionIndex` | 4 | `i` | PyAcc: „immer 0“ |
| 1324 | `float usedFuel` | 4 | `f` | seit letztem Tanken |
| 1328 | `wchar_t deltaLapTime[15]` | 30 | `30s` |  |
| 1358 | (Padding) | 2 | `2x` |  |
| 1360 | `int iDeltaLapTime` | 4 | `i` | ms |
| 1364 | `wchar_t estimatedLapTime[15]` | 30 | `30s` |  |
| 1394 | (Padding) | 2 | `2x` |  |
| 1396 | `int iEstimatedLapTime` | 4 | `i` | ms (PDF vertauscht die Beschreibungen) |
| 1400 | `int isDeltaPositive` | 4 | `i` |  |
| 1404 | `int iSplit` | 4 | `i` | ms |
| 1408 | `int isValidLap` | 4 | `i` |  |
| 1412 | `float fuelEstimatedLaps` | 4 | `f` |  |
| 1416 | `wchar_t trackStatus[33]` | 66 | `66s` |  |
| 1482 | (Padding) | 2 | `2x` |  |
| 1484 | `int missingMandatoryPits` | 4 | `i` |  |
| 1488 | `float Clock` | 4 | `f` | Tageszeit in s |
| 1492 | `int directionLightsLeft` | 4 | `i` |  |
| 1496 | `int directionLightsRight` | 4 | `i` |  |
| 1500 | `int GlobalYellow` | 4 | `i` |  |
| 1504 | `int GlobalYellow1` | 4 | `i` | Sektor 1 |
| 1508 | `int GlobalYellow2` | 4 | `i` | Sektor 2 |
| 1512 | `int GlobalYellow3` | 4 | `i` | Sektor 3 |
| 1516 | `int GlobalWhite` | 4 | `i` |  |
| 1520 | `int GlobalGreen` | 4 | `i` |  |
| 1524 | `int GlobalChequered` | 4 | `i` |  |
| 1528 | `int GlobalRed` | 4 | `i` |  |
| 1532 | `int mfdTyreSet` | 4 | `i` |  |
| 1536 | `float mfdFuelToAdd` | 4 | `f` |  |
| 1540 | `float mfdTyrePressureLF` | 4 | `f` |  |
| 1544 | `float mfdTyrePressureRF` | 4 | `f` |  |
| 1548 | `float mfdTyrePressureLR` | 4 | `f` |  |
| 1552 | `float mfdTyrePressureRR` | 4 | `f` |  |
| 1556 | `ACC_TRACK_GRIP_STATUS trackGripStatus` | 4 | `i` |  |
| 1560 | `ACC_RAIN_INTENSITY rainIntensity` | 4 | `i` |  |
| 1564 | `ACC_RAIN_INTENSITY rainIntensityIn10min` | 4 | `i` |  |
| 1568 | `ACC_RAIN_INTENSITY rainIntensityIn30min` | 4 | `i` |  |
| 1572 | `int currentTyreSet` | 4 | `i` |  |
| 1576 | `int strategyTyreSet` | 4 | `i` |  |
| 1580 | `int gapAhead` | 4 | `i` | ms zum Vordermann |
| 1584 | `int gapBehind` | 4 | `i` | ms zum Hintermann |

**Gesamt: 1588 B.** Rechnung: 12 + 120 + 44 (=176) + 66 + 2 (=244) + 3×4 (=256) + 720 (=976) + 240 (=1216) + 28×4 (=1328)
+ 30 + 2 + 4 (=1364) + 30 + 2 + 5×4 (=1416) + 66 + 2 (=1484) + 26×4 (=1588).

```python
ACC_GRAPHIC_FMT = "<iii30s30s30s30siiiiiffiiii66s2xffi180f60iifiiiififfiiiiiiifiiifiiiiif30s2xi30s2xiiiif66s2xifiiiiiiiiiiifffffiiiiiiii"
assert struct.calcsize(ACC_GRAPHIC_FMT) == 1588
```

> Hinweis zu `simapi/acdata.h`: Dieser Header lässt in seinem gemeinsamen AC/ACC-Struct `activeCars`, `carCoordinates[60][3]`, `carID[60]` und `playerCarID` weg
> und deklariert die Strings ab `deltaLapTime` als `char`. Für ACC ist das **falsch**. Maßgeblich sind die PDF und PyAccSharedMemory. Die Gesamtgröße 1588
> stimmt exakt mit der Map-Größe in PyAccSharedMemory überein.

### 1.4 `SPageFileStatic` (820 B)

| Offset | C-Typ / Feld | Bytes | `struct` | Hinweis |
|---:|---|---:|---|---|
| 0 | `wchar_t smVersion[15]` | 30 | `30s` |  |
| 30 | `wchar_t acVersion[15]` | 30 | `30s` |  |
| 60 | `int numberOfSessions` | 4 | `i` |  |
| 64 | `int numCars` | 4 | `i` |  |
| 68 | `wchar_t carModel[33]` | 66 | `66s` | Kunos-ID, z. B. `ferrari_488_gt3_evo` (PDF Appendix 2) |
| 134 | `wchar_t track[33]` | 66 | `66s` |  |
| 200 | `wchar_t playerName[33]` | 66 | `66s` |  |
| 266 | `wchar_t playerSurname[33]` | 66 | `66s` |  |
| 332 | `wchar_t playerNick[33]` | 66 | `66s` |  |
| 398 | (Padding) | 2 | `2x` |  |
| 400 | `int sectorCount` | 4 | `i` |  |
| 404 | `float maxTorque` | 4 | `f` | nicht gezeigt |
| 408 | `float maxPower` | 4 | `f` | nicht gezeigt |
| 412 | `int maxRpm` | 4 | `i` |  |
| 416 | `float maxFuel` | 4 | `f` |  |
| 420 | `float suspensionMaxTravel[4]` | 16 | `4f` | nicht gezeigt |
| 436 | `float tyreRadius[4]` | 16 | `4f` | nicht gezeigt |
| 452 | `float maxTurboBoost` | 4 | `f` |  |
| 456 | `float deprecated_1` | 4 | `f` |  |
| 460 | `float deprecated_2` | 4 | `f` |  |
| 464 | `int penaltiesEnabled` | 4 | `i` |  |
| 468 | `float aidFuelRate` | 4 | `f` |  |
| 472 | `float aidTireRate` | 4 | `f` |  |
| 476 | `float aidMechanicalDamage` | 4 | `f` |  |
| 480 | `float AllowTyreBlankets` | 4 | `f` | PDF: float (AC: int) |
| 484 | `float aidStability` | 4 | `f` |  |
| 488 | `int aidAutoClutch` | 4 | `i` |  |
| 492 | `int aidAutoBlip` | 4 | `i` | in ACC immer 1 |
| 496 | `int hasDRS` | 4 | `i` | nicht genutzt |
| 500 | `int hasERS` | 4 | `i` | nicht genutzt |
| 504 | `int hasKERS` | 4 | `i` | nicht genutzt |
| 508 | `float kersMaxJ` | 4 | `f` | nicht genutzt |
| 512 | `int engineBrakeSettingsCount` | 4 | `i` | nicht genutzt |
| 516 | `int ersPowerControllerCount` | 4 | `i` | nicht genutzt |
| 520 | `float trackSplineLength` | 4 | `f` | nicht genutzt |
| 524 | `wchar_t trackConfiguration[33]` | 66 | `66s` | nicht genutzt. **Länge in der PDF nicht angegeben**, siehe unten |
| 590 | (Padding) | 2 | `2x` |  |
| 592 | `float ersMaxJ` | 4 | `f` | nicht genutzt |
| 596 | `int isTimedRace` | 4 | `i` | nicht genutzt |
| 600 | `int hasExtraLap` | 4 | `i` | nicht genutzt |
| 604 | `wchar_t carSkin[33]` | 66 | `66s` | nicht genutzt |
| 670 | (Padding) | 2 | `2x` |  |
| 672 | `int reversedGridPositions` | 4 | `i` | nicht genutzt |
| 676 | `int PitWindowStart` | 4 | `i` | Öffnung Boxenfenster |
| 680 | `int PitWindowEnd` | 4 | `i` | Schließung Boxenfenster |
| 684 | `int isOnline` | 4 | `i` | Multiplayer |
| 688 | `wchar_t dryTyresName[33]` | 66 | `66s` |  |
| 754 | `wchar_t wetTyresName[33]` | 66 | `66s` |  |

**Gesamt: 820 B.** Rechnung: 400 (wie AC) + 124 (=524) + 66 + 2 (=592) + 12 + 66 + 2 (=672) + 4×4 (=688) + 2×66 (=820). Kein End-Padding nötig.

```python
ACC_STATIC_FMT = "<30s30sii66s66s66s66s66s2xiffif4f4ffffifffffiiiiifiif66s2xfii66s2xiiii66s66s"
assert struct.calcsize(ACC_STATIC_FMT) == 820
```

> **`trackConfiguration`: UNVERIFIED `[33]` vs. `[15]`.** Die PDF schreibt nur `wchar_t trackConfiguration` ohne Länge.
> PyAccSharedMemory liest 33 Zeichen + 2 Pad (Layout 820 B), mappt aber nur **784 B**. Genau diese Größe ergibt sich mit `[15]`
> (30 B + 2 Pad, also −36 B). Mit `[15]` verschieben sich alle Felder ab `ersMaxJ` um −36: `PitWindowStart` liegt dann bei 640, `isOnline` bei 648,
> `dryTyresName` bei 652 und `wetTyresName` bei 718. Für den Race Engineer sind `PitWindowStart/End`, `isOnline` und die Reifennamen relevant.
> **Empfehlung:** 4096 B mappen und beide Layouts parsen. Gewählt wird das Layout, bei dem `isOnline ∈ {0,1}` gilt und `dryTyresName` als
> plausibler, druckbarer Text dekodiert. Einmal live gegen ACC prüfen und den Befund hier nachtragen.

### 1.5 Enums (int32), PDF v1.8.12

```text
ACC_STATUS:            ACC_OFF=0, ACC_REPLAY=1, ACC_LIVE=2, ACC_PAUSE=3

ACC_SESSION_TYPE:      ACC_UNKNOWN=-1, ACC_PRACTICE=0, ACC_QUALIFY=1, ACC_RACE=2, ACC_HOTLAP=3,
                       ACC_TIMEATTACK=4, ACC_DRIFT=5, ACC_DRAG=6, ACC_HOTSTINT=7, ACC_HOTSTINTSUPERPOLE=8
                       (ACHTUNG: andere Werte als RaceSessionType der Broadcasting API!)

ACC_FLAG_TYPE:         ACC_NO_FLAG=0, ACC_BLUE_FLAG=1, ACC_YELLOW_FLAG=2, ACC_BLACK_FLAG=3,
                       ACC_WHITE_FLAG=4, ACC_CHECKERED_FLAG=5, ACC_PENALTY_FLAG=6,
                       ACC_GREEN_FLAG=7, ACC_ORANGE_FLAG=8

ACC_PENALTY_TYPE:      ACC_None=0,
                       ACC_DriveThrough_Cutting=1, ACC_StopAndGo_10_Cutting=2, ACC_StopAndGo_20_Cutting=3,
                       ACC_StopAndGo_30_Cutting=4, ACC_Disqualified_Cutting=5, ACC_RemoveBestLaptime_Cutting=6,
                       ACC_DriveThrough_PitSpeeding=7, ACC_StopAndGo_10_PitSpeeding=8, ACC_StopAndGo_20_PitSpeeding=9,
                       ACC_StopAndGo_30_PitSpeeding=10, ACC_Disqualified_PitSpeeding=11,
                       ACC_RemoveBestLaptime_PitSpeeding=12, ACC_Disqualified_IgnoredMandatoryPit=13,
                       ACC_PostRaceTime=14, ACC_Disqualified_Trolling=15, ACC_Disqualified_PitEntry=16,
                       ACC_Disqualified_PitExit=17, ACC_Disqualified_Wrongway=18,
                       ACC_DriveThrough_IgnoredDriverStint=19, ACC_Disqualified_IgnoredDriverStint=20,
                       ACC_Disqualified_ExceededDriverStintLimit=21

ACC_TRACK_GRIP_STATUS: ACC_GREEN=0, ACC_FAST=1, ACC_OPTIMUM=2, ACC_GREASY=3, ACC_DAMP=4, ACC_WET=5, ACC_FLOODED=6

ACC_RAIN_INTENSITY:    ACC_NO_RAIN=0, ACC_DRIZZLE=1, ACC_LIGHT_RAIN=2, ACC_MEDIUM_RAIN=3, ACC_HEAVY_RAIN=4, ACC_THUNDERSTORM=5

ACC_WHEELS_TYPE:       ACC_FrontLeft=0, ACC_FrontRight=1, ACC_RearLeft=2, ACC_RearRight=3
```

Die PDF-Anhänge enthalten außerdem je Auto `mainDisplayIndex`-Seiten, Kunos-IDs (`carModel`), `brakePressure`-Koeffizienten,
`brakeBias`-Offsets, den maximalen Lenkwinkel, `CarModelId` (identisch mit dem `carModelType` der Broadcasting API) und die Max-RPM. Stand der Liste: Challengers Pack 2022.
Für spätere DLCs (GT2, 2023/24-Autos) fehlen die Werte (**UNVERIFIED**).

### 1.6 Andere Autos im Shared Memory

- `activeCars` (Offset 252), `carCoordinates[60][3]` (256) und `carID[60]` (976) liefern **Weltpositionen** aller Autos auf der Strecke (max. 60).
  `playerCarID` (1216) identifiziert den Spieler. Namen, Positionen im Klassement und Rundenzeiten anderer Autos gibt es dort **nicht**.
- Für den Spieler: `position`, `gapAhead`/`gapBehind` (ms), Flaggen.
- Ob `carID` mit dem `carIndex` der Broadcasting API übereinstimmt, ist **UNVERIFIED**. Live prüfen, dann lassen sich beide Quellen joinen.

---

## 2. Broadcasting API (UDP)

Kunos liefert ein **C#-SDK** mit dem Spiel aus (`steamapps/common/Assetto Corsa Competizione/sdk/broadcasting/`, Projekt `ksBroadcastingNetwork`
mit `BroadcastingNetworkProtocol.cs`). Die Beschreibung unten folgt diesem SDK (Mirror der frühen v2-Fassung: `angel-git/acc-broadcasting`).
Die Änderungen der aktuellen **Protokollversion 4** sind aus zwei unabhängigen Ports abgeleitet: Go `toonknapen/accbroadcastingsdk` v3 und
Rust `bradfier/acbc` (inkl. pcap-Testdaten).

### 2.1 Konfiguration und Netzwerk

- Datei: `%USERPROFILE%\Documents\Assetto Corsa Competizione\Config\broadcasting.json`

  ```json
  { "updListenerPort": 9000, "connectionPassword": "asd", "commandPassword": "" }
  ```

  - Der Schlüssel heißt tatsächlich **`updListenerPort`** (Tippfehler von Kunos, nicht „udp“).
  - Bei Port `0` bzw. ohne Eintrag ist der Listener aus (**UNVERIFIED**, ob der Default inzwischen 9000 ist). Nach einer Änderung ACC neu starten.
  - Die Datei ist bei ACC typischerweise **UTF-16LE** kodiert. Beim programmatischen Schreiben die Kodierung beibehalten (Community-Wissen, **UNVERIFIED**).
- **ACC ist der UDP-Server** und lauscht auf `updListenerPort`. Der **Client startet** mit `REGISTER_COMMAND_APPLICATION` an `<ACC-IP>:<port>`.
  ACC antwortet an Absender-IP und -Port. Das funktioniert auf `127.0.0.1` und im **LAN** (Firewall freigeben), da das API für Broadcast- bzw. Regie-Rechner gedacht ist.
- `connectionPassword` ist Pflicht für jede Verbindung. `commandPassword` wird nur für schreibende Befehle gebraucht (Kamera, HUD, Replay).
  Ohne bzw. mit falschem Command-Passwort gilt die Verbindung als read-only.
- Nach erfolgreicher Registrierung sendet ACC alle `msRealtimeUpdateInterval` ms ein `REALTIME_UPDATE` und **je Auto** ein `REALTIME_CAR_UPDATE`.
  `ENTRY_LIST`/`TRACK_DATA` kommen auf Anfrage und bei Änderungen.
- Ein UDP-Datagramm entspricht einer Nachricht. Der Empfangspuffer sollte großzügig sein (Go-SDK: 32 KiB).

### 2.2 Encoding-Grundregeln

- Little Endian. **Erstes Byte jeder Nachricht = Message-Typ** (`uint8`).
- `string` = **`uint16` Länge in Bytes, danach UTF-8-Bytes**, ohne NUL.
- `bool` = `uint8` (≠0 → true).
- Ungültige Zeiten oder Splits = `Int32.MaxValue` (2147483647), entspricht „keine Zeit“.
- Python-Hilfsfunktionen:

  ```python
  import struct
  def rd_str(b, o):
      (n,) = struct.unpack_from("<H", b, o); o += 2
      return b[o:o+n].decode("utf-8", "replace"), o + n
  def wr_str(s: str) -> bytes:
      d = s.encode("utf-8"); return struct.pack("<H", len(d)) + d
  ```

### 2.3 Outbound (Client → ACC)

| Typ | Name | Payload nach dem Typ-Byte |
|---:|---|---|
| 1 | `REGISTER_COMMAND_APPLICATION` | `u8 protocolVersion (=4)`, `string displayName`, `string connectionPassword`, `int32 msRealtimeUpdateInterval`, `string commandPassword` |
| 9 | `UNREGISTER_COMMAND_APPLICATION` | `int32 connectionId` |
| 10 | `REQUEST_ENTRY_LIST` | `int32 connectionId` |
| 11 | `REQUEST_TRACK_DATA` | `int32 connectionId` |
| 49 | `CHANGE_HUD_PAGE` | `int32 connectionId`, `string hudPage` |
| 50 | `CHANGE_FOCUS` | `int32 connectionId`, `u8 hasCar` [+ `u16 carIndex`], `u8 hasCamera` [+ `string cameraSet`, `string camera`] |
| 51 | `INSTANT_REPLAY_REQUEST` | `int32 connectionId`, `float startSessionTime`, `float durationMS`, `int32 initialFocusedCarIndex (-1)`, `string initialCameraSet`, `string initialCamera` |
| 52 | `PLAY_MANUAL_REPLAY_HIGHLIGHT` | im SDK als „TODO, but planned“ markiert |
| 60 | `SAVE_MANUAL_REPLAY_HIGHLIGHT` | im SDK als „TODO, but planned“ markiert |

Referenz-Bytes (aus dem acbc-Test, displayName „Your name“, Passwort „asd“, Intervall 250, Command-Passwort leer):
`01 04 09 00 59 6f 75 72 20 6e 61 6d 65 03 00 61 73 64 fa 00 00 00 00 00`.

### 2.4 Inbound (ACC → Client)

| Typ | Name |
|---:|---|
| 1 | `REGISTRATION_RESULT` |
| 2 | `REALTIME_UPDATE` |
| 3 | `REALTIME_CAR_UPDATE` |
| 4 | `ENTRY_LIST` |
| 5 | `TRACK_DATA` |
| 6 | `ENTRY_LIST_CAR` |
| 7 | `BROADCASTING_EVENT` |

**`LapInfo`** (gemeinsamer Baustein, variable Länge 13 + 4·n Bytes):

| Typ | Feld | Hinweis |
|---|---|---|
| `int32` | `laptimeMS` | MaxValue = keine Zeit |
| `uint16` | `carIndex` |  |
| `uint16` | `driverIndex` |  |
| `uint8` | `splitCount` (n) |  |
| `int32[n]` | `splits` | MaxValue = ungültig bzw. nicht vorhanden |
| `uint8` | `isInvalid` |  |
| `uint8` | `isValidForBest` |  |
| `uint8` | `isOutlap` |  |
| `uint8` | `isInlap` | daraus ergibt sich LapType: Outlap / Inlap / Regular |

**1 – `REGISTRATION_RESULT`**: `int32 connectionId`, `u8 connectionSuccess`, `u8 isReadonlyByte`, `string errorMessage`.
Achtung: Das Kunos-SDK interpretiert `isReadonly = (byte == 0)`. Der Wert 1 bedeutet also „schreibberechtigt“.
Danach fordert das SDK sofort Entry List und Track Data an.

**2 – `REALTIME_UPDATE`**:

| Typ | Feld | Hinweis |
|---|---|---|
| `uint16` | `eventIndex` |  |
| `uint16` | `sessionIndex` |  |
| `uint8` | `sessionType` | `RaceSessionType` (s. u.) |
| `uint8` | `phase` | `SessionPhase` |
| `float` | `sessionTime` | **ms** seit Sessionstart |
| `float` | `sessionEndTime` | ms bis Sessionende |
| `int32` | `focusedCarIndex` |  |
| `string` | `activeCameraSet` |  |
| `string` | `activeCamera` |  |
| `string` | `currentHudPage` |  |
| `uint8` | `isReplayPlaying` |  |
| `float` ×2 | nur wenn Replay: `replaySessionTime`, `replayRemainingTime` | v4. Das v2-SDK las zusätzlich `int32 replayFocusedCar` (**UNVERIFIED** für v4) |
| `float` | `timeOfDay` | v4: **Sekunden** seit Mitternacht (v2: `uint16` hhmm) |
| `uint8` | `ambientTemp` | °C (acbc liest int8) |
| `uint8` | `trackTemp` | °C |
| `uint8` | `clouds` | /10 → 0.0..1.0 |
| `uint8` | `rainLevel` | /10 |
| `uint8` | `wetness` | /10 |
| `LapInfo` | `bestSessionLap` |  |

**3 – `REALTIME_CAR_UPDATE`** (fester Teil 38 B inkl. Typ-Byte, danach 3× `LapInfo`):

| Offset (inkl. Typ) | Typ | Feld | Hinweis |
|---:|---|---|---|
| 1 | `uint16` | `carIndex` |  |
| 3 | `uint16` | `driverIndex` | ändert sich beim Fahrerwechsel |
| 5 | `uint8` | `driverCount` | v4 (fehlt in v2) |
| 6 | `uint8` | `gear` | Kunos: `gear = raw − 2` → R=−1, N=0 |
| 7 | `float` | `worldPosX` | Go-SDK-Kommentar: „always 0“ (ACC 1.3.12), **UNVERIFIED** heute |
| 11 | `float` | `worldPosY` | dito |
| 15 | `float` | `yaw` | dito |
| 19 | `uint8` | `carLocation` | `CarLocation` |
| 20 | `uint16` | `kmh` |  |
| 22 | `uint16` | `position` | offizielle Position (1-basiert) |
| 24 | `uint16` | `cupPosition` | Position in der Cup-Kategorie |
| 26 | `uint16` | `trackPosition` | Position auf der Strecke (Go: „always 0“, UNVERIFIED) |
| 28 | `float` | `splinePosition` | 0..1 |
| 32 | `uint16` | `laps` | absolvierte Runden |
| 34 | `int32` | `delta` | ms, Echtzeit-Delta zur besten Sessionrunde |
| 38 | `LapInfo` | `bestSessionLap` |  |
| … | `LapInfo` | `lastLap` |  |
| … | `LapInfo` | `currentLap` | Laptime läuft live mit, Splits bleiben leer |

Rechnung fester Teil: 1 + 2 + 2 + 1 + 1 + 4 + 4 + 4 + 1 + 2 + 2 + 2 + 2 + 4 + 2 + 4 = **38 B**.

**4 – `ENTRY_LIST`**: `int32 connectionId`, `uint16 carEntryCount`, `uint16 carIndex[carEntryCount]`.
(Das v2-SDK las danach noch `uint16 driverEntryCount` + `uint16[]`. In v4 entfällt das laut acbc-Testvektor `04 01000000 0100 e903`.)

**5 – `TRACK_DATA`**: `int32 connectionId`, `string trackName`, `int32 trackId`, `int32 trackMeters`,
`uint8 cameraSetCount` × { `string cameraSetName`, `uint8 cameraCount`, `string camera[cameraCount]` },
`uint8 hudPageCount`, `string hudPage[hudPageCount]`. Track Data wird bei einem neuen Session-Start **nicht** neu gesendet (Go-SDK).

**6 – `ENTRY_LIST_CAR`** (v4):

| Typ | Feld | Hinweis |
|---|---|---|
| `uint16` | `carIndex` |  |
| `uint8` | `carModelType` | = `CarModelId` (PDF Appendix 6), z. B. 24 = Ferrari 488 GT3 Evo |
| `string` | `teamName` |  |
| `int32` | `raceNumber` |  |
| `uint8` | `cupCategory` | Overall/Pro=0, ProAm=1, Am=2, Silver=3, National=4 |
| `uint8` | `currentDriverIndex` |  |
| `uint16` | `nationality` | `NationalityEnum` |
| `uint8` | `driversOnCarCount` |  |
| je Fahrer: `string firstName`, `string lastName`, `string shortName`, `uint8 category`, `uint16 nationality` | | `DriverCategory`: Bronze=0, Silver=1, Gold=2, Platinum=3 (Error=255, UNVERIFIED) |

(Das v2-SDK hatte zusätzlich `teamCarName`, `displayName`, je Fahrer `driverIndex`, `hasDriverInfo` und `nickname`. Für v4 gilt die Tabelle oben,
übereinstimmend in Go und Rust und durch den acbc-Testvektor belegt.)

**7 – `BROADCASTING_EVENT`**: `uint8 type`, `string msg`, `int32 timeMs`, `int32 carId` (hier int32, sonst uint16).
`BroadcastingCarEventType`: None=0, GreenFlag=1, SessionOver=2, PenaltyCommMsg=3, Accident=4, LapCompleted=5,
BestSessionLap=6, BestPersonalLap=7. Laut Go-SDK wurden 1–4 in ACC 1.3.12 nie gesendet (**UNVERIFIED** heute).

### 2.5 Broadcasting-Enums

```text
RaceSessionType: Practice=0, Qualifying=4, Superpole=9, Race=10, Hotlap=11, Hotstint=12, HotlapSuperpole=13, Replay=14
SessionPhase:    NONE=0, Starting=1, PreFormation=2, FormationLap=3, PreSession=4, Session=5,
                 SessionOver=6, PostSession=7, ResultUI=8
CarLocation:     NONE=0, Track=1, Pitlane=2, PitEntry=3, PitExit=4
CupCategory:     Overall=0, ProAm=1, Am=2, Silver=3, National=4
DriverCategory:  Bronze=0, Silver=1, Gold=2, Platinum=3
Nationality:     Any=0, Italy=1, Germany=2, France=3, Spain=4, GreatBritain=5, … (vollständige Liste 0..77 im Go-SDK buffer.go)
```

### 2.6 Python-Ports und Lizenzen

- Einen gepflegten, klar lizenzierten Python-Port der Broadcasting API konnte ich auf PyPI **nicht** finden. Geprüft wurden
  `acc-broadcasting`, `accbroadcasting`, `pyaccbroadcasting`, `acc-broadcast` und `acc-udp`: alle 404. **Empfehlung:** eigene Clean-Room-Implementierung
  nach dieser Spezifikation (wenige hundert Zeilen).
- Das Kunos-SDK liegt dem Spiel bei und hat **keine Open-Source-Lizenz**. Es dient als Referenz, Code wird nicht ins Repo kopiert.
- `bradfier/acbc` (Rust) steht unter **AGPL-3.0**. Nur als Spezifikationsquelle lesen, keinen Code übernehmen.
- `toonknapen/accbroadcastingsdk` (Go) hat **keine LICENSE-Datei**, es gilt also „all rights reserved“. Nur lesen.
- `rrennoir/PyAccSharedMemory` (Shared Memory, Python) steht unter **MIT** und ist als Abhängigkeit oder Vorlage nutzbar.

---

## 3. Was Chef woher bekommt (ACC)

| Bedarf | Quelle |
|---|---|
| Pedale, RPM, Reifen (Druck, Temperatur, Verschleiß), Bremsen, Schäden, G-Kräfte | Shared Memory Physics |
| Sprit (`fuelXLap`, `fuelEstimatedLaps`, `usedFuel`), Stint-Zeiten, Pflichtstopps, Flaggen, Penalty | Shared Memory Graphics |
| Wetter jetzt und Prognose (`rainIntensityIn10min/30min`), Grip | Shared Memory Graphics |
| Gaps zum Vorder- und Hintermann | Shared Memory `gapAhead/gapBehind` **oder** Broadcasting (`splinePosition` + `delta`) |
| Klassement, Rundenzeiten, Boxenstatus **aller** Autos, Fahrernamen, Klassen | Broadcasting API |
| Positionen aller Autos auf der Karte | Shared Memory `carCoordinates[60][3]` (Broadcasting `worldPos` evtl. 0) |
| Session-Phase (Formation, Rennen, Ende), Boxenfenster | Broadcasting `SessionPhase` und Static `PitWindowStart/End` |

---

## 4. Assetto Corsa EVO (Stand 2026)

- **Status:** Early Access (Steam App 3058630). Laut 505-Games-Changelog lag Version 0.6 am 15.04.2026 vor. Ob es inzwischen 1.0 gibt, ist **UNVERIFIED**.
- **Shared Memory ist offiziell dokumentiert.** Kunos hat im Forum den Thread „Shared Memory API documentation“ (Thread 83659) veröffentlicht,
  dazu ein Google Doc „SharedFileOut.h“ mit Changelog (31.03.2026: innere Structs mit fester Größe, `display_current_page_index[16]`;
  01.04.2026: `car_ids` in `SPageFileGraphicEvo` hinzugefügt, Beispiel-Solution aktualisiert). Das Google Doc war aus der Recherche-Umgebung
  nicht erreichbar. Ausgewertet wurde die Markdown-Kopie `docs/ACE_SharedFileOut_Documentation_v1.md` im MIT-lizenzierten Rust-Crate `kerb` (v0.4.0).
- **Map-Namen (neu):** `Local\acevo_pmf_physics`, `Local\acevo_pmf_graphics`, `Local\acevo_pmf_static`.
- **Layout:** Laut `kerb` nutzt das Spiel **kein** `#pragma pack`, sondern natürliches C-Alignment (`repr(C)`). Strings sind `char[n]` (nicht mehr `wchar_t`).
  - `SPageFilePhysics`: weitgehend AC/ACC-artig (packetId, gas, brake, fuel, gear, rpms, …, Vibrationsfelder inkl. `roadVibrations`).
  - `SPageFileGraphicEvo`: komplett neu, mit Sub-Structs fester Größe: `SMEvoTyreState[256 B]` ×4, `SMEvoDamageState[128]`, `SMEvoPitInfo[64]`,
    `SMEvoElectronics[128]` (×4: Wert, Min, Max, modifizierbar), `SMEvoInstrumentation[128]` (×3), `SMEvoSessionState[256]`, `SMEvoTimingState[256]`,
    `SMEvoAssistsState[64]`. Für andere Autos gibt es `car_coordinates[60][3]`, `uint64 car_ids[60][2]` (128-Bit-IDs als zwei u64), `active_cars` (u8) und
    `gap_ahead`/`gap_behind` (**float, Sekunden**, nicht ms wie bei ACC). Dazu `focused_car_id_a/b` und `player_car_id_a/b` (je u64).
  - `SPageFileStaticEvo`: `sm_version`, `ac_evo_version`, Session-Typ und -Name, Startgrip, Start-Temperaturen, Track-Infos inkl. Länge, Geo-Koordinaten.
  - Neue Enums: `ACEVO_STATUS` (wie AC), `ACEVO_SESSION_TYPE` (UNKNOWN=-1, TIME_ATTACK=0, RACE=1, HOT_STINT=2, CRUISE=3),
    `ACEVO_FLAG_TYPE` (0..10, **andere Nummerierung** als AC/ACC, z. B. WHITE=1, GREEN=2, RED=3, BLUE=4, YELLOW=5),
    `ACEVO_CAR_LOCATION`, `ACEVO_ENGINE_TYPE`, `ACEVO_STARTING_GRIP`.
- Exakte Offsets für EVO sind hier **bewusst nicht** aufgeführt. Die Doku befindet sich noch im Fluss (Breaking Changes im März/April 2026).
  Vor einer Implementierung die aktuelle Kunos-Fassung ziehen und mit `kerb`s `struct_size_tests` abgleichen.
- **UDP/Broadcasting für EVO:** Es ist keine offizielle Netzwerkschnittstelle bekannt (**UNVERIFIED**). In Steam-Diskussionen berichteten Nutzer anfangs,
  dass SimHub keine Daten erhält. Das betraf die Zeit vor der Shared-Memory-Doku.

---

## Quellen

| Quelle | URL | Lizenz / Status | Verwendet für |
|---|---|---|---|
| Kunos „ACC Shared Memory v1.8.12“ (PDF, im PyAccSharedMemory-Repo mitgeliefert) | https://github.com/rrennoir/PyAccSharedMemory/blob/main/ACCSharedMemoryDocumentationV1.8.12.pdf | Kunos, proprietär (Dokumentation) | **Primärquelle** Shared Memory, Enums, Appendices |
| rrennoir/PyAccSharedMemory (`src/pyaccsharedmemory.py`, PyPI `pyaccsharedmemory` 1.0.0) | https://github.com/rrennoir/PyAccSharedMemory · https://pypi.org/project/pyaccsharedmemory/ | MIT | Lesereihenfolge, Map-Größen 800/1588/784, `trackConfiguration` = 33 |
| Spacefreak18/simapi – `include/acdata.h` | https://github.com/Spacefreak18/simapi | LGPL-3.0 | Abgleich (für ACC teils fehlerhaft, siehe Hinweis) |
| Kunos Broadcasting SDK (C#, `sdk/broadcasting` im Spielordner) | Spielinstallation; Mirror v0.4/v2: https://github.com/angel-git/acc-broadcasting | Kunos, keine OSS-Lizenz; Mirror ohne Lizenz | Protokollstruktur, Message-IDs, Outbound-Encoding |
| toonknapen/accbroadcastingsdk (Go, v3 = Protokoll 4) | https://github.com/toonknapen/accbroadcastingsdk | keine LICENSE-Datei | v4-Feldlisten, Enums, Praxis-Hinweise (WorldPos=0, Events) |
| bradfier/acbc (Rust, inkl. pcap-Testdaten) | https://github.com/bradfier/acbc · https://docs.rs/acbc | AGPL-3.0 | v4-Parser, Testvektoren (Registration, Entry List, Entry List Car) |
| Kunos-Forum „Shared Memory API documentation“ (AC EVO) | https://www.assettocorsa.net/forum/index.php?threads/shared-memory-api-documentation.83659/ | Kunos; **nicht erreichbar** | – |
| Kunos Google Doc „SharedFileOut.h“ (AC EVO) | https://docs.google.com/document/d/1WzqMLkW2o_C0LGcvdMRelAV31ZIifux0CSHD9k6ddz0/edit | Kunos; **nicht erreichbar** | – |
| mvoof/kerb (Rust, `docs/ACE_SharedFileOut_Documentation_v1.md`, `src/ac_evo/*`) | https://github.com/mvoof/kerb · https://crates.io/crates/kerb | MIT | AC-EVO-Struct-Übersicht, Map-Namen, Alignment |
| 505 Games – AC EVO 0.6 Changelog (15.04.2026) | https://support.505games.com/support/solutions/articles/150000225211-assetto-corsa-evo-0-6-changelog-15-apr-2026 | – | EVO-Versionsstand (nur Suchergebnis, nicht geöffnet) |

Eigene Verifikation: Die Offsets wurden mit einem Layout-Rechner (pack 4, wchar = 2 B) bestimmt und mit `ctypes.Structure(_pack_=4)` gegengeprüft.
Die `struct`-Formate wurden mit `struct.calcsize` geprüft (800 / 1588 / 820). Physics- und Graphics-Größe stimmen unabhängig davon mit den Map-Größen in PyAccSharedMemory überein.
Für die Broadcasting-Strukturen variabler Länge wurde der feste Teil von `REALTIME_CAR_UPDATE` (38 B inkl. Typ-Byte) nachgerechnet.
