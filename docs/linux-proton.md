# PIT WALL unter Linux (Bazzite) mit Proton-Spielen

Diese Anleitung ist für **Bazzite** geschrieben (immutables Fedora, KDE, Steam als rpm), gilt aber für jedes
Linux mit Steam und Proton. Kurzfassung:

| Spiel | Weg zu PIT WALL | Bridge nötig? |
|---|---|---|
| F1 24 / F1 25 | UDP-Telemetrie (Port 20777) | nein |
| Gran Turismo 7 (PS5) | UDP im LAN (Port 33740) | nein, aber Firewall |
| Assetto Corsa | Shared Memory **+** UDP 9996 **+** In-Game-App PitWallAC | für SHM ja |
| ACC | Broadcasting-UDP (Port 9000) **+** Shared Memory | für SHM ja |
| iRacing | Shared Memory (irsdk) | ja |
| rFactor 2 / Le Mans Ultimate | Shared Memory (Plugin bzw. `LMU_Data`) | ja |
| ETS2 / ATS | nativer Linux-Build + SCS-Plugin → `/dev/shm/SCSTelemetry` | nein |

---

## 1. Installation (ohne rpm-ostree-Layering)

Auf Bazzite wird nichts ins Basissystem installiert. PIT WALL läuft nativ im Home-Verzeichnis mit
[uv](https://docs.astral.sh/uv/):

```bash
brew install uv                          # oder: curl -LsSf https://astral.sh/uv/install.sh | sh
git clone <repo> ~/pitwall && cd ~/pitwall
uv venv --python 3.12 && uv pip install -e '.[all]'
uv run pitwall voices download de_DE-thorsten-medium
uv run pitwall doctor
```

`./start.sh` erledigt das beim ersten Start automatisch.

**distrobox** brauchst du nur zum *Bauen* (z. B. `pitwall-relay.exe` neu kompilieren):

```bash
distrobox create -n pitwall -i registry.fedoraproject.org/fedora-toolbox:43
distrobox enter pitwall -- sudo dnf -y install mingw64-gcc
distrobox enter pitwall -- ./scripts/build-relay.sh
```

Eine fertige `pitwall-relay.exe` liegt bereits in `pitwall/bridge/`.

---

## 2. Warum eine Bridge? Was macht `pitwall-relay.exe`?

Windows-Spiele unter Proton legen ihr Shared Memory (`Local\acpmf_physics`, `Local\IRSDKMemMapFileName`,
`$rFactor2SMMP_Telemetry$`, `LMU_Data` …) im **wineserver** an. Ein natives Linux-Programm sieht diese
Speicherbereiche nicht – es gibt keine Datei unter `/dev/shm`.

`pitwall-relay.exe` ist ein winziges Windows-Programm (45 KB, C, Quelltext in
`pitwall/bridge/pitwall_relay.c`), das **im selben Proton-Prefix wie das Spiel** läuft:

1. Es wartet, bis das Spiel seine Maps anlegt (`OpenFileMapping`, nur lesend).
2. Es schickt geänderte 4-KB-Seiten per UDP an `127.0.0.1:20790` (Protokoll `PWR1`).
3. PIT WALL setzt die Seiten wieder zu einer Kopie der Map zusammen.

Es greift nicht in das Spiel ein, legt keine Maps an und braucht keine Rechte. Loopback-UDP funktioniert
aus Proton heraus ohne Firewall-Freigabe.

### 2.1 Startoption setzen (einmal pro Spiel)

```bash
mkdir -p ~/.local/bin && ln -sf ~/pitwall/scripts/pitwall-launch ~/.local/bin/pitwall-launch
```

Steam → Spiel → Eigenschaften → Allgemein → **Startoptionen**:

```
~/.local/bin/pitwall-launch %command%
```

`pitwall-launch` erkennt das Spiel an der Steam-App-ID (AC 244210, ACC 805550, iRacing 266410, rF2 365960,
LMU 2399420), kopiert den Relay nach `~/.local/share/pitwall/` (dort ist er im Steam-Container sichtbar)
und lässt ihn von Proton selbst starten (`PROTON_REMOTE_DEBUG_CMD`). Proton startet ihn **vor** dem Spiel
und beendet ihn mit dem Spiel. `pitwall doctor` zeigt die passende Zeile für jedes installierte Spiel an.

Varianten:

| Situation | Startoption |
|---|---|
| Standard | `~/.local/bin/pitwall-launch %command%` |
| Sim festlegen | `~/.local/bin/pitwall-launch --sim acc %command%` |
| Proton-Start klappt nicht | `PITWALL_LAUNCH_MODE=parallel ~/.local/bin/pitwall-launch %command%` (startet den Relay nach 10 s per `proton run`) |
| Ohne Wrapper | `PROTON_REMOTE_DEBUG_CMD="/var/home/DEINNAME/.local/share/pitwall/pitwall-relay.exe --sim ac" %command%` |
| Manuell testen | `protontricks-launch --appid 244210 ~/.local/share/pitwall/pitwall-relay.exe --sim ac --verbose` |

Log: `~/.local/share/pitwall/relay-<appid>.log`. Mehr Details: `PROTON_LOG=1 %command%` → `~/steam-<appid>.log`.

### 2.2 Prüfen

```bash
uv run pitwall doctor --sim assetto_corsa --seconds 10
```

Im Dashboard (Status-Kachel → Quelle) erscheinen „Relay empfängt“ und die angekündigten Maps.
Wenn nichts ankommt:

- `ss -uanp | grep 20790` – lauscht PIT WALL?
- Steht im Relay-Log `pitwall-relay 1.x: N Maps → 127.0.0.1:20790`? Wenn nicht: Startoption prüfen.
- iRacing über den eigenen Launcher statt Steam: Der Relay muss im Prefix des Non-Steam-Shortcuts laufen
  (Startoption des Shortcuts oder `protontricks-launch --appid <shortcut-id>`).

### 2.3 Alternative: Datei-Spiegel in `/dev/shm`

Wer bereits [simshmbridge](https://github.com/spacefreak18/simshmbridge) oder simd nutzt: PIT WALL liest
auch Dateien wie `/dev/shm/acpmf_physics` direkt (`shm.mirror_dir` in der Konfiguration). Veraltete
Dateien nach Spielende werden nach 30 s ohne Änderung ignoriert.

---

## 3. Push-to-Talk mit dem Lenkrad (evdev)

systemd vergibt für Joysticks und Lenkräder (`ID_INPUT_JOYSTICK=1`) automatisch Leserechte an den
angemeldeten Benutzer (`uaccess`). **Lenkradtasten funktionieren deshalb meist ohne weitere Schritte.**

Prüfen: `uv run pitwall doctor` → Abschnitt „Push-to-Talk“, oder `getfacl /dev/input/by-id/*event-joystick`.

Wird das Lenkrad nicht als Joystick erkannt (z. B. Logitech RS50 ohne Treiber), gib nur dieses eine Gerät frei –
**nicht** die ganze Gruppe `input` (die erlaubt das Mitlesen aller Tastaturen):

```bash
# Vendor/Product-ID herausfinden (Logitech = 046d)
grep -H . /sys/class/input/event*/device/id/{vendor,product} 2>/dev/null | paste - - | grep -i 046d
sudo tee /etc/udev/rules.d/70-pitwall-wheel.rules <<'RULE'
SUBSYSTEM=="input", ATTRS{idVendor}=="046d", ATTRS{idProduct}=="XXXX", TAG+="uaccess"
RULE
sudo udevadm control --reload && sudo udevadm trigger
```

Nur wenn das nicht geht: `grep -E '^input:' /usr/lib/group | sudo tee -a /etc/group && sudo usermod -aG input $USER`
und neu anmelden.

Die Funktaste anlernen: Dashboard → Einstellungen → **Funktaste anlernen** → Taste am Lenkrad drücken.
Das Mikrofon ist nur offen, solange die Taste gedrückt ist.

**Logitech RS50:** Für FFB/TrueForce gibt es den Community-Treiber `mescon/logitech-trueforce-linux-driver`.
Dessen Relay belegt UDP 20780–20784 – PIT WALL nutzt 20790/20791, es gibt keinen Konflikt.

---

## 4. Audio: Ausgabegerät und Ducking

- Ausgabegerät: Dashboard → Einstellungen → Audio, oder `pitwall devices`. Bei PipeWire funktionieren
  Geräte wie `pipewire` oder `pulse`.
- PIT WALL spielt Funksprüche mit der Rolle **Assistant** ab.

**Spiel automatisch leiser (optional, WirePlumber ≥ 0.5):**
`~/.config/wireplumber/wireplumber.conf.d/60-pitwall-ducking.conf`

```
wireplumber.profiles = { main = { policy.linking.role-based.loopbacks = required } }
wireplumber.settings = {
  node.stream.default-media-role = "Multimedia"
  linking.role-based.duck-level = 0.3
}
wireplumber.components = [
  { type = virtual, provides = policy.linking.role-based.loopbacks
    requires = [ loopback.sink.role.multimedia loopback.sink.role.assistant ] }
  { name = libpipewire-module-loopback, type = pw-module-client
    arguments = { node.name = "loopback.sink.role.multimedia", node.description = "Multimedia"
      capture.props = { device.intended-roles = [ "Music" "Movie" "Game" "Multimedia" ]
        policy.role-based.priority = 10
        policy.role-based.action.same-priority = "mix"
        policy.role-based.action.lower-priority = "mix" } }
    provides = loopback.sink.role.multimedia }
  { name = libpipewire-module-loopback, type = pw-module-client
    arguments = { node.name = "loopback.sink.role.assistant", node.description = "Race Engineer"
      capture.props = { device.intended-roles = [ "Assistant" ]
        policy.role-based.priority = 40
        policy.role-based.action.same-priority = "mix"
        policy.role-based.action.lower-priority = "duck" } }
    provides = loopback.sink.role.assistant }
]
```

Dann `systemctl --user restart wireplumber`. WirePlumber selbst warnt, dass diese Policy für Desktops
nicht überall passt – ohne die Datei funktioniert alles, nur ohne Ducking.

---

## 5. Firewall (firewalld)

Alles über `127.0.0.1` (Relay, AC 9996, ACC 9000, AC-App 20791) braucht **keine** Freigabe.
Nur Daten aus dem LAN brauchen offene Ports:

```bash
Z="$(firewall-cmd --get-default-zone)"
sudo firewall-cmd --permanent --zone="$Z" --add-port=33740/udp   # GT7 von der PS5
sudo firewall-cmd --permanent --zone="$Z" --add-port=20777/udp   # F1 von Konsole/zweitem PC
sudo firewall-cmd --permanent --zone="$Z" --add-port=8077/tcp    # Dashboard auf Tablet/Handy
sudo firewall-cmd --reload
```

---

## 6. Overlay über dem Spiel

| Weg | Hinweise |
|---|---|
| **Zweitgerät** (Tablet/Handy): `http://<PC-IP>:8077` | am robustesten |
| **KDE**: Spiel im randlosen Fenster, Browserfenster mit `http://127.0.0.1:8077/overlay` + KWin-Fensterregel „Immer im Vordergrund“, „Keine Titelleiste“, Deckkraft | bei echtem Vollbild liegt das Spiel darüber |
| **Steam-Overlay** (Umschalt+Tab): Webbrowser öffnen, Overlay-URL laden, anheften | ohne Transparenz |
| **OBS**: Browserquelle `http://127.0.0.1:8077/overlay` (1920×1080, transparent) | für Streams |
| **Gaming Mode (gamescope)** | fremde Fenster werden nicht über das Spiel gelegt – Funk (Audio) funktioniert trotzdem |

Das Overlay ist optional – der Engineer spricht immer über Audio.

---

## 7. Autostart (optional)

```ini
# ~/.config/systemd/user/pitwall.service
[Unit]
Description=PIT WALL Renningenieur
After=pipewire.service

[Service]
WorkingDirectory=%h/pitwall
ExecStart=%h/.local/bin/uv run pitwall run
Restart=on-failure

[Install]
WantedBy=default.target
```

`systemctl --user daemon-reload && systemctl --user enable --now pitwall`

---

## 8. Fehlersuche

| Symptom | Ursache / Lösung |
|---|---|
| `doctor`: „Relay-Port 20790 belegt“ | PIT WALL läuft schon (z. B. als Dienst) |
| Relay-Log leer | Startoption nicht übernommen (Tippfehler, fehlendes `%command%`) |
| Relay sendet, aber „unvollständig“ | erste Vollbilder kommen alle 2 s – kurz warten |
| AC: keine Gegner | In-Game-App PitWallAC aktivieren (docs/setup-assetto-corsa.md) |
| ACC: nur eigenes Auto | `broadcasting.json` im Prefix: `steamapps/compatdata/805550/pfx/drive_c/users/steamuser/Documents/Assetto Corsa Competizione/Config/` |
| LMU: keine Daten | Einstellungen → Gameplay → „Enable Plugins“ an, Spiel neu starten |
| Kein Ton | `pitwall say "Funkcheck"` und `pitwall devices` |
