"""``pitwall doctor``: prüft Installation, Rechte, Ports, Proton-Setup und ob Spieldaten ankommen.

Alles nur lesend und lokal – kein Netzwerkzugriff nach außen, keine kostenpflichtigen API-Aufrufe.
"""

from __future__ import annotations

import asyncio
import contextlib
import grp
import importlib.util
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"
_SYMBOL = {OK: "✓", WARN: "!", FAIL: "✗", INFO: "·"}
_COLOR = {OK: "\033[32m", WARN: "\033[33m", FAIL: "\033[31m", INFO: "\033[36m"}

# Steam-App-IDs → (Adapter, Anzeigename)
STEAM_GAMES = {
    244210: ("assetto_corsa", "Assetto Corsa"),
    805550: ("acc", "Assetto Corsa Competizione"),
    266410: ("iracing", "iRacing"),
    365960: ("rfactor2", "rFactor 2"),
    2399420: ("lmu", "Le Mans Ultimate"),
    2488620: ("f1", "F1 24"),
    3059520: ("f1", "F1 25"),
    227300: ("ets2", "Euro Truck Simulator 2"),
    270880: ("ets2", "American Truck Simulator"),
}
# Adapter → (Protokoll, Port, Richtung)
PORTS = {"f1": ("udp", "f1.port", 20777), "gt7": ("udp", None, 33740), "relay": ("udp", None, 20790),
         "acext": ("udp", None, 20791), "web": ("tcp", "web.port", 8077)}
RELAY_SIMS = {"assetto_corsa": "ac", "acc": "acc", "iracing": "iracing", "rfactor2": "rf2", "lmu": "lmu",
              "ets2": "ets2"}


@dataclass
class Check:
    group: str
    status: str
    title: str
    detail: str = ""
    fix: str = ""


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, group: str, status: str, title: str, detail: str = "", fix: str = "") -> None:
        self.checks.append(Check(group, status, title, detail, fix))

    @property
    def failures(self) -> int:
        return sum(1 for c in self.checks if c.status == FAIL)

    def render(self, color: bool = True) -> str:
        out: list[str] = []
        group = None
        for c in self.checks:
            if c.group != group:
                group = c.group
                out.append(f"\n\033[1m{group}\033[0m" if color else f"\n{group}")
            sym = _SYMBOL[c.status]
            if color:
                sym = f"{_COLOR[c.status]}{sym}\033[0m"
            line = f"  {sym} {c.title}"
            if c.detail:
                line += f" – {c.detail}"
            out.append(line)
            if c.fix and c.status in (WARN, FAIL):
                out.extend(f"      → {fl}" for fl in c.fix.splitlines())
        return "\n".join(out)


# ----------------------------------------------------------------- System


def _is_bazzite() -> bool:
    try:
        text = Path("/etc/os-release").read_text()
    except OSError:
        return False
    return "bazzite" in text.lower()


def check_system(r: Report) -> None:
    g = "System"
    r.add(g, OK if sys.version_info >= (3, 11) else FAIL, f"Python {platform.python_version()}",
          fix="PIT WALL braucht Python ≥ 3.11 (z. B. `uv python install 3.12`).")
    osname = platform.system()
    if osname == "Linux":
        immutable = Path("/run/ostree-booted").exists()
        distro = "Bazzite" if _is_bazzite() else platform.freedesktop_os_release().get("PRETTY_NAME", "Linux") \
            if hasattr(platform, "freedesktop_os_release") else "Linux"
        r.add(g, INFO, f"{distro}", "immutables System (rpm-ostree)" if immutable else "")
        if immutable:
            r.add(g, INFO, "Empfehlung", "Installation per `uv` im Home-Verzeichnis – kein rpm-ostree-Layering nötig")
    else:
        r.add(g, INFO, f"{osname} {platform.release()}")


def check_python_packages(r: Report) -> None:
    g = "Python-Pakete"
    pkgs = [
        ("anthropic", "llm", "Claude als Gehirn (ohne: Phrasebook-Modus)"),
        ("sounddevice", "audio", "Audio-Ausgabe/Mikrofon"),
        ("faster_whisper", "stt", "lokale Spracherkennung"),
        ("piper", "tts", "lokale Stimme (Piper)"),
        ("evdev", "ptt-linux", "Lenkradtasten unter Linux"),
        ("pygame", "ptt-sdl", "Lenkradtasten über SDL (Windows/Linux)"),
    ]
    for mod, extra, what in pkgs:
        found = importlib.util.find_spec(mod) is not None
        if mod == "evdev" and sys.platform != "linux":
            continue
        r.add(g, OK if found else WARN, mod, what, fix=f"uv pip install -e '.[{extra}]'")


# ----------------------------------------------------------------- Secrets


def check_secrets(r: Report, cfg: Config) -> None:
    from .config import secret

    g = "Claude / Schlüssel"
    key = secret("ANTHROPIC_API_KEY")
    if key:
        masked = key[:7] + "…" + key[-4:] if len(key) > 12 else "gesetzt"
        r.add(g, OK, "ANTHROPIC_API_KEY", masked)
    else:
        r.add(g, WARN, "ANTHROPIC_API_KEY fehlt", "Engineer läuft im Phrasebook-Modus (offline, ohne freie Fragen)",
              fix="Im Dashboard unter Einstellungen eintragen oder in ~/.config/pitwall/.env setzen.")
    r.add(g, INFO, "Modelle", f"Funk: {cfg.get('brain.radio_model')}, Strategie: {cfg.get('brain.strategy_model')}")
    if secret("ELEVENLABS_API_KEY"):
        r.add(g, OK, "ELEVENLABS_API_KEY", "gesetzt (optional, kostenpflichtig)")


# ------------------------------------------------------------------- Audio


def check_audio(r: Report, cfg: Config) -> None:
    g = "Audio & Stimme"
    if importlib.util.find_spec("sounddevice") is None:
        r.add(g, WARN, "Keine Audioausgabe", "sounddevice fehlt", fix="uv pip install -e '.[audio]'")
    else:
        try:
            import sounddevice as sd

            out = sd.query_devices(kind="output")
            r.add(g, OK, "Ausgabegerät", str(out.get("name", "?")))
            try:
                inp = sd.query_devices(kind="input")
                r.add(g, OK, "Mikrofon", str(inp.get("name", "?")))
            except Exception:
                r.add(g, WARN, "Kein Mikrofon", fix="Headset anschließen; ohne Mikro keine Sprachfragen.")
        except Exception as exc:
            r.add(g, FAIL, "PortAudio-Fehler", str(exc),
                  fix="Linux: PipeWire/PulseAudio läuft? `pactl info`. Bazzite: ist standardmäßig vorhanden.")
    if sys.platform == "linux":
        r.add(g, OK if shutil.which("pw-cli") or shutil.which("pactl") else WARN, "PipeWire/PulseAudio",
              "für Ducking der Spiel-Lautstärke (docs/linux-proton.md)")
    from .voice.tts import voices_dir

    engine = str(cfg.get("voice.tts_provider", "piper"))
    r.add(g, INFO, "Stimme", engine)
    if engine in ("piper", "elevenlabs"):
        voice = str(cfg.get("voice.piper_voice" if cfg.language == "de" else "voice.piper_voice_en", ""))
        path = voices_dir(cfg) / f"{voice}.onnx"
        if path.exists():
            r.add(g, OK, "Piper-Stimme", voice)
        else:
            r.add(g, WARN, "Piper-Stimme nicht heruntergeladen", voice, fix=f"pitwall voices download {voice}")
    stt = importlib.util.find_spec("faster_whisper") is not None
    r.add(g, OK if stt else WARN, "Spracherkennung", f"faster-whisper ({cfg.get('voice.stt_model', 'small')})"
          if stt else "nicht installiert", fix="uv pip install -e '.[stt]'")


# --------------------------------------------------------------------- PTT


def check_ptt(r: Report, cfg: Config) -> None:
    g = "Push-to-Talk"
    backend = str(cfg.get("ptt.backend", "auto"))
    button = int(cfg.get("ptt.button", -1))
    device = str(cfg.get("ptt.device", "")) or "alle Joysticks"
    r.add(g, OK if button >= 0 else WARN, "Funktaste", f"Taste {button} an {device}" if button >= 0
          else "noch nicht angelernt (Leertaste im Dashboard geht immer)",
          fix="Dashboard → Einstellungen → 'Funktaste anlernen', dann Taste am Lenkrad drücken.")
    if sys.platform != "linux" or backend == "sdl":
        return
    events = sorted(Path("/dev/input").glob("event*"))
    readable = [e for e in events if os.access(e, os.R_OK)]
    if not events:
        r.add(g, WARN, "Keine /dev/input/event*-Geräte")
        return
    if not readable:
        user = os.environ.get("USER", "")
        in_group = False
        with contextlib.suppress(KeyError):
            in_group = user in grp.getgrnam("input").gr_mem
        r.add(g, FAIL, "Keine Leserechte auf Eingabegeräte", f"{len(events)} Geräte, Gruppe input: "
              f"{'ja (neu anmelden?)' if in_group else 'nein'}",
              fix="Variante A (empfohlen): udev-Regel nur für das Lenkrad, siehe docs/linux-proton.md#push-to-talk\n"
                  "Variante B: sudo usermod -aG input $USER  (danach ab- und anmelden)")
        return
    r.add(g, OK, "Eingabegeräte lesbar", f"{len(readable)}/{len(events)}")
    if importlib.util.find_spec("evdev") is not None:
        try:
            import evdev

            wheels = []
            for path in readable:
                with contextlib.suppress(OSError):
                    dev = evdev.InputDevice(str(path))
                    name = dev.name
                    dev.close()
                    if any(k in name.lower() for k in ("logitech", "wheel", "fanatec", "moza", "simucube",
                                                       "thrustmaster", "rs50", "g923", "g29", "pxn")):
                        wheels.append(name)
            r.add(g, OK if wheels else INFO, "Lenkrad erkannt" if wheels else "Kein Lenkrad erkannt",
                  ", ".join(sorted(set(wheels))[:4]))
        except Exception as exc:
            r.add(g, WARN, "evdev-Fehler", str(exc))


# ------------------------------------------------------------------- Ports


def _port_free(proto: str, port: int, host: str = "0.0.0.0") -> bool:
    kind = socket.SOCK_DGRAM if proto == "udp" else socket.SOCK_STREAM
    with socket.socket(socket.AF_INET, kind) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def check_ports(r: Report, cfg: Config) -> None:
    g = "Netzwerk"
    for name, (proto, key, default) in PORTS.items():
        port = int(cfg.get(key, default)) if key else default
        free = _port_free(proto, port, "127.0.0.1" if name in ("relay", "acext") else "0.0.0.0")
        r.add(g, OK if free else WARN, f"{proto.upper()} {port} ({name})", "frei" if free else "belegt",
              fix="Läuft PIT WALL schon? Sonst belegt ein anderes Tool (z. B. SimHub) den Port – "
                  "dort Weiterleitung aktivieren oder Port ändern.")
    if sys.platform == "linux" and shutil.which("firewall-cmd"):
        try:
            state = subprocess.run(["firewall-cmd", "--state"], capture_output=True, text=True, timeout=3)
            running = state.stdout.strip() == "running"
        except (OSError, subprocess.TimeoutExpired):
            running = False
        if running:
            ports = ""
            with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                ports = subprocess.run(["firewall-cmd", "--list-ports"], capture_output=True, text=True,
                                       timeout=3).stdout
            gt7_open = "33740/udp" in ports
            r.add(g, OK if gt7_open else WARN, "firewalld aktiv", f"offene Ports: {ports.strip() or '–'}",
                  fix="Nur für GT7 (PS5 im LAN) und F1 von einer Konsole nötig:\n"
                      "sudo firewall-cmd --permanent --add-port=33740/udp --add-port=20777/udp && "
                      "sudo firewall-cmd --reload")


# ------------------------------------------------------------ Proton/Steam


def steam_libraries() -> list[Path]:
    home = Path.home()
    roots = [home / ".local/share/Steam", home / ".steam/steam", home / ".steam/root",
             home / ".var/app/com.valvesoftware.Steam/.local/share/Steam"]
    libs: list[Path] = []
    for root in roots:
        vdf = root / "steamapps/libraryfolders.vdf"
        if not vdf.exists():
            continue
        libs.append(root)
        with contextlib.suppress(OSError):
            for line in vdf.read_text(errors="replace").splitlines():
                line = line.strip()
                if line.startswith('"path"'):
                    p = Path(line.split('"')[3])
                    if p.exists():
                        libs.append(p)
    seen: set[Path] = set()
    out = []
    for lib in libs:
        rp = lib.resolve()
        if rp not in seen:
            seen.add(rp)
            out.append(lib)
    return out


def installed_games(libs: list[Path]) -> dict[int, Path]:
    found: dict[int, Path] = {}
    for lib in libs:
        for appid in STEAM_GAMES:
            if (lib / f"steamapps/appmanifest_{appid}.acf").exists():
                found[appid] = lib
    return found


def check_proton(r: Report, cfg: Config) -> None:
    if sys.platform != "linux":
        return
    g = "Steam / Proton"
    libs = steam_libraries()
    if not libs:
        r.add(g, INFO, "Keine Steam-Bibliothek gefunden")
        return
    r.add(g, OK, "Steam-Bibliotheken", ", ".join(str(lib) for lib in libs[:3]))
    relay = Path(__file__).parent / "bridge" / "pitwall-relay.exe"
    r.add(g, OK if relay.exists() else FAIL, "pitwall-relay.exe", str(relay) if relay.exists() else "fehlt",
          fix="scripts/build-relay.sh ausführen (braucht mingw-w64).")
    games = installed_games(libs)
    for appid, lib in sorted(games.items()):
        key, name = STEAM_GAMES[appid]
        prefix = lib / f"steamapps/compatdata/{appid}/pfx"
        detail = "Proton-Prefix vorhanden" if prefix.exists() else "noch nie gestartet"
        if key in RELAY_SIMS:
            launch = f"{Path(__file__).resolve().parent.parent / 'scripts' / 'pitwall-launch'} " \
                     f"--sim {RELAY_SIMS[key]} %command%"
            r.add(g, INFO, name, detail, fix="")
            r.add(g, INFO, "  Steam-Startoption", launch)
        else:
            r.add(g, INFO, name, f"{detail}; UDP-Telemetrie, keine Bridge nötig")
    mirror = Path(str(cfg.get("shm.mirror_dir", "/dev/shm")))
    mirrors = sorted(p.name for p in mirror.glob(f"{cfg.get('shm.prefix', 'pitwall-')}*")) if mirror.exists() else []
    scs = (mirror / "SCS" / "SCSTelemetry").exists() or (mirror / "SCSTelemetry").exists()
    if mirrors:
        r.add(g, OK, "Shared-Memory-Spiegel", ", ".join(mirrors[:6]))
    if scs:
        r.add(g, OK, "ETS2/ATS-Telemetrie", "SCSTelemetry gefunden")


# --------------------------------------------------------------- Live-Test


async def check_live(r: Report, cfg: Config, sim: str, seconds: float) -> None:
    from .adapters.registry import ADAPTERS, create_adapter

    g = f"Live-Daten ({seconds:.0f} s)"
    keys = list(cfg.get("sim.autodetect", [])) if sim in ("all", "auto") else [sim]
    keys = [k for k in keys if k in ADAPTERS]
    if not keys:
        r.add(g, FAIL, f"Unbekannte Quelle '{sim}'", fix=f"Verfügbar: {', '.join(ADAPTERS)}")
        return
    print(f"\nWarte {seconds:.0f} s auf Daten von: {', '.join(keys)} … (Spiel starten und auf die Strecke fahren)")

    async def probe(key: str) -> tuple[str, int, str, str]:
        try:
            adapter = create_adapter(key, cfg)
        except Exception as exc:
            return key, -1, f"nicht ladbar: {exc}", ""
        try:
            await adapter.start()
        except OSError as exc:
            return key, -1, f"Start fehlgeschlagen: {exc}", adapter.hint
        frames = 0
        what = ""
        deadline = time.monotonic() + seconds

        async def consume() -> None:
            nonlocal frames, what
            async for frame in adapter.frames():
                frames += 1
                if frames == 1:
                    s = frame.session
                    what = f"{s.track_name or '?'} · {s.session_type.value} · {len(frame.cars)} Autos"

        task = asyncio.create_task(consume())
        with contextlib.suppress(TimeoutError, asyncio.CancelledError):
            await asyncio.wait_for(asyncio.shield(task), timeout=max(0.1, deadline - time.monotonic()))
        task.cancel()
        with contextlib.suppress(Exception):
            await adapter.stop()
        return key, frames, what, adapter.hint

    results = await asyncio.gather(*(probe(k) for k in keys))
    any_data = False
    for key, frames, what, hint in results:
        name = ADAPTERS[key][2]
        if frames > 0:
            any_data = True
            r.add(g, OK, name, f"{frames} Frames – {what}")
        elif frames < 0:
            r.add(g, FAIL, name, what, fix=hint)
        else:
            r.add(g, INFO if sim in ("all", "auto") else FAIL, name, "keine Daten", fix=hint)
    if sim in ("all", "auto") and not any_data:
        r.checks[-1].status = WARN
        r.add(g, WARN, "Kein Spiel liefert gerade Daten",
              fix="Spiel starten, Telemetrie aktivieren (docs/setup-*.md) und `pitwall doctor --sim <spiel>` "
                  "erneut ausführen. Ohne Spiel: `pitwall dry-run`.")


async def run_doctor(cfg: Config, sim: str = "all", seconds: float = 6.0, live: bool = True) -> int:
    r = Report()
    check_system(r)
    check_python_packages(r)
    check_secrets(r, cfg)
    check_audio(r, cfg)
    check_ptt(r, cfg)
    check_ports(r, cfg)
    check_proton(r, cfg)
    print("PIT WALL doctor")
    print(r.render(sys.stdout.isatty()))
    r2 = Report()
    if live and seconds > 0:
        await check_live(r2, cfg, sim, seconds)
        print(r2.render(sys.stdout.isatty()))
    fails = r.failures + r2.failures
    print(f"\n{'Alles bereit.' if not fails else f'{fails} Problem(e) gefunden – Hinweise oben.'}")
    return 1 if fails else 0
