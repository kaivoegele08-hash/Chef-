# rFactor 2 und Le Mans Ultimate: Telemetrie über Shared Memory

Stand der Recherche: 2026-10-28. Dieses Dokument ist die Grundlage für den Python-Parser. Alle Offsets
wurden aus den Original-Headern berechnet: `ctypes.Structure` mit `_pack_ = 4`, Windows-x64-Typgrößen.
Gegengeprüft wurde mit den C#-Marshal-Definitionen des Plugins und mit zwei unabhängigen LMU-Implementierungen
(siehe Abschnitt 2.9). Was nicht verifiziert ist, ist mit **UNVERIFIED** markiert.

Konventionen auf Windows x64 (MSVC, LLP64): `long` und `unsigned long` haben **4 Bytes**, `bool` hat 1 Byte,
`short` 2 Bytes, `double` 8 Bytes, `float` 4 Bytes. Alles ist Little Endian.
`#pragma pack(push, 4)`: Kein Feld ist stärker als auf 4 Bytes ausgerichtet. Ein `double` kann deshalb
auf Offset 4, 12, 20 … liegen. Wer mit Pythons Standardausrichtung (8 Bytes für `double`) parst,
verschiebt alle folgenden Offsets und liest nur noch Unsinn.

---

## Teil A – rFactor 2: rF2SharedMemoryMapPlugin (TheIronWolfModding)

### A.1 Herkunft und Lizenz

- Repository: <https://github.com/TheIronWolfModding/rF2SharedMemoryMapPlugin>, Autor Vytautas Leonavičius ("The Iron Wolf"), Crew-Chief-Umfeld.
- **Lizenz: GPL-3.0, nicht MIT.** Die Datei `LICENSE` im Repository ist die "GNU GENERAL PUBLIC LICENSE Version 3".
  Laut README darf die DLL zusätzlich weitergegeben werden, wenn die Weitergabe kostenlos ist und die README beiliegt.
- Konsequenz für uns: Wir kopieren keinen Code. Wir implementieren nur das binäre Interface, also Namen und
  Offsets, in Python neu. **UNVERIFIED:** ob ein Struct-Layout als Schnittstellenbeschreibung urheberrechtlich
  geschützt ist, ist eine Rechtsfrage. Gängige Praxis: Andere Projekte (SimHub, TinyPedal) machen genau das.
- `Include/InternalsPlugin.hpp` (ISI/Studio 397) trägt den Vermerk "PROPRIETARY AND CONFIDENTIAL, Copyright (c) 2017 Studio 397 B.V."
  → **nicht** in unser Repository übernehmen.
- Letztes Release laut README: **v3.7.15.1 (25.03.2023)**, "Inserts DeltaBest into the rF2Telemetry buffer". Dazu gibt es einen Widerspruch, siehe A.4.3.
- Das Plugin kopiert die Game-Structs per `memcpy` 1:1. Belegt ist das in `Source/rFactor2SharedMemoryMap.cpp`, z. B.
  `memcpy(&(mTelemetry.mpWriteBuff->mVehicles[i]), &info, sizeof(rF2VehicleTelemetry))`.
  **Das Layout im Speicher bestimmt also das Spiel, nicht `rF2State.h`.**

### A.2 Gemappte Buffer (Namen)

Definiert sind die Namen in `Source/rFactor2SharedMemoryMap.cpp` (`MM_*_FILE_NAME`).

| Buffer | Mapping-Name | Struct (C++) | Update-Rate | Version-Block | `mBytesUpdatedHint` |
|---|---|---|---|---|---|
| Telemetry | `$rFactor2SMMP_Telemetry$` | `rF2Telemetry` | ~50 Hz (Aufruf alle 10 ms, Daten ändern sich alle 20 ms) | ja | ja |
| Scoring | `$rFactor2SMMP_Scoring$` | `rF2Scoring` | 5 Hz | ja | ja |
| Rules | `$rFactor2SMMP_Rules$` | `rF2Rules` | 3 Hz | ja | ja |
| MultiRules | `$rFactor2SMMP_MultiRules$` | `rF2MultiRules` | bei Session-Wechsel | ja | ja |
| ForceFeedback | `$rFactor2SMMP_ForceFeedback$` | `rF2ForceFeedback` (1× `double`) | 400 Hz | vorhanden, wird laut Kommentar nicht gepflegt | nein |
| Graphics | `$rFactor2SMMP_Graphics$` | `rF2Graphics` | 400 Hz, **standardmäßig deaktiviert** | vorhanden, laut Kommentar "not versioned" | nein |
| PitInfo | `$rFactor2SMMP_PitInfo$` | `rF2PitInfo` (`rF2PitMenu`) | 100 Hz | ja | nein |
| Weather | `$rFactor2SMMP_Weather$` | `rF2Weather` | 1 Hz, **standardmäßig deaktiviert** | ja | nein |
| Extended | `$rFactor2SMMP_Extended$` | `rF2Extended` (Plugin-eigene Daten) | 5 Hz und bei Callbacks | ja | nein |
| HWControl (Input) | `$rFactor2SMMP_HWControl$` | `rF2HWControl` | Lesen 5 Hz | ja + `mLayoutVersion` | nein |
| WeatherControl (Input) | `$rFactor2SMMP_WeatherControl$` | `rF2WeatherControl` | | ja | nein |
| RulesControl (Input) | `$rFactor2SMMP_RulesControl$` | `rF2RulesControl` | | ja | nein |
| PluginControl (Input) | `$rFactor2SMMP_PluginControl$` | `rF2PluginControl` | | ja | nein |

Für einen Race Engineer, der nur liest, reichen **Telemetry, Scoring und Extended**, optional noch Rules und PitInfo.
In Input-Buffer (HWControl usw.) schreiben wir nicht.

**Suffix pro PID (nur Dedicated Server):** Laut `Include/MappedBuffer.h::MapMemoryFile` gilt:
- Normaler Client: Der Name ist exakt `$rFactor2SMMP_<TYPE>$`.
- Wenn der Prozessname `Dedicated.exe` enthält, wird die PID ohne Trenner angehängt: `$rFactor2SMMP_Telemetry$12345`.
- Ist zusätzlich die Option `DedicatedServerMapGlobally = 1` in `CustomPluginVariables.json` gesetzt, lautet der Name `Global\$rFactor2SMMP_Telemetry$12345`.
  Dafür braucht das Konto das Recht "Create Global Objects".

**Buffer abbestellen:** Mit `UnsubscribedBuffersMask` in `UserData\player\CustomPluginVariables.JSON` lassen sich Buffer abschalten.
Die Werte werden addiert: Telemetry = 1, Scoring = 2, Rules = 4, MultiRules = 8, ForceFeedback = 16, Graphics = 32, PitInfo = 64, Weather = 128.
Extended lässt sich nicht abbestellen. Wird Scoring abbestellt, ist PluginControl deaktiviert.
`EnableDirectMemoryAccess = 1` schaltet zusätzliche Extended-Felder frei, z. B. Pit-Speed-Limit und Statusmeldungen.

### A.3 Speicherlayout: Pack, Version-Block, Torn Reads

- `rF2State.h` beginnt mit `#pragma pack(push, 4)` ("Use 4 to match ISI pack") und endet mit `#pragma pack(pop)`.
- **Mapping = `rF2MappedBufferVersionBlock` (8 Bytes) + `BuffT`.** In C++ ist der Version-Block **nicht** Teil der Structs.
  `MappedBuffer<BuffT>` legt ihn vor das Struct: `CreateFileMappingA(..., sizeof(rF2MappedBufferVersionBlock) + sizeof(BuffT), name)`
  und `pBuf = (char*)view + sizeof(rF2MappedBufferVersionBlock)`. Die C#-Structs in `rF2Data.cs` modellieren den Block deshalb
  als erste Felder. Die Offsets unten sind **ab Beginn des Mappings** gezählt, so wie im C#-Layout.

```c
struct rF2MappedBufferVersionBlock {          // Offset 0 im Mapping
  unsigned long mVersionUpdateBegin;          // +0, uint32: wird VOR dem Schreiben inkrementiert
  unsigned long mVersionUpdateEnd;            // +4, uint32: wird NACH dem Schreiben inkrementiert
};
// Bei Telemetry, Scoring, Rules, MultiRules folgt direkt:
  int mBytesUpdatedHint;                      // +8, int32: 0 = unbekannt, also ganzen Buffer lesen
```

**Schreiber** (`MappedBuffer::BeginUpdate` und `EndUpdate`):
1. `BeginUpdate`: Gilt `Begin != End` (inkonsistenter Zustand), wird `End = Begin` gesetzt. Danach `InterlockedIncrement(Begin)`.
2. Der Buffer-Inhalt wird per `memcpy` geschrieben.
3. `EndUpdate`: `InterlockedIncrement(End)`. Gilt danach `Begin != End`, wird `Begin = End` gesetzt.

Im Ruhezustand gilt also `Begin == End`. Während des Schreibens ist `Begin == End + 1`.

**Leser-Algorithmus gegen Torn Reads.** Er folgt `rF2SMMonitor/MappedBuffer.cs::GetMappedData`, wie ihn auch Crew Chief nutzt:

```text
for retry in range(10):
    b0, e0 = read_u32(0), read_u32(4)        # 1. Vorprüfung
    if (b0, e0) == stuck_versions: return FAIL         # optional: hängender Frame
    if skip_unchanged and (b0, e0) == last_ok: return UNCHANGED
    if b0 != e0: sleep(1 ms); continue
    raw = mapping[0 : nbytes]                # 2. Hauptlesung inklusive Version-Block
    b1, e1 = u32(raw, 0), u32(raw, 4)
    if b1 != e1: sleep(1 ms); continue
    b2, e2 = read_u32(0), read_u32(4)        # 3. Nachprüfung
    if (b2, e2) != (b1, e1): sleep(1 ms); continue
    last_ok = (b1, e1); return parse(raw)
return FAIL (Frame verwerfen, alten Zustand behalten)
```

Der Schritt "Nachprüfung" ist nötig. Im Kommentar des Autors steht der Grund: Der Leser liest `Begin`, und kurz danach
beginnt der Schreiber. Liest der Leser `End`, bevor der Schreiber fertig ist, sieht die Kopie konsistent aus, ist aber zerrissen.
Erst ein zweites Lesen des Headers nach dem Kopieren deckt das auf.

**Teil-Lesen mit `mBytesUpdatedHint`:** Das Plugin setzt `mBytesUpdatedHint = offsetof(rF2Telemetry, mVehicles[mNumVehicles])`,
analog `offsetof(rF2Scoring, mVehicles[numScoringVehicles])`. **Achtung:** `offsetof` zählt ab dem C++-Struct, also **ohne**
den 8-Byte-Version-Block. Der C#-Monitor liest dennoch `mBytesUpdatedHint` Bytes ab Mapping-Offset 0 und schneidet damit
die letzten 8 Bytes des letzten Fahrzeugs ab. **Unser Parser liest `8 + mBytesUpdatedHint` Bytes.**
Für Telemetry sind das `8 + 8 + n*1888 = 16 + n*1888`, für Scoring `8 + 4 + 548 + n*584 = 560 + n*584`.
Ist der Hint 0, wird der ganze Buffer gelesen.

Hinweise:
- Telemetry wird erst als fertig markiert, wenn das Plugin annimmt, dass alle Fahrzeuge ein Update bekommen haben.
  Updates mit gleicher Spielzeit werden übersprungen.
- **Die Reihenfolge der Fahrzeuge in Telemetry und Scoring ist verschieden.** Zugeordnet wird immer über `mID`.
  Negative `mID` werden nicht unterstützt. Die Spanne max(mID) − min(mID) muss kleiner als 512 bleiben (`MAX_MAPPED_IDS`).
- `MAX_MAPPED_VEHICLES = 128`.

### A.4 Structs mit Offsets

Die Offsets sind relativ zum Struct-Anfang, sofern nicht anders angegeben. Die C-Typen stammen wörtlich aus `Include/rF2State.h` (master).

#### `rF2Vec3` (sizeof = 24 Bytes)

| Offset (dez) | Offset (hex) | Feld | C-Typ | Größe |
|---:|---:|---|---|---:|
| 0 | 0x0 | `x` | `double` | 8 |
| 8 | 0x8 | `y` | `double` | 8 |
| 16 | 0x10 | `z` | `double` | 8 |

#### `rF2Wheel` (sizeof = 260 Bytes), entspricht `TelemWheelV01`

| Offset (dez) | Offset (hex) | Feld | C-Typ | Größe | Einheit / Bemerkung |
|---:|---:|---|---|---:|---|
| 0 | 0x0 | `mSuspensionDeflection` | `double` | 8 | m |
| 8 | 0x8 | `mRideHeight` | `double` | 8 | m |
| 16 | 0x10 | `mSuspForce` | `double` | 8 | N (Pushrod-Last) |
| 24 | 0x18 | `mBrakeTemp` | `double` | 8 | **°C** (laut Header Celsius, nicht Kelvin) |
| 32 | 0x20 | `mBrakePressure` | `double` | 8 | 0.0–1.0 (Fahrereingabe × Bremsbalance) |
| 40 | 0x28 | `mRotation` | `double` | 8 | rad/s |
| 48 | 0x30 | `mLateralPatchVel` | `double` | 8 | m/s |
| 56 | 0x38 | `mLongitudinalPatchVel` | `double` | 8 | m/s |
| 64 | 0x40 | `mLateralGroundVel` | `double` | 8 | m/s |
| 72 | 0x48 | `mLongitudinalGroundVel` | `double` | 8 | m/s |
| 80 | 0x50 | `mCamber` | `double` | 8 | rad |
| 88 | 0x58 | `mLateralForce` | `double` | 8 | N |
| 96 | 0x60 | `mLongitudinalForce` | `double` | 8 | N |
| 104 | 0x68 | `mTireLoad` | `double` | 8 | N |
| 112 | 0x70 | `mGripFract` | `double` | 8 | Anteil der Aufstandsfläche, der rutscht |
| 120 | 0x78 | `mPressure` | `double` | 8 | **kPa** |
| 128 | 0x80 | `mTemperature` | `double[3]` | 24 | **Kelvin**, Reihenfolge links/mitte/rechts (nicht innen/außen) |
| 152 | 0x98 | `mWear` | `double` | 8 | 0.0–1.0 "fraction of maximum"; Richtung siehe A.6 |
| 160 | 0xA0 | `mTerrainName` | `char[16]` | 16 | Material-Präfix aus der TDF |
| 176 | 0xB0 | `mSurfaceType` | `unsigned char` | 1 | 0 dry, 1 wet, 2 grass, 3 dirt, 4 gravel, 5 rumblestrip, 6 special |
| 177 | 0xB1 | `mFlat` | `bool` | 1 | |
| 178 | 0xB2 | `mDetached` | `bool` | 1 | |
| 179 | 0xB3 | `mStaticUndeflectedRadius` | `unsigned char` | 1 | cm |
| 180 | 0xB4 | `mVerticalTireDeflection` | `double` | 8 | m |
| 188 | 0xBC | `mWheelYLocation` | `double` | 8 | m, relativ zum Fahrzeug |
| 196 | 0xC4 | `mToe` | `double` | 8 | rad |
| 204 | 0xCC | `mTireCarcassTemperature` | `double` | 8 | **Kelvin** |
| 212 | 0xD4 | `mTireInnerLayerTemperature` | `double[3]` | 24 | **Kelvin** |
| 236 | 0xEC | `mExpansion` | `unsigned char[24]` | 24 | in LMU belegt, siehe B.4 |

#### `rF2VehicleTelemetry` (sizeof = 1888 Bytes), entspricht `TelemInfoV01`

| Offset (dez) | Offset (hex) | Feld | C-Typ | Größe | Einheit / Bemerkung |
|---:|---:|---|---|---:|---|
| 0 | 0x0 | `mID` | `long` | 4 | Slot-ID; im Multiplayer wiederverwendbar |
| 4 | 0x4 | `mDeltaTime` | `double` | 8 | s seit dem letzten Update |
| 12 | 0xC | `mElapsedTime` | `double` | 8 | s Session-Zeit |
| 20 | 0x14 | `mLapNumber` | `long` | 4 | |
| 24 | 0x18 | `mLapStartET` | `double` | 8 | s |
| 32 | 0x20 | `mVehicleName` | `char[64]` | 64 | |
| 96 | 0x60 | `mTrackName` | `char[64]` | 64 | |
| 160 | 0xA0 | `mPos` | `rF2Vec3` | 24 | m, Weltkoordinaten |
| 184 | 0xB8 | `mLocalVel` | `rF2Vec3` | 24 | m/s, Fahrzeugkoordinaten |
| 208 | 0xD0 | `mLocalAccel` | `rF2Vec3` | 24 | m/s² |
| 232 | 0xE8 | `mOri` | `rF2Vec3[3]` | 72 | Zeilen der Orientierungsmatrix |
| 304 | 0x130 | `mLocalRot` | `rF2Vec3` | 24 | rad/s |
| 328 | 0x148 | `mLocalRotAccel` | `rF2Vec3` | 24 | rad/s² |
| 352 | 0x160 | `mGear` | `long` | 4 | −1 R, 0 N, 1+ Vorwärtsgänge |
| 356 | 0x164 | `mEngineRPM` | `double` | 8 | U/min |
| 364 | 0x16C | `mEngineWaterTemp` | `double` | 8 | °C |
| 372 | 0x174 | `mEngineOilTemp` | `double` | 8 | °C |
| 380 | 0x17C | `mClutchRPM` | `double` | 8 | U/min |
| 388 | 0x184 | `mUnfilteredThrottle` | `double` | 8 | 0..1 |
| 396 | 0x18C | `mUnfilteredBrake` | `double` | 8 | 0..1 |
| 404 | 0x194 | `mUnfilteredSteering` | `double` | 8 | −1..1 (links → rechts) |
| 412 | 0x19C | `mUnfilteredClutch` | `double` | 8 | 0..1 |
| 420 | 0x1A4 | `mFilteredThrottle` | `double` | 8 | 0..1 |
| 428 | 0x1AC | `mFilteredBrake` | `double` | 8 | 0..1 |
| 436 | 0x1B4 | `mFilteredSteering` | `double` | 8 | −1..1 |
| 444 | 0x1BC | `mFilteredClutch` | `double` | 8 | 0..1 |
| 452 | 0x1C4 | `mSteeringShaftTorque` | `double` | 8 | Nm |
| 460 | 0x1CC | `mFront3rdDeflection` | `double` | 8 | m |
| 468 | 0x1D4 | `mRear3rdDeflection` | `double` | 8 | m |
| 476 | 0x1DC | `mFrontWingHeight` | `double` | 8 | m |
| 484 | 0x1E4 | `mFrontRideHeight` | `double` | 8 | m |
| 492 | 0x1EC | `mRearRideHeight` | `double` | 8 | m |
| 500 | 0x1F4 | `mDrag` | `double` | 8 | N (UNVERIFIED) |
| 508 | 0x1FC | `mFrontDownforce` | `double` | 8 | N (UNVERIFIED) |
| 516 | 0x204 | `mRearDownforce` | `double` | 8 | N (UNVERIFIED) |
| 524 | 0x20C | `mFuel` | `double` | 8 | **Liter** |
| 532 | 0x214 | `mEngineMaxRPM` | `double` | 8 | Drehzahlbegrenzer |
| 540 | 0x21C | `mScheduledStops` | `unsigned char` | 1 | |
| 541 | 0x21D | `mOverheating` | `bool` | 1 | |
| 542 | 0x21E | `mDetached` | `bool` | 1 | Teile (außer Räder) abgefallen |
| 543 | 0x21F | `mHeadlights` | `bool` | 1 | |
| 544 | 0x220 | `mDentSeverity` | `unsigned char[8]` | 8 | 0 keine, 1 etwas, 2 mehr; 8 Zonen |
| 552 | 0x228 | `mLastImpactET` | `double` | 8 | s |
| 560 | 0x230 | `mLastImpactMagnitude` | `double` | 8 | |
| 568 | 0x238 | `mLastImpactPos` | `rF2Vec3` | 24 | |
| 592 | 0x250 | `mEngineTorque` | `double` | 8 | Nm |
| 600 | 0x258 | `mCurrentSector` | `long` | 4 | 0-basiert; **Vorzeichenbit = Boxengasse** (z. B. 0x80000002) |
| 604 | 0x25C | `mSpeedLimiter` | `unsigned char` | 1 | |
| 605 | 0x25D | `mMaxGears` | `unsigned char` | 1 | |
| 606 | 0x25E | `mFrontTireCompoundIndex` | `unsigned char` | 1 | |
| 607 | 0x25F | `mRearTireCompoundIndex` | `unsigned char` | 1 | |
| 608 | 0x260 | `mFuelCapacity` | `double` | 8 | **Liter** |
| 616 | 0x268 | `mFrontFlapActivated` | `unsigned char` | 1 | |
| 617 | 0x269 | `mRearFlapActivated` | `unsigned char` | 1 | DRS |
| 618 | 0x26A | `mRearFlapLegalStatus` | `unsigned char` | 1 | 0 disallowed, 1 detected, 2 allowed |
| 619 | 0x26B | `mIgnitionStarter` | `unsigned char` | 1 | 0 off, 1 ignition, 2 ignition + starter |
| 620 | 0x26C | `mFrontTireCompoundName` | `char[18]` | 18 | |
| 638 | 0x27E | `mRearTireCompoundName` | `char[18]` | 18 | |
| 656 | 0x290 | `mSpeedLimiterAvailable` | `unsigned char` | 1 | |
| 657 | 0x291 | `mAntiStallActivated` | `unsigned char` | 1 | |
| 658 | 0x292 | `mUnused` | `unsigned char[2]` | 2 | |
| 660 | 0x294 | `mVisualSteeringWheelRange` | `float` | 4 | Grad |
| 664 | 0x298 | `mRearBrakeBias` | `double` | 8 | laut Header Anteil hinten; siehe A.6 |
| 672 | 0x2A0 | `mTurboBoostPressure` | `double` | 8 | |
| 680 | 0x2A8 | `mPhysicsToGraphicsOffset` | `float[3]` | 12 | |
| 692 | 0x2B4 | `mPhysicalSteeringWheelRange` | `float` | 4 | Grad |
| 696–847 | 0x2B8 | *Hybrid- und Expansion-Block* | | 152 | **siehe Tabelle A.4.3** |
| 848 | 0x350 | `mWheels` | `rF2Wheel[4]` | 1040 | Reihenfolge FL, FR, RL, RR |

##### A.4.3 Bereich 696–847: Widerspruch im Repository

Im Repository stehen zwei Varianten. `rF2State.h` und `rF2Data.cs` (master) haben **kein** `mDeltaBest`.
`Include/InternalsPlugin.hpp` im selben Repository enthält dagegen `double mDeltaBest; // (omitted in error by S397)`
und einen auf 103 Bytes verkürzten Expansion-Block. Gleichzeitig behauptet die README zu v3.7.15.1, DeltaBest sei eingefügt worden.
Da das Plugin per `memcpy` kopiert, gilt im Speicher das **Spiel-Layout**. Das LMU-Header-Layout (Teil B) deckt sich mit der `mDeltaBest`-Variante.

| Offset | Variante 1: `rF2State.h` / `rF2Data.cs` | Variante 2: `InternalsPlugin.hpp` (mit Forums-Fix) = LMU-Layout | Typ |
|---:|---|---|---|
| 696 | `mBatteryChargeFraction` | `mDeltaBest` | `double` |
| 704 | `mElectricBoostMotorTorque` | `mBatteryChargeFraction` (0..1) | `double` |
| 712 | `mElectricBoostMotorRPM` | `mElectricBoostMotorTorque` (Nm, negativ beim Rekuperieren) | `double` |
| 720 | `mElectricBoostMotorTemperature` | `mElectricBoostMotorRPM` | `double` |
| 728 | `mElectricBoostWaterTemperature` | `mElectricBoostMotorTemperature` | `double` |
| 736 | `mElectricBoostMotorState` (u8) + `mExpansion[111]` ab 737 | `mElectricBoostWaterTemperature` | `double` |
| 744 | (Expansion) | `mElectricBoostMotorState` (`unsigned char`: 0 unavailable, 1 inactive, 2 propulsion, 3 regeneration) | `unsigned char` |
| 745 | (Expansion) | `mExpansion[103]` (in LMU belegt, siehe B.4) | |

**Empfehlung:** Variante 2 als Standard für rF2 ab 2023 und für LMU verwenden. Die Variante sollte konfigurierbar sein.
**UNVERIFIED** für die laufende rF2-Version: Live prüfen, ob der Wert bei 696 ein Delta in Sekunden oder ein Ladezustand zwischen 0 und 1 ist.
Alle Offsets bis 695 und ab 848 sind in beiden Varianten identisch, die Größe ist in beiden 1888.

#### `rF2ScoringInfo` (sizeof = 548 Bytes), entspricht `ScoringInfoV01`

| Offset (dez) | Offset (hex) | Feld | C-Typ | Größe | Einheit / Bemerkung |
|---:|---:|---|---|---:|---|
| 0 | 0x0 | `mTrackName` | `char[64]` | 64 | |
| 64 | 0x40 | `mSession` | `long` | 4 | siehe Enums |
| 68 | 0x44 | `mCurrentET` | `double` | 8 | s |
| 76 | 0x4C | `mEndET` | `double` | 8 | s (Sessionende) |
| 84 | 0x54 | `mMaxLaps` | `long` | 4 | bei zeitbasierten Sessions nahe INT32_MAX, also keine Rundenzahl |
| 88 | 0x58 | `mLapDist` | `double` | 8 | m, Streckenlänge |
| 96 | 0x60 | `pointer1` (statt `char* mResultsStream`) | `unsigned char[8]` | 8 | Pointer-Platzhalter (x64); ignorieren |
| 104 | 0x68 | `mNumVehicles` | `long` | 4 | |
| 108 | 0x6C | `mGamePhase` | `unsigned char` | 1 | siehe Enums |
| 109 | 0x6D | `mYellowFlagState` | `signed char` | 1 | siehe Enums |
| 110 | 0x6E | `mSectorFlag` | `signed char[3]` | 3 | lokale Gelbphasen je Sektor; Indexbedeutung siehe Enums |
| 113 | 0x71 | `mStartLight` | `unsigned char` | 1 | Frame der Startampel |
| 114 | 0x72 | `mNumRedLights` | `unsigned char` | 1 | |
| 115 | 0x73 | `mInRealtime` | `bool` | 1 | im Auto statt im Monitor |
| 116 | 0x74 | `mPlayerName` | `char[32]` | 32 | |
| 148 | 0x94 | `mPlrFileName` | `char[64]` | 64 | |
| 212 | 0xD4 | `mDarkCloud` | `double` | 8 | 0..1 |
| 220 | 0xDC | `mRaining` | `double` | 8 | 0..1 |
| 228 | 0xE4 | `mAmbientTemp` | `double` | 8 | °C |
| 236 | 0xEC | `mTrackTemp` | `double` | 8 | °C |
| 244 | 0xF4 | `mWind` | `rF2Vec3` | 24 | m/s (Vektor) |
| 268 | 0x10C | `mMinPathWetness` | `double` | 8 | 0..1 |
| 276 | 0x114 | `mMaxPathWetness` | `double` | 8 | 0..1 |
| 284 | 0x11C | `mGameMode` | `unsigned char` | 1 | 1 server, 2 client, 3 server + client |
| 285 | 0x11D | `mIsPasswordProtected` | `bool` | 1 | |
| 286 | 0x11E | `mServerPort` | `unsigned short` | 2 | |
| 288 | 0x120 | `mServerPublicIP` | `unsigned long` | 4 | |
| 292 | 0x124 | `mMaxPlayers` | `long` | 4 | |
| 296 | 0x128 | `mServerName` | `char[32]` | 32 | |
| 328 | 0x148 | `mStartET` | `float` | 4 | s seit Mitternacht (Event-Startzeit) |
| 332 | 0x14C | `mAvgPathWetness` | `double` | 8 | 0..1 |
| 340 | 0x154 | `mExpansion` | `unsigned char[200]` | 200 | in LMU teilweise belegt, siehe B.4 |
| 540 | 0x21C | `pointer2` (statt `VehicleScoringInfoV01* mVehicle`) | `unsigned char[8]` | 8 | Pointer-Platzhalter; ignorieren |

#### `rF2VehicleScoring` (sizeof = 584 Bytes), entspricht `VehicleScoringInfoV01`

| Offset (dez) | Offset (hex) | Feld | C-Typ | Größe | Einheit / Bemerkung |
|---:|---:|---|---|---:|---|
| 0 | 0x0 | `mID` | `long` | 4 | Slot-ID (Schlüssel für die Telemetrie-Zuordnung) |
| 4 | 0x4 | `mDriverName` | `char[32]` | 32 | |
| 36 | 0x24 | `mVehicleName` | `char[64]` | 64 | |
| 100 | 0x64 | `mTotalLaps` | `short` | 2 | absolvierte Runden |
| 102 | 0x66 | `mSector` | `signed char` | 1 | 0 = Sektor 3, 1 = Sektor 1, 2 = Sektor 2 |
| 103 | 0x67 | `mFinishStatus` | `signed char` | 1 | siehe Enums |
| 104 | 0x68 | `mLapDist` | `double` | 8 | m |
| 112 | 0x70 | `mPathLateral` | `double` | 8 | m, Abstand zum (grob genäherten) Mittelpfad |
| 120 | 0x78 | `mTrackEdge` | `double` | 8 | m |
| 128 | 0x80 | `mBestSector1` | `double` | 8 | s |
| 136 | 0x88 | `mBestSector2` | `double` | 8 | s, **kumuliert S1 + S2** |
| 144 | 0x90 | `mBestLapTime` | `double` | 8 | s |
| 152 | 0x98 | `mLastSector1` | `double` | 8 | s |
| 160 | 0xA0 | `mLastSector2` | `double` | 8 | s, kumuliert |
| 168 | 0xA8 | `mLastLapTime` | `double` | 8 | s |
| 176 | 0xB0 | `mCurSector1` | `double` | 8 | s, falls gültig |
| 184 | 0xB8 | `mCurSector2` | `double` | 8 | s, kumuliert, falls gültig |
| 192 | 0xC0 | `mNumPitstops` | `short` | 2 | |
| 194 | 0xC2 | `mNumPenalties` | `short` | 2 | offene Strafen |
| 196 | 0xC4 | `mIsPlayer` | `bool` | 1 | |
| 197 | 0xC5 | `mControl` | `signed char` | 1 | −1 nobody, 0 local player, 1 local AI, 2 remote, 3 replay |
| 198 | 0xC6 | `mInPits` | `bool` | 1 | zwischen Boxeneinfahrt und -ausfahrt |
| 199 | 0xC7 | `mPlace` | `unsigned char` | 1 | 1-basiert |
| 200 | 0xC8 | `mVehicleClass` | `char[32]` | 32 | |
| 232 | 0xE8 | `mTimeBehindNext` | `double` | 8 | s |
| 240 | 0xF0 | `mLapsBehindNext` | `long` | 4 | |
| 244 | 0xF4 | `mTimeBehindLeader` | `double` | 8 | s |
| 252 | 0xFC | `mLapsBehindLeader` | `long` | 4 | |
| 256 | 0x100 | `mLapStartET` | `double` | 8 | s |
| 264 | 0x108 | `mPos` | `rF2Vec3` | 24 | m |
| 288 | 0x120 | `mLocalVel` | `rF2Vec3` | 24 | m/s |
| 312 | 0x138 | `mLocalAccel` | `rF2Vec3` | 24 | m/s² |
| 336 | 0x150 | `mOri` | `rF2Vec3[3]` | 72 | |
| 408 | 0x198 | `mLocalRot` | `rF2Vec3` | 24 | rad/s |
| 432 | 0x1B0 | `mLocalRotAccel` | `rF2Vec3` | 24 | rad/s² |
| 456 | 0x1C8 | `mHeadlights` | `unsigned char` | 1 | |
| 457 | 0x1C9 | `mPitState` | `unsigned char` | 1 | siehe Enums |
| 458 | 0x1CA | `mServerScored` | `unsigned char` | 1 | |
| 459 | 0x1CB | `mIndividualPhase` | `unsigned char` | 1 | GamePhase-Werte plus 9 after formation, 10 under yellow, 11 under blue |
| 460 | 0x1CC | `mQualification` | `long` | 4 | 1-basiert, −1 = ungültig |
| 464 | 0x1D0 | `mTimeIntoLap` | `double` | 8 | s, geschätzt |
| 472 | 0x1D8 | `mEstimatedLapTime` | `double` | 8 | s |
| 480 | 0x1E0 | `mPitGroup` | `char[24]` | 24 | in der Regel der Teamname |
| 504 | 0x1F8 | `mFlag` | `unsigned char` | 1 | 0 green, 6 blue |
| 505 | 0x1F9 | `mUnderYellow` | `bool` | 1 | hat das FCY an Start/Ziel gesehen |
| 506 | 0x1FA | `mCountLapFlag` | `unsigned char` | 1 | 0 nicht zählen, 1 Runde ohne Zeit, 2 Runde mit Zeit |
| 507 | 0x1FB | `mInGarageStall` | `bool` | 1 | |
| 508 | 0x1FC | `mUpgradePack` | `unsigned char[16]` | 16 | |
| 524 | 0x20C | `mPitLapDist` | `float` | 4 | m, Position der Box auf der Runde |
| 528 | 0x210 | `mBestLapSector1` | `float` | 4 | s, S1 der besten Runde |
| 532 | 0x214 | `mBestLapSector2` | `float` | 4 | s, S2 der besten Runde (kumuliert, UNVERIFIED) |
| 536 | 0x218 | `mExpansion` | `unsigned char[48]` | 48 | in LMU belegt, siehe B.4 |

#### `rF2Telemetry`: Mapping-Layout von `$rFactor2SMMP_Telemetry$` (Mapping-Größe 241680 Bytes)

| Offset (dez) | Offset (hex) | Feld | C-Typ | Größe |
|---:|---:|---|---|---:|
| 0 | 0x0 | `mVersionUpdateBegin` | `unsigned long` | 4 |
| 4 | 0x4 | `mVersionUpdateEnd` | `unsigned long` | 4 |
| 8 | 0x8 | `mBytesUpdatedHint` | `int` | 4 |
| 12 | 0xC | `mNumVehicles` | `long` | 4 |
| 16 | 0x10 | `mVehicles` | `rF2VehicleTelemetry[128]` | 241664 |

Fahrzeug `i` liegt bei `16 + i*1888`, Rad `j` dieses Fahrzeugs bei `16 + i*1888 + 848 + j*260`.

#### `rF2Scoring`: Mapping-Layout von `$rFactor2SMMP_Scoring$` (Mapping-Größe 75312 Bytes)

| Offset (dez) | Offset (hex) | Feld | C-Typ | Größe |
|---:|---:|---|---|---:|
| 0 | 0x0 | `mVersionUpdateBegin` | `unsigned long` | 4 |
| 4 | 0x4 | `mVersionUpdateEnd` | `unsigned long` | 4 |
| 8 | 0x8 | `mBytesUpdatedHint` | `int` | 4 |
| 12 | 0xC | `mScoringInfo` | `rF2ScoringInfo` | 548 |
| 560 | 0x230 | `mVehicles` | `rF2VehicleScoring[128]` | 74752 |

Fahrzeug `i` liegt bei `560 + i*584`. Ein ScoringInfo-Feld liegt bei `12 + offset`, z. B. `mNumVehicles` bei 116 und `mGamePhase` bei 120.

#### Weitere Buffer (Kurzfassung)

- `$rFactor2SMMP_Extended$`: Version-Block (8 Bytes), **ohne** `mBytesUpdatedHint`, danach direkt `rF2Extended`.
  Die ersten Felder: `char mVersion[12]` (Offset 8), `bool is64bit` (20), 3 Bytes Padding, `rF2PhysicsOptions mPhysics` (24, 40 Bytes, 4-Byte-Ausrichtung wegen der `float`-Felder;
  TC/ABS/Stabilitätshilfe, Fuel-/Tire-Multiplikator), danach `rF2TrackedDamage mTrackedDamages[512]` (ab Offset 64, je 2×`double`:
  max. und akkumulierte Aufprallstärke). Es folgen Session-Flags, Ticks, Meldungen, `mCurrentPitSpeedLimit` (`float`, m/s;
  braucht DMA) usw. Die Offsets hinter `mPhysics` haben wir **nicht ausgerechnet**. Wer sie braucht, berechnet sie mit derselben Methode aus `rF2State.h`.
- `$rFactor2SMMP_ForceFeedback$`: Version-Block, dann `double mForceValue` bei Offset 8.

### A.5 Enums

| Feld | Werte |
|---|---|
| `mGamePhase` (u8) | 0 Garage / vor Sessionstart, 1 WarmUp (Recon-Runden, nur Rennen), 2 GridWalk, 3 Formation, 4 Countdown (Startampel), 5 GreenFlag, 6 FullCourseYellow / Safety Car, 7 SessionStopped, 8 SessionOver, 9 Paused (Heartbeat-Aufruf) |
| `mYellowFlagState` (s8) | −1 Invalid, 0 NoFlag, 1 Pending, 2 PitClosed, 3 PitLeadLap, 4 PitOpen, 5 LastLap, 6 Resume, 7 RaceHalt (nicht genutzt) |
| `mSession` (long) | 0 Testday, 1–4 Practice 1–4, 5–8 Qualifying 1–4, 9 Warmup, 10–13 Race 1–4 |
| `mFinishStatus` (s8) | 0 None, 1 Finished, 2 DNF, 3 DQ |
| `mPitState` (u8) | 0 None, 1 Request, 2 Entering, 3 Stopped, 4 Exiting |
| `mFlag` (u8) | 0 Green, 6 Blue (laut Header die einzigen Werte) |
| `mSectorFlag[3]` (s8) | Laut Header: "whether there are any local yellows at the moment in each sector (not sure if sector 0 is first or last, so test)". Allgemein gilt 0 = keine Gelbphase, ≠0 = gelb. **UNVERIFIED:** die Bedeutung der Indizes und Werte; in LMU wurden die Werte 1 und 11 beobachtet. Vermutlich gilt wie bei `mSector`: Index 0 = Sektor 3. |
| `mSector` (s8) | 0 Sektor 3, 1 Sektor 1, 2 Sektor 2 |
| `mControl` (s8) | −1 Nobody, 0 Player, 1 AI, 2 Remote, 3 Replay |
| `mCountLapFlag` (u8) | 0 DoNotCount, 1 CountLapButNotTime, 2 CountLapAndTime |
| `mIndividualPhase` (u8) | GamePhase-Werte plus 9 after formation, 10 under yellow, 11 under blue (nicht genutzt) |
| `mSurfaceType` (u8) | 0 Dry, 1 Wet, 2 Grass, 3 Dirt, 4 Gravel, 5 Kerb, 6 Special |
| `mRearFlapLegalStatus` | 0 Disallowed, 1 DetectedButNotAllowedYet, 2 Allowed |
| `mIgnitionStarter` | 0 Off, 1 Ignition, 2 IgnitionAndStarter |

### A.6 Einheiten und Fallstricke

- Reifentemperaturen (`mTemperature`, `mTireCarcassTemperature`, `mTireInnerLayerTemperature`) sind in **Kelvin**; für °C 273.15 abziehen.
  `mBrakeTemp`, Wasser- und Öltemperatur sind laut Header bereits in **°C**.
- `mPressure` ist in kPa (× 0.1450377 = psi). `mFuel` und `mFuelCapacity` sind in **Liter**. Strecken in m, Zeiten in s.
- Sektorzeiten sind kumuliert: S2 = `mLastSector2 − mLastSector1`, S3 = `mLastLapTime − mLastSector2`. Ungültige Zeiten sind ≤ 0, oft −1 (**UNVERIFIED** für alle Felder).
- `mWear`: Laut Header "0.0-1.0, fraction of maximum". In der Praxis zeigen Tools 1.0 als neu; eine LMU-Messung ergab Werte, die mit REST `tireCondition` übereinstimmen (0.93 … 0.83). **UNVERIFIED:** die Richtung ist live zu prüfen.
- `mRearBrakeBias`: Laut Header "fraction of brakes on rear". Ein LMU-Projekt (race-engineer, siehe Quellen) hat live gemessen, dass `mRearBrakeBias × 100` dem im Garage-Menü angezeigten **Front**-Wert entspricht (52.5). **UNVERIFIED** in allgemeiner Form.
- Geschwindigkeit: `sqrt(x²+y²+z²)` von `mLocalVel` in m/s, × 3.6 = km/h. Gängige Community-Konvention: Bei Vorwärtsfahrt ist `mLocalVel.z` negativ (**UNVERIFIED**).
- Strings sind nullterminierte `char`-Arrays. **UNVERIFIED** ist die Kodierung (vermutlich ANSI/CP1252); wir dekodieren tolerant mit `errors="replace"` und schneiden am ersten `\0` ab.
- `mMaxLaps` ist bei zeitbasierten Sessions eine Zahl nahe INT32_MAX. Werte über 1 000 000 behandeln wir als "unbegrenzt".
- `pointer1` und `pointer2` sind 8 Bytes groß, weil das Spiel ein 64-Bit-Build ist. Ein 32-Bit-Build hätte 4 Bytes und andere Offsets, aber rF2 und LMU sind x64.
- Telemetrie in rF2 kann nur für das Spielerfahrzeug vollständig sein, je nach Server-Einstellungen; Gegner haben teilweise Nullen. **UNVERIFIED**.

### A.7 Größen und Gegenprüfung

| Struct | sizeof (berechnet) | Gegenprüfung |
|---|---:|---|
| `rF2Vec3` | 24 | `static_assert(sizeof(rF2Vec3) == sizeof(TelemVect3))` |
| `rF2Wheel` | **260** | `static_assert(... == sizeof(TelemWheelV01))`; LMU-Bridge `lmuWheelSize = 260`; pyLMU `ctypes.sizeof(LMUWheel) = 260` |
| `rF2VehicleTelemetry` | **1888** | static_assert gegen `TelemInfoV01`; LMU-Bridge `lmuVehicleTelemetrySize = 1888`; irdashies-Review "sizeof(TelemInfoV01) is 1888"; Radoffset 848 (`lmuWheelOffset = 848`) |
| `rF2ScoringInfo` | **548** | static_assert gegen `ScoringInfoV01`; `lmuScoringInfoSize = 548`; goLMUSharedMemory: "Size is 548 bytes" |
| `rF2VehicleScoring` | **584** | static_assert gegen `VehicleScoringInfoV01`; `lmuVehicleScoringSize = 584` |
| Mapping Telemetry | 241680 | 8 + 4 + 4 + 128·1888 |
| Mapping Scoring | 75312 | 8 + 4 + 548 + 128·584 |

Der C#-Monitor bestimmt seine Puffergröße zur Laufzeit mit `Marshal.SizeOf(typeof(MappedBufferT))`; feste Zahlen stehen dort nicht.
Die genannten Konstanten stammen aus unabhängigen LMU-Implementierungen, die auf demselben ISI-Struct-Satz beruhen.
Alle gemeinsamen Felder liegen dort auf identischen Offsets; das wurde automatisch gegen pyLMUSharedMemory verglichen.
Die Pack-4-Annahme ist damit dreifach bestätigt. Live gegen ein laufendes rF2 wurde **nicht** geprüft.

### A.8 Python-Skizze (nur Lesen)

```python
import mmap, struct
TELEM_NAME = "$rFactor2SMMP_Telemetry$"; TELEM_SIZE = 241680
m = mmap.mmap(-1, TELEM_SIZE, tagname=TELEM_NAME, access=mmap.ACCESS_READ)  # nur unter Windows
def read_consistent(m, full_size, header_len=12, base=16, stride=1888, n_field_off=12):
    for _ in range(10):
        b0, e0 = struct.unpack_from("<II", m, 0)
        if b0 != e0: continue
        hint = struct.unpack_from("<i", m, 8)[0]
        n = 8 + hint if hint > 0 else full_size        # +8: Hint zählt ohne Version-Block
        raw = m[:n]
        b1, e1 = struct.unpack_from("<II", raw, 0)
        b2, e2 = struct.unpack_from("<II", m, 0)
        if b1 == e1 and (b1, e1) == (b2, e2):
            return raw
    return None
```

Achtung: `mmap(-1, size, tagname)` legt das Mapping an, falls es noch nicht existiert. Ein leeres, genulltes Mapping
bedeutet "Spiel oder Plugin läuft nicht". Wer das sicher erkennen will, nutzt `OpenFileMappingW` über ctypes.

---

## Teil B – Le Mans Ultimate (LMU)

### B.1 Überblick

LMU basiert auf der rF2-Engine (gMotor) und nutzt dieselbe InternalsPlugin-API.
**Seit LMU 1.2** (Release-Datum **UNVERIFIED**, vermutlich 2025) liefert das Spiel ein **eigenes Shared-Memory-Interface** mit.
Der Header `SharedMemoryInterface.hpp` von Studio 397 liegt im Spielordner unter `Le Mans Ultimate\Support\SharedMemoryInterface`.
Das sagen übereinstimmend pyLMUSharedMemory (TinyPedal) und apex-lmu. Den Header selbst konnten wir nicht einsehen, weil er nicht öffentlich im Netz liegt.
Die folgenden Angaben stammen aus drei unabhängigen Nachbauten: pyLMUSharedMemory (Python ctypes), apex-lmu (Go, explizite Offsets) und goLMUSharedMemory (Go).

**Voraussetzung:** Settings → Gameplay → **Enable Plugins = ON**, danach das Spiel neu starten.
Diese Einstellung gilt auch ohne installiertes Plugin; ist sie aus, veröffentlicht LMU kein `LMU_Data` (laut lmu-pitwall verifiziert).

### B.2 Namen

| Objekt | Name | Quelle |
|---|---|---|
| Daten-Mapping | `LMU_Data` | pyLMU `LMU_SHARED_MEMORY_FILE`, apex-lmu `lmuSharedMemoryName`, goLMU `SharedMemoryName` |
| Lock-Mapping (8 Bytes) | `LMU_SharedMemoryLockData` | apex-lmu `openLMUMemoryLock` |
| Lock-Event | `LMU_SharedMemoryLockEvent` | apex-lmu (`CreateEventW`, auto-reset) |
| Update-Event | `LMU_Data_Event` | goLMU `SharedMemoryEventName` (**UNVERIFIED**, nur eine Quelle) |
| Prozess | `Le Mans Ultimate.exe` | pyLMU `LMU_PROCESS_NAME`, apex-lmu |

**Lock-Protokoll** (aus apex-lmu, Nachbau des SDK-Locks; Details **UNVERIFIED** gegen den Original-Header):
Das Lock-Mapping enthält `int32 waiters` bei +0 und `int32 busy` bei +4.
Erwerben: bis zu 4000-mal Spin-CAS `busy: 0 → 1`. Klappt das nicht, `waiters += 1`, dann eine Schleife aus CAS-Versuch und
`WaitForSingleObject(event, 100 ms)`; bei Erfolg `waiters −= 1`.
Freigeben: `busy = 0`; gilt `waiters > 0`, wird `SetEvent(event)` aufgerufen.
Innerhalb des Locks wird der ganze Payload kopiert. In Python geht das über `ctypes.windll.kernel32`; für das CAS eignet sich `InterlockedCompareExchange`.
Ohne Lock (pyLMU "copy access") kopiert man nur, wenn `generic.events.SME_UPDATE_SCORING` oder `SME_UPDATE_TELEMETRY`
gesetzt ist **und** `scoringInfo.mNumVehicles == telemetry.activeVehicles` gilt. Das ist eine Heuristik.

### B.3 Layout von `LMU_Data` (`SharedMemoryLayout` / `SharedMemoryObjectOut`)

- `MAX_MAPPED_VEHICLES = 104` (nicht 128).
- Die Payload-Structs (`InternalsPlugin.hpp`) sind mit Pack 4 gepackt. Die äußeren Wrapper aus `SharedMemoryInterface.hpp` nutzen
  die Windows-Standardausrichtung (laut apex-lmu). Deshalb gibt es 4 Bytes Padding vor dem 8-Byte-`size_t` und 4 Bytes Tail-Padding.
- **Payload 324 820 Bytes**, `sizeof(SharedMemoryLayout)` = **324 824 Bytes**. Die Werte aus apex-lmu und pyLMU stimmen überein.
- Es gibt **keinen** Version-Block wie beim rF2-Plugin. Konsistenz sichert das Lock.

| Abs. Offset | Feld | Typ | Größe |
|---:|---|---|---:|
| 0 | `generic` (`SharedMemoryGeneric`) | struct | 332 |
| 0 | ├ `events` (`SharedMemoryEvent`, 16 × `uint32`, Index = Enum-Wert) | `uint32[16]` | 64 |
| 64 | ├ `gameVersion` | `int32` | 4 |
| 68 | ├ `FFBTorque` | `float` | 4 |
| 72 | └ `appInfo` (`ApplicationStateV01`: `mAppWindow` u64, `mWidth`, `mHeight`, `mRefreshRate`, `mWindowed` u32, `mOptionsLocation` u8, `mOptionsPage` char[31], `mExpansion[204]`) | struct | 260 |
| 332 | `paths` (`SharedMemoryPathData`: `userData`, `customVariables`, `stewardResults`, `playerProfile`, `pluginsFolder`, je `char[260]`) | struct | 1300 |
| 1632 | `scoring` (`SharedMemoryScoringData`) | struct | 126 832 |
| 1632 | ├ `scoringInfo` (`ScoringInfoV01`) | struct | 548 |
| 2180 | ├ 4 Bytes Padding + `scoringStreamSize` (`size_t`, bei 2184) | | 12 |
| 2192 | ├ `vehScoringInfo[104]` (`VehicleScoringInfoV01`) | struct[104] | 60 736 |
| 62 928 | └ `scoringStream` | `char[65536]` | 65 536 |
| 128 464 | `telemetry` (`SharedMemoryTelemtryData`, Tippfehler im Original) | struct | 196 356 |
| 128 464 | ├ `activeVehicles` | `uint8` | 1 |
| 128 465 | ├ `playerVehicleIdx` | `uint8` | 1 |
| 128 466 | ├ `playerHasVehicle` | `bool` | 1 |
| 128 468 | └ `telemInfo[104]` (`TelemInfoV01`) | struct[104] | 196 352 |

Fahrzeug-Scoring `i` liegt bei `2192 + i*584`, Telemetrie `i` bei `128468 + i*1888`.
Laut irdashies-Diskussion gilt in LMU `telemInfo[i].mID == i`, sodass `playerVehicleIdx` direkt als Index dient (**UNVERIFIED**).
Wir ordnen trotzdem über `mID` zu.

**`SharedMemoryEvent`-Enum** (Reihenfolge = Index in `events[]`):
0 `SME_ENTER`, 1 `SME_EXIT`, 2 `SME_STARTUP`, 3 `SME_SHUTDOWN`, 4 `SME_LOAD`, 5 `SME_UNLOAD`, 6 `SME_START_SESSION`,
7 `SME_END_SESSION`, 8 `SME_ENTER_REALTIME`, 9 `SME_EXIT_REALTIME`, 10 `SME_UPDATE_SCORING`, 11 `SME_UPDATE_TELEMETRY`,
12 `SME_INIT_APPLICATION`, 13 `SME_UNINIT_APPLICATION`, 14 `SME_SET_ENVIRONMENT`, 15 `SME_FFB` (danach `SME_MAX`).
Die Namen entsprechen den InternalsPlugin-Callbacks. **UNVERIFIED** ist die Semantik der Werte (Flag oder Zähler).

Raten: Telemetrie ~100 Hz, Scoring 5 Hz (laut lmu-pitwall).

### B.4 LMU-Struct-Erweiterungen (gleiche Basis, belegte Expansion-Bereiche)

Die Größen sind identisch zu rF2: Wheel 260, Telemetrie 1888, ScoringInfo 548, VehicleScoring 584. Alle rF2-Felder liegen auf den Offsets aus Teil A.
LMU belegt zusätzlich Teile der Expansion-Bereiche. Die Angaben stammen aus pyLMUSharedMemory `lmu_data.py`; die Offsets haben wir berechnet.

**`TelemWheelV01` (LMU), ab 236:** `mOptimalTemp` `float`@236 (°C) · `mCompoundIndex` u8@240 · `mCompoundType` u8@241 (0 soft, 1 medium, 2 hard, 3 wet) · `mExpansion[18]`@242.

**`TelemInfoV01` (LMU), ab 696** (entspricht Variante 2 in A.4.3):

| Offset | Feld | Typ | Bemerkung |
|---:|---|---|---|
| 696 | `mDeltaBest` | `double` | s |
| 704 | `mBatteryChargeFraction` | `double` | 0..1 |
| 712 | `mElectricBoostMotorTorque` | `double` | Nm |
| 720 | `mElectricBoostMotorRPM` | `double` | |
| 728 | `mElectricBoostMotorTemperature` | `double` | |
| 736 | `mElectricBoostWaterTemperature` | `double` | |
| 744 | `mElectricBoostMotorState` | u8 | 0 unavailable, 1 inactive, 2 propulsion, 3 regeneration |
| 745 | `mLapInvalidated` | `bool` | |
| 746 | `mABSActive` | `bool` | |
| 747 | `mTCActive` | `bool` | |
| 748 | `mSpeedLimiterActive` | `bool` | |
| 749 | `mWiperState` | u8 | 0 off, 1 auto, 2 slow, 3 fast |
| 750–751 | `mTC`, `mTCMax` | u8 | TC-Stufe / Maximum |
| 752–753 | `mTCSlip`, `mTCSlipMax` | u8 | |
| 754–755 | `mTCCut`, `mTCCutMax` | u8 | |
| 756–757 | `mABS`, `mABSMax` | u8 | |
| 758–759 | `mMotorMap`, `mMotorMapMax` | u8 | |
| 760–761 | `mMigration`, `mMigrationMax` | u8 | Bremsmigration |
| 762–763 | `mFrontAntiSway`, `mFrontAntiSwayMax` | u8 | |
| 764–765 | `mRearAntiSway`, `mRearAntiSwayMax` | u8 | |
| 766 | `mLiftAndCoastProgress` | u8 | 0–255 |
| 767 | `mTrackLimitsSteps` | u8 | normierte Track-Limit-Punkte |
| 768 | `mRegen` | `float` | kW |
| 772 | `mStateOfCharge` | `float` | Batterie, laut pyLMU "percent" (Skala **UNVERIFIED**) |
| 776 | `mVirtualEnergy` | `float` | **Virtual Energy**, Anteil (laut pyLMU "fraction") |
| 780 | `mTimeGapCarAhead` | `float` | s |
| 784 | `mTimeGapCarBehind` | `float` | s |
| 788 | `mTimeGapPlaceAhead` | `float` | s |
| 792 | `mTimeGapPlaceBehind` | `float` | s |
| 796 | `mVehicleModel` | `char[30]` | Marke und Modell |
| 826 | `mVehicleClass` | u8 | Klassen-ID (nicht identisch mit dem Scoring-String) |
| 827 | `mVehicleChampionship` | u8 | Meisterschaft und Jahr |
| 828 | `mExpansion[20]` | | |
| 848 | `mWheel[4]` | `TelemWheelV01[4]` | |

**`ScoringInfoV01` (LMU), ab 340:** `mSessionTimeRemaining` `float`@340 · `mTimeOfDay` `float`@344 · `mIsFixedSetup` `bool`@348 ·
`mTrackGripLevel` u8@349 · `mCloudCoverage` u8@350 · `mTrackLimitsStepsPerPenalty` u8@351 · `mTrackLimitsStepsPerPoint` u8@352 ·
`mExpansion[187]`@353 · Pointer@540.

**`VehicleScoringInfoV01` (LMU), ab 536:** `mSteamID` `unsigned long long`@536 · `mVehFilename` `char[32]`@544 ·
`mAttackMode` `short`@576 (Formel E) · `mFuelFraction` u8@578 (0x00 = 0 %, 0xFF = 100 % Kraftstoff oder Batterie) · `mDRSState` `bool`@579 · `mExpansion[4]`@580.

Für einen Parser heißt das: **ein gemeinsamer Decoder für die ISI-Structs**. Für LMU kommen optionale Felder aus den Expansion-Bereichen dazu;
nur die Container unterscheiden sich (rF2-Plugin-Mappings oder `LMU_Data`).

### B.5 Funktioniert das rF2-Plugin noch mit LMU?

- Ja, laut Community-Berichten (LMU-Forum, April 2025) und laut race-engineer, das das Plugin am 14.06.2026 live auf einem aktuellen Build bestätigt hat:
  Beide Mappings öffnen sich, der Pack-4-Decode ist korrekt.
  Installation: `rFactor2SharedMemoryMapPlugin64.dll` nach `Le Mans Ultimate\Plugins\`, alternativ `Bin64\Plugins` (je nach Build **UNVERIFIED**).
  Freischalten in `UserData\player\CustomPluginVariables.JSON` mit `"rFactor2SharedMemoryMapPlugin64.dll": {" Enabled": 1, ...}`.
  Der Schlüssel hat ein **führendes Leerzeichen**.
- Da das Plugin per `memcpy` kopiert, enthalten die rF2-Mappings unter LMU auch die LMU-Erweiterungen aus B.4 (z. B. `mVirtualEnergy` bei Telemetrie-Offset 776).
  Das ist eine logische Folgerung und **UNVERIFIED**.
- Dennoch empfehlen wir **`LMU_Data` als primäre Quelle für LMU**: Es ist offiziell, braucht keine DLL-Installation und deckt bis zu 104 Fahrzeuge ab.
  Das rF2-Plugin ist der Fallback. Es bleibt die Primärquelle für rFactor 2.
- Linux/Proton: Wine legt benannte Mappings intern an; pyLMU liest unter Linux `/dev/shm/LMU_Data`. Das funktioniert nur, wenn eine Bridge im Wine-Prefix die Daten dorthin spiegelt (**UNVERIFIED**, nicht Teil unseres MVP).

### B.6 LMU REST API (`localhost:6397`)

Die API ist inoffiziell, nicht dokumentiert und ändert sich zwischen Versionen. Wir nutzen **nur GET** und fragen mit etwa 1–2 Hz ab, abseits des SHM-Pfads.
Basis-URL ist `http://127.0.0.1:6397`; einige Nutzer berichten, dass nur IPv6 `[::1]` funktioniert.
Die Swagger-UI liegt unter `/swagger/index.html`, die Spec unter `/swagger-schema.json` (OpenAPI 2.0).
Die Feldnamen stammen aus Live-Mitschnitten von race-engineer (2026-06) und gelten **UNVERIFIED** für andere Builds.

| Endpoint | Inhalt (Auszug) | Quelle |
|---|---|---|
| `GET /rest/garage/UIScreen/RepairAndRefuel` | `fuelInfo.currentVirtualEnergy` / `maxVirtualEnergy` (roher Wert; Anteil = current/max), `currentFuel`/`maxFuel` (L), `currentBattery`/`maxBattery`; `pitMenu`, `pitRecommendations`, `pitStopLength`, `weatherForecast`; Verschleiß von Aero, Bremsen, Aufhängung | lmu-pitwall, race-engineer, TinyPedal |
| `GET /rest/strategy/usage` | VE-Verbrauch je Runde, nach Fahrer | lmu-pitwall |
| `GET /rest/strategy/pitstop-estimate` | `{fuel, tires, brakes, brakeDucts, damage, driverSwap, penalties, ve, total}` in s | race-engineer |
| `GET /rest/garage/getVehicleCondition` | `fuel`, `fuelCapacity`, `tireCondition[4]`, `brakeCondition[4]`, `suspensionDamage[4]`, `vehicleDamage` | race-engineer |
| `GET /rest/garage/getPlayerGarageData` | aktuelles Setup als `VM_*`-Objekte `{value, stringValue, …}` (Bremsbalance, Motor-Mapping, VE, Mischung) | race-engineer, TinyPedal |
| `GET /rest/garage/UIScreen/TireManagement` | `tireInventory`, `expectedUsage`, `optimalCompoundConditions`, `wheelInfo` | race-engineer |
| `GET /rest/garage/UIScreen/CarSetupOverview` | `carSetup`, `carPresetSetups`, `weatherForecast` | race-engineer |
| `GET /rest/watch/standings` | je Fahrzeug: Klasse, Position, Runden- und Sektorzeiten, Abstände (auch zum Klassenführenden), `fuelFraction`, `veFraction`, `pitState`, Strafen | race-engineer |
| `GET /rest/watch/sessionInfo` | Temperaturen, `gamePhase`, `maxTime`, `timeRemainingInGamePhase`, `raining`, `windSpeed` … | race-engineer |
| `GET /rest/sessions/GetGameState` | Enum-Strings wie `gamePhase: "GPHASE_GREEN"`, `PitState: "NONE"`, `MultiStintState` | race-engineer |
| `GET /rest/sessions`, `/rest/sessions/weather`, `/rest/sessions/getAllVehicles` | Session, Wetter und Vorhersage, Fahrzeugliste | TinyPedal, LMU-Forum |

Es gibt auch **schreibende** Endpunkte; Crew Chief setzt damit z. B. das Pit-Menü. Diese rufen wir **nicht** auf.

---

## Quellen

| Quelle | URL | Lizenz | Genutzt für |
|---|---|---|---|
| rF2SharedMemoryMapPlugin: `Include/rF2State.h`, `Include/MappedBuffer.h`, `Source/rFactor2SharedMemoryMap.cpp`, `Monitor/rF2SMMonitor/rF2SMMonitor/rF2Data.cs`, `.../MappedBuffer.cs`, README | <https://github.com/TheIronWolfModding/rF2SharedMemoryMapPlugin> | **GPL-3.0** (`LICENSE`); DLL-Weitergabe laut README frei, mit README | Struct-Definitionen, Namen, Sync-Algorithmus |
| `Include/InternalsPlugin.hpp` (im selben Repository, von S397) | <https://github.com/TheIronWolfModding/rF2SharedMemoryMapPlugin/blob/master/Include/InternalsPlugin.hpp> | proprietär, © Studio 397 ("PROPRIETARY AND CONFIDENTIAL") | `mDeltaBest`-Variante |
| pyLMUSharedMemory (TinyPedal), `lmu_data.py`, `lmu_mmap.py` | <https://github.com/TinyPedal/pyLMUSharedMemory> | **UNVERIFIED** (keine LICENSE-Datei gefunden; TinyPedal selbst steht unter GPL-3.0, ebenfalls UNVERIFIED) | LMU-Layout, Felder, Events |
| apex-lmu, `bridge/lmu_memory_windows.go`, `bridge/lmu_contract.go`, README | <https://github.com/ralfboltshauser/apex-lmu> | GPL-3.0-or-later (README-Badge) | Lock-Namen und -Protokoll, Größen und Offsets, Hinweis "seit LMU 1.2" |
| goLMUSharedMemory | <https://pkg.go.dev/github.com/stephenhoran/goLMUSharedMemory> | UNVERIFIED | `LMU_Data_Event`, ScoringInfo 548 |
| lmu-pitwall, README | <https://github.com/Swizzjack/lmu-pitwall> | MIT (laut README) | "Enable Plugins"-Voraussetzung, Raten, REST-Endpoints |
| race-engineer, `docs/03-LMU-INTEGRATION.md` | <https://github.com/Alexander-Gro/race-engineer/blob/main/docs/03-LMU-INTEGRATION.md> | Apache-2.0 | Plugin-Kompatibilität mit LMU, REST-Mitschnitte, Bremsbalance-Beobachtung |
| irdashies PR #747 (LMU-Bridge) | <https://github.com/tariknz/irdashies/pull/747> | UNVERIFIED | `sizeof(TelemInfoV01) = 1888`, `mID == i` |
| pyRfactor2SharedMemory (Tony Whitley), `rF2data.py` | <https://github.com/TonyWhitley/pyRfactor2SharedMemory> | UNVERIFIED | ältere Telemetrie-Variante (`mExpansion[152]`, Radoffset 848) |
| LMU-Forum: REST API documentation | <https://community.lemansultimate.com/index.php?threads/rest-api-documentation.3278/> | – | REST-Basis-URL und Swagger (zitiert nach race-engineer, nicht direkt abgerufen) |
| LMU-Forum: rF2-Plugin-Installation | <https://community.lemansultimate.com/index.php?threads/unable-to-install-rf2-shared-memory-map-plugin.7021/> | – | Plugin-Pfad (zitiert nach race-engineer, nicht direkt abgerufen) |
| LMU-Wiki: Telemetry | <https://lemansultimate.wiki.gg/wiki/Telemetry> | – | **nicht abrufbar** (Netzwerk-Sperre in der Recherche-Umgebung) |

### Offene Punkte (UNVERIFIED, live zu prüfen)

1. Bedeutung von Telemetrie-Offset 696 in aktuellem rF2: `mDeltaBest` oder `mBatteryChargeFraction`?
2. Den Original-`SharedMemoryInterface.hpp` aus dem LMU-Support-Ordner einsehen und mit B.3 vergleichen (Event-Semantik, Lock, `LMU_Data_Event`).
3. Richtung von `mWear`, Semantik von `mRearBrakeBias` in LMU, Werte von `mSectorFlag`.
4. Skala von `mStateOfCharge` und `mVirtualEnergy` (0..1 oder 0..100).
5. Payloads der REST-Endpoints auf dem aktuellen LMU-Build (Swagger-Spec ziehen).
