# Einrichtung: EA SPORTS F1 24 / F1 25 (PC und PS5)

PIT WALL liest die offizielle UDP-Telemetrie des Spiels. Unterstützt werden die
UDP-Formate **2024**, **2025** und **2026** (F1 25 mit „2026 Season Pack“,
24 Autos). Das Format wird automatisch an jedem Paket erkannt.

## 1. Im Spiel

Hauptmenü → **Einstellungen** → **Telemetrie-Einstellungen** (unterster Eintrag):

| Einstellung | Wert |
|---|---|
| UDP-Telemetrie | **An** |
| UDP-Broadcast-Modus | Aus (oder An, wenn mehrere Programme mitlesen sollen) |
| UDP-IP-Adresse | **IP des PCs**, auf dem PIT WALL läuft (PC-Spiel auf demselben Rechner: `127.0.0.1`) |
| UDP-Port | **20777** |
| UDP-Senderate | **20 Hz** (30 Hz geht auch; 60 Hz nur, wenn du die Daten auch für ein Motion-Rig brauchst) |
| UDP-Format | **2025** (F1 25), **2024** (F1 24) oder **2026** (Season Pack) |
| Deine Telemetrie | egal – betrifft nur, was *andere* von dir sehen |

Die IP deines PCs: Linux `ip -4 addr` (z. B. `192.168.1.20`), Windows `ipconfig`.
Tipp: Im Router eine feste IP (DHCP-Reservierung) für den PC vergeben.

## 2. Firewall (Bazzite / Fedora)

```bash
sudo firewall-cmd --permanent --add-port=20777/udp
sudo firewall-cmd --reload
```

(Spielt F1 auf demselben PC über Proton mit `127.0.0.1`, ist keine Freigabe nötig.)

Windows: Beim ersten Start fragt die Windows-Firewall → „Private Netzwerke“ erlauben.

## 3. Starten

```bash
pitwall run --sim f1        # oder einfach: pitwall run  (Auto-Erkennung)
pitwall doctor --sim f1     # prüft, ob Pakete ankommen
```

## 4. Mehrere Programme gleichzeitig (SimHub, Crew Chief …)

Entweder im Spiel den Broadcast-Modus einschalten oder PIT WALL weiterleiten lassen
(`~/.config/pitwall/config.toml`):

```toml
[f1]
forward = ["127.0.0.1:20778"]   # SimHub o. ä. dann auf 20778 lauschen lassen
```

## Was der Engineer in F1 kann

Volle Gegnerdaten (Abstände, Reifen + Alter, Boxenstopps), Safety Car/VSC inkl.
„kommt diese Runde rein“, Gelb pro Sektor (Marshal-Zonen), Strafen, Track Limits,
Wettervorhersage, Startampel/Lights out, Flashback-Erkennung, ERS/DRS, Schäden,
Reifenverschleiß. Sprit wird in **kg** angegeben (wie im Spiel).

## Fehlersuche

* „Keine Daten“: IP im Spiel prüfen, Port, Firewall, PC und PS5 im selben Netz
  (kein Gast-WLAN mit Client-Isolation).
* „unbekanntes UDP-Format“ im Log: Im Spiel 2024, 2025 oder 2026 wählen.
* Online-Rennen: Bei Gegnern mit „Telemetrie: eingeschränkt“ fehlen deren Sprit-
  und Schadenswerte (das Spiel sendet 0). Reifen und Abstände sind trotzdem da.
