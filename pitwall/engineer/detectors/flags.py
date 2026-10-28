"""Flaggen & Sicherheit: Gelb (Sektor), SC/VSC, Blau, Rot, Strafen, Track Limits, Incidents."""

from __future__ import annotations

from ...core.frame import Capability, Flag, GameEventKind, SafetyCar
from ...core.state import StateEventKind
from ..events import Priority, RadioEvent
from ..speech import Spoken
from .base import Detector, EngineerContext

_PENALTY_DE = {
    "drive_through": "Durchfahrtsstrafe", "stop_go": "Stop-and-Go", "time": "Zeitstrafe", "warning": "Verwarnung",
    "grid": "Startplatzstrafe", "disqualified": "Disqualifikation", "lap_invalidated": "Runde gestrichen",
    "this_and_next_lap_invalidated": "Diese und nächste Runde gestrichen", "retired": "Aus dem Rennen genommen",
}
_PENALTY_EN = {
    "drive_through": "drive through", "stop_go": "stop and go", "time": "time penalty", "warning": "warning",
    "grid": "grid penalty", "disqualified": "disqualified", "lap_invalidated": "lap deleted",
    "this_and_next_lap_invalidated": "this and next lap deleted", "retired": "retired",
}


class FlagDetector(Detector):
    name = "flags"

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.last_yellow_sectors: tuple[int, ...] = ()
        self.last_blue_t = -1e9
        self.last_warnings: int | None = None
        self.last_incidents: int | None = None
        self.last_flag = Flag.NONE

    def update(self, ctx: EngineerContext) -> list[RadioEvent]:
        out: list[RadioEvent] = []
        f = ctx.frame
        s = f.session
        sp = ctx.speaker
        if ctx.state.finished:
            return out
        sc_ev = ctx.has_event(StateEventKind.SAFETY_CAR_CHANGED)
        if sc_ev:
            to = sc_ev.data["to"]
            frm = sc_ev.data["from"]
            if to is SafetyCar.SC:
                out.append(ctx.event("flags.sc_deployed", Priority.CRITICAL, "flags", ttl=15, interrupt=True,
                                     allow_in_silence=True))
            elif to is SafetyCar.VSC:
                out.append(ctx.event("flags.vsc_deployed", Priority.CRITICAL, "flags", ttl=15, interrupt=True,
                                     allow_in_silence=True))
            elif to is SafetyCar.FCY:
                out.append(ctx.event("flags.fcy", Priority.CRITICAL, "flags", ttl=15, interrupt=True,
                                     allow_in_silence=True))
            elif to is SafetyCar.ENDING:
                key = "flags.vsc_ending" if frm is SafetyCar.VSC else "flags.sc_ending"
                out.append(ctx.event(key, Priority.HIGH, "flags", ttl=20, allow_in_silence=True,
                                     cooldown_key="flags.ending", cooldown_s=30))
            elif to is SafetyCar.NONE and frm in (SafetyCar.SC, SafetyCar.VSC, SafetyCar.FCY, SafetyCar.ENDING):
                out.append(ctx.event("flags.green", Priority.HIGH, "flags", ttl=8, allow_in_silence=True))
        # Spiel-Events für SC (F1 meldet "returning")
        for ge in f.events:
            if ge.kind is GameEventKind.SAFETY_CAR and ge.data.get("event") == "returning" and \
                    s.safety_car not in (SafetyCar.NONE, SafetyCar.ENDING):
                key = "flags.vsc_ending" if ge.data.get("type") == "vsc" else "flags.sc_ending"
                out.append(ctx.event(key, Priority.HIGH, "flags", ttl=20, cooldown_key="flags.ending",
                                     cooldown_s=30))
            elif ge.kind is GameEventKind.PENALTY and ge.car_idx == f.player_idx:
                ptype = str(ge.data.get("penalty_type", ""))
                table = _PENALTY_DE if sp.lang == "de" else _PENALTY_EN
                text = table.get(ptype, ge.data.get("text") or ptype or ("Strafe" if sp.lang == "de" else "penalty"))
                secs = ge.data.get("time")
                if ptype == "time" and secs:
                    secs_sp = sp.seconds_unit(float(secs), 0)
                    text_sp = Spoken(f"{secs_sp.display} {text}", f"{secs_sp.spoken} {text}")
                else:
                    text_sp = Spoken(str(text), str(text))
                if ptype == "warning" or ge.data.get("infringement") in ("corner_cutting", "track_limits"):
                    continue  # Track Limits meldet unten der Warnungszähler
                out.append(ctx.event("flags.penalty", Priority.HIGH, "flags", ttl=20, penalty=text_sp))
            elif ge.kind is GameEventKind.RED_FLAG:
                out.append(ctx.event("flags.red", Priority.CRITICAL, "flags", ttl=20, interrupt=True,
                                     allow_in_silence=True))

        fl_ev = ctx.has_event(StateEventKind.FLAG_CHANGED)
        if fl_ev and f.has(Capability.FLAGS):
            to = s.flag
            if to is Flag.BLUE and f.t - self.last_blue_t > 25:
                self.last_blue_t = f.t
                out.append(ctx.event("flags.blue", Priority.HIGH, "flags", ttl=6, allow_in_silence=True))
            elif to is Flag.RED:
                out.append(ctx.event("flags.red", Priority.CRITICAL, "flags", ttl=20, interrupt=True,
                                     allow_in_silence=True, cooldown_key="flags.red", cooldown_s=60))
            elif to is Flag.BLACK:
                out.append(ctx.event("flags.black", Priority.CRITICAL, "flags", ttl=20, interrupt=True))
            elif to is Flag.MEATBALL:
                out.append(ctx.event("flags.meatball", Priority.CRITICAL, "flags", ttl=20, interrupt=True))
            elif to is Flag.BLACK_WHITE:
                out.append(ctx.event("flags.warning", Priority.HIGH, "flags", ttl=10))
            elif to in (Flag.YELLOW, Flag.DOUBLE_YELLOW) and not s.yellow_sectors and \
                    s.safety_car is SafetyCar.NONE:
                key = "flags.double_yellow" if to is Flag.DOUBLE_YELLOW else "flags.yellow"
                sector = (ctx.me.sector if ctx.me and ctx.me.sector else 1)
                out.append(ctx.event(key, Priority.HIGH, "flags", ttl=6, allow_in_silence=True,
                                     cooldown_key="flags.yellow", cooldown_s=20, sector=sp.int_(sector)))
            self.last_flag = to
        # Sektor-Gelb (F1 Marshal-Zonen, rF2 Sektorflaggen, ACC)
        if f.has(Capability.SECTOR_FLAGS) and s.safety_car is SafetyCar.NONE:
            ys = tuple(sorted(set(s.yellow_sectors)))
            new = [x for x in ys if x not in self.last_yellow_sectors]
            if new and ctx.me is not None:
                # nur melden, wenn der Sektor vor mir liegt (aktueller oder nächster)
                my = ctx.me.sector or 1
                relevant = [x for x in new if x in (my, my % 3 + 1)]
                if relevant:
                    key = "flags.double_yellow" if s.flag is Flag.DOUBLE_YELLOW else "flags.yellow"
                    out.append(ctx.event(key, Priority.HIGH, "flags", ttl=6, allow_in_silence=True,
                                         cooldown_key="flags.yellow", cooldown_s=15, sector=sp.int_(relevant[0])))
            self.last_yellow_sectors = ys

        # Track Limits / Incidents
        w = f.player.warnings
        if w is not None:
            if self.last_warnings is not None and w > self.last_warnings:
                out.append(ctx.event("flags.track_limits", Priority.INFO, "flags", ttl=10, count=sp.int_(w),
                                     cooldown_key="flags.tl", cooldown_s=10))
            self.last_warnings = w
        inc = f.player.incidents
        if inc is not None:
            if self.last_incidents is not None and inc >= self.last_incidents + 4:
                out.append(ctx.event("flags.incidents", Priority.INFO, "flags", ttl=10, count=sp.int_(inc)))
                self.last_incidents = inc
            elif self.last_incidents is None:
                self.last_incidents = inc
        return out
