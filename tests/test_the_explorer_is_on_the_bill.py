"""What the repository explorer spends reaches the bill: of the turn it ran in, or its own.

`ContextExplorer.explore` runs a whole `Agent` of its own (`chimera/core/explorer.py`) and kept only
the evidence, dropping the run's tokens and price. So a turn that asked `explore_repository` was
billed for its own steps and not for the sub-agent's, and its dollar ceiling never saw them either;
and `chimera explore` printed what it found and nothing about what finding it cost.

Inside a turn the explorer now runs on the turn's ceiling and adds what it spent to the turn's
tally, so the turn's receipt and its usage row carry it. Standalone it prints its price and writes
its own row.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import chimera.fusion.receipts as receipts
from chimera.api.usage import load_usage
from chimera.config import get_settings
from chimera.core import Agent, AgentConfig, ExploreRepositoryTool
from chimera.core.explorer import EXPLORER_CONTRACT_SYSTEM, EXPLORER_SYSTEM
from chimera.fusion.receipts import ModelPrice
from chimera.providers.gateway import CompletionResult, ToolCall
from chimera.tools import ToolRegistry

OUTER_MODEL = "test/outer-free"  # priced at a known zero, so a total is the explorer's alone
EXPLORE_MODEL = "test/explore-priced"  # $3/M in, $4/M out
#: 2,000 prompt tokens at $3/M plus 200 completion tokens at $4/M: one answering explorer call.
EXPLORE_COST = 0.0068
ANSWER = "<final_answer>\nsrc/parser.py:10-20 (the parser)\n</final_answer>"


@pytest.fixture(autouse=True)
def _priced(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exact prices for the fake models, restored afterwards. The shipped catalogue is folded in
    first, so the restore cannot leave the table without it."""
    receipts._ensure_catalog_registered()
    monkeypatch.setattr(receipts, "_PRICES", [
        (OUTER_MODEL, ModelPrice(0.0, 0.0)), (EXPLORE_MODEL, ModelPrice(3.0, 4.0)),
        *receipts._PRICES,
    ])


def _system_of(messages: list[Any]) -> str:
    first = messages[0]
    return str(first.get("content") if isinstance(first, dict) else getattr(first, "content", ""))


class _Model:
    """The outer loop asks the explorer once, then answers; the explorer follows ``explore``.

    Each entry of ``explore`` is one explorer call: ``"answer"`` returns the evidence block,
    ``"search"`` a paid `glob` call, ``"boom"`` raises the way a provider does. ``explore_model``
    is who answers the explorer.
    """

    def __init__(self, explore: list[str], *, explore_model: str = EXPLORE_MODEL,
                 outer_tokens: int = 10) -> None:
        self.explore = list(explore)
        self.explore_model = explore_model
        self.outer_tokens = outer_tokens
        self.explorer_calls = 0
        self.outer_calls = 0

    def complete(self, messages: list[Any], **_k: Any) -> CompletionResult:
        system = _system_of(messages)
        if EXPLORER_SYSTEM[:60] in system or EXPLORER_CONTRACT_SYSTEM[:60] in system:
            self.explorer_calls += 1
            step = self.explore.pop(0) if self.explore else "answer"
            if step == "boom":
                raise RuntimeError("the provider fell over")
            if step == "search":
                return CompletionResult(
                    content="", model=self.explore_model, prompt_tokens=1000,
                    completion_tokens=100,
                    tool_calls=[ToolCall(id="g1", name="glob", arguments={"pattern": "*.py"})],
                )
            return CompletionResult(content=ANSWER, model=self.explore_model,
                                    prompt_tokens=2000, completion_tokens=200)
        self.outer_calls += 1
        if self.outer_calls == 1:
            call = ToolCall(id="x1", name="explore_repository", arguments={"query": "the parser"})
            return CompletionResult(content="", model=OUTER_MODEL,
                                    prompt_tokens=self.outer_tokens, tool_calls=[call])
        return CompletionResult(content="done", model=OUTER_MODEL, prompt_tokens=self.outer_tokens)


def _turn(backend: _Model, tmp_path: Path, *, contract: bool = False, **config: Any) -> Any:
    registry = ToolRegistry()
    registry.register(ExploreRepositoryTool(backend, tmp_path, max_turns=3, contract=contract))
    return Agent(backend, registry, AgentConfig(max_steps=4, **config)).run("fix the parser")


@pytest.mark.parametrize("contract", [False, True], ids=["plain", "contract"])
def test_inside_a_turn_the_explorer_is_in_the_turns_tokens_and_price(
    tmp_path: Path, contract: bool
) -> None:
    """Both of the explorer's paths: today's, and the contract behind CHIMERA_EXPLORER_CONTRACT."""
    backend = _Model(["answer"])

    result = _turn(backend, tmp_path, contract=contract)

    assert backend.explorer_calls == 1 and result.stopped_reason == "final"
    assert result.prompt_tokens == 20 + 2000
    assert result.completion_tokens == 200
    assert result.usd == pytest.approx(EXPLORE_COST)


def test_the_explorers_spend_counts_against_the_turns_ceiling(tmp_path: Path) -> None:
    """$0.0068 of a $0.005 ceiling: the step after the explorer is refused, as after any call."""
    backend = _Model(["answer"])

    result = _turn(backend, tmp_path, max_usd=0.005)

    assert backend.explorer_calls == 1
    assert result.stopped_reason == "spend"


def test_an_explorer_under_a_ceiling_already_reached_makes_no_call(tmp_path: Path) -> None:
    """The ceiling is checked before each call, the explorer's included, so no money is spent to
    discover the turn was over budget. The turn's first step costs the whole ceiling here."""
    receipts._PRICES.insert(0, (OUTER_MODEL, ModelPrice(1.0, 1.0)))
    backend = _Model(["answer"], outer_tokens=10_000)

    result = _turn(backend, tmp_path, max_usd=0.005)

    assert backend.explorer_calls == 0
    assert result.stopped_reason == "spend"


def test_an_explorer_that_died_after_paying_still_bills_what_it_paid(tmp_path: Path) -> None:
    backend = _Model(["search", "boom"])

    result = _turn(backend, tmp_path)

    assert backend.explorer_calls == 2
    assert result.prompt_tokens == 20 + 1000
    assert result.usd == pytest.approx(1000 * 3 / 1e6 + 100 * 4 / 1e6)


def test_a_run_is_open_only_while_it_runs(tmp_path: Path) -> None:
    """A run left open after it returned, or after it raised, would collect the spend of a tool
    called later on the same thread, into a tally nobody reads any more."""
    from chimera.core.agent import enclosing_run

    seen: list[object] = []

    class _Peek(_Model):
        def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
            seen.append(enclosing_run())
            return super().complete(messages, **kwargs)

    _turn(_Peek(["answer"]), tmp_path)
    assert seen and all(run is not None for run in seen)
    assert enclosing_run() is None

    with pytest.raises(RuntimeError):
        Agent(_Boom(), ToolRegistry(), AgentConfig(max_steps=2)).run("x")
    assert enclosing_run() is None


class _Boom:
    def complete(self, messages: list[Any], **_k: Any) -> CompletionResult:
        raise RuntimeError("the provider fell over")


def test_an_unpriced_explorer_makes_the_turns_price_unknown(tmp_path: Path) -> None:
    backend = _Model(["answer"], explore_model="test/no-price-anywhere")

    result = _turn(backend, tmp_path)

    assert result.usd is None, "an unpriced call must never read as a low total"


class _Gateway:
    """The Code turn's `LLMGateway`, one per turn, so the script lives on the class."""

    script: _Model

    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        return _Gateway.script.complete(messages, **kwargs)


def test_through_the_code_turn_the_exploration_is_on_the_turns_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The desktop's own path: the explorer mounted as a seam, behind the governance wrappers,
    and the usage row the Cost screen reads."""
    from fastapi.testclient import TestClient

    from chimera.api import build_api_app
    from chimera.api.usage import summarize_usage
    from chimera.config import Settings
    from chimera.interface import ChatSession

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("CHIMERA_PREFIX_NONCE", "")
    get_settings.cache_clear()
    monkeypatch.setattr("chimera.providers.LLMGateway", _Gateway)
    _Gateway.script = _Model(["answer"])
    ws = tmp_path / "ws"
    ws.mkdir()
    settings = Settings(CHIMERA_HOME=str(home))  # type: ignore[call-arg]
    client = TestClient(build_api_app(
        lambda: ChatSession(Agent(_Gateway(), ToolRegistry())), workspace=ws, settings=settings
    ))

    client.post("/api/code/turn", json={
        "message": "fix the parser", "stream": False, "explorer": True,
    })

    assert _Gateway.script.explorer_calls == 1
    [row] = load_usage(home / "usage.jsonl")
    assert (row.prompt_tokens, row.completion_tokens) == (20 + 2000, 200)
    assert row.usd == pytest.approx(EXPLORE_COST)
    assert summarize_usage([row])["totals"]["turns"] == 1


# ------------------------------------------------------------------ `chimera explore`

runner = CliRunner()


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    get_settings.cache_clear()
    yield tmp_path / "home"
    get_settings.cache_clear()


def _cli(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, backend: _Model) -> Any:
    monkeypatch.setattr("chimera.providers.LLMGateway", lambda *_a, **_k: backend)
    return runner.invoke(
        __import__("chimera.cli.main", fromlist=["app"]).app,
        ["explore", "the parser", "--workspace", str(tmp_path)],
    )


def test_standalone_it_prints_its_price_and_writes_its_own_row(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out = _cli(monkeypatch, tmp_path, _Model(["answer"]))

    assert out.exit_code == 0, out.output
    assert "src/parser.py" in out.output
    assert "$0.0068" in out.output
    [row] = load_usage(home / "usage.jsonl")
    assert row.session_id.startswith("explore:")
    assert (row.model, row.prompt_tokens, row.completion_tokens) == (EXPLORE_MODEL, 2000, 200)
    assert row.usd == pytest.approx(EXPLORE_COST)


def test_standalone_an_unknown_price_is_said_and_logged_as_unknown(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out = _cli(monkeypatch, tmp_path, _Model(["answer"], explore_model="test/no-price-anywhere"))

    assert out.exit_code == 0, out.output
    assert "cost unknown" in out.output
    [row] = load_usage(home / "usage.jsonl")
    assert row.usd is None


def test_standalone_a_run_that_died_after_paying_is_still_logged(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out = _cli(monkeypatch, tmp_path, _Model(["search", "boom"]))

    assert out.exit_code != 0
    [row] = load_usage(home / "usage.jsonl")
    assert (row.prompt_tokens, row.completion_tokens) == (1000, 100)


def test_standalone_a_call_that_never_returned_writes_nothing(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out = _cli(monkeypatch, tmp_path, _Model(["boom"]))

    assert out.exit_code != 0
    assert load_usage(home / "usage.jsonl") == []
