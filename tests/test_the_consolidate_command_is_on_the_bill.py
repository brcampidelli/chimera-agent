"""`chimera memory consolidate` says what its merges cost, and writes it down.

The command asks a model to merge each cluster of similar facts (`model_summarizer`) on a gateway
nothing metered: it printed how many items it merged away and nothing about what merging them
cost, and the Cost screen never saw it. It is a command of its own, not part of any conversation,
so it gets a usage row of its own, under `consolidate:<id>`, and prints the price beside the count.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

import chimera.fusion.receipts as receipts
from chimera.api.usage import UsageRecord, load_usage
from chimera.cli.main import app
from chimera.config import get_settings
from chimera.fusion.receipts import ModelPrice
from chimera.memory.manager import MemoryManager
from chimera.memory.store import MemoryStore
from chimera.providers.gateway import CompletionResult

MERGE_MODEL = "test/consolidate-priced"  # $1/M in, $2/M out
#: 500 prompt tokens at $1/M plus 50 completion tokens at $2/M: one merge.
MERGE_COST = 0.0006

runner = CliRunner()


@pytest.fixture(autouse=True)
def _priced(monkeypatch: pytest.MonkeyPatch) -> None:
    """One exact price for the fake model, restored afterwards. The shipped catalogue is folded in
    first, so the restore cannot leave the table without it."""
    receipts._ensure_catalog_registered()
    monkeypatch.setattr(
        receipts, "_PRICES", [(MERGE_MODEL, ModelPrice(1.0, 2.0)), *receipts._PRICES]
    )


class _Gateway:
    """Answers each merge; ``script`` says, merge by merge, "ok" or "boom" (raise, as a provider)."""

    model = MERGE_MODEL
    script: list[str] = []
    merges = 0

    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    def complete(self, messages: list[Any], **_k: Any) -> CompletionResult:
        _Gateway.merges += 1
        step = _Gateway.script.pop(0) if _Gateway.script else "ok"
        if step == "boom":
            raise RuntimeError("the provider fell over")
        return CompletionResult(content="A merged fact.", model=_Gateway.model,
                                prompt_tokens=500, completion_tokens=50)


@pytest.fixture
def home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setattr("chimera.providers.LLMGateway", _Gateway)
    monkeypatch.setattr(_Gateway, "model", MERGE_MODEL)
    monkeypatch.setattr(_Gateway, "script", [])
    monkeypatch.setattr(_Gateway, "merges", 0)
    get_settings.cache_clear()
    yield tmp_path / "home"
    get_settings.cache_clear()


def _consolidate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *clusters: int) -> Any:
    """Run the command over a memory holding ``clusters``: one pair of similar facts per entry."""
    memory = MemoryManager(MemoryStore(tmp_path / "memory.json"))
    pairs = [
        ("The user prefers tabs for indentation", "The user prefers tabs for indentation in Python"),
        ("The user lives in Belo Horizonte, Brazil", "The user lives in Belo Horizonte"),
    ]
    for first, second in pairs[: len(clusters)]:
        memory.add(first, "semantic")
        memory.add(second, "semantic")
    monkeypatch.setattr("chimera.cli.main._memory_manager", lambda: memory)
    return runner.invoke(app, ["memory", "consolidate"])


def _rows(home: Path) -> list[UsageRecord]:
    return load_usage(home / "usage.jsonl")


def test_it_prints_the_price_and_writes_a_row_of_its_own(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out = _consolidate(monkeypatch, tmp_path, 1)

    assert out.exit_code == 0, out.output
    assert _Gateway.merges == 1
    assert "merged away 1" in out.output and "$0.0006" in out.output
    [row] = _rows(home)
    assert row.session_id.startswith("consolidate:")
    assert (row.model, row.prompt_tokens, row.completion_tokens) == (MERGE_MODEL, 500, 50)
    assert row.usd == pytest.approx(MERGE_COST)


def test_an_unknown_price_is_said_and_logged_as_unknown(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(_Gateway, "model", "test/no-price-anywhere")

    out = _consolidate(monkeypatch, tmp_path, 1)

    assert out.exit_code == 0, out.output
    assert "cost unknown" in out.output
    [row] = _rows(home)
    assert row.usd is None


def test_nothing_to_merge_asks_no_model_and_writes_nothing(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out = _consolidate(monkeypatch, tmp_path)

    assert out.exit_code == 0, out.output
    assert _Gateway.merges == 0
    assert _rows(home) == []


def test_a_run_that_failed_part_way_bills_the_merges_it_paid_for(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(_Gateway, "script", ["ok", "boom"])

    out = _consolidate(monkeypatch, tmp_path, 1, 2)

    assert out.exit_code != 0
    assert _Gateway.merges == 2
    [row] = _rows(home)
    assert (row.prompt_tokens, row.completion_tokens) == (500, 50)


def test_a_merge_that_never_returned_writes_nothing(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(_Gateway, "script", ["boom"])

    out = _consolidate(monkeypatch, tmp_path, 1)

    assert out.exit_code != 0
    assert _rows(home) == []
