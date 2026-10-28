"""Strategie: Reifenbriefing, Boxenfenster, Box diese Runde, SC-Stopp, Planwechsel, Undercut."""

from __future__ import annotations

from ...core.frame import PitState, SafetyCar, SessionPhase
from ...core.state import StateEventKind
from ..describe import describe_plan
from ..events import Priority, RadioEvent
from ..speech import Spoken
from .base import Detector, EngineerContext


class StrategyDetector(Detector):
    name = "strategy"

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.briefed = False
        self.window_called_for: int | None = None
        self.box_called_laps: list[int] = []
        self.box_now_called_lap: int | None = None
        self.sc_call_done = False
        self.announced_stop_lap: int | None = None
        self.announced_plan_label: str | None = None
        self.pit_exit_t: float | None = None
        self.pit_exit_reported = False
        self.undercut_chance_stint: int | None = None
        self.teammate_warned_lap: int | None = None
        self.rivals_to_stop_reported = False
        self.weather_box_for: tuple[str, int] | None = None

    def on_flashback(self, ctx: EngineerContext) -> None:
        self.box_called_laps = [lap for lap in self.box_called_laps if ctx.me and lap < ctx.me.lap]
        self.box_now_called_lap = None
        self.sc_call_done = False

    def update(self, ctx: EngineerContext) -> list[RadioEvent]:
        out: list[RadioEvent] = []
        me = ctx.me
        if not ctx.is_race or me is None or ctx.state.finished:
            return out
        sp = ctx.speaker
        snap = ctx.strategy
        s = ctx.frame.session
        plans = snap.plans
        plan_a = plans.plan_a if plans else None

        # Reifenbriefing vor dem Start (Startaufstellung/Einführungsrunde)
        brief_enabled = bool(ctx.config.get("engineer.tyre_briefing", True))
        if brief_enabled and not self.briefed and plan_a is not None and (
            s.phase in (SessionPhase.GRID, SessionPhase.FORMATION) or (me.lap <= 1 and (me.lap_pct or 0) < 0.3)
        ):
            self.briefed = True
            comp = sp.compound(ctx.frame.player.tyre_compound)
            b = plans.plan_b if plans else None
            plan_b_text = Spoken("", "")
            if b is not None:
                rendered = describe_plan(b, sp)
                plan_b_text = Spoken(f"Plan B: {rendered.display}.",
                                     f"Plan B: {rendered.spoken}.")
            out.append(ctx.event("strategy.briefing", Priority.INFO, "strategy", ttl=40, allow_in_silence=False,
                                 compound=comp, plan_a=describe_plan(plan_a, sp), plan_b_text=plan_b_text))
            self.announced_stop_lap = snap.next_stop_lap
            self.announced_plan_label = plan_a.label

        if me.lap < 1 or (s.phase is not SessionPhase.RUNNING and s.phase is not SessionPhase.UNKNOWN):
            return out

        # Boxenausfahrt
        if ctx.has_event(StateEventKind.PIT_EXIT):
            out.append(ctx.event("strategy.pit_confirm", Priority.INFO, "strategy", ttl=8))
            self.pit_exit_t = ctx.t
            self.pit_exit_reported = False
            self.box_called_laps.clear()
            self.announced_stop_lap = snap.next_stop_lap
            self.window_called_for = None
        if self.pit_exit_t is not None and not self.pit_exit_reported and ctx.t - self.pit_exit_t > 20 and \
                not me.in_pit:
            self.pit_exit_reported = True
            if snap.ahead and snap.ahead.gap_s is not None:
                out.append(ctx.event("strategy.pit_exit_position", Priority.INFO, "strategy", ttl=10,
                                     pos=sp.position(me.position), name=sp.name(snap.ahead.name),
                                     gap=sp.seconds(abs(snap.ahead.gap_s))))
            if snap.rivals_to_stop and not self.rivals_to_stop_reported:
                ahead_to_stop = [i for i in snap.rivals_to_stop
                                 if (c := ctx.frame.car(i)) and c.position and me.position and c.position < me.position]
                if len(ahead_to_stop) >= 2:
                    self.rivals_to_stop_reported = True
                    out.append(ctx.event("strategy.rivals_to_stop", Priority.INFO, "strategy", ttl=30,
                                         n=sp.int_(len(ahead_to_stop))))

        # Safety Car → Stopp lohnt?
        if s.safety_car in (SafetyCar.SC, SafetyCar.VSC, SafetyCar.ENDING) and plan_a is not None and not self.sc_call_done and \
                not me.in_pit and snap.sc_gain_s is not None:
            self.sc_call_done = True
            if snap.box_under_sc:
                next_comp = plan_a.compounds[1] if len(plan_a.compounds) > 1 else None
                out.append(ctx.event("strategy.box_under_sc", Priority.CRITICAL, "strategy", ttl=25, interrupt=True,
                                     allow_in_silence=True, compound=sp.compound(next_comp),
                                     gain=sp.seconds_unit(snap.sc_gain_s, 0)))
                self.box_called_laps.append(me.lap)
                ctx.flags["box_requested_lap"] = me.lap
                ctx.flags["box_compound"] = next_comp
            elif plan_a.stops > 0:
                out.append(ctx.event("strategy.stay_out_sc", Priority.INFO, "strategy", ttl=20))
        if s.safety_car is SafetyCar.NONE:
            self.sc_call_done = False

        # Wetter-Crossover → Box für Regen-/Trockenreifen
        adv = snap.weather
        current = ctx.frame.player.tyre_compound
        if adv and adv.recommendation and me.pit_state is PitState.ON_TRACK and (
            self.weather_box_for != (adv.recommendation, ctx.state.pit_stops_made)
        ) and 0.1 < (me.lap_pct or 0) < 0.75 and current is not None:
            target = {"inters": "inter", "wets": "wet"}.get(adv.recommendation)
            if adv.recommendation == "slicks":
                target = "dry" if current not in ("inter",) and not ctx.formula else "medium"
            if target and target != current:
                self.weather_box_for = (adv.recommendation, ctx.state.pit_stops_made)
                remaining = (s.total_laps or 999) - me.lap
                if remaining >= 2:
                    out.append(ctx.event("strategy.box_this_lap", Priority.CRITICAL, "strategy", ttl=20,
                                         compound=sp.compound(target), cooldown_key="strategy.box", cooldown_s=20))
                    self.box_called_laps.append(me.lap)
                    ctx.flags["box_requested_lap"] = me.lap
                    ctx.flags["box_compound"] = target

        if plan_a is None:
            return out

        # Planwechsel / Plan A plus N
        if snap.next_stop_lap is not None and self.announced_stop_lap is not None and \
                ctx.has_event(StateEventKind.LAP_COMPLETED) and not me.in_pit:
            shift = snap.next_stop_lap - self.announced_stop_lap
            if plan_a.label != self.announced_plan_label and self.announced_plan_label is not None:
                out.append(ctx.event("strategy.plan_change", Priority.HIGH, "strategy", ttl=30,
                                     plan=describe_plan(plan_a, sp)))
                self.announced_plan_label = plan_a.label
                self.announced_stop_lap = snap.next_stop_lap
            elif shift >= 2 and snap.next_stop_lap > me.lap + 1:
                out.append(ctx.event("strategy.plan_a_plus", Priority.INFO, "strategy", ttl=30, laps=sp.int_(shift)))
                self.announced_stop_lap = snap.next_stop_lap
            elif shift <= -2 and snap.next_stop_lap >= me.lap:
                out.append(ctx.event("strategy.plan_earlier", Priority.HIGH, "strategy", ttl=30,
                                     lap=sp.int_(snap.next_stop_lap)))
                self.announced_stop_lap = snap.next_stop_lap
        elif self.announced_stop_lap is None and snap.next_stop_lap is not None:
            self.announced_stop_lap = snap.next_stop_lap
            self.announced_plan_label = plan_a.label

        # Boxenfenster
        stop_no = ctx.state.pit_stops_made
        if snap.window_open and snap.window and self.window_called_for != stop_no and \
                snap.next_stop_lap and snap.next_stop_lap > me.lap and not me.in_pit and me.lap >= 2:
            self.window_called_for = stop_no
            out.append(ctx.event("strategy.window_open", Priority.INFO, "strategy", ttl=30,
                                 lo=sp.int_(snap.window[0]), hi=sp.int_(snap.window[1])))

        # Box diese Runde (rechtzeitig vor der Boxeneinfahrt, max. 2 Runden in Folge)
        pct = me.lap_pct or 0.0
        if snap.box_this_lap and me.pit_state is PitState.ON_TRACK and me.lap not in self.box_called_laps and \
                0.1 < pct < 0.75 and len(self.box_called_laps) < 2:
            self.box_called_laps.append(me.lap)
            next_comp = plan_a.compounds[1] if len(plan_a.compounds) > 1 else None
            out.append(ctx.event("strategy.box_this_lap", Priority.CRITICAL, "strategy", ttl=20,
                                 cooldown_key="strategy.box", cooldown_s=20, compound=sp.compound(next_comp)))
            ctx.flags["box_requested_lap"] = me.lap
            ctx.flags["box_compound"] = next_comp
        if me.lap in self.box_called_laps and self.box_now_called_lap != me.lap and 0.82 < pct < 0.95 and \
                me.pit_state is PitState.ON_TRACK:
            self.box_now_called_lap = me.lap
            out.append(ctx.event("strategy.box_now", Priority.CRITICAL, "strategy", ttl=5, allow_in_silence=True))
        if me.lap in self.box_called_laps and snap.teammate_in_pit and self.teammate_warned_lap != me.lap and \
                pct > 0.6:
            self.teammate_warned_lap = me.lap
            out.append(ctx.event("strategy.teammate_in_pit", Priority.HIGH, "strategy", ttl=10))

        # Gegner stoppt → Undercut-Gefahr
        for ev in ctx.all_events(StateEventKind.OPPONENT_PIT_ENTRY):
            idx = ev.data.get("idx")
            if snap.behind and idx == snap.behind.idx and snap.behind.gap_s is not None and abs(snap.behind.gap_s) < 4:
                if plan_a.stops > 0 and (snap.next_stop_lap or 999) - me.lap <= 8:
                    out.append(ctx.event("strategy.rival_pitted_behind", Priority.HIGH, "strategy", ttl=15,
                                         name=sp.name(snap.behind.name)))
            elif snap.ahead and idx == snap.ahead.idx and snap.ahead.gap_s is not None and abs(snap.ahead.gap_s) < 4:
                out.append(ctx.event("strategy.rival_pitted_ahead", Priority.INFO, "strategy", ttl=15,
                                     name=sp.name(snap.ahead.name)))

        # Undercut-Chance
        uc = snap.undercut_chance
        stint_no = len(ctx.state.stints)
        if uc and uc.feasible and snap.window_open and self.undercut_chance_stint != stint_no and \
                ctx.has_event(StateEventKind.LAP_COMPLETED) and (ctx.frame.player.tyre_age_laps or 0) >= 4 and \
                not me.in_pit and not snap.box_this_lap and me.lap not in self.box_called_laps:
            self.undercut_chance_stint = stint_no
            out.append(ctx.event("strategy.undercut_chance", Priority.INFO, "strategy", ttl=20,
                                 name=sp.name(uc.rival_name), gap=sp.seconds(uc.gap_s)))
        return out
