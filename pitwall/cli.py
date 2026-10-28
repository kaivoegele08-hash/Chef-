"""Kommandozeile: ``pitwall run | dry-run | doctor | record | voices | devices``."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys
import webbrowser
from pathlib import Path

from .config import Config


def _setup_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")


def cmd_run(args: argparse.Namespace, cfg: Config) -> int:
    from .app import PitWallApp
    from .web.server import serve

    kwargs: dict[str, object] = {}
    key = args.sim or str(cfg.get("sim.active", "auto"))
    if args.replay:
        key = "replay"
        kwargs = {"path": args.replay, "speed": args.speed, "start_lap": args.start_lap}
    elif key == "simulator":
        kwargs = {"scenario": args.scenario, "time_scale": args.speed}
        if args.laps:
            kwargs["laps"] = args.laps
    host = args.host or str(cfg.get("web.host", "0.0.0.0"))
    port = args.port or int(cfg.get("web.port", 8077))

    async def main() -> None:
        app = PitWallApp(cfg, key, kwargs, enable_voice=not args.no_voice, enable_brain=not args.no_llm,
                         enable_ptt=not args.no_ptt)
        await app.start()
        url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '::') else host}:{port}/"
        print(f"\n  PIT WALL läuft → Dashboard {url}   Overlay {url}overlay\n")
        if not args.no_browser and cfg.get("web.open_browser", True):
            with contextlib.suppress(Exception):
                webbrowser.open(url)
        try:
            await serve(app, host, port)
        finally:
            await app.stop()

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
    return 0


def cmd_dry_run(args: argparse.Namespace, cfg: Config) -> int:
    from .dryrun import run_frames, simulate

    if args.replay:
        from .adapters.replay import iter_frames

        _, it = iter_frames(Path(args.replay), cfg)
        result = run_frames((f for _, f in it), cfg, out=sys.stdout)
    else:
        result = simulate(cfg, args.scenario, laps=args.laps, seed=args.seed, follow_engineer=not args.no_follow,
                          out=sys.stdout)
    eng = result.engineer
    print(f"\n{len(result.utterances)} Funksprüche, {result.frames} Frames, {result.laps} Runden, "
          f"{result.wall_s:.1f} s Rechenzeit")
    if eng is not None:
        print("Director:", eng.director.stats.to_dict())
        if args.debrief and result.state is not None:
            from .engineer.debrief import build_debrief

            text, _ = build_debrief(result.state, eng, None, cfg.language)
            print("\n" + text)
    return 0


def cmd_doctor(args: argparse.Namespace, cfg: Config) -> int:
    from .doctor import run_doctor

    return asyncio.run(run_doctor(cfg, args.sim, args.seconds))


def cmd_record(args: argparse.Namespace, cfg: Config) -> int:
    from .adapters.registry import create_adapter
    from .core.recorder import Recorder

    async def main() -> None:
        adapter = create_adapter(args.sim, cfg)
        out = Path(args.out)
        rec = Recorder(out, args.sim, args.sim)
        adapter.recorder = rec
        await adapter.start()
        print(f"Nehme {adapter.display_name} auf → {out} (Strg+C zum Beenden)")
        n = 0
        try:
            async for frame in adapter.frames():
                if args.sim == "simulator":
                    rec.write_frame(frame)
                n += 1
                if n % 200 == 0:
                    print(f"\r{n} Frames, {rec.records} Datensätze", end="", flush=True)
        finally:
            rec.close()
            await adapter.stop()

    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main())
    return 0


def cmd_voices(args: argparse.Namespace, cfg: Config) -> int:
    from .voice.tts import PIPER_VOICES, download_piper_voice, voices_dir

    target = voices_dir(cfg)
    if args.action == "list":
        for name in sorted(PIPER_VOICES):
            mark = "✓" if (target / f"{name}.onnx").exists() else " "
            print(f" [{mark}] {name}")
        print(f"\nOrdner: {target}")
        return 0
    names = args.names or [str(cfg.get("voice.piper_voice"))]
    for name in names:
        for p in download_piper_voice(name, target):
            print("geladen:", p)
    return 0


def cmd_say(args: argparse.Namespace, cfg: Config) -> int:
    """Testet die Sprachausgabe (TTS + Funk-Effekt)."""
    from .voice.audio import AudioPlayer, to_wav_bytes
    from .voice.radio_fx import apply_radio_fx
    from .voice.tts import make_tts

    tts = make_tts(cfg)
    audio, sr = tts.synthesize(args.text)
    if not args.no_fx:
        audio = apply_radio_fx(audio, sr, float(cfg.get("voice.radio_fx_intensity", 0.6)))
    if args.out:
        Path(args.out).write_bytes(to_wav_bytes(audio, sr))
        print("gespeichert:", args.out)
    else:
        AudioPlayer(cfg.get("voice.output_device") or None).play(audio, sr)
    print(f"TTS: {tts.name}, {len(audio) / sr:.2f} s")
    return 0


def cmd_devices(args: argparse.Namespace, cfg: Config) -> int:
    from .voice.audio import list_input_devices, list_output_devices

    print("Audio-Ausgabe:")
    for d in list_output_devices():
        print(f"  [{d['index']}] {d['name']}{' (Standard)' if d['default'] else ''}")
    print("Mikrofone:")
    for d in list_input_devices():
        print(f"  [{d['index']}] {d['name']}{' (Standard)' if d['default'] else ''}")
    if sys.platform.startswith("linux"):
        try:
            import evdev

            print("Eingabegeräte (evdev):")
            for path in evdev.list_devices():
                try:
                    dev = evdev.InputDevice(path)
                    print(f"  {path}: {dev.name}")
                except OSError as exc:
                    print(f"  {path}: kein Zugriff ({exc})")
        except ImportError:
            print("python-evdev nicht installiert (pip install 'pitwall[ptt-linux]')")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pitwall", description="PIT WALL – KI-Renningenieur")
    p.add_argument("--config", help="Pfad zu einer eigenen config.toml")
    p.add_argument("--lang", choices=["de", "en"], help="Sprache des Engineers")
    p.add_argument("--log", default=None, help="Log-Level (DEBUG, INFO, …)")
    sub = p.add_subparsers(dest="cmd")

    r = sub.add_parser("run", help="Engineer + Dashboard starten")
    r.add_argument("--sim", help="auto | simulator | f1 | gt7 | assetto_corsa | acc | iracing | rfactor2 | lmu | ets2")
    r.add_argument("--scenario", default="default", help="Simulator-Szenario")
    r.add_argument("--laps", type=int)
    r.add_argument("--replay", help="Aufnahme (.pwrec) abspielen")
    r.add_argument("--speed", type=float, default=1.0, help="Replay/Simulator-Geschwindigkeit (0 = max)")
    r.add_argument("--start-lap", type=int, default=0)
    r.add_argument("--host")
    r.add_argument("--port", type=int)
    r.add_argument("--no-voice", action="store_true")
    r.add_argument("--no-llm", action="store_true")
    r.add_argument("--no-ptt", action="store_true")
    r.add_argument("--no-browser", action="store_true")
    r.set_defaults(func=cmd_run)

    d = sub.add_parser("dry-run", help="Rennen simulieren/abspielen und alle Funksprüche als Text ausgeben")
    d.add_argument("--scenario", default="default")
    d.add_argument("--laps", type=int, default=20)
    d.add_argument("--seed", type=int)
    d.add_argument("--replay")
    d.add_argument("--no-follow", action="store_true", help="simulierter Fahrer ignoriert Box-Anweisungen")
    d.add_argument("--debrief", action="store_true")
    d.set_defaults(func=cmd_dry_run)

    doc = sub.add_parser("doctor", help="Prüft Setup und ob Daten vom Spiel ankommen")
    doc.add_argument("--sim", default="all")
    doc.add_argument("--seconds", type=float, default=6.0)
    doc.set_defaults(func=cmd_doctor)

    rec = sub.add_parser("record", help="Nur aufnehmen (ohne Engineer)")
    rec.add_argument("--sim", required=True)
    rec.add_argument("--out", required=True)
    rec.set_defaults(func=cmd_record)

    v = sub.add_parser("voices", help="Piper-Stimmen auflisten/herunterladen")
    v.add_argument("action", choices=["list", "download"])
    v.add_argument("names", nargs="*")
    v.set_defaults(func=cmd_voices)

    s = sub.add_parser("say", help="Sprachausgabe testen")
    s.add_argument("text")
    s.add_argument("--out", help="als WAV speichern statt abspielen")
    s.add_argument("--no-fx", action="store_true")
    s.set_defaults(func=cmd_say)

    dv = sub.add_parser("devices", help="Audio- und Eingabegeräte auflisten")
    dv.set_defaults(func=cmd_devices)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    overrides: dict[str, dict[str, object]] = {}
    if args.lang:
        overrides["general"] = {"language": args.lang}
    cfg = Config.load(args.config, overrides or None)
    _setup_logging(args.log or str(cfg.get("general.log_level", "INFO")))
    if not getattr(args, "cmd", None):
        args = parser.parse_args(["run", *(argv or sys.argv[1:])])
    return int(args.func(args, cfg))


if __name__ == "__main__":
    raise SystemExit(main())
