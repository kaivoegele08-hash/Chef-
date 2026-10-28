# Projekt-Prompt für Claude Code: „PIT WALL“ – KI-Renningenieur für mehrere Sims

> Kopiere alles ab hier in Claude Code (am besten in einen leeren Projektordner). Lege den Text zusätzlich als `docs/PROJECT_BRIEF.md` ab, damit er über Sessions hinweg erhalten bleibt.

---

## 0. Deine Rolle und Arbeitsweise

Du bist Lead-Engineer für ein Open-Source-Projekt: einen **KI-Renningenieur**, der während ich fahre live Telemetrie aus meinem Rennspiel liest, die Lage analysiert, mich **per Funk (Sprachausgabe) proaktiv anspricht** und **auf Sprachfragen antwortet**, die ich per Push-to-Talk-Taste am Lenkrad stelle – wie ein echter F1-/GT-Renningenieur am Kommandostand.

Arbeitsregeln:
1. **Starte im Plan-Modus.** Lies diesen Brief vollständig, recherchiere offene Protokoll-Details (siehe Abschnitt 4) im Web, und lege mir zuerst einen Architektur- und Meilensteinplan vor. Erst nach meiner Freigabe implementieren.
2. **Lege eine `CLAUDE.md`** im Repo an mit: Projektziel, Architekturübersicht, Code-Konventionen, Befehle zum Starten/Testen, Liste der unterstützten Sims mit Status.
3. **Verifiziere jedes Telemetrie-Protokoll** (Ports, Offsets, Struct-Layouts, Verschlüsselung) gegen offizielle Doku bzw. etablierte Community-Projekte, bevor du Parser schreibst. Die Angaben unten sind ein Startpunkt, nicht garantiert aktuell. Wenn sich Formate geändert haben (neue Spielversion), richte dich nach der aktuellen Quelle und notiere sie in `docs/protocols/<sim>.md`.
4. **Kopiere keinen Code** aus Repos ohne passende Lizenz (das Vorbild-Repo hat soweit erkennbar keine Lizenz). Inspiration ja, Copy-Paste nein. Bei MIT/Apache/BSD-Code: Lizenz respektieren und vermerken.
5. Du kannst die Spiele nicht selbst ausführen. Deshalb ist ein **Aufnahme-/Replay-System und ein Simulator Pflicht** (Abschnitt 11), damit alles ohne Spiel testbar ist.
6. Arbeite in kleinen, lauffähigen Schritten, mit `pytest`-Tests für Parser, Strategie-Mathematik und Event-Detektoren. Committe nach jedem Meilenstein mit aussagekräftiger Message.
7. Nutze Subagents/parallele Arbeit nur, wo es sich lohnt (z. B. Protokoll-Recherche pro Sim parallel).
8. Frag mich, bevor du Entscheidungen triffst, die Geld kosten (bezahlte APIs) oder schwer rückgängig zu machen sind.

---

## 1. Vorbild: Was das Video zeigt

Vorlage ist das YouTube-Video „I Built the World's First AI F1 Race Engineer“ bzw. das Projekt **BOX BOX** (GitHub: `ploverbyHB/Box-Box`). Kernfunktionen des Originals:

- Liest **F1 24 UDP-Telemetrie** (Port 20777) für alle 22 Autos; zusätzlich GT7-Telemetrie.
- Modelliert **Reifen, Sprit, ERS, Wetter und Gegner in der Nähe**, bevor eine Strategie-Empfehlung entsteht.
- Fragt in einem festen Intervall (Standard 15 s) ein **LLM (DeepSeek)** nach Kommandostand-Ratschlägen und spricht sie per **ElevenLabs** aus.
- **Boxenfunk in beide Richtungen:** Mikrofon-Taste/Leertaste → Frage zu Reifen, Boxenfenster, Abständen, Sprit → Antwort als Text und Stimme.
- **Live-Dashboard** im Browser (FastAPI + WebSocket): Wertung, Streckenkarte, Daten des gewählten Autos, Funk-Log.
- **22-Auto-Simulator** als Fallback ohne laufendes Spiel.
- Aufbau: `server.py` (FastAPI, UDP-Listener, Dashboard, Funk-API), `f1_parser.py`, `engine_models.py` (Reifen/Sprit/Wetter/Gegner), `analyzer.py` (LLM), `tts_bridge.py`, `simulator.py`, `static/index.html`.

Weiterentwicklungen der Community, die ich ebenfalls will:
- **F1 25** inkl. Season-Pack-Format, echte **Strategie-Engine**: Boxenverlust und Wiedereinstiegsposition, sprit-korrigierte Reifendegradation, Rennzeit von 0-/1-/2-Stopp-Plänen, Warteschlange wenn der Teamkollege gleichzeitig an die Box kommt.
- **Engineer-Gedächtnis** pro Rennen: was er gesagt hat, was ich gesagt/gefragt habe. Reset bei Rennneustart, Flashback-Erkennung (zurückgespulte Runden rückgängig machen).
- **Race-Start-Call erst bei „Lights out“**, nicht schon in der Startaufstellung.
- **Fahrcoaching** aus meiner eigenen Telemetrie: Kurven, in denen ich Zeit verliere (zu spät/zu früh gebremst, zu viel Speed abgebaut, spät am Gas), Blockieren, Wheelspin, Unter-/Übersteuern, Ausritte, Rollen ohne Gas – und welche Gewohnheiten den limitierenden Reifen verschleißen.
- **Funk-Regie wie im echten Funk:** kurz, Zahlen zuerst, **nur auf Geraden, nie in Bremszonen**; dringende Calls unterbrechen, Geplauder wird rationiert, **Funkstille auf der Qualifying-Push-Runde**.
- **Strategisches Denken:** Plan A/B je nach Degradation („Plan A plus drei“ wenn die Reifen halten), Stopp unter Safety Car wenn im Fenster, Gegner die noch stoppen müssen, Reifen-Offsets bei Abstandsangaben, Reifenbriefing vor dem Start, fragt nach der Balance und merkt sich meine Antwort.
- **Langzeitgedächtnis:** Sessions werden aufgezeichnet; der Engineer lernt meine Notizen und Ergebnisse über Zeit (z. B. typischer Spritverbrauch pro Strecke/Auto, meine Schwachkurven).
- **Funk-Overlay über dem Spiel** im Broadcast-Stil (Teamfarbe, Wellenform, Live-Untertitel), auch als **OBS-Browserquelle**.

Ziel: **Alles davon, aber Sim-übergreifend** und mit **Claude** als Gehirn.

---

## 2. Mein Setup (Zielumgebung)

- **PC:** Intel Core i7, GTX 1660, **Bazzite (immutable Fedora-basiertes Linux)**, Spiele laufen über **Steam/Proton**. Ein Monitor.
- **PS5** im selben Heimnetz.
- **Lenkrad:** Logitech RS50 (Direct Drive) mit RS Track Wheel + RS Pedals (vorher G920). Push-to-Talk soll auf eine **Lenkradtaste** legbar sein.
- **Spiele:** Assetto Corsa, iRacing, Gran Turismo 7 (PS5), rFactor 2, später Euro Truck Simulator 2. Klassen: GT und Formel. Zusätzlich F1 24/25 als Referenz zum Original.
- Ich fahre Wettbewerb, 12–24 h pro Woche → Latenz und Zuverlässigkeit sind wichtiger als Spielereien.
- **Sprache des Engineers: Deutsch** (konfigurierbar, Englisch als Option).

Konsequenzen:
- Python-Umgebung auf Bazzite über **distrobox/toolbox** oder `uv`-venv im Home-Verzeichnis (kein `dnf install` ins Basissystem). Dokumentiere beides.
- Ports in **firewalld** freigeben (UDP-Ports der Sims) – Befehle in die Doku.
- Audio über **PipeWire**; Ausgabegerät wählbar (z. B. Headset statt Lautsprecher).
- Lenkradtasten unter Linux über **evdev** (Fallback `pygame`/SDL Joystick). Unter Windows `pygame`. Tasten-Mapping per „Taste drücken zum Zuweisen“ im Dashboard.
- **Shared-Memory-Sims unter Proton:** Spiele wie iRacing, Assetto Corsa und rFactor 2 legen ihre Telemetrie als Windows-Shared-Memory an, das in der Wine-Prefix lebt und für ein natives Linux-Python **nicht direkt sichtbar** ist. Lösungen recherchieren und bewerten, z. B.:
  - Bridge-Programm, das innerhalb derselben Proton-Prefix läuft und das Mapping nach `/dev/shm` spiegelt (Community-Projekte wie *simshmbridge* / *simapi* prüfen),
  - oder ein kleines Windows-Helper-EXE (unter Proton gestartet), das Shared Memory liest und per **UDP/WebSocket an localhost** weiterreicht,
  - oder, wo vorhanden, die **UDP-Schnittstelle des Spiels** statt Shared Memory nutzen (z. B. Assetto Corsa Remote-Telemetrie).
  Ergebnis dokumentieren in `docs/linux-proton.md` mit Schritt-für-Schritt-Anleitung (inkl. Steam-Startoptionen, falls nötig).
- Das Tool soll auch unter **Windows** laufen (Freunde von mir nutzen Windows), aber Linux ist Priorität 1.

---

## 3. Gesamtarchitektur

```
 ┌───────────────┐   ┌────────────────────┐   ┌──────────────────┐
 │ Sim-Adapter   │──▶│ Normalisierter     │──▶│ Race State       │
 │ (pro Spiel)   │   │ TelemetryFrame     │   │ + Strategie-     │
 │ UDP / ShMem   │   │ + Capabilities     │   │   Engine         │
 └───────────────┘   └────────────────────┘   └────────┬─────────┘
                                                       │
                       ┌───────────────────────────────┼───────────────┐
                       ▼                               ▼               ▼
              ┌─────────────────┐           ┌──────────────────┐ ┌───────────┐
              │ Event-Detektoren│──Events──▶│ Radio Director   │ │ Dashboard │
              │ (regelbasiert)  │           │ (Priorität,Timing│ │ FastAPI + │
              └─────────────────┘           │  Rationierung)   │ │ WebSocket │
                                            └────────┬─────────┘ └───────────┘
                                                     ▼
       PTT-Taste ─▶ Mikro ─▶ STT ─▶ ┌──────────────────────────┐
                                    │ Engineer Brain (Claude)  │
                                    │ Tool-Use auf Live-Daten  │
                                    │ + Phrasebook-Fallback    │
                                    └───────────┬──────────────┘
                                                ▼
                                   TTS ─▶ Funk-Effekt ─▶ Audio-Ausgabe
                                                └─▶ Overlay-Untertitel
```

Prinzipien:
- **Deterministische Mathematik, KI nur für Sprache und Abwägung.** Spritrechnung, Abstände, Boxenfenster, Degradation rechnet Python-Code exakt. Das LLM formuliert, priorisiert, beantwortet freie Fragen und wägt Strategie-Optionen ab – auf Basis der berechneten Zahlen. **Das LLM erfindet niemals Zahlen.**
- **Capabilities statt Annahmen:** Jeder Adapter meldet, welche Datenfelder er liefert (z. B. GT7 liefert keine Gegnerdaten, ETS2 keine Rundenzeiten). Detektoren und Prompt passen sich an; der Engineer sagt ehrlich „Dazu habe ich keine Daten“.
- **Asynchron:** `asyncio` durchgängig. Telemetrie-Ingest darf nie durch LLM/TTS blockiert werden.
- **Offline-fähig:** Ohne API-Key oder Internet funktioniert der Engineer im **Phrasebook-Modus** (vorgefertigte deutsche Sätze mit Zahlen-Templates + lokales TTS).

Vorgeschlagene Struktur (darfst du im Plan verbessern):
```
pitwall/
  adapters/        base.py, f1.py, gt7.py, assetto_corsa.py, acc.py, iracing.py,
                   rfactor2.py, lmu.py, ets2.py, ams2.py, replay.py, simulator.py
  core/            frame.py (Datenmodell), state.py, track.py, session.py, recorder.py
  strategy/        fuel.py, tyres.py, pit.py, plans.py, gaps.py
  engineer/        detectors/ (je Thema eine Datei), director.py, phrasebook_de.yaml,
                   phrasebook_en.yaml, memory.py, coaching.py
  brain/           llm.py (Claude-Client), tools.py, prompts/, conversation.py
  voice/           stt.py, tts.py, radio_fx.py, ptt.py (evdev/pygame/keyboard)
  web/             server.py, static/dashboard.html, static/overlay.html
  config/          default.toml, sims/*.toml, tracks/*.toml (Boxenverlust etc.)
  tests/           fixtures/ (aufgezeichnete Pakete), test_*.py
  docs/            protocols/, linux-proton.md, setup-<sim>.md
```

---

## 4. Sim-Adapter (Herzstück der Multi-Sim-Fähigkeit)

Gemeinsames Interface (`adapters/base.py`):
- `async start()`, `async stop()`, `async frames()` → liefert `TelemetryFrame`s
- `capabilities: set[Capability]`
- `detect()` → prüft, ob das Spiel gerade Daten sendet (für **Auto-Erkennung**: Dashboard zeigt „Erkannt: Assetto Corsa“)
- Jeder Adapter schreibt Rohdaten optional in den Recorder (für Replays/Tests).

Startwerte zu den Protokollen (**alle vor Implementierung verifizieren**):

### F1 24 / F1 25 (PC und PS5)
- UDP, Standardport **20777**, In-Game: Telemetrie an, IP = PC im LAN, Senderate ≥ 20 Hz. Telemetrie kommt nur auf der Strecke, im Menü nur Events.
- Header mit `packetFormat` (2024/2025) und `packetId`. Relevante Pakete: Motion, Session, Lap Data, Event, Participants, Car Telemetry, Car Status, Final Classification, Car Damage, Session History, Tyre Sets. Formatversion per Header unterscheiden (24 vs. 25 vs. Season-Pack mit mehr Autos – Array-Größe nicht hart auf 22 codieren).
- Liefert alle Autos → volles Feature-Set (Gegner, Gaps, Reifen der Gegner, Safety Car, Flaggen, ERS).

### Gran Turismo 7 (PS5)
- UDP: PC schickt regelmäßig (~alle 10 s, sonst stoppt der Stream) einen **Heartbeat an die PS5, Port 33739**; Daten kommen auf **Port 33740**.
- Pakete sind **Salsa20-verschlüsselt**; Key/Nonce-Ableitung, Magic-Bytes und die neueren Heartbeat-Varianten mit längeren Paketen anhand aktueller Community-Doku verifizieren (z. B. Projekte wie *gt7dashboard*, *PDTools*).
- Felder u. a.: Position/Geschwindigkeit, RPM, Gang, **Sprit**, Reifentemperaturen, Fahrwerk, Rundenzahl, beste/letzte Runde, Position vor dem Rennen.
- **Keine Gegnerdaten** → Capabilities entsprechend; Abstände nur indirekt. Engineer fokussiert Sprit, Reifentemperaturen, Rundenzeiten, Konstanz, Coaching.
- PS5-IP im Dashboard einstellbar, mit „Suchen“-Button (Broadcast-Versuch).

### Assetto Corsa (PC, Proton)
- **Shared Memory** `acpmf_physics`, `acpmf_graphics`, `acpmf_static` (reich: Sprit, Reifentemps/-druck/-verschleiß, Schäden, Position, Rundenzeiten, Gaps eingeschränkt).
- Alternative ohne Bridge: **AC-Remote-Telemetrie über UDP Port 9996** (Handshake, dann Subscribe) – weniger Felder, aber unter Linux sofort nutzbar. Implementiere beides, wähle automatisch das reichhaltigere verfügbare.
- Andere Autos: nur begrenzt; prüfen, was graphics-Memory liefert, ggf. Gaps über Positions-Splines schätzen.

### Assetto Corsa Competizione / AC EVO (optional)
- ACC: Shared Memory wie AC (andere Struktur-Version) + **Broadcasting-API über UDP** (Standardport 9000, Konfigdatei im Dokumente-Ordner) für alle Autos → volle Gegnerdaten.
- AC EVO: aktuellen Stand der Telemetrie recherchieren; Adapter nur anlegen, wenn dokumentiert.

### iRacing (PC, Proton)
- **iRacing SDK (irsdk)**: Shared-Memory-Map mit Header, Variablen-Headern und rotierenden Datenpuffern (60 Hz) + **Session-Info als YAML** (Fahrerliste, Strecke, Wetter, Rennlänge).
- Liefert für alle Autos u. a. `CarIdxLapDistPct`, `CarIdxPosition`, `CarIdxLastLapTime`, `CarIdxOnPitRoad`, `CarIdxTrackSurface` → Gaps, Boxenstopps, Gegner. Eigene Daten sehr detailliert (Sprit, Reifentemps nur in Box, Flaggen, Incidents).
- Unter Linux zwingend Bridge (Abschnitt 2). Pure-Python-Parser schreiben (kein Windows-only `mmap`-Tag-Zugriff voraussetzen), damit derselbe Parser auf einer `/dev/shm`-Kopie oder einem weitergeleiteten Byte-Stream läuft.
- Spezielle iRacing-Events: Incident-Count, Flaggen (gelb, Blau, Black/Meatball), Fast Repairs, Meldung „Pit Limiter an?“, Tankfüllung über **Pit-Commands** (nur ansagen, nicht automatisch setzen – optional als Feature mit meiner Freigabe).

### rFactor 2 und Le Mans Ultimate (PC, Proton)
- rF2: **Shared-Memory-Plugin** (verbreitet: *rF2SharedMemoryMapPlugin*) mit Buffern für Telemetry, Scoring, Rules, Extended. Scoring liefert alle Autos → Gegner/Gaps/Boxenstatus.
- LMU: aktuellen Stand prüfen (eingebaute Shared-Memory-Schnittstelle vs. rF2-Plugin); Adapter so bauen, dass beide über gemeinsame Strukturen laufen.
- Plugin-Installation in `docs/setup-rfactor2.md` beschreiben.

### Euro Truck Simulator 2 / American Truck Simulator (später)
- **SCS Telemetry SDK** über ein Plugin, das Shared Memory bereitstellt (Community-Plugin prüfen; ETS2 läuft nativ unter Linux → native `.so`-Variante bevorzugen, sonst Proton-Weg).
- **Anderer Engineer-Modus: „Disponent/Beifahrer“** statt Renningenieur. Themen: Lieferzeit/Deadline, Restdistanz, Müdigkeit/Schlafbedarf, Tankstopp-Planung, Schäden an Zugmaschine/Anhänger, Tempolimit, Strafen, Fracht. Gleiche Pipeline, eigenes Phrasebook + eigener System-Prompt.

### Automobilista 2 / Project CARS (optional)
- Shared Memory bzw. UDP (Standard 5606). Nur, wenn Zeit bleibt.

### Replay- und Simulator-Adapter
- `replay.py`: spielt aufgezeichnete Rohpakete/Frames in Echtzeit oder beschleunigt ab.
- `simulator.py`: synthetisches Rennen mit 20+ Autos, Reifenabbau, Boxenstopps, Safety Car, Regen, Unfällen – deterministisch per Seed. Muss jeden Detektor auslösen können.

---

## 5. Normalisiertes Datenmodell (`core/frame.py`)

Pydantic- oder `dataclass(slots=True)`-Modelle. Einheiten: SI intern (m, s, km/h nur für Ausgabe, °C, kPa/psi wählbar, Liter).

- `SessionInfo`: sim, Strecke (+ Länge), Session-Typ (Training/Quali/Rennen/Zeitfahren), Rennlänge (Runden oder Zeit), Wetter + Prognose (falls vorhanden), Fahrzeugklasse, Anzahl Autos, SC-/Flaggenstatus.
- `PlayerCar`: Position, Runde, Rundendistanz %, Sektor, Zeiten (aktuell/letzte/beste/Sektoren), Speed, Gang, RPM, Gas/Bremse/Lenkung, Sprit (L + Kapazität), Reifen je Rad (Mischung, Alter in Runden, Temp. innen/mitte/außen bzw. Oberfläche/Kern, Druck, Verschleiß %), Bremsentemp., Schäden je Bauteil, ERS/Hybrid (Ladung, Modus), DRS, Boxenstatus, Strafen.
- `OtherCar[]`: Fahrername, Position, Rundendistanz, Gap zu mir und zum Vordermann, letzte Runde, Reifen + Alter (falls sichtbar), Boxenstopps bisher, in der Box ja/nein, Teamkollege-Flag.
- `capabilities`: Set von Enums (`OPPONENTS`, `TYRE_WEAR`, `TYRE_TEMPS`, `FUEL`, `ERS`, `WEATHER_FORECAST`, `DAMAGE`, `FLAGS`, `PIT_STATUS`, `SECTOR_TIMES`, `INPUTS`, `TRACK_POSITION_XY`, …).
- Zeitstempel + Sequenznummer pro Frame.

---

## 6. Race State und Strategie-Engine (`strategy/`)

Alles mit Unit-Tests und Simulator-Szenarien abgesichert.

- **Sprit:** gleitender Mittelwert Verbrauch/Runde (Ausreißer wie Boxen-/SC-Runden ausschließen), Restrunden bzw. Restzeit → benötigter Sprit, Reserve (konfigurierbar, z. B. +0,5 Runden), „Sprit sparen: X Liter/Runde Ziel“, **Lift-and-Coast-Empfehlung**. Bei Zeitrennen: Runden aus Rundenzeit-Schätzung ableiten.
- **Reifen:** Degradation per Regression der Rundenzeiten, **sprit-korrigiert** (konfigurierbarer Zeitwert pro kg/Liter), Verschleiß-Hochrechnung bis Grenzwert, Temperaturfenster je Mischung/Sim (Konfig), Überhitzung/zu kalt.
- **Boxenstopp:** Boxenverlust pro Strecke (Konfigdatei, wird aus echten Stopps **gelernt** und gespeichert), **Wiedereinstiegsposition** und Verkehr nach dem Stopp, Boxenfenster, Under-/Overcut-Bewertung gegenüber direkten Gegnern, „Box unter Safety Car“-Logik (SC halbiert ca. den Verlust – Faktor konfigurierbar/gelernt).
- **Stopp-Pläne:** Rennzeit-Schätzung für 0/1/2(/3)-Stopp-Varianten mit Mischungswahl → Plan A + Plan B, laufende Neuberechnung jede Runde und bei Events.
- **Gaps:** zum Vorder-/Hintermann, zu direkten Rivalen, Delta pro Runde („holst 0,3 pro Runde“), Hochrechnung „in X Runden in DRS-Reichweite“, Reifenoffset-bereinigte Gaps.
- **Teamkollege:** gleichzeitiger Stopp → Warteschlange (wo Daten vorhanden).
- **Flashback/Rewind/Neustart** erkennen (Rundenzahl/Zeit springt zurück) → State zurückrollen bzw. neu initialisieren, Funk-Queue leeren.
- **Langzeitwissen (SQLite):** pro Sim/Strecke/Auto: Spritverbrauch, Boxenverlust, typische Rundenzeit, Reifenleben, meine Notizen zur Balance.

---

## 7. Event-Detektoren (`engineer/detectors/`)

Regelbasiert, jede Klasse liefert `RadioEvent(priority, category, data, expires_at, cooldown_key)`. Mindestens:

- **Rennen:** Lights out / Startbericht nach Runde 1 (Positionsgewinn/-verlust), Positionswechsel, Überholt/Wurde überholt, letzte Runde, Zielflagge mit Ergebnis + Kurzdebrief.
- **Abstände:** Vordermann in Reichweite / DRS, Hintermann kommt näher (Delta-Trend), „Gap stabil“.
- **Strategie:** Boxenfenster offen, „Box diese Runde“ (dringend), Gegner ist in die Box (Undercut-Gefahr), Plan-Wechsel A→B, Reifenbriefing vor dem Start.
- **Sprit:** knapp/zu viel, Spar-Ziel, Bestätigung „Sprit passt bis ins Ziel“.
- **Reifen:** Temperatur außerhalb Fenster, hoher Verschleiß, Plattfuß/Schaden, Blockier-Häufung.
- **Flaggen/Sicherheit:** Gelb im Sektor X, Safety Car / VSC, Blaue Flagge, Strafe/Verwarnung, Track Limits, Schaden gemeldet.
- **Wetter:** Regen in X Minuten (falls Prognose), Streckentemperatur-Wechsel, Crossover-Punkt Slicks/Inters.
- **Persönliche Bestzeit / Schnellste Runde**, Sektor-Bestzeiten (nur in passenden Momenten).
- **Qualifying-Modus:** Out-Lap-Infos, Verkehrswarnung vor Push-Lap, **Funkstille während Push-Lap**, danach Zeit + Position.
- **ETS2-Modus:** eigene Detektoren (Deadline, Müdigkeit, Tankempfehlung, Tempolimit, Schaden).

---

## 8. Radio Director (`engineer/director.py`) – das, was es „echt“ wirken lässt

- **Prioritätsklassen:** `CRITICAL` (Box jetzt, SC, Schaden, Plattfuß) > `HIGH` (Undercut-Gefahr, Gelb) > `INFO` (Gaps, Sprit) > `CHATTER` (Lob, Small Talk).
- **Timing nach Streckenposition:** Pro Strecke werden Bremszonen und Geraden **automatisch aus der eigenen Telemetrie gelernt** (Bremse > Schwelle / Vollgas + Lenkwinkel klein über Distanz-Bins). Nicht-kritische Nachrichten warten auf die nächste Gerade; CRITICAL darf sofort.
- **Ablaufzeit:** veraltete Infos (z. B. Gap von vor 2 Kurven) verfallen, statt verspätet gesprochen zu werden.
- **Rationierung:** max. N Nicht-kritische Nachrichten pro Runde, Cooldowns je Kategorie, keine Wiederholung gleicher Aussagen.
- **Unterbrechen:** CRITICAL kann laufende CHATTER-Ausgabe abbrechen (Audio stoppen, kurzer „Klick“).
- **Fragen haben Vorrang** vor proaktiven Infos, außer CRITICAL.
- **Modi:** „Gesprächig“, „Normal“, „Nur Wichtiges“, „Stumm“ – per Sprachbefehl („Funk ruhig bitte“) und Dashboard.
- **Funkstille-Regeln:** Quali-Push-Lap, letzte 2 Kurven vor Ziel (außer CRITICAL), wenn ich gerade spreche (PTT gedrückt).

---

## 9. Engineer Brain mit Claude (`brain/`)

- **Anthropic Python SDK**, API-Key aus `.env` (`ANTHROPIC_API_KEY`). Modell konfigurierbar; Vorschlag: schnelles kleines Modell (z. B. Claude Haiku) für Funksprüche und Fragen (Latenz!), größeres Modell (z. B. Claude Sonnet) optional für Strategie-Abwägungen und Debriefs. **Aktuelle Modellnamen in der Anthropic-Doku nachschlagen**, nicht raten.
- **Prompt Caching** für System-Prompt + statischen Session-Kontext, um Latenz/Kosten zu senken. **Streaming**, damit TTS schon beim ersten Satz starten kann.
- **Tool Use statt Datendump:** Das Modell bekommt Tools wie `get_gaps()`, `get_fuel_status()`, `get_tyre_status()`, `get_pit_plan()`, `compare_strategies(stops)`, `get_rival(name)`, `get_weather()`, `get_last_laps(n)`, `get_corner_analysis()`, `remember_driver_note(text)`. Antworten der Tools sind die exakt berechneten Werte aus Abschnitt 6.
- **Instant Intents ohne LLM:** Häufige Fragen („Abstand nach vorne?“, „Wie viel Sprit?“, „Wie viele Runden noch?“) per Schlüsselwort-/Intent-Matching direkt aus dem Phrasebook beantworten → < 300 ms. Nur freie/komplexe Fragen gehen ans LLM.
- **Proaktive Calls:** Detektor-Events werden standardmäßig per Phrasebook-Template gesprochen (schnell, zuverlässig). Das LLM wird nur für komplexe Lagen genutzt (z. B. „SC + Regen + Gegner hat gestoppt“) und für das periodische Strategie-Update (Intervall konfigurierbar, Standard 1×/Runde statt alle 15 s).
- **System-Prompt (Deutsch), Kernpunkte:**
  - Du bist mein Renningenieur, Name konfigurierbar (Standard: „Max“).
  - Funkstil: **maximal 1–2 kurze Sätze, Zahlen zuerst**, Fahrername oder „okay“ als Einstieg wie im echten Funk, kein Smalltalk in kritischen Phasen, keine Emojis, keine Markdown-Ausgabe (wird vorgelesen).
  - Zahlen sprechfreundlich: „null Komma vier“, Rundenzeiten „eins zweiunddreißig Komma fünf“, Positionen „P3“ → „P drei“.
  - **Nutze ausschließlich Tool-Daten. Wenn ein Wert fehlt, sag es.** Keine erfundenen Gegnernamen oder Gaps.
  - Kenne Sim und Fahrzeugklasse (GT vs. Formel vs. Truck) und passe Vokabular an (kein ERS/DRS bei GT3).
  - Merke dir meine Aussagen zur Balance/zum Auto in diesem Rennen.
- **Fallback-Kette:** Claude nicht erreichbar/Timeout (> 2,5 s) → Phrasebook-Antwort „Moment, prüfe das“ + Instant-Intent-Antwort, wenn möglich. Fehler nie ins Audio durchreichen.
- **Kostenkontrolle:** Token-Zähler pro Session im Dashboard, Tageslimit konfigurierbar.
- **Debrief nach der Session:** Zusammenfassung (Stints, Spritverbrauch, beste Runden, Schwachkurven, Strategie-Rückblick) als Text im Dashboard + gespeichert.

---

## 10. Sprache: STT, TTS, Funk-Sound, PTT (`voice/`)

- **Push-to-Talk:** Tastatur-Hotkey und **Lenkradtaste** (evdev unter Linux, pygame/SDL als Fallback). Mapping im Dashboard per „Taste drücken“. Funktioniert, während das Spiel im Vordergrund ist (globaler Input, nicht Browser-Fokus!). Zusätzlich Mikro-Button im Dashboard.
- **STT lokal:** `faster-whisper` (Deutsch, Modellgröße konfigurierbar; GTX 1660 → CUDA möglich, sonst CPU mit `small`/`base` int8). Kurzer Funk-Kontext-Prompt („Box, Sprit, Reifen, Abstand…“) zur besseren Erkennung. Optional Cloud-STT als Alternative.
- **TTS:** Provider-Interface mit
  - **Piper** (lokal, kostenlos, deutsche Stimmen) als Standard,
  - **ElevenLabs** optional (wie im Original; Flash-Modell für niedrige Latenz),
  - weitere Provider leicht ergänzbar.
  Satzweises Streaming: erster Satz wird gesprochen, während der Rest noch generiert wird.
- **Funk-Effekt (`radio_fx.py`):** Bandpass ~300–3400 Hz, leichte Kompression/Sättigung, dezentes Rauschen, **Klick/Piepton am Anfang und Ende** („Squelch“). An/aus + Intensität einstellbar.
- **Ausgabe:** wählbares Audiogerät (PipeWire/sounddevice). Lautstärke separat vom Spiel. Optional „Ducking“-Hinweis in der Doku (Spiel leiser während Funk über PipeWire-Regeln).
- **Latenzziele:** Instant-Intent-Antwort < 1 s vom Loslassen der Taste bis Audio; LLM-Antwort < 2,5 s. Messe und zeige Latenzen im Dashboard (Debug-Panel).

---

## 11. Aufnahme, Replay, Simulator, Tests

- **Recorder:** speichert Rohpakete/Shared-Memory-Snapshots mit Zeitstempel (komprimiert, z. B. `.jsonl.zst` oder eigenes Binärformat) + Session-Metadaten. Ein-Klick-Aufnahme im Dashboard.
- **Replay:** Echtzeit, 2×, 10×, Sprung zu Runde X – über denselben Adapter-Pfad wie Live-Daten.
- **Fixtures:** Für jeden Adapter mindestens eine Test-Datei mit synthetischen, gemäß Spezifikation gebauten Paketen (du baust die Bytes selbst nach der Struct-Definition); echte Aufnahmen von mir kommen später dazu.
- **Szenario-Tests:** Simulator-Szenarien mit erwarteten Funksprüchen: „Undercut durch Rivalen“, „SC im Boxenfenster“, „Sprit knapp“, „Regen kommt“, „Flashback“, „Rennneustart“, „Quali-Push-Lap Funkstille“.
- **Engineer-Dry-Run:** CLI-Modus, der ein Replay abspielt und alle Funksprüche mit Zeitstempel als Text ausgibt (ohne Audio) → schnell beurteilbar.

---

## 12. Dashboard und Overlay (`web/`)

- **FastAPI + WebSocket**, Frontend als **eine einzelne, eigenständige HTML-Datei** pro Ansicht (kein Build-Step, Libraries höchstens per CDN). Dunkles Design im Stil eines F1-Kommandostands.
- **Dashboard-Ansichten:**
  - Live-Timing-Tabelle (Position, Name, Gap, Intervall, letzte Runde, Reifen + Alter, Stopps) – passt sich an Capabilities an.
  - Streckenkarte (aus XY-Positionen gelernt, Autos als Punkte, ich hervorgehoben).
  - Mein Auto: Reifen-Grafik mit Temps/Verschleiß/Druck, Sprit-Balken + Hochrechnung, ERS, Schäden.
  - Strategie-Panel: Plan A/B mit Ziel-Runden, Boxenfenster-Leiste, Rejoin-Prognose.
  - Funk-Log (Engineer + meine transkribierten Fragen), Latenzen.
  - Coaching-Seite: Kurvenliste mit Zeitverlust und Ursache, Vergleich mit meiner besten Runde (Speed/Gas/Bremse-Traces).
  - Einstellungen: Sim-Auswahl/Auto-Erkennung, Ports, PS5-IP, Sprache, Engineer-Name, Gesprächigkeit, TTS-Provider/Stimme, Funk-Effekt, PTT-Mapping, API-Keys (lokal in `.env`, nie im Browser speichern), Audiogerät.
- **Overlay (`overlay.html`)**: transparenter Hintergrund, Broadcast-Funk-Einblendung mit Teamfarbe, Wellenform, Live-Untertitel Wort für Wort. Als OBS-Browserquelle nutzbar. Unter Linux: Hinweis, dass ein Overlay über einem Vollbildspiel ggf. nur über OBS/Gamescope-Möglichkeiten geht – recherchieren und dokumentieren.
- Zweitgerät: Dashboard ist im LAN erreichbar (z. B. Tablet/Handy neben dem Lenkrad), Bind-Adresse konfigurierbar.

---

## 13. Fahrcoaching (`engineer/coaching.py`)

- Strecke in Kurven segmentieren (automatisch aus Lenkwinkel + Speed-Minima gelernt, nummeriert T1…Tn; Namen optional per Konfig).
- Pro Kurve vergleichen: aktuelle Runde vs. persönliche Bestrunde (oder Median der besten 3): Bremspunkt, min. Speed, Gas-Wiederaufnahme, Rollphasen, Zeitverlust.
- Erkennen: zu früh/zu spät gebremst, zu viel Speed abgebaut, spät am Gas, Blockieren (Radschlupf), Wheelspin, Unter-/Übersteuern (Gierrate vs. Lenkwinkel, wo verfügbar), Ausritte.
- Ausgabe: im Training/Zeitfahren als Funk („Kurve 7: Bremst zehn Meter zu früh, null Komma zwei liegen da“), im Rennen nur auf Anfrage oder sparsam. Ausführlich im Dashboard.
- Verknüpfung mit Reifenverschleiß: welche Gewohnheiten den limitierenden Reifen belasten.

---

## 14. Konfiguration

- `.env`: `ANTHROPIC_API_KEY`, optional `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`.
- `config/default.toml`: Sprache, Engineer-Name, Modellnamen, Gesprächigkeit, Strategie-Intervall, Reserve-Sprit, Ports je Sim (F1 20777, GT7 33739/33740, AC 9996, ACC 9000 …), Audio-Geräte, PTT-Mapping, Funk-Effekt.
- `config/sims/*.toml`: Sim-spezifische Defaults (Reifenfenster, Klassenvokabular, verfügbare Features).
- `config/tracks/*.toml`: Boxenverlust, SC-Faktor, Kurvennamen – teils gelernt und automatisch überschrieben.
- Ein **Start-Skript** für Linux (`start.sh`) und Windows (`start.ps1`), das venv prüft, Abhängigkeiten installiert und Server + Browser öffnet.

---

## 15. Meilensteine (jeweils mit Abnahmekriterium)

1. **M1 – Grundgerüst:** Projektstruktur, Datenmodell, Simulator, Dashboard zeigt simuliertes 20-Auto-Rennen live. ✅ `pytest` grün, Dashboard aktualisiert sich ≥ 10 Hz.
2. **M2 – Strategie + Detektoren + Phrasebook + lokales TTS:** Engineer spricht proaktiv auf Deutsch im Simulator, mit Funk-Effekt und Director-Timing. ✅ Dry-Run eines 20-Runden-Simulator-Rennens liefert plausible, nicht spammige Funksprüche.
3. **M3 – PTT + STT + Instant Intents + Claude mit Tool Use:** Fragen per Taste, Antworten mit echten Zahlen. ✅ Latenzziele aus Abschnitt 10 im Debug-Panel erreicht.
4. **M4 – Erste echte Sims:** F1 24/25 (UDP) und **GT7 (PS5)** inkl. Recorder/Replay. ✅ Parser-Tests mit Fixtures, Setup-Doku je Sim.
5. **M5 – Linux-Proton-Bridge + Assetto Corsa + iRacing + rFactor 2/LMU.** ✅ `docs/linux-proton.md` mit getesteter Anleitung (Testen übernehme ich, du lieferst Schritt-für-Schritt + Diagnose-Befehl `pitwall doctor`, der prüft, ob Daten ankommen).
6. **M6 – Coaching, Langzeitgedächtnis, Debrief, Overlay.**
7. **M7 – ETS2-Disponentenmodus.**
8. **M8 – Feinschliff:** Paketierung, README auf Deutsch + Englisch, Screenshots/GIF-Platzhalter, Fehlerbehandlung, Logging.

---

## 16. Qualitätsanforderungen

- Python 3.11+, Typ-Annotationen, `ruff` + `mypy` (oder `pyright`) sauber.
- Keine blockierenden Aufrufe im Event-Loop; CPU-lastiges (STT) in Thread/Prozess.
- Robust gegen kaputte/unerwartete Pakete (loggen, verwerfen, nie abstürzen), Spiel-Neustarts, Netzunterbrechungen.
- Ressourcenschonend: läuft neben dem Spiel auf i7 + GTX 1660 ohne FPS-Einbruch (STT nur bei PTT aktiv, kein Dauer-Mithören).
- Datenschutz: Mikrofon nur bei gedrückter Taste; API-Keys nur lokal.
- Gute Fehlermeldungen in der UI („Keine Daten von GT7 – PS5-IP prüfen, Heartbeat läuft, Port 33740 in firewalld offen?“).

---

## 17. Erste Aufgabe für dich jetzt

1. Recherchiere die aktuellen Telemetrie-Spezifikationen für F1 25, GT7, Assetto Corsa (Shared Memory + UDP 9996), iRacing SDK, rFactor 2/LMU Shared Memory und ETS2 SCS SDK sowie die besten Wege, Shared Memory unter Proton/Linux auszulesen.
2. Prüfe die aktuellen Claude-Modellnamen, Tool-Use- und Prompt-Caching-Doku sowie Piper-Stimmen für Deutsch.
3. Lege mir einen **Plan** vor: finale Ordnerstruktur, Bibliotheken mit Begründung, Datenmodell-Entwurf, Risiken (v. a. Proton-Bridge, GT7-Verschlüsselung, Latenz), und die Meilensteine M1–M8 mit Aufwandsschätzung.
4. Warte auf meine Freigabe, dann starte mit M1.
