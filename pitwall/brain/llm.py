"""Engineer Brain mit Claude (Anthropic Python SDK).

* Schnelles Modell (Haiku) für Funk/Fragen, optional größeres (Sonnet) für
  Strategie-Abwägung und Debrief.
* Tool Use statt Datendump: das Modell holt sich exakt berechnete Werte.
* Prompt Caching für System-Prompt + Tool-Liste (statisch, deterministisch sortiert).
* Streaming: Sätze werden ausgegeben, sobald sie fertig sind → TTS startet früh.
* Timeout/Fehler → Fallback auf Phrasebook; Fehler gehen nie ins Audio.
* Token-Zähler + Tageslimit.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..config import Config, secret
from .tools import run_tool, tool_definitions

if TYPE_CHECKING:
    from ..engineer.engineer import Engineer

log = logging.getLogger(__name__)
_PROMPT_DIR = Path(__file__).resolve().parent / "prompts"

# $/MTok (Eingabe, Ausgabe) – nur für die Kostenanzeige im Dashboard
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
}

_SENTENCE_END = re.compile(r"([.!?])(\s+|$)")


@dataclass
class Usage:
    day: str = field(default_factory=lambda: date.today().isoformat())
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    calls: int = 0
    cost_usd: float = 0.0
    session_input: int = 0
    session_output: int = 0

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class TokenBudget:
    def __init__(self, path: Path, daily_limit: int):
        self.path = path
        self.daily_limit = daily_limit
        self.usage = Usage()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("day") == self.usage.day:
                for k, v in data.items():
                    if hasattr(self.usage, k):
                        setattr(self.usage, k, v)
                self.usage.session_input = self.usage.session_output = 0
        except (OSError, ValueError):
            pass

    @property
    def exhausted(self) -> bool:
        if self.daily_limit <= 0:
            return False
        return self.usage.input_tokens + self.usage.output_tokens >= self.daily_limit

    def add(self, model: str, usage: Any) -> None:
        today = date.today().isoformat()
        if self.usage.day != today:
            self.usage = Usage(day=today)
        inp = int(getattr(usage, "input_tokens", 0) or 0)
        out = int(getattr(usage, "output_tokens", 0) or 0)
        cr = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        cw = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
        u = self.usage
        u.input_tokens += inp + cr + cw
        u.output_tokens += out
        u.cache_read += cr
        u.cache_write += cw
        u.session_input += inp + cr + cw
        u.session_output += out
        u.calls += 1
        pin, pout = PRICES.get(model, (1.0, 5.0))
        u.cost_usd += (inp * pin + cr * pin * 0.1 + cw * pin * 1.25 + out * pout) / 1_000_000
        try:
            self.path.write_text(json.dumps(u.to_dict()), encoding="utf-8")
        except OSError:
            pass


class LLMUnavailable(RuntimeError):
    pass


class Brain:
    def __init__(self, config: Config):
        self.config = config
        bc = config.section("brain")
        self.enabled = bool(bc.get("enabled", True))
        self.radio_model = str(bc.get("radio_model", "claude-haiku-5-5"))
        self.strategy_model = str(bc.get("strategy_model", "claude-sonnet-5-5"))
        self.use_strategy_model = bool(bc.get("use_strategy_model", True))
        self.timeout_s = float(bc.get("timeout_s", 2.5))
        self.max_tokens = int(bc.get("max_tokens", 300))
        self.caching = bool(bc.get("prompt_caching", True))
        self.lang = config.language
        self.budget = TokenBudget(config.data_dir / "llm_usage.json", int(bc.get("daily_token_limit", 400000)))
        self.client: Any = None
        self.status = "aus"
        self.last_latency_ms: float | None = None
        self.last_first_token_ms: float | None = None
        self.last_error: str | None = None
        self.history: list[dict[str, Any]] = []  # kurze Q/A-Historie dieses Rennens
        if not self.enabled:
            self.status = "deaktiviert"
            return
        if not secret("ANTHROPIC_API_KEY"):
            self.status = "kein API-Key (Phrasebook-Modus)"
            return
        try:
            import anthropic

            self.client = anthropic.AsyncAnthropic(max_retries=0, timeout=max(10.0, self.timeout_s * 4))
            self.status = "bereit"
        except ImportError:
            self.status = "anthropic-SDK nicht installiert (pip install 'pitwall[llm]')"

    @property
    def available(self) -> bool:
        return self.client is not None and not self.budget.exhausted

    def reset(self) -> None:
        self.history.clear()

    def system_prompt(self) -> str:
        name = str(self.config.get("engineer.name", "Max"))
        driver = str(self.config.get("engineer.driver_name", "") or "")
        de = self.lang == "de"
        text = (_PROMPT_DIR / f"system_{'de' if de else 'en'}.md").read_text(encoding="utf-8")
        label = driver or ("deinem Fahrer" if de else "your driver")
        return text.replace("{engineer_name}", name).replace("{driver_label}", label)

    def _system_blocks(self) -> list[dict[str, Any]]:
        block: dict[str, Any] = {"type": "text", "text": self.system_prompt()}
        if self.caching:
            block["cache_control"] = {"type": "ephemeral"}
        return [block]

    @staticmethod
    def situation_header(eng: Engineer) -> str:
        f = eng.frame
        if f is None:
            return "Lage: keine Telemetrie."
        s = f.session
        me = f.me
        parts = [f"Sim={f.sim}", f"Klasse={s.vehicle_class}", f"Session={s.session_type}",
                 f"Strecke={s.track_name}"]
        if me:
            parts.append(f"Runde={me.lap}" + (f"/{s.total_laps}" if s.total_laps else ""))
            if me.position:
                parts.append(f"Position=P{me.position}")
        if s.safety_car.value != "none":
            parts.append(f"SC={s.safety_car}")
        if s.flag.value not in ("none", "green"):
            parts.append(f"Flagge={s.flag}")
        return "Lage: " + ", ".join(parts)

    async def ask(self, question: str, eng: Engineer, strategic: bool = False) -> AsyncIterator[str]:
        """Beantwortet eine Frage – liefert fertige Sätze nacheinander (Streaming)."""
        if not self.available:
            raise LLMUnavailable(self.status if not self.budget.exhausted else "Tageslimit erreicht")
        model = self.strategy_model if (strategic and self.use_strategy_model) else self.radio_model
        memory = eng.memory.context_text(10, self.lang)
        user_text = f"{self.situation_header(eng)}\n{memory}\n\nFahrer: {question}" if self.lang == "de" else \
            f"{self.situation_header(eng)}\n{memory}\n\nDriver: {question}"
        messages: list[dict[str, Any]] = [*self.history[-6:], {"role": "user", "content": user_text}]
        full_answer: list[str] = []
        async for sentence in self._run(model, messages, strategic):
            full_answer.append(sentence)
            yield sentence
        answer = " ".join(full_answer).strip()
        if answer:
            self.history.append({"role": "user", "content": f"Fahrer: {question}"})
            self.history.append({"role": "assistant", "content": answer})
            self.history = self.history[-12:]

    async def situation_update(self, eng: Engineer, summary: str) -> str | None:
        """Periodisches Strategie-Update / komplexe Lage. Gibt Funkspruch oder None zurück."""
        if not self.available:
            return None
        model = self.strategy_model if self.use_strategy_model else self.radio_model
        content = f"{self.situation_header(eng)}\n{eng.memory.context_text(8, self.lang)}\n\n{summary}"
        sentences = []
        try:
            async for s in self._run(model, [{"role": "user", "content": content}], strategic=True):
                sentences.append(s)
        except (LLMUnavailable, TimeoutError):
            return None
        text = " ".join(sentences).strip()
        if not text or text in ("–", "-", "—") or len(text) < 4:
            return None
        return text

    def _params(self, model: str, strategic: bool) -> dict[str, Any]:
        params: dict[str, Any] = {"model": model, "max_tokens": self.max_tokens * (3 if strategic else 1)}
        if model.startswith("claude-haiku"):
            # Latenz: kein Denken, niedrige Effort-Stufe (bei Haiku 5.5 erlaubt bis effort=high)
            params["thinking"] = {"type": "disabled"}
            params["output_config"] = {"effort": "low"}
        elif model.startswith("claude-sonnet-5"):
            params["thinking"] = {"type": "between_tools"} if not strategic else {"type": "adaptive"}
            params["output_config"] = {"effort": "low" if not strategic else "medium"}
        else:
            params["output_config"] = {"effort": "low"}
        return params

    async def _run(self, model: str, messages: list[dict[str, Any]], strategic: bool) -> AsyncIterator[str]:
        """Tool-Use-Schleife mit Streaming; gibt Sätze aus, sobald sie vollständig sind."""
        import anthropic

        from ..engineer.engineer import Engineer  # noqa: F401  (nur Typ)

        eng = self._current_eng
        tools = tool_definitions(cache_last=self.caching)
        t0 = time.monotonic()
        first_token_at: float | None = None
        convo = list(messages)
        for _round in range(4):
            buf = ""
            try:
                stream_cm = self.client.messages.stream(system=self._system_blocks(), tools=tools, messages=convo,
                                                        **self._params(model, strategic))
                async with asyncio.timeout(self.timeout_s * (3 if strategic else 2) + 4):
                    async with stream_cm as stream:
                        it = stream.text_stream.__aiter__()
                        while True:
                            timeout = None
                            if first_token_at is None:
                                timeout = max(0.2, self.timeout_s - (time.monotonic() - t0)) * (2 if strategic else 1)
                            try:
                                chunk = await asyncio.wait_for(it.__anext__(), timeout=timeout)
                            except StopAsyncIteration:
                                break
                            if first_token_at is None:
                                first_token_at = time.monotonic()
                                self.last_first_token_ms = (first_token_at - t0) * 1000
                            buf += chunk
                            while True:
                                m = _SENTENCE_END.search(buf)
                                if not m:
                                    break
                                sentence = _clean(buf[: m.end(1)])
                                buf = buf[m.end():]
                                if sentence:
                                    yield sentence
                        final = await stream.get_final_message()
            except TimeoutError as exc:
                self.last_error = "Timeout"
                raise LLMUnavailable("Timeout") from exc
            except anthropic.APIConnectionError as exc:
                self.last_error = "Verbindung"
                raise LLMUnavailable("keine Verbindung") from exc
            except anthropic.RateLimitError as exc:
                self.last_error = "Rate-Limit"
                raise LLMUnavailable("Rate-Limit") from exc
            except anthropic.APIStatusError as exc:
                self.last_error = f"API {exc.status_code}"
                log.warning("Claude-API-Fehler %s: %s", exc.status_code, getattr(exc, "message", exc))
                raise LLMUnavailable(f"API-Fehler {exc.status_code}") from exc
            self.budget.add(model, final.usage)
            rest = _clean(buf)
            if final.stop_reason == "refusal":
                raise LLMUnavailable("refusal")
            if final.stop_reason != "tool_use":
                if rest:
                    yield rest
                self.last_latency_ms = (time.monotonic() - t0) * 1000
                return
            if rest:
                yield rest
            # Tools ausführen (alle Ergebnisse in EINER User-Nachricht zurück)
            convo.append({"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in final.content]})
            results = []
            for block in final.content:
                if block.type == "tool_use":
                    out = run_tool(eng, block.name, block.input if isinstance(block.input, dict) else {})
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": out})
            convo.append({"role": "user", "content": results})
        self.last_latency_ms = (time.monotonic() - t0) * 1000

    # Engineer-Referenz für Tool-Ausführung (gesetzt durch bind)
    _current_eng: Any = None

    def bind(self, eng: Engineer) -> None:
        self._current_eng = eng

    def info(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "available": self.available,
            "radio_model": self.radio_model,
            "strategy_model": self.strategy_model if self.use_strategy_model else None,
            "usage": self.budget.usage.to_dict(),
            "daily_limit": self.budget.daily_limit,
            "last_latency_ms": None if self.last_latency_ms is None else round(self.last_latency_ms),
            "last_first_token_ms": None if self.last_first_token_ms is None else round(self.last_first_token_ms),
            "last_error": self.last_error,
        }


def _clean(text: str) -> str:
    """Markdown/Emojis entfernen – alles wird vorgelesen."""
    t = re.sub(r"[*_#`>\[\]]", "", text)
    t = re.sub(r"[\U0001F300-\U0001FAFF☀-➿]", "", t)
    return " ".join(t.split()).strip()
