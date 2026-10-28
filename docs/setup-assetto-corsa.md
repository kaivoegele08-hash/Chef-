# Einrichtung: Assetto Corsa (2014)

PIT WALL nutzt bis zu drei Quellen gleichzeitig und kombiniert sie automatisch:

| Quelle | Liefert | Linux/Proton |
|---|---|---|
| **Shared Memory** (`acpmf_physics/graphics/static`) | Sprit, Reifen (Temperatur, Druck, Verschleiß), Bremsen, Schäden, Flaggen, Position | über `pitwall-relay.exe` |
| **UDP 9996** (Remote Telemetry) | Tempo, Pedale, Rundenzeiten – kein Sprit, keine Reifen | funktioniert sofort |
| **In-Game-App PitWallAC** | alle Autos: Positionen, Runden, Zeiten, Boxengasse → Gaps & Gegner | funktioniert sofort |

Ohne die App kennt PIT WALL keine Gegner – AC schreibt nur das eigene Auto ins Shared Memory.

## 1. In-Game-App installieren (empfohlen)

1. `pitwall/bridge/ac_app/PitWallAC` nach `<Assetto Corsa>/apps/python/PitWallAC` kopieren
   (Steam → AC → Verwalten → Lokale Dateien durchsuchen).
2. Spiel starten → Optionen → General → UI Modules → **PitWallAC** anhaken.
3. Im Auto die App in der rechten Leiste einblenden: „PIT WALL: N Autos, … Pakete“.

Zeigt die App „kein socket-Modul“, siehe `pitwall/bridge/ac_app/README.md`
(mit Content Manager + Custom Shaders Patch ist das Modul meist vorhanden).

## 2. Shared Memory

- **Windows:** nichts zu tun.
- **Linux/Proton:** Startoption `~/.local/bin/pitwall-launch %command%` (siehe [linux-proton.md](linux-proton.md)).

## 3. Starten und prüfen

```bash
pitwall run --sim assetto_corsa
pitwall doctor --sim assetto_corsa --seconds 10
```

Im Dashboard unter „Quelle & Datenfelder“ siehst du, welche der drei Quellen aktiv sind.

## Grenzen

- Rundenrennen vs. Zeitrennen erkennt AC über `isTimedRace`; bei Zeitrennen rechnet die Strategie über die Restzeit.
- Reifenverschleiß meldet AC als „Restgrip“ – PIT WALL rechnet ihn grob in Prozent um. Für Strategie zählen vor allem Rundenzeiten.
- Gaps berechnet PIT WALL selbst (Zeit an Streckenpunkten) – ohne App nur zum eigenen Auto, also keine.
