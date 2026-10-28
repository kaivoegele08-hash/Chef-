# Einrichtung: Assetto Corsa Competizione

| Quelle | Liefert | Linux/Proton |
|---|---|---|
| **Broadcasting-API** (UDP 9000) | Klassement, Namen, Rundenzeiten, Sektoren, Boxenstatus **aller** Autos, Session-Phase, Wetter | funktioniert sofort (127.0.0.1) |
| **Shared Memory** | Sprit (inkl. Verbrauch/Runde), Reifen, Bremsen, Schäden, Flaggen je Sektor, Regen-Prognose 10/30 min, Pflichtstopp | über `pitwall-relay.exe` |

Beides zusammen ist ideal. Nur Broadcasting reicht für Gaps und Gegner, aber ohne Sprit und Reifen.

## 1. broadcasting.json

Datei (UTF-16, mit einem Editor wie Kate öffnen):

- Windows: `Dokumente\Assetto Corsa Competizione\Config\broadcasting.json`
- Proton: `~/.local/share/Steam/steamapps/compatdata/805550/pfx/drive_c/users/steamuser/Documents/Assetto Corsa Competizione/Config/broadcasting.json`

```json
{
    "updListenerPort": 9000,
    "connectionPassword": "asd",
    "commandPassword": ""
}
```

(`updListenerPort` ist von Kunos wirklich so geschrieben.) Danach ACC **neu starten**.

Das Passwort trägst du in PIT WALL unter Einstellungen → ACC ein (oder `ACC_BROADCAST_PASSWORD` in `.env`).
PIT WALL meldet sich nur lesend an – es steuert weder Kamera noch HUD.

## 2. Shared Memory

- **Windows:** nichts zu tun.
- **Linux/Proton:** Startoption `~/.local/bin/pitwall-launch %command%` ([linux-proton.md](linux-proton.md)).

## 3. Starten

```bash
pitwall run --sim acc
pitwall doctor --sim acc --seconds 10
```

## Hinweise

- ACC-Rennen sind Zeitrennen: Die Strategie rechnet über die Restzeit; Sprit-Prognosen nutzen den
  Verbrauch pro Runde aus dem Spiel und die eigene Messung.
- Reifentemperaturen: ACC liefert die Kerntemperatur (das, was das Spiel anzeigt). Das Zielfenster
  steht in `config/sims/acc.toml` (70–95 °C, Druck 27,3–28,0 psi heiß).
- LAN: ACC kann auf einem anderen PC laufen → `acc.broadcast_host` auf dessen IP, dort UDP 9000 freigeben.
