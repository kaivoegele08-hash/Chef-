"""PIT WALL App: verbindet Adapter → State → Engineer → Stimme/Dashboard/Overlay.

Nebenläufigkeit (asyncio):

* Ingest-Task: Frames vom Adapter, State-Update, Engineer (nie blockiert).
* Ticker-Task: Director weitertakten, Dashboard-Broadcast (update_hz).
* Sprache: TTS/Wiedergabe in Threads, STT in Thread.
* Web: uvicorn im selben Event-Loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .adapters.base import SimAdapter
from .adapters.registry import AutoAdapter, create_adapter
from .config import Config
from .core.frame import SessionType, TelemetryFrame
from .core.recorder import Recorder
from .core.state import RaceState, StateEventKind
from .engineer.engineer import Engineer
from .engineer.events import Priority, Utterance
from .engineer.intents import answer as intent_answer
from .engineer.intents import classify
from .storage.db import LongTermMemory

log = logging.getLogger(__name__)


@dataclass
class Latency:
    """Messwerte für das Debug-Panel (ms)."""

    samples: dict[str, deque[float]] = field(default_factory=dict)

    def add(self, key: str, ms: float) -> None:
        self.samples.setdefault(key, deque(maxlen=50)).append(ms)

    def to_dict(self) -> dict[str, Any]:
        out = {}
        for k, v in self.samples.items():
            vals = sorted(v)
            out[k] = {"last": round(v[-1]), "median": round(vals[len(vals) // 2]), "n": len(vals)}
        return out


class PitWallApp:
    def __init__(self, config: Config, adapter_key: str | None = None, adapter_kwargs: dict[str, Any] | None = None,
                 enable_voice: bool = True, enable_brain: bool = True, enable_ptt: bool = True):
        self.config = config
        self.adapter_key = adapter_key or str(config.get("sim.active", "auto"))
        self.adapter_kwargs = adapter_kwargs or {}
        self.state = RaceState()
        self.long_memory = LongTermMemory(config.data_dir / "pitwall.db")
        self.engineer = Engineer(config, self.long_memory, track_dir=config.data_dir / "tracks")
        self.latency = Latency()
        self.adapter: SimAdapter | None = None
        self.frame: TelemetryFrame | None = None
        self.recorder: Recorder | None = None
        self.radio_log: deque[dict[str, Any]] = deque(maxlen=120)
        self.clients: set[Any] = set()  # Dashboard-WebSockets
        self.overlay_clients: set[Any] = set()
        self._tasks: list[asyncio.Task[Any]] = []
        self._speech_tasks: dict[int, asyncio.Task[Any]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._last_broadcast = 0.0
        self._last_map_sent = 0.0
        self._t_offset = 0.0  # Frame-Zeit − monotonic (für Idle-Ticks)
        self.session_started = False
        self.started_at = time.time()
        self.debrief_text: str | None = None
        self.coach: Any = None
        self.voice: Any = None
        self.stt: Any = None
        self.mic: Any = None
        self.ptt: Any = None
        self.brain: Any = None
        self._ptt_t0: float | None = None
        self._ptt_source = ""
        self.enable_voice = enable_voice
        self.enable_brain = enable_brain
        self.enable_ptt = enable_ptt
        self.engineer.sinks.append(self._on_utterance)
        self.engineer.interrupt_sinks.append(self._on_interrupt)
        self._last_strategy_llm_lap = -1
        self._init_optional()

    # ------------------------------------------------------- Optionales
    def _init_optional(self) -> None:
        from .engineer.coaching import Coach

        self.coach = Coach(self.config)
        self.engineer.coach = self.coach  # type: ignore[attr-defined]
        self.engineer.extra_detectors.append(self.coach.detector())
        if self.enable_brain:
            from .brain.llm import Brain

            self.brain = Brain(self.config)
            self.brain.bind(self.engineer)
        if self.enable_voice:
            from .voice.audio import AudioPlayer
            from .voice.output import VoiceOutput
            from .voice.tts import make_tts

            vc = self.config.section("voice")
            self.voice = VoiceOutput(make_tts(self.config), AudioPlayer(vc.get("output_device") or None,
                                                                        float(vc.get("volume", 1.0))),
                                     bool(vc.get("radio_fx", True)), float(vc.get("radio_fx_intensity", 0.6)),
                                     on_event=self._on_speech_event)
            from .voice.stt import Recorder as MicRecorder
            from .voice.stt import SpeechToText

            self.stt = SpeechToText(self.config)
            self.mic = MicRecorder(vc.get("input_device") or None, float(self.config.get("ptt.max_record_s", 12)))

    # --------------------------------------------------------------- Lauf
    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        await self.select_adapter(self.adapter_key, **self.adapter_kwargs)
        self._tasks.append(asyncio.create_task(self._ticker(), name="ticker"))
        if self.stt is not None and self.stt.available:
            self._tasks.append(asyncio.create_task(asyncio.to_thread(self.stt.load), name="stt-load"))
        if self.voice is not None:
            self._tasks.append(asyncio.create_task(asyncio.to_thread(self.voice.tts.warmup), name="tts-warmup"))
        if self.enable_ptt:
            from .voice.ptt import Binding, PTTManager

            pc = self.config.section("ptt")
            binding = None
            if int(pc.get("button", -1)) >= 0:
                binding = Binding(str(pc.get("backend_bound", "evdev")), str(pc.get("device", "")),
                                  int(pc.get("button", -1)))
            self.ptt = PTTManager(self.ptt_press, self.ptt_release, binding, str(pc.get("backend", "auto")))
            await self.ptt.start()
        if self.config.get("recorder.enabled", False):
            self.start_recording()

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        if self.ptt is not None:
            await self.ptt.stop()
        if self.adapter is not None:
            with contextlib.suppress(Exception):
                await self.adapter.stop()
        self.stop_recording()
        self._finish_session()
        self.engineer.save()

    async def select_adapter(self, key: str, **kwargs: Any) -> None:
        if self.adapter is not None:
            with contextlib.suppress(Exception):
                await self.adapter.stop()
        for t in [t for t in self._tasks if t.get_name() == "ingest"]:
            t.cancel()
            self._tasks.remove(t)
        self.adapter_key = key
        self.adapter = create_adapter(key, self.config, **kwargs)
        try:
            await self.adapter.start()
        except OSError as exc:
            self.adapter.hint = f"Start fehlgeschlagen: {exc}"
            log.error("Adapter %s: %s", key, exc)
        self._tasks.append(asyncio.create_task(self._ingest(self.adapter), name="ingest"))
        log.info("Adapter aktiv: %s", self.adapter.display_name)

    async def _ingest(self, adapter: SimAdapter) -> None:
        try:
            async for frame in adapter.frames():
                self.on_frame(frame)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Ingest abgebrochen")

    def on_frame(self, frame: TelemetryFrame) -> None:
        t0 = time.perf_counter()
        self.frame = frame
        self._t_offset = frame.t - time.monotonic()
        events = self.state.update(frame)
        for ev in events:
            if ev.kind is StateEventKind.SESSION_RESET:
                self._finish_session()
                self.session_started = False
                if self.brain is not None:
                    self.brain.reset()
                self.coach.reset()
            elif ev.kind is StateEventKind.LAP_COMPLETED:
                rec = ev.data["record"]
                self.long_memory.record_lap(rec.lap, rec.time_s, rec.fuel_used_l, rec.compound, rec.tyre_age,
                                            rec.valid, rec.clean, rec.position, rec.sectors)
                self._maybe_strategy_llm(frame)
            elif ev.kind is StateEventKind.LIGHTS_OUT:
                self.long_memory.set_start_position(self.state.start_position)
            elif ev.kind is StateEventKind.CHEQUERED:
                if self._loop is not None:
                    self._loop.call_later(20.0, self._finish_session)
                else:
                    self._finish_session()
        if not self.session_started and frame.me is not None:
            self.session_started = True
            s = frame.session
            self.long_memory.start_session(str(frame.sim), s.track_id or s.track_name, s.car_name,
                                           str(s.session_type), recording=str(self.recorder.path) if self.recorder
                                           else None)
        if self.recorder is not None and frame.sim.value == "simulator":
            self.recorder.write_frame(frame)
        self.coach.observe(frame, self.state, events, self.engineer.track)
        self.engineer.process(frame, self.state, events)
        self.latency.add("frame_processing", (time.perf_counter() - t0) * 1000)

    async def _ticker(self) -> None:
        hz = float(self.config.get("web.update_hz", 15))
        while True:
            await asyncio.sleep(1.0 / hz)
            now_frame_t = time.monotonic() + self._t_offset
            # Director auch ohne neue Frames weitertakten (Antworten im Menü, Spiel pausiert)
            if self.frame is None or (self.adapter and not self.adapter.receiving(0.5)):
                self.engineer.tick_idle(now_frame_t)
            await self.broadcast()

    # ----------------------------------------------------------- Sprache
    def _on_utterance(self, utt: Utterance) -> None:
        entry = utt.to_dict()
        entry["role"] = "engineer"
        self.radio_log.append(entry)
        self._push_overlay({"type": "radio", **entry})
        if self.voice is None or self._loop is None:
            return
        t_req = utt.event.data.get("_t_request") if isinstance(utt.event.data, dict) else None
        task = self._loop.create_task(self._speak(utt, t_req))
        self._speech_tasks[utt.event.id] = task

    async def _speak(self, utt: Utterance, t_request: float | None) -> None:
        try:
            stream = utt.event.data.get("_stream") if isinstance(utt.event.data, dict) else None
            if stream is not None:
                dur = await self.voice.speak_stream(utt.event.id, stream, utt.priority.name, t_request)
            else:
                dur = await self.voice.speak(utt.event.id, utt.spoken, utt.priority.name, t_request)
            self.engineer.director.speech_finished(utt.start_t + dur)
        finally:
            self._speech_tasks.pop(utt.event.id, None)

    def _on_interrupt(self, utt: Utterance) -> None:
        if self.voice is not None:
            self.voice.interrupt()
        self._push_overlay({"type": "interrupt", "id": utt.event.id})

    def _on_speech_event(self, ev: Any) -> None:
        if ev.kind == "start" and ev.latency_ms is not None:
            self.latency.add("answer_to_audio", ev.latency_ms)
        if ev.kind == "sentence":
            self.engineer.director.speech_duration(time.monotonic() + self._t_offset, ev.duration_s + 0.2)
        self._push_overlay({"type": "speech", "kind": ev.kind, "id": ev.utterance_id, "text": ev.text,
                            "duration": round(ev.duration_s, 2), "env": ev.envelope, "priority": ev.priority})

    # ---------------------------------------------------------------- PTT
    def ptt_press(self, source: str = "button") -> None:
        self.engineer.ptt_active = True
        self._ptt_t0 = time.monotonic()
        self._ptt_source = source
        if self.voice is not None and self.voice.busy:
            cur = self.engineer.director.current
            if cur is None or cur.priority < Priority.CRITICAL:
                self.voice.interrupt()
        if self.mic is not None and source != "browser":
            self.mic.start()
        self._push_overlay({"type": "ptt", "pressed": True})

    def ptt_release(self, source: str = "button") -> None:
        self.engineer.ptt_active = False
        self._push_overlay({"type": "ptt", "pressed": False})
        if source == "browser" or self.mic is None:
            return
        audio = self.mic.stop()
        t_release = time.monotonic()
        if self._loop is not None:
            self._loop.create_task(self._transcribe_and_answer(audio, t_release))

    async def _transcribe_and_answer(self, audio: Any, t_release: float) -> None:
        if self.stt is None or not self.stt.available:
            self.engineer.say("answer.unknown")
            return
        text = await asyncio.to_thread(self.stt.transcribe, audio)
        self.latency.add("stt", (time.monotonic() - t_release) * 1000)
        if not text:
            return
        await self.handle_question(text, t_release)

    async def handle_audio_upload(self, data: bytes) -> str:
        from .voice.stt import decode_audio_bytes

        t0 = time.monotonic()
        audio = await asyncio.to_thread(decode_audio_bytes, data)
        if self.stt is None or not self.stt.available:
            return ""
        text = await asyncio.to_thread(self.stt.transcribe, audio)
        self.latency.add("stt", (time.monotonic() - t0) * 1000)
        if text:
            await self.handle_question(text, t0)
        return str(text)

    # ------------------------------------------------------------- Fragen
    async def handle_question(self, text: str, t_request: float | None = None) -> dict[str, Any]:
        """Instant Intent → Phrasebook (< 300 ms), sonst Claude mit Tools, sonst Fallback."""
        t_request = t_request or time.monotonic()
        now = time.monotonic() + self._t_offset
        lap = self.frame.me.lap if self.frame and self.frame.me else 0
        self.engineer.memory.add(now, lap, "driver", text)
        self.radio_log.append({"role": "driver", "text": text, "t": round(now, 2), "lap": lap})
        self._push_overlay({"type": "driver", "text": text})
        names = [c.driver_name for c in self.frame.cars if c.driver_name and not c.is_player] if self.frame else []
        intent = classify(text, names, self.engineer.memory.awaiting_balance)
        if intent is not None:
            result = intent_answer(intent, self.engineer)
            if result is not None and result[0] is not None:
                key, data = result
                data["_t_request"] = t_request
                self.engineer.say(key, Priority.ANSWER, "answer", t=now, **data)
                self.latency.add("intent", (time.monotonic() - t_request) * 1000)
                if intent.name == "box_request" and self.adapter is not None and hasattr(self.adapter, "command"):
                    self.adapter.command("box", compound=self.engineer.flags.get("box_compound"))
                return {"source": "intent", "intent": intent.name}
        # freie Frage → LLM
        if self.brain is not None and self.brain.available:
            return await self._ask_llm(text, t_request, now)
        self.engineer.say("answer.no_llm" if self.brain is not None else "answer.unknown", t=now,
                          _t_request=t_request)
        return {"source": "fallback"}

    async def _ask_llm(self, text: str, t_request: float, now: float) -> dict[str, Any]:
        from .brain.llm import LLMUnavailable

        queue: asyncio.Queue[str | None] = asyncio.Queue()
        strategic = bool(_is_strategic(text))
        first_sentence: asyncio.Future[str | None] = asyncio.get_running_loop().create_future()

        async def producer() -> None:
            try:
                async for sentence in self.brain.ask(text, self.engineer, strategic=strategic):
                    if not first_sentence.done():
                        first_sentence.set_result(sentence)
                    await queue.put(sentence)
            except LLMUnavailable as exc:
                log.info("LLM nicht verfügbar: %s", exc)
                if not first_sentence.done():
                    first_sentence.set_result(None)
            except Exception:
                log.exception("LLM-Fehler")
                if not first_sentence.done():
                    first_sentence.set_result(None)
            finally:
                await queue.put(None)

        task = asyncio.create_task(producer())
        timeout = float(self.config.get("brain.timeout_s", 2.5)) * (2.0 if strategic else 1.0)
        try:
            first = await asyncio.wait_for(asyncio.shield(first_sentence), timeout=timeout)
        except TimeoutError:
            first = None
            # "Moment, prüfe das" und trotzdem weiter auf die Antwort warten
            self.engineer.say("answer.check", t=now)
            try:
                first = await asyncio.wait_for(asyncio.shield(first_sentence), timeout=timeout * 2)
            except TimeoutError:
                task.cancel()
                return {"source": "timeout"}
        if first is None:
            self.engineer.say("answer.check" if self.frame else "answer.unknown", t=now)
            return {"source": "llm_failed"}

        async def stream() -> Any:
            while True:
                item = await queue.get()
                if item is None:
                    return
                yield item

        full: list[str] = []

        async def collecting_stream() -> Any:
            async for s in stream():
                full.append(s)
                yield s

        self.latency.add("llm_first_sentence", (time.monotonic() - t_request) * 1000)
        ev = self.engineer.say("llm.answer", Priority.ANSWER, "answer", text=first, spoken=first,
                               t=time.monotonic() + self._t_offset, ttl=30)
        ev.data["_stream"] = collecting_stream()
        ev.data["_t_request"] = t_request
        ev.source = "llm"
        return {"source": "llm", "model": self.brain.strategy_model if strategic else self.brain.radio_model}

    def _maybe_strategy_llm(self, frame: TelemetryFrame) -> None:
        """Periodisches Strategie-Update (Standard 1× pro Runde) bzw. bei komplexen Lagen."""
        if self.brain is None or not self.brain.available or frame.session.session_type is not SessionType.RACE:
            return
        setting = str(self.config.get("engineer.strategy_update", "lap"))
        if setting == "off" or frame.me is None:
            return
        lap = frame.me.lap
        if lap <= 2 or lap == self._last_strategy_llm_lap:
            return
        snap = self.engineer.snapshot
        complex_flags = [frame.session.safety_car.value != "none", bool(snap.weather and snap.weather.recommendation),
                         bool(snap.undercut_threat and snap.undercut_threat.feasible), snap.plan_changed]
        every = 1 if setting == "lap" else 3
        if sum(complex_flags) < 2 and (lap % max(1, every * 3) != 0):
            return  # Standard: alle 3 Runden ruhiges Update, sofort bei komplexer Lage
        self._last_strategy_llm_lap = lap
        import json

        summary = ("Lage-Update (keine Frage). Strategie-Daten: " +
                   json.dumps(snap.to_dict(), ensure_ascii=False, default=str)[:6000])
        if self._loop is None:
            return

        async def run() -> None:
            text = await self.brain.situation_update(self.engineer, summary)
            if text:
                prio = Priority.HIGH if sum(complex_flags) >= 2 else Priority.INFO
                self.engineer.say("llm.update", prio, "strategy", text=text, spoken=text, ttl=25)

        self._loop.create_task(run())

    # ---------------------------------------------------------- Aufnahme
    def start_recording(self) -> dict[str, Any]:
        if self.recorder is not None:
            return self.recorder.info()
        folder = Path(self.config.get("recorder.directory") or (self.config.data_dir / "recordings"))
        sim = str(self.frame.sim) if self.frame else self.adapter_key
        stamp = time.strftime("%Y%m%d-%H%M%S")
        adapter_key = self.adapter_key
        target = self.adapter
        if isinstance(self.adapter, AutoAdapter) and self.adapter.active is not None:
            target = self.adapter.active
            adapter_key = target.key
        self.recorder = Recorder(folder / f"{stamp}_{sim}.pwrec", sim, adapter_key,
                                 str(self.config.get("recorder.compression", "auto")))
        if target is not None:
            target.recorder = self.recorder
        return self.recorder.info()

    def stop_recording(self) -> dict[str, Any] | None:
        rec = self.recorder
        if rec is None:
            return None
        info = rec.info()
        for a in [self.adapter, getattr(self.adapter, "active", None)]:
            if a is not None and id(getattr(a, "recorder", None)) == id(rec):
                a.recorder = None
        rec.close()
        self.recorder = None
        return info

    # ------------------------------------------------------------ Debrief
    def _finish_session(self) -> None:
        if not self.state.laps:
            return
        try:
            from .engineer.debrief import build_debrief

            text, data = build_debrief(self.state, self.engineer, self.coach, self.config.language)
            self.debrief_text = text
            self.long_memory.save_debrief(text, data)
            me = self.frame.me if self.frame else None
            self.long_memory.end_session(me.position if me else None, self.state.best_lap_s, len(self.state.laps))
            if self.frame is not None:
                s = self.frame.session
                self.long_memory.record_corner_losses(str(self.frame.sim), s.track_id or s.track_name,
                                                      self.coach.session_corner_summary())
        except Exception:
            log.exception("Debrief fehlgeschlagen")

    # --------------------------------------------------------- Broadcast
    def _push_overlay(self, msg: dict[str, Any]) -> None:
        if self._loop is None:
            return
        for ws in list(self.overlay_clients | self.clients):
            self._loop.create_task(_safe_send(ws, msg, self))

    def snapshot(self) -> dict[str, Any]:
        from .web.snapshot import build_snapshot

        extra = {
            "adapter": self.adapter.info() if self.adapter else None,
            "radio": list(self.radio_log)[-40:],
            "latency": self.latency.to_dict(),
            "director": {"mode": self.engineer.director.mode, "stats": self.engineer.director.stats.to_dict(),
                         "queue": len(self.engineer.director.queue)},
            "brain": self.brain.info() if self.brain else None,
            "voice": self.voice.info() if self.voice else None,
            "stt": {"status": self.stt.status, "last_ms": self.stt.last_ms} if self.stt else None,
            "ptt": self.ptt.info() if self.ptt else None,
            "recording": self.recorder.info() if self.recorder else None,
            "engineer": {"name": self.config.get("engineer.name"), "lang": self.config.language},
            "track_model": {"ready": bool(self.engineer.track and self.engineer.track.ready),
                            "laps": self.engineer.track.laps_learned if self.engineer.track else 0,
                            "corners": len(self.engineer.track.corners) if self.engineer.track else 0},
        }
        return build_snapshot(self.frame, self.state, self.engineer.snapshot, self.engineer.track, extra)

    async def broadcast(self) -> None:
        if not self.clients:
            return
        snap = self.snapshot()
        now = time.monotonic()
        if now - self._last_map_sent > 5.0 and self.engineer.track is not None:
            self._last_map_sent = now
            snap["track_map"] = self.engineer.track.map_points(step=2)
        for ws in list(self.clients):
            await _safe_send(ws, snap, self)


async def _safe_send(ws: Any, msg: dict[str, Any], app: PitWallApp) -> None:
    try:
        await ws.send_json(msg)
    except Exception:
        app.clients.discard(ws)
        app.overlay_clients.discard(ws)


_STRATEGIC_WORDS = ("strategie", "strategy", "lohnt", "worth", "sollen wir", "should we", "vergleich", "compare",
                    "zwei stopps", "two stop", "undercut", "overcut", "plan b", "alternativ")


def _is_strategic(text: str) -> bool:
    low = text.lower()
    return any(w in low for w in _STRATEGIC_WORDS)
