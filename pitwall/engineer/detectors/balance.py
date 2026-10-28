"""Fragt nach der Balance (einmal pro Stint, früh) und merkt sich die Antwort."""

from __future__ import annotations

from ...core.state import StateEventKind
from ..events import Priority, RadioEvent
from .base import Detector, EngineerContext


class BalanceDetector(Detector):
    name = "balance"

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.asked_stints: set[int] = set()

    def on_flashback(self, ctx: EngineerContext) -> None:
        pass

    def update(self, ctx: EngineerContext) -> list[RadioEvent]:
        out: list[RadioEvent] = []
        if not ctx.config.get("engineer.ask_balance", True) or ctx.me is None or ctx.is_quali:
            return out
        if not ctx.has_event(StateEventKind.LAP_COMPLETED):
            return out
        stint = len(ctx.state.stints)
        stint_obj = ctx.state.current_stint()
        laps_in = stint_obj.laps(ctx.me.lap) if stint_obj else 0
        if stint in self.asked_stints or stint > 2:
            return out
        if laps_in >= 3 and (ctx.me.lap >= 3) and not ctx.state.finished:
            self.asked_stints.add(stint)
            ctx.memory.awaiting_balance = True
            out.append(ctx.event("balance.ask", Priority.CHATTER, "balance", ttl=30))
        return out
