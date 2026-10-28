# Einrichtung: rFactor 2 und Le Mans Ultimate

Beide Spiele nutzen dieselbe ISI-Datenstruktur. PIT WALL dekodiert sie mit einem gemeinsamen Parser.

## Le Mans Ultimate

LMU bringt seit Version 1.2 ein eigenes Shared Memory (`LMU_Data`) mit – keine DLL nötig.

1. LMU → Einstellungen → Gameplay → **Enable Plugins = An**, Spiel neu starten.
2. Windows: fertig. Linux/Proton: Startoption `~/.local/bin/pitwall-launch %command%` ([linux-proton.md](linux-proton.md)).
3. `pitwall run --sim lmu`

Zusätzlich zu Sprit, Reifen, Schäden und Klassement nutzt PIT WALL bei LMU:
Virtual Energy (Hypercar), Batterie-Ladestand, ungültige Runden, TC/ABS/Motor-Map-Stufen und Track-Limit-Punkte.

Ist das rF2-Plugin zusätzlich installiert, bevorzugt PIT WALL trotzdem `LMU_Data`.

## rFactor 2

rFactor 2 braucht das **rF2SharedMemoryMapPlugin** (The Iron Wolf, kostenlos):

1. `rFactor2SharedMemoryMapPlugin64.dll` von der Release-Seite des Projekts nach `<rFactor 2>/Bin64/Plugins/` kopieren.
2. In `<rFactor 2>/UserData/player/CustomPluginVariables.JSON` aktivieren:
   ```json
   "rFactor2SharedMemoryMapPlugin64.dll": { " Enabled": 1 }
   ```
   (Das Leerzeichen vor `Enabled` gehört dazu.)
3. Linux/Proton: Startoption `~/.local/bin/pitwall-launch %command%`.
4. `pitwall run --sim rfactor2`

## Was PIT WALL nutzt

- Klassement, Abstände (vom Spiel berechnet), Sektorzeiten, Boxenstopps, Ausfälle aller Autos (Scoring, 5 Hz)
- Eigenes Auto (Telemetrie, ~50 Hz): Sprit, Reifentemperatur innen/mitte/außen + Karkasse, Druck, Verschleiß,
  Bremsentemperatur, Schäden, Reifenplatzer
- Full Course Yellow / Safety Car, lokale Gelbphasen pro Sektor, Startampel, Regen und Streckennässe

Hinweise: Die Fahrzeugreihenfolge in Telemetrie und Scoring ist verschieden – PIT WALL ordnet über die
Fahrzeug-ID zu. Halb geschriebene Datensätze (Versionszähler ungleich) werden verworfen.
