# Windows-Shared-Memory von Proton-Spielen unter nativem Linux lesen

Stand der Recherche: 2026-10-28. Zielsystem: Bazzite (immutable Fedora, KDE/Wayland oder Gaming-Mode/gamescope), Steam (rpm, nicht Flatpak), Proton.
Dieses Dokument ist die Recherchebasis für `docs/linux-proton.md`.

## 0. Ergebnis in fünf Sätzen

1. Benannte Windows-File-Mappings (`Local\acpmf_physics`, `Local\IRSDKMemMapFileName`, `$rFactor2SMMP_Telemetry$`, `LMU_Data`, `Local\SCSTelemetry`) existieren nur im **wineserver** des jeweiligen Prefix. Ein natives Linux-Programm sieht sie nicht.
2. Sie lassen sich sichtbar machen. Variante (a): Ein Helper legt das Mapping **vor dem Spiel** mit einer Datei in `/dev/shm` als Backing an (`CreateFile` + `CreateFileMapping(hFile, …, name)`). Wine mappt die Unix-Datei dann `MAP_SHARED`, und alles, was das Spiel schreibt, landet in der tmpfs-Datei. So arbeiten simshmbridge, shm-bridge und Datalink.
3. Variante (b): Ein kleiner Windows-Helper im selben Prefix öffnet das Mapping **nach dem Spiel** ganz normal per `OpenFileMapping` und schickt die Bytes per **UDP an 127.0.0.1**. Proton nutzt keinen Netzwerk-Namespace, Loopback funktioniert. So macht es der mescon/logitech-trueforce-Relay produktiv mit 60 Hz.
4. Variante (c), spieleigenes UDP, gibt es nur bei AC (9996, wenige Felder) und ACC (Broadcasting, Port 9000, Gegner und Timing, aber keine Physik). iRacing, rF2 und LMU haben kein offizielles UDP.
5. **Empfehlung für pitwall:** (b) als Standardweg, (a) als optionaler Turbo-Pfad, (c) zusätzlich, wo vorhanden. Gestartet wird der Helper über einen **Steam-Launch-Wrapper** (`pitwall-launch %command%`), alternativ über `PROTON_REMOTE_DEBUG_CMD`.

---

## 1. Warum native Programme das Mapping nicht sehen

- Wine implementiert `CreateFileMapping(INVALID_HANDLE_VALUE, …, "Name")` (also vom Pagefile gestützt) als Section-Objekt im **wineserver**. Der Name lebt im Objekt-Namespace des wineservers (`\Sessions\1\BaseNamedObjects\…`). Den Speicher stellt der wineserver über einen anonymen bzw. unverlinkten Dateideskriptor bereit und reicht ihn an die Wine-Prozesse weiter. Es gibt **keinen Pfad unter `/dev/shm/<Name>`**.
  - UNVERIFIED: ob aktuelle Wine-/Proton-Versionen dafür `memfd_create` oder eine gelöschte Temp-Datei im Server-Verzeichnis nutzen. Für uns irrelevant, weil von außen in keinem Fall per Name erreichbar.
- Der Wine-Bug „Accessing shared memory from Linux“ (#54015) diskutiert genau das. Wine bietet keine Weiterleitung nach Unix an (laut simshmbridge-README). Die Bug-Seite selbst war aus der Recherche-Umgebung nicht abrufbar, Status UNVERIFIED.
- **File-backed Mappings funktionieren:** Öffnet ein Windows-Prozess unter Wine eine reale Datei (z. B. `Z:\dev\shm\acpmf_physics`, Wine-Laufwerk `Z:` = `/`) und erzeugt daraus `CreateFileMapping(hFile, NULL, PAGE_READWRITE, 0, size, "acpmf_physics")`, entsteht ein **benanntes** Mapping, dessen Seiten per `mmap(MAP_SHARED)` auf die Unix-Datei zeigen. Öffnet oder erzeugt das Spiel später ein Mapping mit demselben Namen, bekommt es **dasselbe** Objekt (`ERROR_ALREADY_EXISTS`) und schreibt damit direkt in die tmpfs-Datei. Das ist der Kern von simshmbridge (`HELPERPROCESSFIRST`) und shm-bridge.
- Voraussetzung dafür: Der Helper muss **vor** dem Spiel laufen, und das Spiel muss ein existierendes Mapping akzeptieren. Die meisten Spiele tun das, manche nicht. Für diesen Fall hat simshmbridge den Modus `HELPERPROCESSSECOND` plus `bridge`: Das anonyme Section-Handle wird als stdin an ein **natives** ELF-Programm übergeben, das Wine per `CreateProcess` startet und das den Inhalt periodisch (~980 Hz) nach `/dev/shm` kopiert.
- Steam Linux Runtime (pressure-vessel) teilt `/dev/shm` mit dem Host. Belegt ist das indirekt dadurch, dass simshmbridge, shm-bridge und Datalink unter Proton 8+ (mit Container) funktionieren. Explizite Doku-Stelle: UNVERIFIED.

---

## 2. Projekte im Überblick

| Projekt | Lizenz | Sprache | Mechanismus | Spiele | Stand |
| --- | --- | --- | --- | --- | --- |
| **spacefreak18/simshmbridge** | GPL-3.0 | C (mingw) | `acshm`/`createsimshm` (Linux) legt `/dev/shm/acpmf_*` an. `acbridge.exe` macht im Prefix `chdir("/dev/shm")`, `FindFirstFile("acpmf_*")`, `CreateFile`, `CreateFileMapping(hFile,…,name)`. Alternativ `*handle.exe` + nativer `*handle`: Section-Handle als stdin, Kopierschleife | AC, ACC, AC EVO, AMS2/PCars2, R3E, rF2, LMU | 2026-06 |
| **spacefreak18/simapi** + **simd** | LGPL-3.0 (simapi) | C | Header für viele Sims plus Daemon `simd`. simd legt Stubs aller Mappings an, erkennt das laufende Spiel und mappt in einen gemeinsamen Block `/dev/shm/SIMAPI.DAT`. Bridge per `SIMD_BRIDGE_EXE=~/acbridge.exe %command%`. Läuft als systemd-User-Service | AC/ACC/EVO/Rally, AMS2, rF2, LMU, ETS2/ATS, LFS, BeamNG, DR2, … | 2026-10 |
| **spacefreak18/monocoque** | GPL-3.0 | C | Geräte-Manager (Wheels, Shaker, LEDs) auf simapi | – | 2026-10 |
| **poljar/shm-bridge** | MIT | Rust (`x86_64-pc-windows-gnu`) | wie simshmbridge FIRST: tmpfs-Datei, dann benanntes Mapping, vor dem Spiel. Start per `protontricks-launch --appid APPID shm-bridge.exe` | AC, ACC | 2024 |
| **LukasLichten/Datalink** | MIT | Rust | Launch-Wrapper `Datalink %command%`. Schreibt `proton waitforexitandrun game.exe` um in `… waitforexitandrun datalink-shm-bridge.exe <args> game.exe`. Der Bridge-Prozess erzeugt die Mappings und startet dann das Spiel als Kind, also garantiert im selben Prefix und **vor** dem Spiel. Pro Spiel Config und Env-Datei unter `~/.config/Datalink/<appid>/` | konfigurierbar | 2025-12 |
| **mescon/logitech-trueforce-linux-driver**, Relay | GPL-2.0 (Treiber/Tools), LGPL-2.1+ (libtrueforce) | Rust (Windows-Target) | `C:\logi-tf-relay.exe` im Prefix liest die Sections per Win32 und schickt **UDP an 127.0.0.1:20780** (~60 Hz). Hub fächert auf 20782–20784 auf. Start per Wrapper `logi-launch %command%` | iRacing, R3E, AC/ACC/EVO, rF2, LMU | 2026 |
| schlegp/rF2SharedMemoryMapPlugin_Wine | eigene Lizenz des Originals (Weitergabe nur mit Erlaubnis) | C++ | Wine-angepasster Fork des rF2-Plugins, von simapi für rF2/LMU genutzt. Details zum Mechanismus aus der README nicht ersichtlich (UNVERIFIED) | rF2, LMU | – |
| Swizzjack/lmu-pitwall | – (UNVERIFIED) | – | liest LMUs **eigenes** `LMU_Data`-Mapping (100 Hz Telemetrie, 5 Hz Scoring), das im Spiel per Option aktiviert werden muss | LMU | 2025/26 |
| CrewChief / SimHub unter Linux | CrewChief: GPL-3.0 (UNVERIFIED); SimHub: proprietär | .NET | werden **im selben Prefix wie das Spiel** gestartet (protontricks). Dann sieht der .NET-Prozess das Mapping direkt. simshmbridge kann Mappings auch zurück in ein separates CrewChief-Prefix spiegeln | viele | – |

---

## 3. Bewertung der drei Optionen

### (a) Mirror nach `/dev/shm` (simshmbridge-artig)

- **Pro:**
  - Keine Kopie, keine Latenz: Der native Reader `mmap`t direkt die Datei, in die das Spiel schreibt.
  - Volle Rate (z. B. AC-Physik 333 Hz, iRacing 60 Hz).
  - Bestehende Parser (simapi-Header) funktionieren 1:1.
- **Contra:**
  - Der Helper **muss vor dem Spiel** das Mapping anlegen. Ein Rennen mit `%command% & sleep 5 && proton run bridge.exe` ist fragil.
  - Die Größe muss exakt bekannt sein (`acpmf_physics` usw.).
  - Manche Spiele mögen fremd angelegte Mappings nicht, dann ist der `handle`-Kopiermodus nötig.
  - Ein übrig gebliebenes `/dev/shm/*` mit alten Daten sieht aus wie ein laufendes Spiel.
  - simshmbridge ist GPL-3.0. Für ein eigenes Projekt unter anderer Lizenz neu schreiben oder shm-bridge/Datalink (MIT) nutzen.
- **Eignung:** gut für AC/ACC/AMS2/R3E. Für iRacing UNVERIFIED (iRacing legt Mapping und Event `Local\IRSDKDataValidEvent` selbst an). Für rF2/LMU unnötig, wenn das Wine-Plugin genutzt wird.

### (b) Windows-Helper im Prefix: liest Shared Memory, schickt UDP an localhost (empfohlen)

- **Pro:**
  - Reihenfolge egal: Der Helper wartet per `OpenFileMapping`-Polling, bis das Spiel das Mapping anlegt. Kein Eingriff in die Mapping-Erzeugung, also kein Kompatibilitätsrisiko für das Spiel.
  - Loopback-UDP funktioniert aus Proton. Wine-Winsock nutzt Host-Sockets, pressure-vessel hat keinen eigenen Netzwerk-Namespace. Praxisbeleg: logi-tf-relay.
  - Robust gegenüber Sandboxing (Flatpak-Steam, distrobox-Reader). Keine geteilten Dateien nötig.
  - Unser Python bleibt rein nativ und lauscht nur auf einem UDP-Port. Dasselbe Muster wie F1/GT7.
  - Für iRacing ideal: `WaitForSingleObject(IRSDKDataValidEvent)` und nur bei neuen Daten senden.
- **Contra:**
  - Kopie und Serialisierung. Bei AC-Physik (~800 B) mit 60–100 Hz vernachlässigbar. iRacing-Block (~1 MB) **nicht komplett** senden, sondern im Helper die benötigten Variablen per Header/VarHeader extrahieren oder nur den aktuellen Var-Buffer schicken.
  - UDP-Paketgröße max. 65507 B, praktisch ≤ 1472 B gegen Fragmentierung. Auf localhost ist Fragmentierung unkritisch, trotzdem in Chunks mit Sequenznummer senden.
  - Wir müssen eine `.exe` bauen und ausliefern.
- **Sprache für den Helper:**

  | Sprache | Bau unter Bazzite | Größe | Bewertung |
  | --- | --- | --- | --- |
  | **Go** | `GOOS=windows GOARCH=amd64 go build` (Go per `brew install go` oder distrobox), **kein mingw nötig**, `golang.org/x/sys/windows` hat `OpenFileMapping`/`MapViewOfFile` | ca. 2–3 MB | Empfehlung für Einfachheit und CI. Läuft unter Wine problemlos (UNVERIFIED für Timer-Genauigkeit über 100 Hz) |
  | **Rust** | `rustup target add x86_64-pc-windows-gnu` plus mingw-Linker (distrobox: `mingw64-gcc`) | ca. 0.5–1 MB | bewährt (shm-bridge, Datalink, logi-tf-relay) |
  | **C** | `x86_64-w64-mingw32-gcc` (distrobox: `dnf install mingw64-gcc`) | ca. 50 KB | minimal (simshmbridge), mehr Handarbeit bei Parsing und Sicherheit |
  | Python (embeddable Windows-Python im Prefix) | Download des embeddable ZIP, `mmap.mmap(-1, size, tagname="Local\\acpmf_physics")` funktioniert unter Windows-Python | ca. 15 MB | nur Notlösung. Zweite Python-Laufzeit, langsamer Start |

- **Empfohlenes Paketformat (pitwall-relay v1):** 16-Byte-Header `magic "PWRL"`, `u16 version`, `u16 sim_id`, `u32 seq`, `u16 chunk_idx`, `u16 chunk_count`, danach Payload (Rohbytes des Mappings oder Ausschnitt). Ziel `127.0.0.1:<port>`.
  - Ports: Nicht 20777 (F1) und nicht 20780–20784 (logi-tf-relay) belegen. Vorschlag: **20790/udp**, konfigurierbar.

### (c) Spieleigene UDP-Schnittstellen

| Spiel (AppID) | Native Schnittstelle | Inhalt | Bewertung |
| --- | --- | --- | --- |
| Assetto Corsa (244210) | **UDP 9996** „Remote Telemetry“: Client sendet Handshake (`identifier`, `version`, `operationId` 0), danach Subscribe (1 = Update, 2 = Spot), Dismiss = 3 | `RTCarInfo`: Speed, RPM, Gear, Gas/Bremse, Rad-Slip/Last, Lap-Zeiten. **Kein Sprit, keine Reifentemperaturen, keine Gegner** | sofort unter Linux nutzbar, als Fallback implementieren |
| Assetto Corsa Competizione (805550) | **Broadcasting-API UDP**, `Dokumente/Assetto Corsa Competizione/Config/broadcasting.json` (Prefix: `…/compatdata/805550/pfx/drive_c/users/steamuser/Documents/…`), Feld `updListenerPort` (Schreibweise so im Spiel), üblich **9000**, `connectionPassword` | RegistrationResult, RealtimeUpdate, RealtimeCarUpdate (alle Autos: Position, Rundenzeiten, Splits, Speed, Gang, Spline-Pos), EntryList, TrackData, BroadcastingEvent | Gegner und Timing komplett. **Kein Sprit, keine Reifen, keine Pedale** → mit Shared Memory (Option b) kombinieren |
| iRacing (266410) | keine (nur Shared Memory `Local\IRSDKMemMapFileName` plus Event `Local\IRSDKDataValidEvent`) | – | Option (b) |
| rFactor 2 (365960) | keine offizielle UDP-Telemetrie. Shared Memory nur über Plugin (`rFactor2SharedMemoryMapPlugin64.dll` → `$rFactor2SMMP_Telemetry$`, `$rFactor2SMMP_Scoring$` …) | – | Plugin (Wine-Fork) plus (b), oder (a) mit simapi |
| Le Mans Ultimate (2399420) | keine UDP. Seit 2025 **eigenes Shared Memory `LMU_Data`** (in den Spieloptionen aktivieren, ca. 505 KB laut simapi `LMU_SIZE`). Alternativ rF2-Plugin | – | (b) mit `LMU_Data`. Status der nativen Schnittstelle: teilverifiziert (lmu-pitwall, simapi), offizielle Doku nicht gefunden |
| ETS2 (227300) / ATS (270880) | keine UDP. **Nativer Linux-Build** mit `.so`-Plugin → `/dev/shm/SCSTelemetry` | siehe `docs/protocols/ets2.md` | kein Proton nötig |

AppIDs: Alle hier genannten sind gegen SteamDB-, Steam-Store- oder ProtonDB-Suchtreffer abgeglichen. AC 244210 und ACC 805550 über ProtonDB/Lutris/SteamDB, iRacing 266410 über SteamDB, rF2 365960 über Steam-Store, LMU 2399420 über SteamDB/Steam-Store, ETS2 227300 über Steam-Community. Der Steam-Store selbst war aus der Recherche-Umgebung nicht direkt abrufbar.

Hinweis iRacing: iRacing wird oft über den eigenen Launcher statt über Steam gestartet. Dann existiert `compatdata/266410` nicht, sondern ein Prefix eines Non-Steam-Shortcuts mit generierter ID. Ob iRacing unter Proton derzeit spielbar ist (Anti-Cheat), ist UNVERIFIED.

---

## 4. Helper im Prefix des Spiels starten: Varianten

Die Pfade auf Bazzite: Steam liegt unter `~/.local/share/Steam` (Symlink `~/.steam/steam`). Prefix: `~/.local/share/Steam/steamapps/compatdata/<APPID>/pfx`. `$HOME` ist `/var/home/<user>`.

**Grundregel:** Der Helper muss mit **derselben Proton-Version** und demselben wineserver laufen wie das Spiel. Sonst gibt es einen zweiten wineserver mit eigenem Namespace, oder er bricht mit „wineserver version mismatch“ ab.

### 4.1 Steam-Launch-Wrapper (empfohlen)

`pitwall-launch` ist ein Shell-Skript in `~/.local/bin`. Startoption in Steam:

```
pitwall-launch %command%
```

Das Skript bekommt die komplette Kommandozeile, z. B. `…/reaper SteamLaunch AppId=244210 -- …/SteamLinuxRuntime_sniper/_v2-entry-point --verb=waitforexitandrun -- …/Proton 9.0/proton waitforexitandrun …/acs.exe`. Zwei Wege:

1. **Datalink-Technik:** In der Argumentliste das Wort `waitforexitandrun` suchen und direkt danach den eigenen Helper einsetzen, der das Spiel als Kindprozess startet. Damit läuft der Helper garantiert zuerst und im selben Prefix. Das ist Pflicht für Option (a), für (b) optional.
2. **Parallel-Start (einfacher, reicht für Option b):**
   ```bash
   #!/usr/bin/env bash
   # ~/.local/bin/pitwall-launch
   HELPER="$HOME/.local/share/pitwall/pitwall-relay.exe"
   APPID="${SteamAppId:-${SteamGameId:-0}}"
   (
     sleep 10
     # STEAM_COMPAT_DATA_PATH / STEAM_COMPAT_CLIENT_INSTALL_PATH sind hier von Steam gesetzt
     PROTON="$(printf '%s\n' "$@" | grep -m1 '/proton$')"
     [ -n "$PROTON" ] && "$PROTON" run "$HELPER" --appid "$APPID" --port 20790
   ) &
   exec "$@"
   ```
   `proton run` startet `wine steam.exe <exe>` im selben Prefix und hängt sich an den bereits laufenden wineserver. Der Helper beendet sich selbst, wenn `OpenFileMapping` X Sekunden nach Spielende fehlschlägt. Alternativ prüft er per Parent-PID oder Prozessliste, ob das Spiel noch läuft.
   - Achtung: Der Subshell-Aufruf läuft **außerhalb** des pressure-vessel-Containers, während das Spiel **innerhalb** läuft. Wine im Host-Kontext gegen einen wineserver im Container funktioniert laut simshmbridge-Beispiel (`%command% & sleep 5 && …/proton run …/acbridge.exe`) in der Praxis. Saubere Variante: `steam-runtime-launch-client` bzw. `--verb=run` über den Entry-Point. UNVERIFIED, welche Variante auf aktuellem Proton 10 am stabilsten ist. **Muss im Test (M5) geprüft werden.**

### 4.2 `PROTON_REMOTE_DEBUG_CMD`, die eingebaute Proton-Funktion

Verifiziert im Proton-Skript (`ValveSoftware/Proton`, Branch `proton_10.0`, Datei `proton`, `Session.run()`): Wenn `PROTON_REMOTE_DEBUG_CMD` gesetzt ist, startet Proton **vor** dem Spiel `subprocess.Popen([wine] + shlex.split(PROTON_REMOTE_DEBUG_CMD))` mit derselben Umgebung, im selben Prefix und Container. Nach Spielende wird der Prozess per `kill()` beendet. Relative Pfade werden relativ zum Proton-Verzeichnis aufgelöst, also **absoluten Pfad** verwenden.

Steam-Startoption:

```
PROTON_REMOTE_DEBUG_CMD="/var/home/<user>/.local/share/pitwall/pitwall-relay.exe --port 20790" %command%
```

- **Pro:** startet vor dem Spiel (auch für Option a geeignet), Lebenszyklus automatisch, kein Wrapper-Skript.
- **Contra:** eigentlich für `msvsmon.exe` (Remote-Debugger) gedacht, also undokumentiert und kann sich ändern. Gilt nur für den `run`-Verb-Pfad. Der Pfad muss im Container sichtbar sein: `$HOME` ja, `/tmp` ja, andere Mounts ggf. nicht. Auch mit `waitforexitandrun` wird am Ende `run()` aufgerufen, gilt also. Startoptionen mit Leerzeichen im Pfad sauber quoten.

### 4.3 Manuell / Diagnose: protontricks

- `protontricks-launch --appid 244210 /pfad/pitwall-relay.exe` startet die EXE im Prefix der AppID. Protontricks wählt die Proton-Version aus der Steam-Konfiguration des Spiels.
- `protontricks -c 'wine /pfad/pitwall-relay.exe' 244210` ist dasselbe über eine Shell. simshmbridge nutzt `PROTON_VERSION="GE-Proton9-4" protontricks --background-wineserver --no-runtime -c "wine …/acbridge.exe" 244210`.
- Auf Bazzite ist protontricks üblicherweise als **Flatpak** (`com.github.Matoking.protontricks`) installiert (UNVERIFIED, ob vorinstalliert):
  `flatpak run --command=protontricks-launch com.github.Matoking.protontricks --appid 244210 ~/.local/share/pitwall/pitwall-relay.exe`.
  Flatpak braucht Lesezugriff auf den Pfad: `flatpak override --user --filesystem=~/.local/share/pitwall com.github.Matoking.protontricks`. Für Option (a) zusätzlich `--device=shm` bzw. `--filesystem=/dev/shm` (UNVERIFIED). Für Option (b) nicht nötig.
- Ohne protontricks, direkt:
  ```bash
  APPID=244210
  export STEAM_COMPAT_CLIENT_INSTALL_PATH="$HOME/.local/share/Steam"
  export STEAM_COMPAT_DATA_PATH="$STEAM_COMPAT_CLIENT_INSTALL_PATH/steamapps/compatdata/$APPID"
  PROTON="$STEAM_COMPAT_CLIENT_INSTALL_PATH/steamapps/common/Proton 9.0 (Beta)/proton"   # gleiche Version wie das Spiel!
  "$PROTON" run ~/.local/share/pitwall/pitwall-relay.exe
  ```
  Welche Proton-Version ein Spiel nutzt: `~/.local/share/Steam/steamapps/compatdata/$APPID/config_info` (erste Zeile) bzw. `version`.
  - Alternative ohne Proton-Skript: `WINEPREFIX=$STEAM_COMPAT_DATA_PATH/pfx "<proton-dir>/files/bin/wine" …`. So steht es im simshmbridge- und logi-tf-relay-Beispiel.
  - Verben des Proton-Skripts: `run`, `waitforexitandrun`, `runinprefix`, `getcompatpath`, `getnativepath`, `destroyprefix`.

### 4.4 Nicht empfohlen

`%command% & helper.exe`: Die EXE würde ohne Wine starten. `%command% & sleep 5 && proton run …` ist in Ordnung, wenn der Proton-Pfad stimmt. Das hartkodiert aber die Proton-Version, deshalb ist der Wrapper aus 4.1 besser.

---

## 5. Konkreter Ablauf auf Bazzite (Option b, Assetto Corsa als Beispiel)

1. pitwall nativ installieren (siehe 7), Listener auf UDP 20790.
2. `pitwall-relay.exe` nach `~/.local/share/pitwall/` kopieren. Gebaut in CI oder lokal per `GOOS=windows GOARCH=amd64 go build`.
3. Steam → AC (244210) → Eigenschaften → Startoptionen:
   ```
   PROTON_REMOTE_DEBUG_CMD="/var/home/<user>/.local/share/pitwall/pitwall-relay.exe --sim ac --port 20790" %command%
   ```
   oder `pitwall-launch %command%`.
4. Spiel starten. Der Relay pollt `OpenFileMapping(FILE_MAP_READ, FALSE, "Local\\acpmf_physics")` (plus `acpmf_graphics`, `acpmf_static`), bis es existiert, und sendet dann.
5. `pitwall doctor` prüft:
   - Kommen auf 20790 Pakete an (Quelle 127.0.0.1)?
   - Steigt `seq`?
   - Ist `packetId` im AC-Physikblock steigend?
   - Zusätzlich den AC-UDP-Fallback auf 9996 testen.
6. Diagnose bei Problemen:
   - `PROTON_LOG=1` in den Startoptionen erzeugt `~/steam-<appid>.log`, darin die Ausgabe des Relays.
   - `ss -uanp | grep 20790` zeigt, ob der Listener läuft.
   - Prüfen, ob die Proton-Version des Spiels zu der des Relays passt (siehe Grundregel in Abschnitt 4).

Für rF2/LMU: Spiel-Plugin bzw. LMU-Option für Shared Memory aktivieren, sonst existiert kein Mapping. Für ACC: zusätzlich `broadcasting.json` im Prefix anpassen (siehe 3c) und 9000/udp lokal nutzen.

---

## 6. firewalld auf Bazzite

- **Localhost braucht keine Freigabe.** Loopback ist in firewalld immer erlaubt. Relay, AC 9996 und ACC 9000 laufen alle über 127.0.0.1.
- Freigaben braucht es nur für Pakete aus dem LAN: F1 vom PC/Konsole an 20777, GT7 von der PS5 an 33740, ein zweiter PC.
- Bazzite liefert keine eigene firewalld-Zone mit, sondern erbt Fedoras Default. Bei Fedora Workstation/Atomic ist das `FedoraWorkstation`, das UDP/TCP 1025–65535 bereits erlaubt. Für Bazzite UNVERIFIED, deshalb immer prüfen:
  ```bash
  firewall-cmd --get-default-zone
  firewall-cmd --get-active-zones
  firewall-cmd --zone="$(firewall-cmd --get-default-zone)" --list-all
  ```
- Explizit freigeben (dauerhaft):
  ```bash
  Z="$(firewall-cmd --get-default-zone)"
  sudo firewall-cmd --permanent --zone="$Z" --add-port=20777/udp   # F1
  sudo firewall-cmd --permanent --zone="$Z" --add-port=33740/udp   # GT7 Daten von PS5
  sudo firewall-cmd --reload
  firewall-cmd --zone="$Z" --list-ports
  ```
  Eleganter ist ein eigener Service: Datei `/etc/firewalld/services/pitwall.xml` mit den `<port protocol="udp" port="…"/>`-Einträgen, dann `sudo firewall-cmd --permanent --add-service=pitwall && sudo firewall-cmd --reload`. Entfernen mit `--remove-port=…` bzw. `--remove-service=pitwall`.
- GT7: Der PC sendet den Heartbeat an die PS5 (Port 33739). Ausgehend ist erlaubt, die Antwort kommt auf 33740 herein, also 33740/udp freigeben, falls die Zone das nicht schon tut.

## 7. distrobox vs. uv auf Bazzite

- Bazzite bringt **distrobox** (inkl. ublue-Toolbox-Configs) und **Homebrew** mit. Belegt im Bazzite-`Containerfile`: `FROM ghcr.io/ublue-os/brew` und `/etc/distrobox/*.ini`. `python3` ist im Basis-Image vorhanden. `dnf install` ins Basissystem ist tabu (rpm-ostree-Layering nur im Notfall).
- **Empfehlung: `uv` nativ im Home.**
  - Installation: `brew install uv` oder `curl -LsSf https://astral.sh/uv/install.sh | sh` (nach `~/.local/bin`).
  - Dann `uv sync` / `uv run pitwall`. uv bringt bei Bedarf eine eigene Python-Version mit (`uv python install 3.12`).
  - Native Prozesse haben ohne Umweg Zugriff auf `/dev/input` (evdev), PipeWire-Socket (`$XDG_RUNTIME_DIR/pipewire-0`), `/dev/shm` und localhost-UDP.
  - Abhängigkeiten mit C-Erweiterungen (z. B. `evdev`) kommen als manylinux-Wheels. Gibt es kein Wheel, fehlt ohne distrobox der Compiler.
- **distrobox** ist für das **Bauen** gedacht: mingw für Helper, gcc/cmake für das SCS-Plugin, Rust/Go-Toolchains. Oder als vollständige Dev-Umgebung:
  ```bash
  distrobox create -n pitwall -i registry.fedoraproject.org/fedora-toolbox:43
  distrobox enter pitwall -- sudo dnf -y install gcc cmake mingw64-gcc golang python3-devel
  ```
  - distrobox teilt `$HOME`, `/dev`, `XDG_RUNTIME_DIR` (PipeWire), Netzwerk und `/dev/shm` mit dem Host. evdev und Audio funktionieren dort in der Regel auch.
  - Ein dauerhaft laufender Dienst sollte trotzdem nativ laufen (systemd-User-Unit mit `uv run`). So hängt er nicht vom Container-Start ab.
- Dokumentieren wir beides: `uv` für Nutzer, distrobox für Entwickler und Builds.

---

## 8. Kurzthemen

### 8.1 Overlay über Vollbildspielen unter Linux

| Weg | Funktioniert? | Hinweise |
| --- | --- | --- |
| **KDE Plasma (Wayland), Spiel im randlosen Fenster** + eigenes Overlay-Fenster mit KWin-Fensterregel „Immer im Vordergrund“ (Keep above), transparent, ohne Fokus | ja | Echtes Vollbild: KWin legt das **aktive Vollbildfenster über** die „Keep above“-Ebene (KWin-Layer-Reihenfolge: Normal < Above < Notification < Active/Fullscreen < … < OnScreenDisplay < Overlay). Daher Spiel auf „Randloses Fenster“ stellen oder eine Fensterregel „Ebene: Overlay/OSD“ setzen. UNVERIFIED: genaue Bezeichnung der Option in Plasma 6.x |
| **wlr-layer-shell-Overlay** (Qt mit `LayerShellQt`, Layer `overlay`, leere Input-Region = click-through) | ja unter KWin/Wayland | sauberste Lösung für ein natives Overlay (z. B. PySide6 + QWebEngine lädt `overlay.html`). UNVERIFIED: Verhalten über XWayland-Vollbildspielen mit direct scanout. Ein Overlay deaktiviert direct scanout, kostet also etwas Performance |
| **gamescope / Steam Gaming Mode** | eingeschränkt | gamescope zeigt nur das Spiel, Steam-Overlay und mangoapp. Fremde Fenster kommen nicht darüber. UNVERIFIED: Es gibt ein X-Atom `GAMESCOPE_EXTERNAL_OVERLAY`, mit dem eine X11-App als Overlay markiert werden kann (von Steam/Decky-Komponenten genutzt). Experimentell |
| **MangoHud** | nein für unseren Zweck | zeigt nur eigene Metriken/Text, kein HTML/Browser. Höchstens Text per `custom_text`/`exec`-Elemente in der Config, UNVERIFIED für Live-Untertitel |
| **Steam-Overlay** (Shift+Tab), Webbrowser **anheften** (seit Overlay-Update 2023, mit Transparenz) | wahrscheinlich ja | Browser auf `http://127.0.0.1:<port>/overlay` öffnen und anpinnen. UNVERIFIED unter Proton/Linux und in gamescope; Transparenz der Seite selbst wird nicht übernommen |
| **OBS-Browserquelle** | ja (für Stream/Aufnahme) | sichtbar nur im Stream, nicht auf dem eigenen Bildschirm. Überlagerung im Spiel geht nur mit OBS-Vollbild-Projektor, unpraktisch |
| Zweitgerät (Tablet/Handy) mit Dashboard | ja | robusteste Option bei einem Monitor |

Empfehlung für die Doku: (1) randloses Fenster plus Keep-above bzw. Layer-Shell-Overlay unter KDE, (2) Steam-Overlay-Pin als Alternative, (3) OBS für Streams, (4) Audio-Funk funktioniert immer, das Overlay ist optional.

### 8.2 PipeWire: Ausgabegerät wählen und Ducking

- Geräte auflisten: `wpctl status` (Sinks mit IDs), `pw-cli ls Node`, `pactl list short sinks` (pipewire-pulse).
- In Python:
  - `sounddevice` (PortAudio über die ALSA-PipeWire-Bridge): Gerät `"pipewire"` oder `"pulse"`.
  - Gezielt auf einen Sink: Environment `PULSE_SINK=<sink-name>` (bei Ausgabe über pulse) bzw. `PIPEWIRE_NODE=<node-name>`.
  - Oder abspielen mit `pw-play --target <node-name|id> file.wav` bzw. `paplay --device=<sink>`.
- Stream-Rolle setzen, damit Regeln greifen: `PULSE_PROP="media.role=Assistant"` bzw. `PIPEWIRE_PROPS='{ media.role = "Assistant" }'`, oder `pw-play --properties='{ media.role = "Assistant" }'`. In der Doku Rolle **Assistant** oder **Navigation** für den Engineer, Multimedia/Game für das Spiel.
- **Ducking mit WirePlumber 0.5** (Role-based Policy). Belegt aus `src/config/wireplumber.conf.d.examples/media-role-nodes.conf` und `docs/rst/daemon/configuration/settings.rst`, WirePlumber-Master 0.5.18. Datei `~/.config/wireplumber/wireplumber.conf.d/60-pitwall-ducking.conf` (Auszug, Struktur wie im offiziellen Beispiel):
  ```
  wireplumber.profiles = { main = { policy.linking.role-based.loopbacks = required } }
  wireplumber.settings = {
    node.stream.default-media-role = "Multimedia"   # Streams ohne Rolle (Spiel) → Multimedia
    linking.role-based.duck-level = 0.3             # Lautstärke der geduckten Rolle (0.0–1.0)
  }
  wireplumber.components.rules = [ … aus dem Beispiel übernehmen (capture.props policy.role-based.target = true, playback.props node.passive = true, media.role = "Loopback") … ]
  wireplumber.components = [
    { type = virtual, provides = policy.linking.role-based.loopbacks
      requires = [ loopback.sink.role.multimedia loopback.sink.role.assistant ] }
    { name = libpipewire-module-loopback, type = pw-module-client
      arguments = { node.name = "loopback.sink.role.multimedia", node.description = "Multimedia"
        capture.props = { device.intended-roles = [ "Music" "Movie" "Game" "Multimedia" ]
          policy.role-based.priority = 10
          policy.role-based.action.same-priority = "mix"
          policy.role-based.action.lower-priority = "mix" } }
      provides = loopback.sink.role.multimedia }
    { name = libpipewire-module-loopback, type = pw-module-client
      arguments = { node.name = "loopback.sink.role.assistant", node.description = "Race Engineer"
        capture.props = { device.intended-roles = [ "Assistant" ]
          policy.role-based.priority = 40
          policy.role-based.action.same-priority = "mix"
          policy.role-based.action.lower-priority = "duck" } }
      provides = loopback.sink.role.assistant }
  ]
  ```
  Danach `systemctl --user restart wireplumber`. Die WirePlumber-Doku warnt: Die Role-based Policy „was designed for automotive and mobile systems and may not work as expected on desktop systems“. Das ist also **optional** und muss getestet werden (UNVERIFIED auf Bazzite; die WirePlumber-Version im Image prüfen mit `wireplumber --version`, 0.5.x nötig).
  - Einfachere Alternative ohne Policy: Spiel-Sink-Input vor dem Funkspruch per `wpctl set-volume <id> 0.3` absenken und danach zurücksetzen. pitwall kann das selbst (ID per `pw-dump` finden, Filter `application.process.binary`).

### 8.3 evdev / Lenkrad-Rechte auf Bazzite

- systemd-udev markiert Joysticks/Wheels (`ID_INPUT_JOYSTICK=1`) über `70-uaccess.rules` mit `TAG+="uaccess"`. Der an der Sitzung angemeldete Benutzer bekommt eine ACL auf `/dev/input/eventX`. **Lesen der Lenkradtasten geht damit ohne Gruppe `input`.** Prüfen: `getfacl /dev/input/by-id/*event-joystick`.
- Tastatur-Hotkeys global per evdev brauchen Zugriff auf Tastatur-Event-Devices. Diese sind `root:input 0660` und **nicht** uaccess. Benutzer in Gruppe `input` aufnehmen. Auf Fedora Atomic/Bazzite liegt die Gruppe in `/usr/lib/group`, daher zuerst nach `/etc/group` übernehmen:
  ```bash
  grep -E '^input:' /usr/lib/group | sudo tee -a /etc/group
  sudo usermod -aG input "$USER"   # danach neu anmelden
  ```
  Sicherheitsabwägung: Gruppe `input` heißt Keylogger-fähig. Lieber nur das Lenkrad nutzen, oder per eigener udev-Regel nur ein bestimmtes Gerät freigeben:
  `/etc/udev/rules.d/70-pitwall.rules`: `SUBSYSTEM=="input", ATTRS{idVendor}=="046d", ATTRS{idProduct}=="<pid>", TAG+="uaccess"`, dann `sudo udevadm control --reload && sudo udevadm trigger`.
- hidraw (LEDs, Settings) braucht eigene uaccess-Regeln (`SUBSYSTEM=="hidraw", …, TAG+="uaccess"`).
- **Wheel-Treiber in Bazzite** (belegt im Bazzite-Repo `build_files/install-kernel-akmods`): `new-lg4ff` (Logitech G25–G29/G923-Klassik), `hid-tmff2` (Thrustmaster), `t150-driver`, `hid-fanatecff` (Fanatec), mit Oversteer als GUI. `hid-universal-pidff`/`hid-pidff`-Erweiterungen sind in Bazzite nicht explizit gelistet. Ob der Mainline-Kernel-Stand das abdeckt: UNVERIFIED.
- **Logitech RS50 (und G PRO), Stand 2026:**
  - Kein Mainline-Treiber gefunden.
  - Stattdessen das Projekt **mescon/logitech-trueforce-linux-driver**, ehemals `logitech-rs50-linux-driver`. Kernel-Modul `hid-logitech-dd`, GPL-2.0, ein Fork von JacKeTUs/hid-logitech-hidpp. Es bietet FFB, TrueForce (simuliert aus Telemetrie), Rev-LEDs und sysfs-Settings (`/sys/.../wheel_*`). Verpackt als DKMS (AUR/Debian), als **COPR-akmod** (`akmod-logitech-trueforce`, Fedora/Nobara), OBS und Nix.
  - Für Bazzite/rpm-ostree nicht dokumentiert. akmods auf dem Bazzite-Custom-Kernel sind problematisch (UNVERIFIED, ob layerbar). Dort ggf. per `ujust`/Image-Rebuild oder gar nicht.
  - Ohne Treiber erscheint das RS50 als generisches HID-Gerät. **Tasten und Achsen per evdev funktionieren in der Regel trotzdem** (PTT-Taste), FFB fehlt. UNVERIFIED für den RS50-PC-Modus. Xbox-Editionen müssen per usb_modeswitch in den PC-Modus, die udev-Regel liefert das Projekt mit.
  - Achtung Portkonflikt: Der Relay dieses Treibers belegt UDP **20780–20784**.

---

## Quellen

| Quelle | URL | Lizenz / Hinweis |
| --- | --- | --- |
| spacefreak18/simshmbridge (README, `simshmbridge.c`, `bridge.c`, Makefiles, `examples/acshmstart.sh`) | https://github.com/spacefreak18/simshmbridge | GPL-3.0 |
| spacefreak18/simapi inkl. simd (README, `simd/README.md`, `docs/supportedsims.md`, `docs/rfactor2.md`, `simapi/*.h`) | https://github.com/spacefreak18/simapi, https://spacefreak18.github.io/simapi/supportedsims | LGPL-3.0 |
| spacefreak18/monocoque | https://github.com/spacefreak18/monocoque | GPL-3.0 |
| poljar/shm-bridge | https://github.com/poljar/shm-bridge | MIT |
| LukasLichten/Datalink (`Datalink/src/main.rs`, README) | https://github.com/LukasLichten/Datalink | MIT |
| mescon/logitech-trueforce-linux-driver (README, `docs/SHARED_MEMORY_RELAY.md`, `docs/GAME_SETUP.md`) | https://github.com/mescon/logitech-rs50-linux-driver | GPL-2.0-only / LGPL-2.1+ |
| schlegp/rF2SharedMemoryMapPlugin_Wine | https://github.com/schlegp/rF2SharedMemoryMapPlugin_Wine | eigene Lizenz (Weitergabe nur mit Erlaubnis) |
| Swizzjack/lmu-pitwall (natives `LMU_Data`) | https://github.com/Swizzjack/lmu-pitwall | Lizenz UNVERIFIED |
| Valve Proton, Launcher-Skript `proton` (Branch `proton_10.0`: `PROTON_REMOTE_DEBUG_CMD`, Verben) | https://github.com/ValveSoftware/Proton/blob/proton_10.0/proton | Proton: „governed by a variety of licenses“ (Valve-eigene Teile BSD-artig laut LICENSE), Wine LGPL-2.1 |
| WineHQ Bug 54015 „Accessing shared memory from Linux“ | https://bugs.winehq.org/show_bug.cgi?id=54015 | nicht abrufbar, UNVERIFIED |
| WirePlumber (Mirror; `media-role-nodes.conf`, `settings.rst`, `features.rst`) | https://github.com/PipeWire/wireplumber, https://pipewire.pages.freedesktop.org/wireplumber/ | MIT |
| Bazzite (`Containerfile`, `build_files/install-kernel-akmods`) | https://github.com/ublue-os/bazzite | Apache-2.0 |
| Bazzite-Wheel-Treiber (Fall-Update-Bericht) | https://universal-blue.discourse.group/t/bazzite-fall-update-fedora-43-xbox-allies-legion-go-2-nvidia-gtx/10948 | nicht direkt abrufbar, UNVERIFIED |
| SteamDB / Steam-Store / ProtonDB (AppIDs) | https://steamdb.info/app/266410, https://steamdb.info/app/2399420/info, https://store.steampowered.com/app/365960/rFactor_2/, https://www.protondb.com/app/244210, https://steamdb.info/app/805550/dlc, https://lutris.net/games/install/10916/view, https://steamcommunity.com/app/227300/discussions/0/596268959542548446 | – |
| LMU-Telemetrie-Wiki | https://lemansultimate.wiki.gg/wiki/Telemetry | – |
| AC-Remote-Telemetrie (UDP 9996) und ACC-Broadcasting-SDK | AC: „ACRemoteTelemetry“-Doku im Kunos-Forum; ACC: `Broadcasting SDK` im Spielordner `…/Assetto Corsa Competizione/sdk/broadcasting` | Details aus Fachwissen, Quelle nicht direkt geprüft, UNVERIFIED |
| protontricks | https://github.com/Matoking/protontricks | GPL-3.0 |
