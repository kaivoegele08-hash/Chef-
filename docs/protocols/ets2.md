# Euro Truck Simulator 2 / American Truck Simulator: Telemetrie

Stand der Recherche: 2026-10-28. Zielplattform: Bazzite (immutable Fedora), Steam.
Steam-AppIDs: ETS2 = `227300`, ATS = `270880`.

Kurzfassung:

- ETS2 und ATS haben einen **nativen Linux-Build** (`bin/linux_x64/eurotrucks2` bzw. `amtrucks`). Das offizielle **SCS Telemetry SDK** unterstützt Plugins als `.so` unter Linux. Proton ist für ETS2 also nicht nötig und für die Telemetrie sogar nachteilig.
- Empfohlener Weg: Der Community-Fork **truckermudgeon/scs-sdk-plugin** (MIT) als `.so` in `bin/linux_x64/plugins/` legen. Er schreibt dasselbe Binärlayout wie RenCloud **Rev 12** per `shm_open("/SCSTelemetry")` nach `/dev/shm/SCSTelemetry` (32 KiB). Python liest das per `mmap` und `struct.unpack_from` (Offsets siehe unten, verifiziert).
- Fallback, wenn wir volle Kontrolle wollen: ein eigenes, sehr kleines C-Plugin gegen die SCS-SDK-Header (MIT-Lizenz von SCS), das JSON/UDP an `127.0.0.1` schickt.

---

## 1. Offizielles SCS Telemetry SDK

### 1.1 Aufbau

- Ein Plugin ist eine dynamische Bibliothek (Windows `.dll`, Linux `.so`, macOS `.dylib`). Sie exportiert `scs_telemetry_init(const scs_u32_t version, const scs_telemetry_init_params_t *params)` und optional `scs_telemetry_shutdown(void)`.
- Das Spiel lädt alle Bibliotheken aus dem Unterordner `plugins` neben der Spiel-Binary. Nur unter Windows gibt es zusätzlich die Registry (`HKLM\SOFTWARE\SCS Software\<GAME_NAME>\Plugins`). Das steht so im `readme.txt` des SDK.
- **Linux nativ:** `<Steam>/steamapps/common/Euro Truck Simulator 2/bin/linux_x64/plugins/*.so`
- **Windows / Proton:** `<Spielordner>/bin/win_x64/plugins/*.dll`
- Den Ordner `plugins` gibt es standardmäßig nicht, man muss ihn anlegen.
- Bei jedem Spielstart zeigt das Spiel einen Dialog („SDK has been activated“), der mit OK bestätigt werden muss. Das ist laut RenCloud-README bekannt und nicht abschaltbar.
- Threading: Alle Callbacks laufen im Haupt-Thread des Spiels, und Rückrufe ins Spiel sind nur aus diesen Callbacks erlaubt. Ein Plugin darf dort also **nicht blockieren**: kein blockierendes Netzwerk, kein `fsync`.
- Konsolenbefehle für die Entwicklung: `sdk reinit`, `sdk unload`, `sdk reload`.

### 1.2 Versionen

Es gibt drei verschiedene Versionsnummern, die leicht verwechselt werden:

| Begriff | Wert (Header-Stand im Plugin-Repo) | Bedeutung |
| --- | --- | --- |
| SDK-Paket | „SCS SDK 1.14“, laut SCS-Modding-Wiki die aktuelle stabile Version (Windows/Linux/OSX) | Download-Paket mit Headern und Beispielen |
| Telemetry-API | `SCS_TELEMETRY_VERSION_1_01` (`CURRENT`) | ABI der Init-Parameter |
| ETS2-Game-Telemetry-Version | `SCS_TELEMETRY_EUT2_GAME_VERSION_1_18` (Patch 1.45) | wird in `scs_telemetry_init_params_t::common.game_version` gemeldet |
| ATS-Game-Telemetry-Version | `SCS_TELEMETRY_ATS_GAME_VERSION_1_05` (Patch 1.45) | dito |

Zuordnung laut Header-Kommentaren: ETS2 1.12 = Patch 1.17, 1.13 = Patch 1.27, 1.14 = Patch 1.35, 1.15/1.16 = Patch 1.36, 1.17 = Patch 1.41, 1.18 = Patch 1.45.

UNVERIFIED: ob neuere Spielversionen (1.50+ in 2025/2026) eine höhere Game-Telemetry-Minor melden, etwa 1.19. Die Header in allen geprüften Repos enden bei 1.18. Die SCS-Wiki-Seiten (`modding.scssoft.com`) waren aus der Recherche-Umgebung nicht erreichbar. Für den Parser ist das egal, weil das Plugin-Layout unabhängig davon ist. Fehlende Kanäle bleiben 0.

### 1.3 Events (`scssdk_telemetry_event.h`)

- `SCS_TELEMETRY_EVENT_frame_start`: liefert `scs_telemetry_frame_start_t` mit `render_time`, `simulation_time`, `paused_simulation_time` (alle `scs_timestamp_t`, **Mikrosekunden**) und `flags` (`SCS_TELEMETRY_FRAME_START_FLAG_timer_restart`).
- `SCS_TELEMETRY_EVENT_frame_end`
- `SCS_TELEMETRY_EVENT_paused` / `SCS_TELEMETRY_EVENT_started`
- `SCS_TELEMETRY_EVENT_configuration`: liefert ID und Attributliste, siehe 1.5.
- `SCS_TELEMETRY_EVENT_gameplay` (seit SDK 1.10): liefert ID und Attributliste, siehe 1.6.

### 1.4 Wichtige Kanäle (Auswahl, `common/*_channels.h`)

| Kanal-ID | Typ | Einheit / Bedeutung |
| --- | --- | --- |
| `local.scale` | float | Zeit-/Distanzskalierung der Karte |
| `game.time` | u32 | absolute Spielzeit in **Spielminuten** seit Tag 1, 00:00 |
| `multiplayer.time.offset` | s32 | Offset der Spielzeit im Convoy-Modus |
| `rest.stop` | s32 | Minuten (Spielzeit) bis zur nächsten Pflichtpause |
| `truck.speed` | float | m/s, negativ bei Rückwärtsfahrt |
| `truck.cruise_control` | float | m/s, 0 = Tempomat aus |
| `truck.fuel.amount` | float | Liter |
| `truck.fuel.warning` | bool | Warnleuchte |
| `truck.fuel.consumption.average` | float | Liter/km |
| `truck.fuel.range` | float | km Restreichweite |
| `truck.odometer` | float | km |
| `truck.navigation.distance` | float | Meter bis zum Navi-Ziel |
| `truck.navigation.time` | float | Sekunden ETA (Wert des Routenberaters) |
| `truck.navigation.speed.limit` | float | m/s, beachtet die Option „Route Advisor speed limit“ |
| `truck.wear.engine/transmission/cabin/chassis/wheels` | float | 0..1 |
| `trailer.wear.chassis/wheels/body`, `trailer.cargo.damage` | float | 0..1 (je Trailer `trailer.N.*`) |
| `truck.engine.rpm`, `truck.engine.gear`, `truck.displayed.gear` | float/s32 | |
| `truck.brake.air.pressure`, `truck.oil.temperature`, `truck.water.temperature`, `truck.battery.voltage` | float | psi, °C, V |

### 1.5 Konfigurationen (`common/scssdk_telemetry_common_configs.h`)

Konfig-IDs: `substances`, `controls`, `hshifter`, `truck`, `trailer` (bzw. `trailer.N`, max. 10), `job`.

Wichtige Attribute:

- **truck:** `brand_id`, `brand`, `id`, `name`, `fuel.capacity`, `fuel.warning.factor`, `adblue.capacity`, `rpm.limit`, `gears.forward`, `gears.reverse`, `differential.ratio`, `forward.ratio[]`, `reverse.ratio[]`, `wheels.count`, `license.plate`, …
- **job:** `cargo.id`, `cargo`, `cargo.mass`, `cargo.unit.mass`, `cargo.unit.count`, `destination.city(.id)`, `destination.company(.id)`, `source.city(.id)`, `source.company(.id)`, `income` (u64), `delivery.time`, `planned_distance.km` (u32), `cargo.loaded`, `job.market`, `is.special.job`
  - `delivery.time` ist die **absolute** Spielzeit (Minuten), zu der das Lieferfenster endet. Restzeit = `delivery.time − game.time`.
  - `planned_distance.km` ist die geplante Strecke in simulierten km, ohne Fähren.

### 1.6 Gameplay-Events (`common/scssdk_telemetry_common_gameplay_events.h`)

| Event-ID | Attribute |
| --- | --- |
| `job.cancelled` | `cancel.penalty` (s64) |
| `job.delivered` | `revenue` (s64), `earned.xp` (s32), `cargo.damage` (float), `distance.km` (float), `delivery.time` (u32, Minuten Fahrzeit), `auto.park.used`, `auto.load.used` |
| `player.fined` | `fine.offence` (string, z. B. `speeding`, `red_signal`, `crash`, …), `fine.amount` (s64) |
| `player.tollgate.paid` | `pay.amount` (s64) |
| `player.use.ferry` | `pay.amount`, `source.name`, `target.name`, `source.id`, `target.id` |
| `player.use.train` | wie Ferry |

Lizenz der SDK-Header: `sdk_license.txt` sagt „SCS SDK, Copyright (C) 2016 SCS Software“ und enthält einen **MIT-Lizenztext**. Eigene Plugins und das Weitergeben der Header sind damit erlaubt, solange der Copyright-Hinweis erhalten bleibt.

---

## 2. Community-Plugin RenCloud/scs-sdk-plugin und Linux-Forks

### 2.1 Überblick

| Projekt | Lizenz | Plattform | Shared-Memory-Name | Layout-Rev | Letzter Commit |
| --- | --- | --- | --- | --- | --- |
| RenCloud/scs-sdk-plugin (Fork von nlhans/ets2-sdk-plugin) | MIT („Copyright (c) 2014 Hans“) | **nur Windows** (`#include <windows.h>`, `CreateFileMapping`) | `Local\SCSTelemetry` | 12 | 2023-09 |
| **truckermudgeon/scs-sdk-plugin** | MIT | Windows, **Linux**, macOS (CMake). Release **v1.12.1** (Dez. 2025) mit Binaries | Linux/macOS: `shm_open("/SCSTelemetry")`, also **`/dev/shm/SCSTelemetry`** | 12 (identisch) | 2025-12-19 |
| alxwk/scs-sdk-plugin (Merge aus jackz314, y1lichen, RenCloud) | MIT | Linux (Datei + `mmap`) | Datei **`/dev/shm/SCS/SCSTelemetry`** | 12 (identisch) | 2026-06 |
| jackz314/scs-sdk-plugin | MIT | Linux | `/dev/shm/SCS/SCSTelemetry` | **10** (veraltet) | 2021-01 |
| spacefreak18/simapi (`scs2.h`) | LGPL-3.0 | Linux-Reader | erwartet `SCS/SCSTelemetry` (alxwk-Variante) | Kopie von Rev 12 | 2026-10 |
| kniffen/TruckSim-Telemetry (Node-Addon) | MIT | Win/Linux/macOS-Reader | nutzt den truckermudgeon-Fork | Rev 12 | 2026-07 |

Antworten auf die Fragen aus dem Auftrag:

- RenCloud selbst unterstützt Linux **nicht**.
- Die Forks schon. Sie unterscheiden sich nur im Pfad: `/dev/shm/SCSTelemetry` bei truckermudgeon, `/dev/shm/SCS/SCSTelemetry` bei alxwk/jackz314. Unser Reader probiert **beide**.
- Mapping-Größe: `SCS_PLUGIN_MMF_SIZE = 32*1024` (32768 Bytes). Davon belegt `scsTelemetryMap_t` **21600 Bytes**.
- truckermudgeon legt das Objekt mit Modus `0600` an. Der Reader muss also als derselbe Benutzer laufen wie das Spiel. Das ist bei uns ohnehin der Fall.
- „v1.12“ im Auftrag entspricht dem truckermudgeon-Release v1.12.1 (CMake `VERSION 1.12.1`). Intern ist das **`PLUGIN_REVID 12`** („Rev 12“). Das Feld `scs_values.telemetry_plugin_revision` (Offset 40) enthält dann `12`. Bei jeder neuen Rev sind Offset-Verschiebungen möglich (laut Changelog z. B. Rev 11 → 12 durch `wearBody` im Trailer). **Der Parser muss `telemetry_plugin_revision == 12` prüfen und sonst ablehnen.**

### 2.2 Layout `scsTelemetryMap_t` (Rev 12), verifizierte Offsets

Methode: Ich habe den Header `scs-telemetry/inc/scs-telemetry-common.hpp` (RenCloud master, identisch mit truckermudgeon v1.12.1 und alxwk master) automatisch geparst und alle 298 Felder mit `g++` und `offsetof()` auf x86_64 gegengeprüft. Ergebnis: 0 Abweichungen, `sizeof(scsTelemetryMap_t) = 21600`, `sizeof(scsTrailer_t) = 1560`. Auch mit `#pragma pack(4)`, wie ihn simapi nutzt, ändern sich keine Offsets.

Typen sind **Little Endian**:

| Code | C-Typ |
| --- | --- |
| `bool` | 1 Byte |
| `u32` | `unsigned int` |
| `i32` | `int` |
| `f32` | `float` |
| `f64` | `double` |
| `u64` | `unsigned long long` |
| `i64` | `long long` |
| `char[N]` | NUL-terminiert, UTF-8 |

Die „END OF ZONE“-Kommentare im Header sind inklusive Ende gemeint. Der Kommentar „END OF 14TH ZONE AT OFFSET 21619“ ist **falsch**, korrekt ist 21599: 6000 + 10 × 1560 = 21600.

#### Zonenübersicht

| Zone | Start | Inhalt |
| --- | --- | --- |
| 1 | 0 | Status, Zeitstempel |
| 2 | 40 | `u32` |
| 3 | 500 | `i32` |
| 4 | 700 | `f32` |
| 5 | 1500 | `bool` |
| 6 | 1640 | Float-Vektoren |
| 7 | 2000 | Float-Placement (Kabine/Kopf) |
| 8 | 2200 | Double-Placement (Weltposition Truck) |
| 9 | 2300 | Strings |
| 10 | 4000 | `u64` |
| 11 | 4200 | `i64` |
| 12 | 4300 | Event-Flags |
| 13 | 4400 | Substanzen (25 × 64) |
| 14 | 6000 | 10 Trailer à 1560 |

#### Felder für den Race-Engineer (absolute Offsets)

| Offset | Typ | Feld | Bemerkung / Einheit |
| ---: | --- | --- | --- |
| 0 | bool | `sdkActive` | true, solange das Plugin aktiv ist |
| 4 | bool | `paused` | true bei Pause oder Menü |
| 8 | u64 | `time` | µs, kontinuierlich, aus `paused_simulation_time` (steht in Pause still). **Änderungszähler für den Reader** |
| 16 | u64 | `simulatedTime` | µs, aus `simulation_time` |
| 24 | u64 | `renderTime` | µs, aus `render_time` |
| 32 | i64 | `multiplayerTimeOffset` | |
| 40 | u32 | `scs_values.telemetry_plugin_revision` | muss **12** sein |
| 44 | u32 | `scs_values.version_major` | SDK-Game-Version major |
| 48 | u32 | `scs_values.version_minor` | |
| 52 | u32 | `scs_values.game` | 0 = unbekannt, 1 = ETS2, 2 = ATS |
| 56 | u32 | `scs_values.telemetry_version_game_major` | |
| 60 | u32 | `scs_values.telemetry_version_game_minor` | |
| 64 | u32 | `common_ui.time_abs` | Spielzeit in Minuten (`game.time`) |
| 68 | u32 | `config_ui.gears` | |
| 72 | u32 | `config_ui.gears_reverse` | |
| 80 | u32 | `config_ui.truckWheelCount` | |
| 88 | u32 | `config_ui.time_abs_delivery` | Job-`delivery.time`, absolute Minuten. Restzeit = 88 − 64 |
| 92 | u32 | `config_ui.maxTrailerCount` | |
| 96 | u32 | `config_ui.unitCount` | |
| 100 | u32 | `config_ui.plannedDistanceKm` | |
| 440 | u32 | `gameplay_ui.jobDeliveredDeliveryTime` | Minuten (aus `job.delivered`) |
| 444 | u32 | `gameplay_ui.jobStartingTime` | |
| 448 | u32 | `gameplay_ui.jobFinishedTime` | |
| 500 | i32 | `common_i.restStop` | Minuten bis zur nächsten Pause (`rest.stop`) |
| 504 | i32 | `truck_i.gear` | |
| 508 | i32 | `truck_i.gearDashboard` | |
| 640 | i32 | `gameplay_i.jobDeliveredEarnedXp` | |
| 700 | f32 | `common_f.scale` | `local.scale` |
| 704 | f32 | `config_f.fuelCapacity` | Liter |
| 708 | f32 | `config_f.fuelWarningFactor` | |
| 712 | f32 | `config_f.adblueCapacity` | |
| 740 | f32 | `config_f.engineRpmMax` | |
| 748 | f32 | `config_f.cargoMass` | kg |
| 944 | f32 | `config_f.unitMass` | |
| 948 | f32 | `truck_f.speed` | m/s |
| 952 | f32 | `truck_f.engineRpm` | |
| 956–968 | f32 ×4 | `userSteer`, `userThrottle`, `userBrake`, `userClutch` | |
| 972–984 | f32 ×4 | `gameSteer`, `gameThrottle`, `gameBrake`, `gameClutch` | |
| 988 | f32 | `truck_f.cruiseControlSpeed` | m/s, 0 = aus |
| 992 | f32 | `truck_f.airPressure` | psi |
| 996 | f32 | `truck_f.brakeTemperature` | °C |
| 1000 | f32 | `truck_f.fuel` | Liter |
| 1004 | f32 | `truck_f.fuelAvgConsumption` | l/km |
| 1008 | f32 | `truck_f.fuelRange` | km |
| 1012 | f32 | `truck_f.adblue` | |
| 1016 | f32 | `truck_f.oilPressure` | |
| 1020 | f32 | `truck_f.oilTemperature` | |
| 1024 | f32 | `truck_f.waterTemperature` | |
| 1028 | f32 | `truck_f.batteryVoltage` | |
| 1036 | f32 | `truck_f.wearEngine` | 0..1 |
| 1040 | f32 | `truck_f.wearTransmission` | 0..1 |
| 1044 | f32 | `truck_f.wearCabin` | 0..1 |
| 1048 | f32 | `truck_f.wearChassis` | 0..1 |
| 1052 | f32 | `truck_f.wearWheels` | 0..1 |
| 1056 | f32 | `truck_f.truckOdometer` | km |
| 1060 | f32 | `truck_f.routeDistance` | m (`truck.navigation.distance`) |
| 1064 | f32 | `truck_f.routeTime` | s (`truck.navigation.time`) |
| 1068 | f32 | `truck_f.speedLimit` | m/s (`truck.navigation.speed.limit`) |
| 1456 | f32 | `gameplay_f.jobDeliveredCargoDamage` | |
| 1460 | f32 | `gameplay_f.jobDeliveredDistanceKm` | |
| 1464 | f32 | `gameplay_f.refuelAmount` | Liter (vom Plugin berechnet) |
| 1468 | f32 | `job_f.cargoDamage` | 0..1, Ladungsschaden des aktuellen Jobs |
| 1564 | bool | `config_b.isCargoLoaded` | |
| 1565 | bool | `config_b.specialJob` | |
| 1566 | bool | `truck_b.parkBrake` | |
| 1567 | bool | `truck_b.motorBrake` | |
| 1568 | bool | `truck_b.airPressureWarning` | |
| 1569 | bool | `truck_b.airPressureEmergency` | |
| 1570 | bool | `truck_b.fuelWarning` | |
| 1571 | bool | `truck_b.adblueWarning` | |
| 1572 | bool | `truck_b.oilPressureWarning` | |
| 1573 | bool | `truck_b.waterTemperatureWarning` | |
| 1574 | bool | `truck_b.batteryVoltageWarning` | |
| 1575 | bool | `truck_b.electricEnabled` | |
| 1576 | bool | `truck_b.engineEnabled` | |
| 1578 / 1579 | bool | `blinkerLeftActive` / `blinkerRightActive` | |
| 1583 / 1584 | bool | `lightsBeamLow` / `lightsBeamHigh` | |
| 1588 | bool | `truck_b.lightsHazard` | |
| 1589 | bool | `truck_b.cruiseControl` | vom Plugin gesetzt: `cruiseControlSpeed > 0` |
| 1613 / 1614 | bool | `gameplay_b.jobDeliveredAutoparkUsed` / `…AutoloadUsed` | |
| 2200–2240 | f64 ×6 | `truck_dp.coordinateX/Y/Z`, `rotationX/Y/Z` | Weltposition |
| 2300 | char[64] | `config_s.truckBrandId` | |
| 2364 | char[64] | `config_s.truckBrand` | |
| 2428 | char[64] | `config_s.truckId` | |
| 2492 | char[64] | `config_s.truckName` | |
| 2556 | char[64] | `config_s.cargoId` | |
| 2620 | char[64] | `config_s.cargo` | Ladung (lokalisiert) |
| 2684 | char[64] | `config_s.cityDstId` | |
| 2748 | char[64] | `config_s.cityDst` | Zielstadt |
| 2812 | char[64] | `config_s.compDstId` | |
| 2876 | char[64] | `config_s.compDst` | Zielfirma |
| 2940 | char[64] | `config_s.citySrcId` | |
| 3004 | char[64] | `config_s.citySrc` | |
| 3068 | char[64] | `config_s.compSrcId` | |
| 3132 | char[64] | `config_s.compSrc` | |
| 3196 | char[16] | `config_s.shifterType` | |
| 3404 | char[32] | `config_s.jobMarket` | |
| 3436 | char[32] | `gameplay_s.fineOffence` | |
| 3468 | char[64] | `gameplay_s.ferrySourceName` | |
| 3532 | char[64] | `gameplay_s.ferryTargetName` | |
| 3724 | char[64] | `gameplay_s.trainSourceName` | |
| 3788 | char[64] | `gameplay_s.trainTargetName` | |
| 4000 | u64 | `config_ull.jobIncome` | Job-Einkommen |
| 4200 | i64 | `gameplay_ll.jobCancelledPenalty` | |
| 4208 | i64 | `gameplay_ll.jobDeliveredRevenue` | |
| 4216 | i64 | `gameplay_ll.fineAmount` | |
| 4224 | i64 | `gameplay_ll.tollgatePayAmount` | |
| 4232 | i64 | `gameplay_ll.ferryPayAmount` | |
| 4240 | i64 | `gameplay_ll.trainPayAmount` | |
| 4300 | bool | `special_b.onJob` | Zustand |
| 4301 | bool | `special_b.jobFinished` | Zustand |
| 4302 | bool | `special_b.jobCancelled` | **Toggle** (XOR bei jedem Event) |
| 4303 | bool | `special_b.jobDelivered` | **Toggle** |
| 4304 | bool | `special_b.fined` | **Toggle** |
| 4305 | bool | `special_b.tollgate` | **Toggle** |
| 4306 | bool | `special_b.ferry` | **Toggle** |
| 4307 | bool | `special_b.train` | **Toggle** |
| 4308 | bool | `special_b.refuel` | Zustand (Tankvorgang läuft) |
| 4309 | bool | `special_b.refuelPayed` | Zustand/Puls |
| 4400 | char[25][64] | `substances.substance` | |
| 6000 + i·1560 | `scsTrailer_t` | `trailer.trailer[i]`, i = 0..9 | |

#### Trailer-Unterstruktur `scsTrailer_t` (relativ zum Trailer-Start; Trailer 0 beginnt bei 6000)

| Rel. | Abs. (Trailer 0) | Typ | Feld |
| ---: | ---: | --- | --- |
| 64 | 6064 | bool[16] | `com_b.wheelOnGround` |
| 80 | 6080 | bool | `com_b.attached` |
| 148 | 6148 | u32 | `con_ui.wheelCount` |
| 152 | 6152 | f32 | `com_f.cargoDamage` |
| 156 | 6156 | f32 | `com_f.wearChassis` |
| 160 | 6160 | f32 | `com_f.wearWheels` |
| 164 | 6164 | f32 | `com_f.wearBody` (neu in Rev 12) |
| 872–912 | 6872–6912 | f64 ×6 | `com_dp.worldX/Y/Z`, `rotationX/Y/Z` |
| 920 | 6920 | char[64] | `con_s.id` |
| 1112 | 7112 | char[64] | `con_s.brandId` |
| 1176 | 7176 | char[64] | `con_s.brand` |
| 1240 | 7240 | char[64] | `con_s.name` |
| 1368 | 7368 | char[64] | `con_s.licensePlate` |

Die vollständige Feldliste mit allen 298 Einträgen lässt sich jederzeit aus dem Header neu erzeugen. Empfehlung: Wir checken einen kleinen Generator in `scripts/` ein, der aus `scs-telemetry-common.hpp` ein `struct`-Format erzeugt, und vergleichen das in einem Test gegen diese Tabelle.

### 2.3 Semantik und Fallstricke

- **Event-Flags seit Rev 11 sind Toggles.** `jobDelivered`, `jobCancelled`, `fined`, `tollgate`, `ferry` und `train` werden bei jedem Event per `^= true` umgeschaltet. Der Reader merkt sich den letzten Wert und meldet bei **jeder Änderung** ein Event. Die zugehörigen Werte (z. B. `fineAmount`, `fineOffence`) stehen vorher bereits im Speicher.
- **Keine Seqlock- oder Versionszähler.** Das Plugin schreibt im Haupt-Thread des Spiels ohne Synchronisation. Lesestrategie: `time` (Offset 8) lesen, Block kopieren, `time` erneut lesen. Bei Abweichung erneut versuchen. Bei 20–60 Hz Abtastung ist das unkritisch.
- `time` bleibt in der Pause stehen. Für „Daten veraltet“ eignet sich deshalb `paused || !sdkActive`, nicht nur ein stehender Zeitstempel.
- Beim Spielende setzt das Plugin `sdkActive = false` und die Zeiten auf 0. Das Objekt in `/dev/shm` bleibt bestehen (truckermudgeon: `shm_unlink` erst beim Schließen). Nach einem Spielabsturz kann also ein alter Inhalt mit `sdkActive = true` stehen bleiben. Deshalb zusätzlich prüfen, ob `time` sich innerhalb von N Sekunden ändert, solange `paused == false`.
- Jobwerte werden bei leerer Job-Konfiguration auf 0 bzw. leere Strings zurückgesetzt. Das betrifft auch Bahn- und Fährfahrten (Changelog Rev 10 Update 7).
- `refuel-paid` feuert laut TruckSim-Telemetry „nur einmal pro Session“ (bekanntes Plugin-Problem).
- Einheiten umrechnen: m/s → km/h ×3.6 (ATS: mph ×2.23694). `routeDistance` ist in Metern, `routeTime` in Sekunden **Spielzeit**. Für Echtzeit nach `local.scale` bewerten.
- Restfahrzeit bis zur Deadline (Spielminuten) = `time_abs_delivery − time_abs`. Pausenbedarf = `restStop` (Minuten).

### 2.4 Python-Reader (Skizze)

```python
import mmap, os, struct

CANDIDATES = ("/dev/shm/SCSTelemetry", "/dev/shm/SCS/SCSTelemetry")
SIZE = 32 * 1024

def open_scs():
    for path in CANDIDATES:
        try:
            fd = os.open(path, os.O_RDONLY)
        except FileNotFoundError:
            continue
        try:
            return mmap.mmap(fd, SIZE, mmap.MAP_SHARED, mmap.PROT_READ)
        finally:
            os.close(fd)
    return None

def snapshot(m):
    for _ in range(3):
        t0 = struct.unpack_from("<Q", m, 8)[0]
        buf = m[:21600]                      # Kopie
        if struct.unpack_from("<Q", m, 8)[0] == t0:
            return buf
    return buf

def parse(buf):
    sdk_active, paused = buf[0] != 0, buf[4] != 0
    rev, = struct.unpack_from("<I", buf, 40)
    if rev != 12:
        raise ValueError(f"unsupported SCS plugin revision {rev}")
    game, = struct.unpack_from("<I", buf, 52)
    time_abs, = struct.unpack_from("<I", buf, 64)
    delivery_abs, = struct.unpack_from("<I", buf, 88)
    planned_km, = struct.unpack_from("<I", buf, 100)
    rest_stop, = struct.unpack_from("<i", buf, 500)
    fuel_cap, = struct.unpack_from("<f", buf, 704)
    (speed, rpm) = struct.unpack_from("<2f", buf, 948)
    cc_speed, = struct.unpack_from("<f", buf, 988)
    fuel, fuel_avg, fuel_range = struct.unpack_from("<3f", buf, 1000)
    wear = struct.unpack_from("<5f", buf, 1036)   # engine, transmission, cabin, chassis, wheels
    odo, nav_dist, nav_time, limit = struct.unpack_from("<4f", buf, 1056)
    cargo_dmg, = struct.unpack_from("<f", buf, 1468)
    income, = struct.unpack_from("<Q", buf, 4000)
    flags = buf[4300:4310]
    cstr = lambda off, n=64: buf[off:off+n].split(b"\0", 1)[0].decode("utf-8", "replace")
    return dict(sdk_active=sdk_active, paused=paused, game=game, speed_ms=speed,
                cruise_ms=cc_speed, fuel_l=fuel, fuel_capacity_l=fuel_cap,
                fuel_avg_l_per_km=fuel_avg, fuel_range_km=fuel_range, odometer_km=odo,
                nav_distance_m=nav_dist, nav_time_s=nav_time, speed_limit_ms=limit,
                rest_stop_min=rest_stop, delivery_remaining_min=delivery_abs - time_abs,
                planned_distance_km=planned_km, income=income, cargo=cstr(2620),
                destination=f"{cstr(2876)}, {cstr(2748)}", cargo_damage=cargo_dmg,
                wear=wear, trailer0_wear=struct.unpack_from("<4f", buf, 6152),
                on_job=bool(flags[0]), toggles=bytes(flags[2:8]))
```

---

## 3. Linux-Alternativen im Vergleich

| Option | Status | Bewertung |
| --- | --- | --- |
| **truckermudgeon/scs-sdk-plugin** (`.so`, `/dev/shm/SCSTelemetry`) | gepflegt, Release v1.12.1 mit Binaries, Rev 12 | **Empfohlen.** Gleiches Layout wie Windows, also ein Parser für beide Plattformen. TruckSim-Telemetry nutzt den Fork bereits produktiv. |
| alxwk/scs-sdk-plugin (`/dev/shm/SCS/SCSTelemetry`) | gepflegt (2026), von simapi/monocoque genutzt | Gleichwertig, anderer Pfad. Reader probiert beide. Keine Release-Binaries geprüft (UNVERIFIED), also selbst bauen. |
| jackz314/scs-sdk-plugin | Rev 10, seit 2021 inaktiv | Nicht verwenden (alte Offsets). |
| Funbit/ets2-telemetry-server | GPL-3.0, letzter Commit 2016, .NET/Windows-only | Für Linux ungeeignet. |
| nlhans/ets2-sdk-plugin | MIT, Ursprung, Windows | Historisch. |
| „scs-telemetry-linux“, „truck-telemetry“ o. ä. | keine relevanten, gepflegten Projekte unter diesen Namen gefunden | UNVERIFIED / nicht relevant. |
| **Eigenes C-Plugin → JSON/UDP** | ca. 300 Zeilen C, SDK-Header MIT | Gut als Plan B oder wenn wir Events exakt wollen (keine Toggle-Logik, Attribute direkt aus dem Gameplay-Event). Siehe 3.2. |

### 3.1 Empfohlener Weg (Linux-nativ, Bazzite), Schritt für Schritt

1. **ETS2 nativ starten, nicht unter Proton.** In Steam unter ETS2 → Eigenschaften → Kompatibilität die Option „Bestimmtes Steam Play-Kompatibilitätstool erzwingen“ **ausschalten**.
2. Plugin besorgen:
   - **Variante A:** Release-ZIP von `https://github.com/truckermudgeon/scs-sdk-plugin/releases` (v1.12.1). Die Linux-`.so` heißt laut CMake `libscs-telemetry.so` (`OUTPUT_NAME "scs-telemetry"`). UNVERIFIED: der genaue Asset-Name im ZIP.
   - **Variante B (selbst bauen, in distrobox):**
     ```bash
     distrobox create -n build -i registry.fedoraproject.org/fedora-toolbox:43
     distrobox enter build -- sudo dnf -y install cmake gcc-c++ git
     distrobox enter build -- bash -lc 'git clone https://github.com/truckermudgeon/scs-sdk-plugin.git ~/src/scs-sdk-plugin && cd ~/src/scs-sdk-plugin && cmake -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build --config Release'
     ```
     Ergebnis: `~/src/scs-sdk-plugin/build/bin/libscs-telemetry.so`. Der Spielprozess lädt die Datei, deshalb sollte sie gegen eine ältere glibc gebaut werden. Fedora-43-glibc ist auf Bazzite identisch, das passt. Für Weitergabe an andere Distros bevorzugt im Steam-Runtime-SDK-Container bauen.
3. Installieren:
   ```bash
   GAME="$HOME/.local/share/Steam/steamapps/common/Euro Truck Simulator 2"
   mkdir -p "$GAME/bin/linux_x64/plugins"
   cp libscs-telemetry.so "$GAME/bin/linux_x64/plugins/"
   ```
   Für ATS analog `American Truck Simulator`. Bei Spielen auf einer zweiten Library den Pfad aus `~/.local/share/Steam/steamapps/libraryfolders.vdf` nehmen.
4. Spiel starten. Den SDK-Dialog mit OK bestätigen.
5. Prüfen:
   ```bash
   ls -l /dev/shm/SCSTelemetry
   python3 -c "import struct;b=open('/dev/shm/SCSTelemetry','rb').read(64);print('active',b[0],'paused',b[4],'rev',struct.unpack_from('<I',b,40)[0],'game',struct.unpack_from('<I',b,52)[0])"
   ```
   Erwartet: `active 1`, `rev 12`, `game 1` (ETS2) bzw. `2` (ATS).
6. `pitwall doctor` sollte genau diese Prüfung mitbringen: Datei vorhanden, `rev == 12`, `time` ändert sich, solange `paused == 0`.

Wenn ETS2 doch unter Proton läuft (z. B. für Windows-only-Mods): Die Windows-DLL von RenCloud gehört dann nach `bin/win_x64/plugins/`. Das Mapping `Local\SCSTelemetry` ist von Linux aus aber **nicht sichtbar**. Dann greift der Proton-Weg aus `docs/protocols/proton_shm_research.md`, Option (b): Helper liest `Local\SCSTelemetry` und schickt die 21600 Bytes per UDP. Das Layout bleibt gleich.

### 3.2 Plan B: eigenes Mini-Plugin (JSON/UDP)

- Lizenz: Die SCS-SDK-Header stehen unter MIT. Eigener Code unter beliebiger kompatibler Lizenz; `sdk_license.txt` mitliefern.
- Skelett:
  - `scs_telemetry_init`:
    - Version prüfen (`SCS_TELEMETRY_VERSION_1_01`, ggf. `1_00`).
    - Events registrieren: `frame_end`, `paused`, `started`, `configuration`, `gameplay`.
    - Kanäle mit `register_for_channel(name, SCS_U32_NIL, type, SCS_TELEMETRY_CHANNEL_FLAG_none, cb, ctx)` registrieren und in eine lokale Struct schreiben.
    - Nicht-blockierenden UDP-Socket öffnen (`socket(AF_INET, SOCK_DGRAM|SOCK_NONBLOCK)`, Ziel `127.0.0.1:<port>`).
  - In `frame_end`: rate-limitiert (z. B. 20 Hz, gemessen an `render_time`) ein kompaktes Datagramm schicken. Empfehlung: Binär-Struct mit Magic und Version statt JSON, weil Formatieren im Haupt-Thread Zeit kostet. JSON nur für Config- und Gameplay-Events.
  - `configuration`/`gameplay`: Attribute iterieren (`scs_named_value_t` bis `name == NULL`) und sofort ein Event-Datagramm schicken. Damit haben wir echte Events statt Toggles.
  - `scs_telemetry_shutdown`: Socket schließen.
- Build: `gcc -shared -fPIC -O2 -fvisibility=hidden -o pitwall-scs.so plugin.c -I scs_sdk/include`. Exportierte Symbole: nur `scs_telemetry_init` und `scs_telemetry_shutdown`.
- Vorteil: kein Shared-Memory-Layout, das wir mitpflegen müssen, und unter Proton identisch nutzbar. Mit mingw als DLL gebaut funktioniert UDP aus Proton an localhost.
- Nachteil: eigener Code im Spielprozess. Ein Crash dort beendet das Spiel. Deshalb nur, wenn Plan A nicht reicht.

### 3.3 Capabilities des ETS2-Adapters

Liefert:

- Geschwindigkeit, Tempomat, Tempolimit
- Sprit, Verbrauch, Reichweite, Tankgröße
- Odometer, Navi-Distanz und -ETA
- Job: Ladung, Ziel, Einkommen, Deadline, geplante km, Ladungsschaden
- Schäden: Motor, Getriebe, Kabine, Chassis, Räder, Trailer
- Pausenbedarf
- Events: Strafe, Maut, Fähre, Zug, Lieferung, Abbruch, Tanken

Liefert **nicht**: Rundenzeiten, Gegner, Reifentemperaturen, Wetter.

---

## Quellen

| Quelle | URL | Lizenz / Hinweis |
| --- | --- | --- |
| RenCloud/scs-sdk-plugin (Header `scs-telemetry/inc/scs-telemetry-common.hpp`, `changelog.md`, `README.md`; Commit d7216cb, 2023-09-21) | https://github.com/RenCloud/scs-sdk-plugin | MIT |
| truckermudgeon/scs-sdk-plugin (README, CMakeLists, `sharedmemory.cpp`; Release v1.12.1) | https://github.com/truckermudgeon/scs-sdk-plugin, https://github.com/truckermudgeon/scs-sdk-plugin/releases | MIT |
| alxwk/scs-sdk-plugin | https://github.com/alxwk/scs-sdk-plugin | MIT |
| jackz314/scs-sdk-plugin | https://github.com/jackz314/scs-sdk-plugin | MIT |
| nlhans/ets2-sdk-plugin | https://github.com/nlhans/ets2-sdk-plugin | MIT |
| SCS SDK-Header und `readme.txt`/`sdk_license.txt` (im Plugin-Repo unter `scs_sdk/`) | https://github.com/RenCloud/scs-sdk-plugin/tree/master/scs_sdk | MIT (SCS Software) |
| SCS Modding Wiki, Telemetry SDK und Changelog (nicht abrufbar aus der Recherche-Umgebung; Version 1.14 laut Suchergebnis) | https://modding.scssoft.com/wiki/Documentation/Engine/SDK/Telemetry | UNVERIFIED (nicht direkt gelesen) |
| SCS-Blog zum Telemetry SDK (2015) | https://blog.scssoft.com/2015/02/a-look-on-technical-side-telemetry-sdk.html | – |
| kniffen/TruckSim-Telemetry | https://github.com/kniffen/TruckSim-Telemetry, https://kniffen.dev/TruckSim-Telemetry/ | MIT |
| spacefreak18/simapi (`include/scs2data.h`, `simapi/scs2.h`, `docs/supportedsims.md`) | https://github.com/spacefreak18/simapi | LGPL-3.0 |
| Funbit/ets2-telemetry-server | https://github.com/Funbit/ets2-telemetry-server | GPL-3.0 |
| Offset-Verifikation: eigener Parser plus `g++ offsetof`-Test (lokal, nicht eingecheckt) | – | – |
