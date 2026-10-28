"""Claude-Anbindung mit einem Fake-Client testen (kein Netz, keine Kosten)."""

import asyncio
import json
from types import SimpleNamespace

from pitwall.adapters.simulator import RaceSimulator
from pitwall.brain.llm import Brain
from pitwall.brain.tools import TOOLS, run_tool, tool_definitions
from pitwall.core.state import RaceState
from pitwall.engineer.engineer import Engineer


class _Block(SimpleNamespace):
    def model_dump(self, exclude_none=True):
        return {k: v for k, v in self.__dict__.items() if v is not None}


class _Stream:
    def __init__(self, chunks, final):
        self.chunks, self.final = chunks, final

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    @property
    def text_stream(self):
        async def gen():
            for c in self.chunks:
                await asyncio.sleep(0)
                yield c
        return gen()

    async def get_final_message(self):
        return self.final


class FakeClient:
    """Erste Runde: Tool-Aufruf get_gaps, zweite Runde: Antworttext."""

    def __init__(self):
        self.calls = []
        self.messages = self

    def stream(self, **kw):
        self.calls.append(kw)
        usage = SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=0,
                                cache_creation_input_tokens=0)
        if len(self.calls) == 1:
            final = SimpleNamespace(stop_reason="tool_use", usage=usage,
                                    content=[_Block(type="tool_use", id="t1", name="get_gaps", input={})])
            return _Stream([], final)
        tool_result = json.loads(kw["messages"][-1]["content"][0]["content"])
        gap = abs(tool_result["ahead"]["gap_s"])
        text = f"Okay, {gap:.1f} auf {tool_result['ahead']['name']}. Bleib dran."
        final = SimpleNamespace(stop_reason="end_turn", usage=usage, content=[_Block(type="text", text=text)])
        return _Stream([text[:10], text[10:]], final)


def _engineer(cfg):
    sim = RaceSimulator(laps=8)
    st = RaceState()
    eng = Engineer(cfg)
    for f in sim.run(max_steps=10 * 320):
        eng.process(f, st, st.update(f))
    return eng


def test_tool_definitions_are_deterministic_and_cached():
    d1, d2 = tool_definitions(), tool_definitions()
    assert d1 == d2
    assert [d["name"] for d in d1] == sorted(TOOLS)
    assert d1[-1]["cache_control"] == {"type": "ephemeral"}


def test_all_tools_return_json(cfg):
    eng = _engineer(cfg)
    for name in TOOLS:
        out = json.loads(run_tool(eng, name, {}))
        assert isinstance(out, dict)
    gaps = json.loads(run_tool(eng, "get_gaps", {}))
    assert gaps["ahead"]["gap_s"] is not None


def test_brain_tool_loop_streams_sentences(cfg, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    eng = _engineer(cfg)
    brain = Brain(cfg)
    fake = FakeClient()
    brain.client = fake
    brain.bind(eng)

    async def go():
        return [s async for s in brain.ask("Wie sieht's nach vorne aus?", eng)]

    sentences = asyncio.run(go())
    assert sentences[0].startswith("Okay,") and sentences[-1] == "Bleib dran."
    first = fake.calls[0]
    assert first["model"] == "claude-haiku-5-5"
    assert first["system"][0]["cache_control"]["type"] == "ephemeral"
    assert first["thinking"] == {"type": "disabled"}
    assert "temperature" not in first
    # Tool-Ergebnis in genau einer User-Nachricht zurück
    second = fake.calls[1]["messages"]
    assert second[-1]["role"] == "user" and second[-1]["content"][0]["type"] == "tool_result"
    assert brain.budget.usage.calls == 2


def test_brain_without_key_is_unavailable(cfg):
    brain = Brain(cfg)
    assert not brain.available
    assert "API-Key" in brain.status
