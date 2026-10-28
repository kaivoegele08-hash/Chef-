# PIT WALL – Plan, Entscheidungen und Status

Stand: 2026-10-28. Auftrag: [PROJECT_BRIEF.md](PROJECT_BRIEF.md). Code-Überblick: [../CLAUDE.md](../CLAUDE.md).

## 1. Ordnerstruktur

```
pitwall/
  core/        frame.py (Datenmodell) · state.py (RaceState) · track.py (Streckenmodell) · recorder.py · serialize.py
  adapters/    base.py · registry.py · shm.py · layout.py · simulator.py · replay.py
               f1.py + f1_packets.py · gt7.py · assetto_corsa.py · acc.py · iracing.py · rfactor2.py · ets2.py
  bridge/      pitwall_relay.c + pitwall-relay.exe (Proton) · ac_app/PitWallAC (AC-In-Game-App)
  strategy/    fuel.py · tyres.py · pit.py · plans.py · gaps.py · engine.py
  engineer/    engineer.py · director.py · events.py · speech.py · phrasebook*.py/yaml · intents.py
               coaching.py · debrief.py · memory.py · describe.py · truck.py · detectors/*
  brain/       llm.py · tools.py · prompts/system_{de,en}.md
  voice/       tts.py · radio_fx.py · audio.py · output.py · stt.py · ptt.py
  web/         server.py · snapshot.py · static/{dashboard,overlay}.html
  storage/     db.py (SQLite-Langzeitgedächtnis)
  app.py · cli.py · doctor.py · dryrun.py · config.py
config/        default.toml · sims/*.toml · tracks/*.toml
docs/          Setup je Sim · linux-proton.md · protocols/* (Recherche mit Quellen)
scripts/       pitwall-launch (Steam-Wrapper) · build-relay.sh
tests/         pytest, Fixture-Builder je Protokoll, Wine-Integrationstests
```

## 2. Bibliotheken (und warum)

| Bibliothek | Zweck | Begründung |
|---|---|---|
| FastAPI + uvicorn | Dashboard, WebSocket, REST | asynchron, ein Prozess mit der Telemetrie-Schleife |
| numpy | Funk-FX (FFT-Bandpass), Coaching-Spuren | schnell, keine schweren DSP-Abhängigkeiten |
| PyYAML | Phrasebooks, iRacing-Session-Info | BaseLoader (alles Strings) gegen iRacings unescaped YAML |
| anthropic (optional) | Claude mit Streaming, Tool Use, Prompt-Caching | offizielles SDK |
| piper-tts (optional) | lokale deutsche Stimme | offline, schnell genug für CPU; GPL-3 nur als Laufzeit-Extra |
| faster-whisper (optional) | lokale Spracherkennung | CTranslate2, int8 auf CPU, nur bei PTT aktiv |
| sounddevice (optional) | Audio-Ein/Ausgabe | PortAudio → PipeWire/Pulse/WASAPI; Fallback pw-play/paplay |
| evdev / pygame (optional) | Lenkradtasten | evdev unter Linux ohne Fensterfokus, SDL unter Windows |
| pycryptodome, zstandard (optional) | GT7-Salsa20 schneller, Aufnahmen kleiner | reine Python-Fallbacks vorhanden |
| python-dotenv | `.env` für Schlüssel | Keys nie in Config-Dateien |

Bewusst **nicht**: pyirsdk, pyaccsharedmemory, simapi u. ä. als Abhängigkeit – alle Protokolle sind eigenständig
nach öffentlicher Doku implementiert (Lizenzen: GPL/unklar bei mehreren Vorlagen).

## 3. Datenmodell (Kurzfassung)

`TelemetryFrame` = `SessionInfo` + `PlayerState` + `list[CarState]` + `GameEvent`s + `capabilities`.
Jeder Adapter meldet, welche Felder er wirklich liefert (`Capability`); Detektoren, Tools und UI prüfen das
und sagen sonst ehrlich „keine Daten“. Einheiten SI, Räder FL/FR/RL/RR, Sprit in Litern (F1: kg mit `fuel_unit`).
`RaceState` macht daraus Runden, Stints, Gaps (Zeit an Streckenpunkten, auch ohne native Abstände),
erkennt Flashback/Neustart und lernt den Boxenverlust.

## 4. Risiken und wie sie adressiert sind

| Risiko | Maßnahme | Status |
|---|---|---|
| Shared Memory unter Proton unsichtbar | eigener Relay (`pitwall-relay.exe`) im Prefix, UDP 20790; Steam-Wrapper `pitwall-launch` | ✅ unter Wine mit AC-, iRacing- (1,1 MB) und rF2-Maps getestet; Proton-Startweg live zu prüfen |
| GT7-Verschlüsselung | Salsa20 (Schlüssel/IV laut Recherche), Größenvarianten A/B/~ | ✅ Roundtrip-Tests; Live-PS5 ausstehend |
| Layout-Fehler in Binärstrukturen | `Layout`-Feldlisten + Offset-Asserts gegen die Doku, Builder-Fixtures | ✅ |
| AC und ACC teilen Map-Namen | Unterscheidung über ACC-only-Felder (Static 684+, Physics 588) | ✅ |
| Latenz | Instant-Intents ohne LLM, Haiku mit Streaming und Satz-TTS, Prompt-Caching, Zeitlimit → Phrasebook | ✅ im Debug-Panel messbar |
| Funk-Spam | Director mit Prioritäten, Cooldowns, Rationierung, Gerade-Erkennung aus gelerntem Streckenmodell | ✅ Dry-Run-Tests |
| Erfundene Zahlen | Phrasebook nur mit Detektorwerten, Claude nur über Tools, Prompt-Regel | ✅ |
| Kosten | Tageslimit für Tokens, Haiku als Standard, Strategie-LLM nur periodisch/bei komplexer Lage | ✅ |
| Datenqualität einzelner Sims | iRacing-Reifen nur in der Box, AC ohne Gegner (App), ACC nur Broadcasting unter Linux ohne Bridge | dokumentiert |

## 5. Meilensteine

| | Inhalt | Status |
|---|---|---|
| M1 | Grundgerüst, Datenmodell, Simulator, Dashboard | ✅ |
| M2 | Strategie, Detektoren, Director, Phrasebook DE/EN, TTS + Funk-FX, Dry-Run | ✅ |
| M3 | PTT, STT, Instant-Intents, Claude mit Tool Use | ✅ |
| M4 | F1 24/25 (2024/2025/2026), GT7, Recorder/Replay | ✅ |
| M5 | Proton-Bridge, AC, ACC, iRacing, rF2/LMU, `pitwall doctor`, Linux-Doku | ✅ |
| M6 | Coaching, Langzeitgedächtnis, Debrief, Overlay | ✅ |
| M7 | ETS2/ATS-Disponentenmodus | ✅ |
| M8 | Startskripte, README DE/EN, Logging, mypy/ruff sauber | ✅ |

## 6. Was als Nächstes live geprüft werden muss

Diese Punkte sind gegen Dokumentation und synthetische Daten implementiert, aber noch nicht mit dem echten
Spiel gegengeprüft (Details als **UNVERIFIED** in `docs/protocols/*.md`):

1. **Proton-Start des Relays** über `PROTON_REMOTE_DEBUG_CMD` (Fallback `PITWALL_LAUNCH_MODE=parallel`) auf Bazzite.
2. **GT7** mit echter PS5 (Heartbeat-Variante, Paketgröße), **F1 25** mit Season-Pack-Format 2026.
3. **ACC**: Static-Layout `trackConfiguration[33]` vs. `[15]` (automatische Erkennung eingebaut), Broadcasting v4
   gegen ein laufendes ACC, Zuordnung `carID` (Shared Memory) ↔ `carIndex` (Broadcasting).
4. **iRacing**: Vorzeichen von `SteeringWheelAngle`, `TireCompound`-Indizes, Verhalten unter Proton (Anti-Cheat).
5. **rF2/LMU**: Bedeutung von Telemetrie-Offset 696 in rF2 (`mDeltaBest`-Variante angenommen), Richtung von
   `mWear`, Skala von `mVirtualEnergy`, Index von `mSectorFlag`.
6. **ETS2**: Rev-12-Plugin auf aktuellem Spielstand, `navigation.time` in Spielsekunden.
7. **Logitech RS50** als evdev-Gerät (PTT) unter Bazzite ohne Spezialtreiber.

Werkzeuge dafür: `pitwall doctor --sim <spiel>`, `pitwall record --sim <spiel> --out x.pwrec` (Rohdaten
aufnehmen, später mit `pitwall dry-run --replay x.pwrec` reproduzieren) und das Debug-Panel im Dashboard.
