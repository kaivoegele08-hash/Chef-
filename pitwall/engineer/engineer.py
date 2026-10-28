"""Der Engineer: verbindet State → Strategie → Detektoren → Director → Ausgabe."""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import Config
from ..core.frame import SessionPhase, SimId, TelemetryFrame, VehicleClass
from ..core.state import RaceState, StateEvent, StateEventKind
from ..core.track import TrackModel
from ..strategy.engine import StrategyEngine, StrategySnapshot, sim_profile
from .detectors import Detector, EngineerContext, racing_detectors
from .director import DirectorInput, RadioDirector
from .events import Priority, RadioEvent, Utterance
from .memory import RaceMemory
from .phrasebook import Phrasebook
from .speech import Speaker

log = logging.getLogger(__name__)


class Engineer:
    def __init__(self, config: Config, long_memory: Any | None = None, track_dir: Path | None = None,
                 seed: int = 1):
        self.config = config
        self.lang = config.language
        self.speaker = Speaker(self.lang, str(config.get("general.units_pressure", "psi")),
                               str(config.get("general.units_speed", "kmh")))
        self.pb = Phrasebook(self.lang, seed=seed)
        self.long_memory = long_memory
        self.director = RadioDirector(
            self.pb,
            mode=str(config.get("engineer.verbosity", "normal")),
            max_info_per_lap=int(config.get("engineer.max_info_per_lap", 3)),
            words_per_second=float(config.get("voice.words_per_second", 2.8)),
            on_utterance=self._on_utterance,
            on_interrupt=self._on_interrupt,
        )
        self.strategy = StrategyEngine(config, long_memory)
        self.memory = RaceMemory()
        self.detectors: list[Detector] = racing_detectors()
        self.mode_kind = "racing"  # racing | truck
        self.track_dir = track_dir
        self.track: TrackModel | None = None
        self.track_key = ""
        self.snapshot = StrategySnapshot()
        self.flags: dict[str, Any] = {}
        self.ptt_active = False
        self.sinks: list[Callable[[Utterance], None]] = []
        self.interrupt_sinks: list[Callable[[Utterance], None]] = []
        self.event_sinks: list[Callable[[RadioEvent], None]] = []
        self._last_strategy_t = -1e9
        self._vocab = "formula"
        self._sim: SimId | None = None
        self.extra_detectors: list[Detector] = []
        self.frame: TelemetryFrame | None = None
        self.state: RaceState | None = None
        self.hello_said = False

    # ------------------------------------------------------------- Sinks
    def _on_utterance(self, utt: Utterance) -> None:
        lap = utt.lap or 0
        self.memory.add(utt.start_t, lap, "engineer", utt.text, utt.event.key)
        for sink in self.sinks:
            try:
                sink(utt)
            except Exception:  # noqa: BLE001
                log.exception("Utterance-Sink fehlgeschlagen")

    def _on_interrupt(self, utt: Utterance) -> None:
        for sink in self.interrupt_sinks:
            try:
                sink(utt)
            except Exception:  # noqa: BLE001
                log.exception("Interrupt-Sink fehlgeschlagen")

    # --------------------------------------------------------- Konfig
    def _setup_for(self, frame: TelemetryFrame) -> None:
        if frame.sim is self._sim:
            return
        self._sim = frame.sim
        if frame.sim in (SimId.ETS2, SimId.ATS):
            from .truck import truck_detectors

            self.detectors = truck_detectors()
            self.mode_kind = "truck"
            self._vocab = "truck"
        else:
            self.detectors = racing_detectors()
            self.mode_kind = "racing"
        self.detectors.extend(self.extra_detectors)

    def _vocabulary(self, frame: TelemetryFrame) -> str:
        if self.mode_kind == "truck":
            return "truck"
        vc = frame.session.vehicle_class
        if vc is VehicleClass.FORMULA:
            return "formula"
        if vc is VehicleClass.PROTOTYPE:
            return "prototype"
        if vc is VehicleClass.UNKNOWN:
            return str(sim_profile(str(frame.sim)).get("rules", {}).get("vocabulary", "gt"))
        return "gt"

    def _ensure_track(self, frame: TelemetryFrame) -> None:
        s = frame.session
        key = f"{frame.sim}_{s.track_id or s.track_name or 'unknown'}"
        if key == self.track_key:
            if self.track is not None:
                self.track.ensure_length(s.track_length_m)
            return
        if self.track is not None and self.track_dir is not None:
            self.track.save(self.track_dir)
        self.track_key = key
        self.track = TrackModel.load_or_new(self.track_dir, key) if self.track_dir else TrackModel(key=key)
        # Kurvennamen aus Konfig übernehmen
        from ..strategy.engine import track_profile

        tp = track_profile(_norm(s.track_id or s.track_name))
        names = tp.get("corners", {})
        if names:
            self.track.corner_names = {int(k): str(v) for k, v in names.items()}
        self.track.ensure_length(s.track_length_m)

    # ---------------------------------------------------------- Ablauf
    def process(self, frame: TelemetryFrame, state: RaceState, events: list[StateEvent]) -> Utterance | None:
        self.frame = frame
        self.state = state
        self._setup_for(frame)
        self._ensure_track(frame)
        for ev in events:
            if ev.kind is StateEventKind.SESSION_RESET:
                log.info("Engineer: Reset (%s)", ev.data.get("reason"))
                self.memory.reset(frame.session.session_uid)
                self.director.reset()
                for d in self.detectors:
                    d.reset()
                self.flags.clear()
                self.memory.add(frame.t, 0, "system", f"Session-Reset: {ev.data.get('reason')}")
            elif ev.kind is StateEventKind.FLASHBACK:
                removed = self.memory.rollback(frame.t, frame.me.lap if frame.me else 0)
                self.director.clear(keep_critical=False)
                ctx = self._ctx(frame, state, events)
                for d in self.detectors:
                    d.on_flashback(ctx)
                log.info("Engineer: Flashback (%.1f s zurück, %d Einträge entfernt)", ev.data.get("jump_s", 0), removed)
                self.memory.add(frame.t, frame.me.lap if frame.me else 0, "system", "Flashback")

        # Strecke lernen
        me = frame.me
        if self.track is not None and me is not None and self.mode_kind == "racing":
            p = frame.player
            self.track.observe(me.lap_distance_m, p.speed_ms, p.throttle, p.brake, p.steering, me.x, me.z,
                               me.in_pit or frame.session.phase is not SessionPhase.RUNNING)
            if any(e.kind is StateEventKind.LAP_COMPLETED for e in events):
                self.track.lap_completed()
                if self.track_dir is not None and self.track.laps_learned % 3 == 0:
                    try:
                        self.track.save(self.track_dir)
                    except OSError:
                        log.warning("Streckenmodell konnte nicht gespeichert werden")

        # Strategie (2 Hz + bei Rundenwechsel)
        lap_event = any(e.kind in (StateEventKind.LAP_COMPLETED, StateEventKind.SAFETY_CAR_CHANGED,
                                   StateEventKind.PIT_EXIT, StateEventKind.SESSION_RESET) for e in events)
        if self.mode_kind == "racing" and (lap_event or frame.t - self._last_strategy_t >= 0.5):
            self._last_strategy_t = frame.t
            try:
                self.snapshot = self.strategy.update(frame, state)
            except Exception:  # noqa: BLE001
                log.exception("Strategie-Update fehlgeschlagen")

        ctx = self._ctx(frame, state, events)
        new_events: list[RadioEvent] = []
        for d in self.detectors:
            try:
                new_events.extend(d.update(ctx))
            except Exception:  # noqa: BLE001
                log.exception("Detektor %s fehlgeschlagen", d.name)
        for e in new_events:
            for sink in self.event_sinks:
                sink(e)
        self.director.submit_many(new_events)
        return self.director.tick(self._director_input(frame))

    def _ctx(self, frame: TelemetryFrame, state: RaceState, events: list[StateEvent]) -> EngineerContext:
        self._vocab = self._vocabulary(frame)
        return EngineerContext(frame=frame, state=state, strategy=self.snapshot, track=self.track, events=events,
                               memory=self.memory, config=self.config, speaker=self.speaker, vocabulary=self._vocab,
                               flags=self.flags)

    def _director_input(self, frame: TelemetryFrame) -> DirectorInput:
        me = frame.me
        p = frame.player
        return DirectorInput(
            t=frame.t,
            lap=me.lap if me else 0,
            lap_distance_m=me.lap_distance_m if me else None,
            speed_ms=p.speed_ms,
            throttle=p.throttle,
            brake=p.brake,
            steering=p.steering,
            push_lap=bool(self.flags.get("push_lap")),
            ptt_active=self.ptt_active,
            in_pit=bool(me and me.in_pit),
            track=self.track if self.mode_kind == "racing" else None,
            track_length_m=frame.session.track_length_m,
            quiet_corners=int(self.config.get("engineer.quiet_last_corners", 2)),
            racing=frame.session.phase in (SessionPhase.RUNNING, SessionPhase.UNKNOWN) and self.mode_kind == "racing",
        )

    def tick_idle(self, t: float) -> Utterance | None:
        """Director weitertakten, wenn keine Frames kommen (Menü, Antworten)."""
        if self.frame is None:
            return self.director.tick(DirectorInput(t=t, racing=False))
        inp = self._director_input(self.frame)
        inp.t = t
        return self.director.tick(inp)

    # ------------------------------------------------------- Direkt-Funk
    def say(self, key: str, priority: Priority = Priority.ANSWER, category: str = "answer", text: str | None = None,
            spoken: str | None = None, ttl: float = 15.0, t: float | None = None, **data: Any) -> RadioEvent:
        now = t if t is not None else (self.frame.t if self.frame else 0.0)
        ev = RadioEvent(key=key, priority=priority, category=category, data=data, created_t=now, ttl_s=ttl,
                        text=text, spoken=spoken, source="answer" if priority is Priority.ANSWER else "engineer",
                        allow_in_silence=priority >= Priority.ANSWER)
        self.director.submit(ev)
        return ev

    def save(self) -> None:
        if self.track is not None and self.track_dir is not None:
            try:
                self.track.save(self.track_dir)
            except OSError:
                pass


def _norm(name: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in name.lower()).strip("_")
