# PitWallAC – In-Game-App für Assetto Corsa

Das Shared Memory von Assetto Corsa (2014) enthält nur das eigene Auto. Diese kleine Python-App
läuft *im Spiel* und schickt 10× pro Sekunde Position, Runden und Zeiten aller Autos als JSON an
`127.0.0.1:20791`. PIT WALL kennt damit Gegner, Abstände und Verkehr.

1. Ordner `PitWallAC` nach `<Assetto Corsa>/apps/python/` kopieren
   (Steam: Rechtsklick auf AC → Verwalten → Lokale Dateien durchsuchen).
2. Im Spiel: Optionen → General → UI Modules → **PitWallAC** anhaken.
3. Im Rennen in der rechten App-Leiste PitWallAC einblenden. Das Fenster zeigt an, ob gesendet wird.

**Fehlt das socket-Modul** („kein socket-Modul“): AC bringt Python 3.3 ohne `_socket` mit.
Lege `_socket.pyd` aus einer offiziellen Python-3.3.5-x64-Installation (python.org, PSF-Lizenz) in
`PitWallAC/stdlib64/`. Mit Content Manager + Custom Shaders Patch ist das Modul meist schon vorhanden.

Unter Linux/Proton funktioniert die App genauso – UDP an 127.0.0.1 erreicht PIT WALL direkt,
ohne Bridge.
