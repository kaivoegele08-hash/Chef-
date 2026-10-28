"""Engineer-Dry-Run: spielt Simulator oder Aufnahme ab und gibt alle Funksprüche als Text aus."""

from __future__ import annotations

import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import TextIO

from .config import Config
from .core.frame import TelemetryFrame
from .core.state import RaceState
from .engineer.engineer import Engineer
from .engineer.events import Utterance


@dataclass
class DryRunResult:
    utterances: list[Utterance] = field(default_factory=list)
    frames: int = 0
    wall_s: float = 0.0
    laps: int = 0
    engineer: Engineer | None = None
    state: RaceState | None = None

    def keys(self) -> list[str]:
        return [u.event.key for u in self.utterances]

    def texts(self) -> list[str]:
        return [u.text for u in self.utterances]


def _fmt_t(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m):02d}:{s:04.1f}"


def run_frames(frames: Iterable[TelemetryFrame], config: Config, out: TextIO | None = None,
               follow_box: object | None = None, show_priority: bool = True) -> DryRunResult:
    """Läuft alle Frames durch State + Engineer. ``follow_box``: Adapter mit ``command('box')``."""
    state = RaceState()
    engineer = Engineer(config)
    result = DryRunResult(engineer=engineer, state=state)

    def sink(u: Utterance) -> None:
        result.utterances.append(u)
        if follow_box is not None:
            if u.event.key in ("strategy.box_this_lap", "strategy.box_under_sc", "strategy.box_now"):
                follow_box.command("box", engineer.flags.get("box_compound"))  # type: ignore[attr-defined]
            elif u.event.key in ("fuel.critical", "fuel.save", "fuel.empty_no_refuel"):
                follow_box.command("save_fuel")  # type: ignore[attr-defined]
            elif u.event.key == "fuel.save_done":
                follow_box.command("normal_fuel")  # type: ignore[attr-defined]
        if out is not None:
            lap = f"R{u.lap:>2}" if u.lap else "R -"
            prio = f" {u.priority.name:<8}" if show_priority else ""
            out.write(f"[{lap} {_fmt_t(u.start_t)}]{prio} {u.text}\n")

    engineer.sinks.append(sink)
    t0 = time.perf_counter()
    for frame in frames:
        result.frames += 1
        events = state.update(frame)
        engineer.process(frame, state, events)
        if frame.me:
            result.laps = max(result.laps, frame.me.lap)
    result.wall_s = time.perf_counter() - t0
    return result


def simulate(config: Config, scenario: str = "default", laps: int | None = None, seed: int | None = None,
             follow_engineer: bool = True, out: TextIO | None = None) -> DryRunResult:
    from .adapters.simulator import RaceSimulator

    sc = config.section("simulator")
    sim = RaceSimulator(
        seed=int(seed if seed is not None else sc.get("seed", 7)),
        n_cars=int(sc.get("cars", 20)),
        laps=int(laps if laps is not None else sc.get("laps", 20)),
        scenario=scenario,
        hz=float(sc.get("hz", 20)),
    )
    sim._player_follows_engineer = follow_engineer  # type: ignore[attr-defined]

    class _Cmd:
        def command(self, name: str, arg: str | None = None) -> None:
            if name == "box":
                sim.request_player_pit(arg)
            elif name == "save_fuel":
                sim.player_fuel_saving = True
            elif name == "normal_fuel":
                sim.player_fuel_saving = False

    def frames() -> Iterator[TelemetryFrame]:
        yield from sim.run()

    return run_frames(frames(), config, out, follow_box=_Cmd() if follow_engineer else None)
