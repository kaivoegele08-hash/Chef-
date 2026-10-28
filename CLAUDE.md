# CLAUDE.md – PIT WALL

## Projektziel

PIT WALL ist ein KI-Renningenieur für Rennsimulationen (Deutsch, optional Englisch). Er liest Live-Telemetrie,
spricht proaktiv über „Boxenfunk“ (TTS mit Funk-Effekt), beantwortet Fragen per Push-to-Talk am Lenkrad und
nutzt Claude als Gehirn – mit Tool Use, damit jede genannte Zahl aus echten Daten stammt. Ohne Netz/API-Key
läuft er im Phrasebook-Modus weiter. Zielsystem: Bazzite Linux mit Proton-Spielen, Windows ebenso.

Auftrag und Anforderungen: `docs/PROJECT_BRIEF.md`. Architektur und Status: `docs/PLAN.md`.

## Architektur (Datenfluss)

```
Spiel ──► RawSource (UDP | Shared Memory | Relay | Replay) ──► Decoder ──► TelemetryFrame (normalisiert)
                                                                              │
             ┌────────────────────────────────────────────────────────────────┤
             ▼                                                                ▼
        RaceState (Runden, Stints, Gaps,                             Coach (Kurvenanalyse)
        Flashback/Restart, Boxenverlust)                                      │
             │                                                                │
             ▼                                                                │
        StrategyEngine (Sprit, Reifen, Pläne, Undercut, SC, Wetter) ──► Detektoren ──► RadioEvents
                                                                              │
                         PTT ► STT ► Instant-Intent ─┐                        ▼
                                          └► Claude (Tools) ──────────► RadioDirector (Priorität,
                                                                       Timing auf Geraden, Rationierung)
                                                                              │
                                                Phrasebook DE/EN ◄────────────┤
                                                                              ▼
                                                        TTS (Piper/ElevenLabs) ► Funk-FX ► Audio
                                                        Dashboard/Overlay (WebSocket), SQLite-Gedächtnis
```

| Paket | Inhalt |
|---|---|
| `pitwall/core` | `frame.py` (Datenmodell, SI-Einheiten, Räder FL/FR/RL/RR), `state.py` (RaceState, GapTracker), `track.py` (gelerntes Streckenmodell), `recorder.py` (.pwrec) |
| `pitwall/adapters` | je Sim **RawSource + Decoder**; `registry.py` (Auto-Erkennung), `shm.py` (Shared Memory: Windows / `/dev/shm` / Relay), `layout.py` (benannte struct-Layouts) |
| `pitwall/bridge` | `pitwall_relay.c` → `pitwall-relay.exe` (Proton-Bridge, UDP 20790), `ac_app/PitWallAC` (AC-In-Game-App, UDP 20791) |
| `pitwall/strategy` | Sprit, Reifen/Degradation, Boxenverlust, Planberechnung, Gaps/Undercut, `engine.py` |
| `pitwall/engineer` | Detektoren, `director.py`, Phrasebooks (`phrasebook_*.yaml`), `speech.py` (Zahlen sprechen), `intents.py`, `coaching.py`, `debrief.py`, `truck.py` (ETS2) |
| `pitwall/brain` | Claude-Anbindung (`llm.py`), Tools (`tools.py`), System-Prompts |
| `pitwall/voice` | TTS, Funk-FX, Audio, STT (faster-whisper), PTT (evdev/SDL) |
| `pitwall/web` | FastAPI + WebSocket, `static/dashboard.html`, `static/overlay.html` |
| `pitwall/storage` | Langzeitgedächtnis (SQLite) |

## Konventionen

- Python ≥ 3.11, durchgehend Typannotationen, `from __future__ import annotations`.
- Kommentare, Logs, UI-Texte auf **Deutsch**; Bezeichner auf Englisch.
- Einheiten intern immer SI (m, s, m/s, °C, kPa, Liter bzw. kg bei F1). Umrechnung nur in `speech.py`/UI.
- Adapter: Decoder ist **zustandsbehaftet und netzwerkfrei** (`feed(RawPacket) -> TelemetryFrame | None`), damit
  Replay und Tests denselben Code nutzen. Kaputte Pakete: `safe_feed` loggt und verwirft – nie abstürzen.
- Binärlayouts mit `Layout`-Feldlisten + `assert offset_of(...)` gegen die Doku in `docs/protocols/`.
- Keine Zahl erfinden: Phrasebook-Werte kommen aus Detektoren, Claude bekommt Zahlen nur über Tools.
- Kein Code aus GPL-/unlizenzierten Projekten übernehmen (Protokolle neu implementieren, Quelle in der Doku).
- Nichts Kostenpflichtiges oder schwer Umkehrbares ohne Rückfrage (z. B. ElevenLabs, iRacing-Pit-Befehle sind
  standardmäßig aus). API-Keys nur in `.env` / Umgebung, nie in Config-Dateien, die das Dashboard schreibt.
- Mikrofon nur bei gedrückter PTT-Taste.

## Befehle

```bash
uv venv --python 3.12 && uv pip install -e '.[all,dev]'   # Umgebung
./start.sh                       # Start mit Auto-Erkennung (Linux), start.ps1 unter Windows
pitwall run --sim simulator      # Testrennen ohne Spiel (Szenarien: --scenario undercut|sc_window|rain|…)
pitwall dry-run --laps 20        # komplettes Rennen als Text, ohne Audio
pitwall doctor [--sim acc]       # Setup + Live-Datenprüfung
pitwall run --replay datei.pwrec # Aufnahme abspielen
.venv/bin/python -m pytest -q    # Tests (Wine-Tests werden ohne wine64 übersprungen)
.venv/bin/ruff check pitwall tests
.venv/bin/mypy pitwall
scripts/build-relay.sh           # pitwall-relay.exe neu bauen (mingw)
```

Dashboard: http://127.0.0.1:8077 · Overlay: http://127.0.0.1:8077/overlay

## Unterstützte Sims

| Sim | Weg | Status |
|---|---|---|
| Simulator (eingebaut) | – | ✅ 11 Szenarien, Grundlage für Dry-Runs/Tests |
| F1 24 / F1 25 | UDP 20777, Formate 2024/2025/2026 | ✅ Parser mit Fixtures getestet |
| Gran Turismo 7 (PS5) | UDP 33739/33740, Salsa20 | ✅ Parser + Krypto getestet, PS5-Suche |
| Assetto Corsa | Shared Memory + UDP 9996 + App PitWallAC | ✅ inkl. Wine-Relay-Test |
| ACC | Broadcasting-UDP 9000 + Shared Memory | ✅ Parser getestet (Live-Abgleich ausstehend) |
| iRacing | irsdk Shared Memory | ✅ synthetische Map + Wine-Relay-Test (Live-Abgleich ausstehend) |
| rFactor 2 / LMU | rF2-Plugin bzw. `LMU_Data` | ✅ synthetische Maps + Wine-Relay-Test (Live-Abgleich ausstehend) |
| ETS2 / ATS | SCS-Plugin Rev 12 (`/dev/shm/SCSTelemetry`) | ✅ Disponentenmodus, synthetische Daten |

„Live-Abgleich ausstehend“ heißt: gegen die dokumentierten Layouts implementiert und getestet, aber noch nicht
gegen ein laufendes Spiel geprüft. Offene Punkte stehen als **UNVERIFIED** in `docs/protocols/*.md`.
