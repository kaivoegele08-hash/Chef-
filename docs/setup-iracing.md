# Einrichtung: iRacing

iRacing veröffentlicht alle Daten über das irsdk-Shared-Memory (`Local\IRSDKMemMapFileName`).
PIT WALL liest die Variablen nach Namen (selbstbeschreibende Var-Header) und die Session-Info (YAML).

## 1. Windows

Nichts einzurichten – iRacing starten, auf die Strecke fahren, `pitwall run --sim iracing`.

## 2. Linux / Proton

iRacing läuft je nach Installation als Steam-Spiel (App-ID 266410) oder als Non-Steam-Shortcut.
Der Relay muss im **selben Prefix** laufen:

- Steam-Version: Startoption `~/.local/bin/pitwall-launch %command%`
- Non-Steam-Shortcut: Startoption des Shortcuts `~/.local/bin/pitwall-launch --sim iracing %command%`

Details in [linux-proton.md](linux-proton.md). Ob iRacing unter Proton aktuell spielbar ist
(Anti-Cheat), ändert sich – bitte selbst prüfen.

## 3. Was PIT WALL nutzt

- Positionen, Runden, Zeiten, Boxengasse aller Autos (`CarIdx*`), Fahrernamen/Klassen aus der Session-Info
- Sprit (`FuelLevel`, Tankgröße aus `DriverCarFuelMaxLtr` × `DriverCarMaxFuelPct`)
- Flaggen inkl. Caution (= Safety Car), Meatball, Schwarz, Weiß, „one lap to green“
- Incidents, Wetter (`TrackWetness`, `Precipitation`), Boxen-Tempolimit

**Reifen:** iRacing misst Temperatur und Verschleiß nur in der Box (die Werte stehen während der Fahrt still).
PIT WALL meldet sie deshalb nur beim Stopp; die Strategie stützt sich auf Rundenzeiten und Sprit.

## 4. Boxen-Befehle (optional, standardmäßig aus)

PIT WALL kann das Pit-Menü setzen (Tankmenge, vier Reifen, Fast Repair, Tear-off) – über
`POST /api/sim/command` mit `{"name": "fuel", "liters": 40}` bzw. `tyres`, `no_tyres`, `fast_repair`,
`tearoff`, `clear`. Das ist **aus**, bis du es erlaubst:
Einstellungen → „iRacing Pit-Commands erlauben“ (`iracing.allow_pit_commands = true`).
Funktioniert nur unter Windows – unter Proton erreicht ein natives Linux-Programm iRacings Fenster nicht.
