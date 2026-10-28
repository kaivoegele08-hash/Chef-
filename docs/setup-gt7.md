# Einrichtung: Gran Turismo 7 (PS5)

GT7 sendet Telemetrie nur, solange ein Programm im Netz regelmäßig einen
„Heartbeat“ an die PS5 schickt. Das übernimmt PIT WALL automatisch.
**Im Spiel muss nichts eingestellt werden.**

## 1. PS5-IP herausfinden

* Dashboard → **Einstellungen** → „PS5-IP (GT7)“ → **Suchen** (sucht per
  PlayStation-Discovery und Heartbeat-Broadcast), oder
* auf der PS5: *Einstellungen → Netzwerk → Verbindungsstatus anzeigen*.

Am besten im Router eine feste IP (DHCP-Reservierung) für die PS5 vergeben und
diese eintragen:

```toml
# ~/.config/pitwall/config.toml
[gt7]
ps5_ip = "192.168.1.50"
heartbeat = "~"     # A | B | ~   (~ liefert die meisten Felder)
```

## 2. Firewall (Bazzite / Fedora)

```bash
sudo firewall-cmd --permanent --add-port=33740/udp
sudo firewall-cmd --reload
```

## 3. Starten

```bash
pitwall run --sim gt7
pitwall doctor --sim gt7
```

## Was GT7 liefert – und was nicht

| vorhanden | fehlt (Spiel liefert es nicht) |
|---|---|
| Geschwindigkeit, Drehzahl, Gang, Gas/Bremse | Gegner, Abstände, Positionen anderer |
| Sprit (Liter) und Tankgröße | Reifenverschleiß, Reifendruck |
| Reifen-Oberflächentemperaturen | Schäden, Flaggen, Safety Car |
| Runde, Rundenzahl, letzte/beste Runde | Strecken-ID, Streckenlänge |
| Position vor dem Start (je nach Modus live) | Wetter |

Darum spricht der Engineer bei GT7 vor allem über **Sprit, Reifentemperaturen,
Rundenzeiten, Konstanz und Coaching**. Fragst du nach dem Abstand, sagt er ehrlich:
„Dazu habe ich keine Daten aus diesem Spiel.“

Streckenlänge und Kurven lernt PIT WALL in der ersten vollen Runde. Die Strecke
wird über die Position der Start/Ziel-Linie wiedererkannt, gelernte Daten bleiben
also beim nächsten Mal erhalten.

## Fehlersuche

* Keine Daten: PS5 an, GT7 läuft (Telemetrie kommt auch im Menü nur eingeschränkt),
  IP richtig, Port 33740/udp offen, PC und PS5 im selben Netz.
* Nur ein Programm sollte Heartbeats senden. Laufen SimHub/gt7dashboard parallel,
  dort den Heartbeat abschalten.
* Paketverlust bei WLAN: PS5 per LAN-Kabel anschließen.
