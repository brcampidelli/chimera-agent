"""What the compaction summariser costs is charged to the run that compacted.

`summarise_compaction` makes one model call per compaction (`chimera/core/summarise.py`), and the
Code screen sends it on every turn. The call went to the backend directly, beside the loop's own
`_step`, so its tokens and price reached neither the run's result nor, through it, the turn's row in
`usage.jsonl`: the receipt under the answer and the Cost screen both read a turn that compacted as
cheaper than it was.

It is charged the way the tool router's call already is: to the run's tally and spend ceiling, the
moment it returns. So it lands in the turn's own line rather than in a line of its own, which is
what it is: a call the turn made between two of its steps.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import chimera.fusion.receipts as receipts
from chimera.api.usage import load_usage, summarize_usage
from chimera.config import Settings
from chimera.core import Agent, AgentConfig
from chimera.core.summarise import SYSTEM as SUMMARY_SYSTEM
from chimera.fusion.receipts import ModelPrice
from chimera.interface import ChatSession
from chimera.providers.gateway import CompletionResult, ToolCall
from chimera.tools import ToolRegistry
from chimera.tools.builtin import EchoTool

#: Free, the way a local model is priced: the steps' prompts are what cross the compaction trigger,
#: and a known zero leaves the run's price exactly what the summary cost.
STEP_MODEL = "test/summary-step"
SUMMARY_MODEL = "test/summary-priced"  # $3/M in, $4/M out
#: 2,000 prompt tokens at $3/M plus 200 completion tokens at $4/M.
SUMMARY_COST = 0.0068


@pytest.fixture(autouse=True)
def _priced(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exact prices for the fake models, restored afterwards. The shipped catalogue is folded in
    first, so the restore cannot leave the table without it."""
    receipts._ensure_catalog_registered()
    monkeypatch.setattr(receipts, "_PRICES", [
        (STEP_MODEL, ModelPrice(0.0, 0.0)), (SUMMARY_MODEL, ModelPrice(3.0, 4.0)),
        *receipts._PRICES,
    ])


def _system_of(messages: list[Any]) -> str:
    first = messages[0]
    return str(first.get("content") if isinstance(first, dict) else getattr(first, "content", ""))


class _Model:
    """Steps whose prompt grows past the trigger, and a summariser that answers as told.

    ``summary`` is the summariser's reply; ``summary_model`` is who answers it; ``boom`` makes the
    summariser's call raise, as a provider does. The steps are told apart from it by its system
    prompt, which is the one thing the two calls cannot share.
    """

    def __init__(self, sizes: list[int], *, summary: str = "Always use tabs.",
                 summary_model: str = SUMMARY_MODEL, boom: bool = False) -> None:
        self.sizes = list(sizes)
        self.summary = summary
        self.summary_model = summary_model
        self.boom = boom
        self.summaries = 0
        self.steps = 0
        self.step_prompt = 0

    def complete(self, messages: list[Any], **_k: Any) -> CompletionResult:
        if _system_of(messages) == SUMMARY_SYSTEM:
            self.summaries += 1
            if self.boom:
                raise RuntimeError("the provider fell over")
            return CompletionResult(content=self.summary, model=self.summary_model,
                                    prompt_tokens=2000, completion_tokens=200)
        self.steps += 1
        size = self.sizes.pop(0) if self.sizes else 100
        self.step_prompt += size
        if self.sizes:
            call = ToolCall(id=f"c{self.steps}", name="echo", arguments={"text": f"{self.steps}"})
            return CompletionResult(content="", model=STEP_MODEL, prompt_tokens=size,
                                    tool_calls=[call])
        return CompletionResult(content="done", model=STEP_MODEL, prompt_tokens=size)


def _run(backend: _Model, **config: Any) -> Any:
    registry = ToolRegistry()
    registry.register(EchoTool())
    # 128k fallback window * 0.6 budget * 0.8 trigger = 61,440: the 70,000 step compacts.
    agent = Agent(backend, registry, AgentConfig(
        max_steps=6, context_budget=0.6, keep_recent=2, summarise_compaction=True, **config
    ))
    return agent.run("a long task")


def test_the_summary_call_is_in_the_runs_tokens_and_price() -> None:
    backend = _Model([10_000, 70_000, 500])

    result = _run(backend)

    assert (backend.summaries, result.steplog.compactions) == (1, 1)
    assert result.prompt_tokens == backend.step_prompt + 2000
    assert result.completion_tokens == 200
    assert result.usd == pytest.approx(SUMMARY_COST)


def test_a_reply_that_fell_back_to_the_note_was_still_paid_for() -> None:
    backend = _Model([10_000, 70_000, 500], summary="NONE")

    result = _run(backend)

    assert backend.summaries == 1
    assert result.prompt_tokens == backend.step_prompt + 2000
    assert result.usd == pytest.approx(SUMMARY_COST)


def test_a_summary_call_that_raised_charges_nothing() -> None:
    backend = _Model([10_000, 70_000, 500], boom=True)

    result = _run(backend)

    assert backend.summaries == 1, "the call was attempted, and it fell back to the note"
    assert result.prompt_tokens == backend.step_prompt
    assert result.completion_tokens == 0
    assert result.usd == 0.0


def test_a_summary_on_a_model_without_a_price_makes_the_runs_price_unknown() -> None:
    backend = _Model([10_000, 70_000, 500], summary_model="test/no-price-anywhere")

    result = _run(backend)

    assert backend.summaries == 1
    assert result.usd is None, "an unpriced call must never read as a low total"


def test_the_summary_is_charged_to_the_spend_ceiling() -> None:
    """The ceiling is the run's, and the summary is money the run spent: $0.0068 of a $0.005 cap.

    The steps cost nothing here, so without the charge the run finishes; with it, the step after
    the compaction is refused and the run stops on the spend."""
    backend = _Model([10, 70_000, 10, 10])

    result = _run(backend, max_usd=0.005)

    assert backend.summaries == 1
    assert result.stopped_reason == "spend"


# ------------------------------------------------------------------ through the Code screen's turn


class _Gateway:
    """The Code turn's `LLMGateway`: one instance per turn, so the script lives on the class."""

    script: _Model

    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        return _Gateway.script.complete(messages, **kwargs)


def _frames(text: str) -> dict[str, dict[str, Any]]:
    event, out = "", {}
    for line in text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: "):]
        elif line.startswith("data: "):
            out[event] = json.loads(line[len("data: "):])
    return out


def test_through_the_code_turn_the_summary_is_on_the_turns_own_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.api import build_api_app
    from chimera.config import get_settings

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("CHIMERA_PREFIX_NONCE", "")
    get_settings.cache_clear()
    monkeypatch.setattr("chimera.providers.LLMGateway", _Gateway)
    # The turn keeps six messages verbatim, so four small steps come first: a compaction with
    # nothing old enough to drop is a no-op and never calls the summariser.
    _Gateway.script = _Model([100, 100, 100, 100, 70_000, 500])
    ws = tmp_path / "ws"
    ws.mkdir()
    settings = Settings(CHIMERA_HOME=str(home))  # type: ignore[call-arg]
    client = TestClient(build_api_app(
        lambda: ChatSession(Agent(_Gateway(), ToolRegistry())), workspace=ws, settings=settings
    ))

    response = client.post("/api/code/turn", json={
        "message": "a long task", "stream": False,
        "context_budget": 0.6, "summarise_compaction": True,
    })

    done = _frames(response.text)["done"]
    script = _Gateway.script
    assert script.summaries == 1
    [row] = load_usage(home / "usage.jsonl")
    assert row.prompt_tokens == script.step_prompt + 2000
    assert row.usd == pytest.approx(SUMMARY_COST)
    assert (done["prompt_tokens"], done["usd"]) == (row.prompt_tokens, pytest.approx(row.usd))
    # One turn on the Cost screen, whose total now includes what the summary cost.
    totals = summarize_usage([row])["totals"]
    assert totals["turns"] == 1
    assert totals["usd"] == pytest.approx(SUMMARY_COST)
