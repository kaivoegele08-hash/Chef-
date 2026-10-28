# Einrichtung: Euro Truck Simulator 2 / American Truck Simulator

Im Truck-Modus wird der Renningenieur zum **Disponenten und Beifahrer**: Auftrag und Deadline,
Pausenbedarf, Sprit-Reichweite, Tempolimit, Schäden, Strafen und Lieferungen. Rundenzeiten, Gegner und
Reifenstrategie gibt es hier nicht.

## Linux (nativ – empfohlen)

ETS2/ATS haben einen nativen Linux-Build. Proton ist nicht nötig.

1. Steam → ETS2 → Eigenschaften → Kompatibilität: „Bestimmtes Steam-Play-Tool erzwingen“ **aus**.
2. Telemetrie-Plugin besorgen: [truckermudgeon/scs-sdk-plugin](https://github.com/truckermudgeon/scs-sdk-plugin/releases)
   (MIT, Release v1.12.x, Datei `libscs-telemetry.so`). Selbst bauen geht in einer distrobox:
   ```bash
   distrobox enter pitwall -- sudo dnf -y install cmake gcc-c++ git
   distrobox enter pitwall -- bash -lc 'git clone https://github.com/truckermudgeon/scs-sdk-plugin ~/src/scs && cd ~/src/scs && cmake -B build -DCMAKE_BUILD_TYPE=Release && cmake --build build'
   ```
3. Kopieren:
   ```bash
   GAME="$HOME/.local/share/Steam/steamapps/common/Euro Truck Simulator 2"
   mkdir -p "$GAME/bin/linux_x64/plugins" && cp libscs-telemetry.so "$GAME/bin/linux_x64/plugins/"
   ```
   (ATS: `American Truck Simulator`.)
4. Spiel starten und den Dialog „SDK has been activated“ mit OK bestätigen (kommt bei jedem Start).
5. `pitwall doctor --sim ets2` → „ETS2/ATS-Telemetrie: SCSTelemetry gefunden“.

PIT WALL liest `/dev/shm/SCSTelemetry` (und `/dev/shm/SCS/SCSTelemetry` vom alxwk-Fork).
Nur Plugin-Layout **Rev 12** wird akzeptiert – ältere Plugins meldet PIT WALL im Log.

## Windows / Proton

Die Windows-DLL des Plugins (RenCloud/scs-sdk-plugin bzw. truckermudgeon-Release) nach
`bin/win_x64/plugins/` kopieren. Unter Proton zusätzlich die Startoption
`~/.local/bin/pitwall-launch %command%` setzen (siehe [linux-proton.md](linux-proton.md)).

## Was der Disponent sagt

| Situation | Beispiel |
|---|---|
| Neuer Auftrag | „Neuer Auftrag: Bagger nach Berlin. 420 km, Lieferung bis 20:00.“ |
| Zeitplan knapp | „Wird knapp mit der Lieferung. Deadline 20:00, wir sind etwa 40 Minuten zu spät.“ |
| Pause | „Du solltest in 50 Minuten eine Pause machen.“ |
| Sprit | „Sprit reicht noch etwa 130 km. Nächste Tankstelle einplanen.“ |
| Tempolimit (≥ 8 km/h drüber, 5 s lang) | „Tempolimit 80 km/h. Du bist 12 km/h drüber.“ |
| Schaden (10/25/50 %) | „Schaden am Motor, 25 %. Werkstatt einplanen.“ |
| Strafe, Fähre, Lieferung | „Strafe bekommen, 250 €.“ · „Lieferung angekommen! 12.450 € verdient.“ |

Fragen per Funk: „Wann muss ich Pause machen?“, „Wie weit ist es noch?“, „Wie viel Sprit?“, „Schaden?“ –
alles andere beantwortet Claude mit dem Tool `get_truck_job`.

Hinweis zur Zeit: Die Spielzeit läuft komprimiert. Deadline und Pausen sind Spielzeit (wie im Spiel
angezeigt); „noch etwa N Minuten“ bei der Restdistanz ist Echtzeit.
