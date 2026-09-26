"""What "Tidy memory" costs at the end of a terminal conversation reaches the usage log.

`chimera chat` and `chimera assist` consolidate memory when the session ends and it has outgrown its
budget (`cli.main._maybe_autoconsolidate`), asking a model to merge each cluster of similar facts.
No turn is open by then, and nothing metered those calls.

Outside any turn, the merge gets a row of its own, the way the memory extraction does: filed under
the conversation that just ended, marked `TIDY_KIND`, added to that conversation's spend and not
counted as a turn of it. (On the Code screen the same merge runs inside the turn and goes on the
turn's own row: `test_the_memory_tidy_is_on_the_bill.py`.)
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import chimera.fusion.receipts as receipts
from chimera.api.usage import TIDY_KIND, UsageRecord, load_usage, summarize_usage
from chimera.cli.main import _maybe_autoconsolidate, app
from chimera.config import Settings, get_settings
from chimera.fusion.receipts import ModelPrice
from chimera.memory.manager import MemoryManager
from chimera.memory.store import MemoryStore
from chimera.providers.gateway import CompletionResult

MERGE_MODEL = "test/tidy-priced"  # $1/M in, $2/M out
#: 500 prompt tokens at $1/M plus 50 completion tokens at $2/M.
MERGE_COST = 0.0006


@pytest.fixture(autouse=True)
def _priced(monkeypatch: pytest.MonkeyPatch) -> None:
    """One exact price for the fake model, restored afterwards. The shipped catalogue is folded in
    first, so the restore cannot leave the table without it."""
    receipts._ensure_catalog_registered()
    monkeypatch.setattr(
        receipts, "_PRICES", [(MERGE_MODEL, ModelPrice(1.0, 2.0)), *receipts._PRICES]
    )


class _Gateway:
    """Answers a merge as `model_summarizer` asks for one; ``boom`` makes it raise instead."""

    model = MERGE_MODEL
    boom = False
    merges = 0

    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    def complete(self, messages: list[Any], **_k: Any) -> CompletionResult:
        last = messages[-1]
        text = str(last.get("content") if isinstance(last, dict) else last.content)
        if text.startswith("Merge these related facts"):
            _Gateway.merges += 1
            if _Gateway.boom:
                raise RuntimeError("the provider fell over")
            return CompletionResult(content="The user indents with tabs.", model=_Gateway.model,
                                    prompt_tokens=500, completion_tokens=50)
        return CompletionResult(content="hi", model=_Gateway.model)


@pytest.fixture(autouse=True)
def gateway(monkeypatch: pytest.MonkeyPatch) -> type[_Gateway]:
    monkeypatch.setattr(_Gateway, "model", MERGE_MODEL)
    monkeypatch.setattr(_Gateway, "boom", False)
    monkeypatch.setattr(_Gateway, "merges", 0)
    monkeypatch.setattr("chimera.providers.LLMGateway", _Gateway)
    return _Gateway


def _memory(tmp_path: Path, *, over_budget: bool = True) -> MemoryManager:
    memory = MemoryManager(MemoryStore(tmp_path / "memory.json"))
    memory.add("The user prefers tabs for indentation", "semantic")
    if over_budget:
        # Two facts close enough to be one cluster, and a third over the budget of two.
        memory.add("The user prefers tabs for indentation in Python", "semantic")
        memory.add("The user lives in Belo Horizonte", "semantic")
    return memory


def _settings(tmp_path: Path) -> Settings:
    return Settings(  # type: ignore[call-arg]
        CHIMERA_HOME=str(tmp_path / "home"), CHIMERA_AUTO_CONSOLIDATE=True,
        CHIMERA_MEMORY_BUDGET=2,
    )


def _rows(tmp_path: Path) -> list[UsageRecord]:
    return load_usage(tmp_path / "home" / "usage.jsonl")


def test_the_merge_is_filed_under_the_conversation_as_a_tidy(
    tmp_path: Path, gateway: type[_Gateway]
) -> None:
    _maybe_autoconsolidate(_memory(tmp_path), _settings(tmp_path), "thread-1")

    assert gateway.merges == 1
    [row] = _rows(tmp_path)
    assert (row.session_id, row.route_kind, row.model) == ("thread-1", TIDY_KIND, MERGE_MODEL)
    assert (row.prompt_tokens, row.completion_tokens) == (500, 50)
    assert row.usd == pytest.approx(MERGE_COST)


def test_memory_under_budget_asks_no_model_and_writes_nothing(
    tmp_path: Path, gateway: type[_Gateway]
) -> None:
    _maybe_autoconsolidate(_memory(tmp_path, over_budget=False), _settings(tmp_path), "thread-1")

    assert gateway.merges == 0
    assert _rows(tmp_path) == []


def test_a_merge_that_never_returned_writes_nothing(
    tmp_path: Path, gateway: type[_Gateway]
) -> None:
    gateway.boom = True

    _maybe_autoconsolidate(_memory(tmp_path), _settings(tmp_path), "thread-1")

    assert gateway.merges == 1
    assert _rows(tmp_path) == []


def test_on_the_cost_screen_a_tidy_adds_to_the_spend_and_not_to_the_turns() -> None:
    turn = UsageRecord(ts="2026-09-26T10:00:00+00:00", session_id="thread-1", model="m/chat",
                       prompt_tokens=5000, completion_tokens=500, usd=0.01)
    tidy = UsageRecord(ts="2026-09-26T10:05:00+00:00", session_id="thread-1", model=MERGE_MODEL,
                       prompt_tokens=500, completion_tokens=50, usd=MERGE_COST,
                       route_kind=TIDY_KIND)

    summary = summarize_usage([turn, tidy])

    assert summary["totals"]["turns"] == 1
    assert summary["totals"]["usd"] == pytest.approx(0.01 + MERGE_COST)
    [session] = summary["by_session"]
    assert (session["turns"], session["usd"]) == (1, pytest.approx(0.01 + MERGE_COST))
    assert summary["by_day"][0]["turns"] == 1
    assert summary["route_mix"] == {"single": 1, "fusion": 0, "cascade": 0}


# ------------------------------------------------------------------ through `chimera chat`

runner = CliRunner()


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("CHIMERA_AUTO_CONSOLIDATE", "1")
    monkeypatch.setenv("CHIMERA_MEMORY_BUDGET", "2")
    monkeypatch.setenv("CHIMERA_MEMORY_EXTRACT", "0")
    monkeypatch.setattr("chimera.sandbox._warned", False)
    get_settings.cache_clear()
    yield tmp_path / "home"
    get_settings.cache_clear()


def test_leaving_chat_files_the_tidy_under_the_thread_it_ends(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gateway: type[_Gateway]
) -> None:
    memory = _memory(tmp_path)
    monkeypatch.setattr("chimera.cli.main._memory_manager", lambda: memory)

    result = runner.invoke(app, ["chat"], input="/exit\n")

    assert result.exit_code == 0, result.output
    thread = re.search(r"session (\S+) —", result.output)
    assert thread is not None, result.output
    assert gateway.merges == 1
    [row] = load_usage(home / "usage.jsonl")
    assert (row.session_id, row.route_kind) == (thread.group(1), TIDY_KIND)
    assert row.usd == pytest.approx(MERGE_COST)
