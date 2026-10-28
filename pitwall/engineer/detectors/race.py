"""Rennen: Start, Lights out, Startbericht, Positionswechsel, letzte Runde, Zielflagge."""

from __future__ import annotations

from typing import Any

from ...core.frame import CarStatus, SessionPhase
from ...core.state import StateEventKind
from ..events import Priority, RadioEvent
from ..speech import Spoken
from .base import Detector, EngineerContext


class RaceDetector(Detector):
    name = "race"

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.grid_called = False
        self.start_reported = False
        self.pending_pos: tuple[int, int, float] | None = None  # (from, to, seit)
        self.announced_pos: int | None = None
        self.last_lap_called = False
        self.finished_called = False
        self.laps_to_go_called: set[int] = set()
        self.retired_called = False
        self.pit_quiet_until = -1e9

    def on_flashback(self, ctx: EngineerContext) -> None:
        self.pending_pos = None
        self.announced_pos = ctx.me.position if ctx.me else None
        self.last_lap_called = False
        self.finished_called = False

    def update(self, ctx: EngineerContext) -> list[RadioEvent]:
        out: list[RadioEvent] = []
        if not ctx.is_race:
            return out
        sp = ctx.speaker
        me = ctx.me
        s = ctx.frame.session
        if s.phase in (SessionPhase.GRID, SessionPhase.FORMATION) and not self.grid_called and s.phase is (
            SessionPhase.GRID
        ):
            self.grid_called = True
            out.append(ctx.event("race.grid", Priority.CHATTER, "race", ttl=4))
        if ctx.has_event(StateEventKind.LIGHTS_OUT):
            out.append(ctx.event("race.lights_out", Priority.HIGH, "race", ttl=3, interrupt=True,
                                 allow_in_silence=True))
            self.announced_pos = me.position if me else None
        if me is None:
            return out

        # Startbericht nach Runde 1
        lap_done = ctx.has_event(StateEventKind.LAP_COMPLETED)
        if lap_done and lap_done.data.get("lap") == 1 and not self.start_reported:
            self.start_reported = True
            grid = ctx.state.start_position
            pos = me.position
            if grid and pos:
                diff = grid - pos
                key = "race.start_gained" if diff > 0 else "race.start_lost" if diff < 0 else "race.start_held"
                out.append(ctx.event(key, Priority.INFO, "race", ttl=25, n=sp.int_(abs(diff)), pos=sp.position(pos)))
            self.announced_pos = pos
            self.pending_pos = None

        # Positionswechsel (entprellt; nicht in Runde 1, dafür gibt es den Startbericht).
        # Rund um Boxenstopps (eigene oder fremde) still nachführen statt "überholt" zu melden.
        if ctx.has_event(StateEventKind.PIT_EXIT) or ctx.has_event(StateEventKind.PIT_ENTRY):
            self.pit_quiet_until = ctx.t + 30.0
        if me.in_pit or ctx.t < self.pit_quiet_until:
            self.announced_pos = me.position
            self.pending_pos = None
        elif me.position is not None and self.start_reported and not ctx.state.finished:
            if self.announced_pos is None:
                self.announced_pos = me.position
            if me.position != self.announced_pos:
                if self.pending_pos is None or self.pending_pos[1] != me.position:
                    self.pending_pos = (self.announced_pos, me.position, ctx.t)
                elif ctx.t - self.pending_pos[2] > 4.0 and not me.in_pit:
                    frm, to, _ = self.pending_pos
                    self.pending_pos = None
                    self.announced_pos = to
                    other = ctx.state.car_behind() if to < frm else ctx.state.car_ahead()
                    if other is not None and (other.in_pit or abs(frm - to) > 1):
                        pass  # Positionswechsel durch Boxenstopps → nicht als Überholmanöver melden
                    else:
                        key = "race.overtook" if to < frm else "race.overtaken"
                        data: dict[str, Any] = {"pos": sp.position(to)}
                        if other is not None and other.driver_name:
                            data["name"] = sp.name(other.driver_name)
                        out.append(ctx.event(key, Priority.INFO, "race", ttl=8, cooldown_key="race.pos",
                                             cooldown_s=12, **data))
            else:
                self.pending_pos = None

        total = s.total_laps
        if total and lap_done:
            remaining = total - (me.lap - 1)
            for mark in (10, 5):
                if remaining == mark and mark not in self.laps_to_go_called and total > mark + 3:
                    self.laps_to_go_called.add(mark)
                    out.append(ctx.event("race.laps_to_go", Priority.CHATTER, "race", ttl=20, laps=sp.n_laps(mark)))
        if total and me.lap == total and not self.last_lap_called and me.lap > 1:
            self.last_lap_called = True
            out.append(ctx.event("race.last_lap", Priority.HIGH, "race", ttl=20, pos=sp.position(me.position)))
        if ctx.has_event(StateEventKind.CHEQUERED) and not self.finished_called:
            self.finished_called = True
            pos = me.position or 0
            key = "race.finished_win" if pos == 1 else "race.finished_podium" if pos in (2, 3) else "race.finished"
            out.append(ctx.event(key, Priority.HIGH, "race", ttl=60, allow_in_silence=True, interrupt=True,
                                 pos=sp.position(pos), summary=self._summary(ctx)))
        if me.status in (CarStatus.DNF, CarStatus.RETIRED) and not self.retired_called:
            self.retired_called = True
            out.append(ctx.event("race.retired", Priority.HIGH, "race", ttl=30))
        return out

    def _summary(self, ctx: EngineerContext) -> Spoken:
        sp = ctx.speaker
        st = ctx.state
        de = sp.lang == "de"
        disp: list[str] = []
        spoken: list[str] = []
        me = ctx.me
        if st.start_position and me and me.position:
            diff = st.start_position - me.position
            if diff > 0:
                g = sp.position(st.start_position)
                n = sp.int_(diff)
                disp.append(f"{'Von' if de else 'From'} {g.display} {'aus' if de else ''}, +{n.display}.".replace(" ,", ","))
                spoken.append(f"{'Von' if de else 'From'} {g.spoken}{' aus' if de else ''}, plus {n.spoken}.")
        if st.best_lap_s:
            t = sp.laptime(st.best_lap_s)
            disp.append(f"{'Beste Runde' if de else 'Best lap'} {t.display}.")
            spoken.append(f"{'Beste Runde' if de else 'Best lap'} {t.spoken}.")
        return Spoken(" ".join(disp), " ".join(spoken))
