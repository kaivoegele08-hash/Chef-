"""FastAPI-Server: Dashboard, Overlay, WebSocket-Live-Daten, Funk-API, Einstellungen."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from ..config import user_config_dir

if TYPE_CHECKING:
    from ..app import PitWallApp

log = logging.getLogger(__name__)
STATIC = Path(__file__).resolve().parent / "static"

# Einstellungen, die das Dashboard ändern darf (Schlüssel → Typ)
EDITABLE: dict[str, type] = {
    "general.language": str, "general.units_pressure": str, "general.units_speed": str,
    "engineer.name": str, "engineer.driver_name": str, "engineer.verbosity": str,
    "engineer.max_info_per_lap": int, "engineer.strategy_update": str, "engineer.coaching_in_race": str,
    "engineer.coaching_in_practice": str, "engineer.tyre_briefing": bool, "engineer.ask_balance": bool,
    "engineer.quiet_last_corners": int,
    "voice.tts_provider": str, "voice.piper_voice": str, "voice.piper_voice_en": str, "voice.output_device": str,
    "voice.input_device": str, "voice.radio_fx": bool, "voice.radio_fx_intensity": float, "voice.volume": float,
    "voice.stt_model": str, "voice.stt_device": str, "voice.elevenlabs_voice_id": str,
    "sim.active": str, "gt7.ps5_ip": str, "gt7.heartbeat": str, "f1.port": int, "f1.bind": str,
    "assetto_corsa.mode": str, "acc.broadcast_password": str, "acc.broadcast_port": int,
    "web.update_hz": int, "recorder.enabled": bool,
    "brain.enabled": bool, "brain.radio_model": str, "brain.strategy_model": str, "brain.use_strategy_model": bool,
    "brain.daily_token_limit": int, "brain.timeout_s": float,
    "strategy.fuel_reserve_laps": float, "strategy.default_pit_loss_s": float,
    "ptt.keyboard_key": str, "ptt.backend": str,
    "iracing.allow_pit_commands": bool,
}
SECRETS = ("ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID")
_BACKGROUND: set[asyncio.Task[Any]] = set()


def create_app(pw: PitWallApp) -> FastAPI:
    api = FastAPI(title="PIT WALL", docs_url="/api/docs", redoc_url=None)

    @api.get("/", response_class=HTMLResponse)
    async def dashboard() -> FileResponse:
        return FileResponse(STATIC / "dashboard.html")

    @api.get("/overlay", response_class=HTMLResponse)
    async def overlay() -> FileResponse:
        return FileResponse(STATIC / "overlay.html")

    @api.get("/api/state")
    async def state() -> JSONResponse:
        return JSONResponse(pw.snapshot())

    @api.websocket("/ws")
    async def ws_dashboard(ws: WebSocket) -> None:
        await ws.accept()
        pw.clients.add(ws)
        try:
            await ws.send_json(pw.snapshot())
            if pw.engineer.track is not None:
                await ws.send_json({"type": "track_map", "points": pw.engineer.track.map_points(step=2)})
            while True:
                msg = await ws.receive_json()
                await _ws_command(pw, msg)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            pw.clients.discard(ws)

    @api.websocket("/ws/overlay")
    async def ws_overlay(ws: WebSocket) -> None:
        await ws.accept()
        pw.overlay_clients.add(ws)
        try:
            await ws.send_json({"type": "hello", "engineer": pw.config.get("engineer.name"),
                                "team_color": _team_color(pw)})
            while True:
                await ws.receive_text()
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            pw.overlay_clients.discard(ws)

    # ------------------------------------------------------------ Funk
    @api.post("/api/ask")
    async def ask(body: dict[str, Any]) -> dict[str, Any]:
        text = str(body.get("text", "")).strip()
        if not text:
            raise HTTPException(400, "text fehlt")
        return await pw.handle_question(text)

    @api.post("/api/ask_audio")
    async def ask_audio(request: Request) -> dict[str, Any]:
        data = await request.body()
        if not data:
            raise HTTPException(400, "keine Audiodaten")
        try:
            text = await pw.handle_audio_upload(data)
        except RuntimeError as exc:
            raise HTTPException(500, str(exc)) from exc
        return {"text": text}

    @api.post("/api/ptt/{action}")
    async def ptt(action: str, source: str = "button") -> dict[str, Any]:
        if action == "press":
            pw.ptt_press(source)
        elif action == "release":
            pw.ptt_release(source)
        elif action == "learn":
            if pw.ptt is None:
                raise HTTPException(400, "PTT deaktiviert")
            binding = await pw.ptt.learn(15.0)
            if binding is None:
                return {"ok": False, "reason": "keine Taste gedrückt"}
            pw.ptt.binding = binding
            pw.config.set("ptt.button", binding.code, persist=True)
            pw.config.set("ptt.device", binding.device, persist=True)
            pw.config.set("ptt.backend_bound", binding.backend, persist=True)
            return {"ok": True, "binding": binding.to_dict()}
        else:
            raise HTTPException(404)
        return {"ok": True}

    @api.post("/api/radio/mode")
    async def radio_mode(body: dict[str, Any]) -> dict[str, Any]:
        mode = str(body.get("mode", "normal"))
        pw.engineer.director.set_mode(mode)
        return {"mode": pw.engineer.director.mode}

    @api.post("/api/radio/test")
    async def radio_test() -> dict[str, Any]:
        pw.engineer.say("system.hello", engineer=pw.engineer.speaker.text(str(pw.config.get("engineer.name"))))
        return {"ok": True}

    # ------------------------------------------------------ Sim / Replay
    @api.get("/api/adapters")
    async def adapters() -> dict[str, Any]:
        from ..adapters.registry import available_adapters

        return {"adapters": available_adapters(), "active": pw.adapter.info() if pw.adapter else None}

    @api.post("/api/sim/select")
    async def sim_select(body: dict[str, Any]) -> dict[str, Any]:
        key = str(body.get("key", "auto"))
        kwargs = {k: v for k, v in body.items() if k in ("scenario", "path", "speed", "laps", "start_lap")}
        if key == "simulator" and "speed" in kwargs:
            kwargs["time_scale"] = kwargs.pop("speed")
        try:
            await pw.select_adapter(key, **kwargs)
        except (KeyError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "adapter": pw.adapter.info() if pw.adapter else None}

    @api.post("/api/sim/command")
    async def sim_command(body: dict[str, Any]) -> dict[str, Any]:
        if pw.adapter is None or not hasattr(pw.adapter, "command"):
            raise HTTPException(400, "Adapter unterstützt keine Befehle")
        ok = pw.adapter.command(str(body.get("name")), **{k: v for k, v in body.items() if k != "name"})
        return {"ok": bool(ok)}

    @api.post("/api/gt7/discover")
    async def gt7_discover() -> dict[str, Any]:
        from ..adapters.gt7 import discover_ps5

        ip = await discover_ps5(timeout=2.5)
        if ip:
            pw.config.set("gt7.ps5_ip", ip, persist=True)
        return {"ip": ip}

    @api.get("/api/recordings")
    async def recordings() -> dict[str, Any]:
        folder = Path(pw.config.get("recorder.directory") or (pw.config.data_dir / "recordings"))
        files = sorted(folder.glob("*.pwrec"), key=lambda p: p.stat().st_mtime, reverse=True) if folder.exists() else []
        return {"recordings": [{"name": p.name, "path": str(p), "mb": round(p.stat().st_size / 1e6, 2)}
                               for p in files[:100]], "active": pw.recorder.info() if pw.recorder else None}

    @api.post("/api/record/{action}")
    async def record(action: str) -> dict[str, Any]:
        if action == "start":
            return {"recording": pw.start_recording()}
        if action == "stop":
            return {"saved": pw.stop_recording()}
        raise HTTPException(404)

    # ----------------------------------------------------- Auswertung
    @api.get("/api/coaching")
    async def coaching() -> dict[str, Any]:
        out = pw.coach.analysis(limit=10)
        if pw.coach.best is not None:
            out["reference_trace"] = pw.coach.best.to_dict()
        if pw.coach.last is not None:
            out["last_trace"] = pw.coach.last.to_dict()
        if pw.engineer.track is not None:
            out["corners"] = [c.to_dict() for c in pw.engineer.track.corners]
        return out

    @api.get("/api/debrief")
    async def debrief(llm: bool = False) -> dict[str, Any]:
        from ..engineer.debrief import build_debrief

        text, data = build_debrief(pw.state, pw.engineer, pw.coach, pw.config.language)
        result: dict[str, Any] = {"text": text, "data": data, "history": pw.long_memory.debriefs(10)}
        if llm and pw.brain is not None and pw.brain.available:
            summary = await pw.brain.situation_update(
                pw.engineer, ("Schreibe ein ausführliches Debrief für den Fahrer (6–10 Sätze, Fließtext, keine Listen),"
                              " basierend NUR auf diesen Daten:\n" if pw.config.language == "de" else
                              "Write a detailed debrief for the driver (6–10 sentences, prose), based ONLY on: \n")
                + text)
            result["llm_text"] = summary
        return result

    @api.get("/api/sessions")
    async def sessions() -> dict[str, Any]:
        return {"sessions": pw.long_memory.sessions(50)}

    @api.get("/api/memory")
    async def memory() -> dict[str, Any]:
        f = pw.frame
        if f is None:
            return {"available": False}
        s = f.session
        return pw.long_memory.summary(str(f.sim), s.track_id or s.track_name, s.car_name)

    # ------------------------------------------------------ Einstellungen
    @api.get("/api/config")
    async def get_config() -> dict[str, Any]:
        from ..voice.audio import list_input_devices, list_output_devices
        from ..voice.tts import PIPER_VOICES

        values = {k: pw.config.get(k) for k in EDITABLE}
        return {
            "values": values,
            "secrets": {k: bool(os.environ.get(k)) for k in SECRETS},
            "output_devices": list_output_devices(),
            "input_devices": list_input_devices(),
            "piper_voices": sorted(PIPER_VOICES),
            "ptt": pw.ptt.info() if pw.ptt else None,
        }

    @api.post("/api/config")
    async def set_config(body: dict[str, Any]) -> dict[str, Any]:
        changed = []
        for key, val in body.items():
            tp = EDITABLE.get(key)
            if tp is None:
                continue
            try:
                conv = (val in (True, "true", "1", 1, "on")) if tp is bool else tp(val)
            except (TypeError, ValueError):
                raise HTTPException(400, f"Ungültiger Wert für {key}") from None
            pw.config.set(key, conv, persist=True)
            changed.append(key)
        if "engineer.verbosity" in changed:
            pw.engineer.director.set_mode(str(pw.config.get("engineer.verbosity")))
        if "voice.radio_fx" in changed and pw.voice is not None:
            pw.voice.radio_fx = bool(pw.config.get("voice.radio_fx"))
        if "voice.radio_fx_intensity" in changed and pw.voice is not None:
            pw.voice.intensity = float(pw.config.get("voice.radio_fx_intensity"))
        if "voice.volume" in changed and pw.voice is not None:
            pw.voice.player.volume = float(pw.config.get("voice.volume"))
        restart = [k for k in changed if k.split(".")[0] in ("general", "voice", "brain", "f1", "gt7", "sim")
                   and k not in ("voice.radio_fx", "voice.radio_fx_intensity", "voice.volume")]
        return {"changed": changed, "restart_recommended": restart}

    @api.post("/api/secrets")
    async def set_secrets(body: dict[str, Any]) -> dict[str, Any]:
        """Schreibt API-Keys in die lokale .env (nie in den Browser zurück)."""
        env_path = user_config_dir() / ".env"
        env_path.parent.mkdir(parents=True, exist_ok=True)
        existing: dict[str, str] = {}
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if "=" in line and not line.strip().startswith("#"):
                    k, v = line.split("=", 1)
                    existing[k.strip()] = v.strip()
        for k in SECRETS:
            if k in body and str(body[k]).strip():
                existing[k] = str(body[k]).strip()
                os.environ[k] = existing[k]
        env_path.write_text("".join(f"{k}={v}\n" for k, v in existing.items()), encoding="utf-8")
        try:
            env_path.chmod(0o600)
        except OSError:
            pass
        if pw.enable_brain:
            from ..brain.llm import Brain

            pw.brain = Brain(pw.config)
            pw.brain.bind(pw.engineer)
        return {"secrets": {k: bool(os.environ.get(k)) for k in SECRETS}, "path": str(env_path)}

    @api.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "adapter": pw.adapter.info() if pw.adapter else None}

    return api


async def _ws_command(pw: PitWallApp, msg: dict[str, Any]) -> None:
    kind = msg.get("type")
    if kind == "ask" and msg.get("text"):
        task = asyncio.create_task(pw.handle_question(str(msg["text"])))
        _BACKGROUND.add(task)
        task.add_done_callback(_BACKGROUND.discard)
    elif kind == "ptt":
        if msg.get("pressed"):
            pw.ptt_press(str(msg.get("source", "button")))
        else:
            pw.ptt_release(str(msg.get("source", "button")))
    elif kind == "mode":
        pw.engineer.director.set_mode(str(msg.get("mode", "normal")))


def _team_color(pw: PitWallApp) -> str:
    f = pw.frame
    if f and f.me and f.me.team_color:
        return f.me.team_color
    return "#f5c518"


async def serve(pw: PitWallApp, host: str, port: int) -> None:
    import uvicorn

    config = uvicorn.Config(create_app(pw), host=host, port=port, log_level="warning", loop="none", ws="auto")
    server = uvicorn.Server(config)
    await server.serve()
