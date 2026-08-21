"""What the Cost screen counts, and what it used to leave out.

The screen reads `usage.jsonl` and reports what it finds as *the* spend. Two paths wrote to it —
and one of those two, `/api/chat/stream`, is a route no screen in the app calls (`streamChat` is
defined in api.ts and has zero consumers). So a dashboard that answers "what has this cost me"
was answering from one path, with a number confidently too low.

That is worse than an absent dashboard. An absent one is not consulted.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from chimera.api.usage import load_usage, record_spend


class _Receipt:
    def __init__(self, model: str, prompt: int, completion: int, usd: float | None) -> None:
        self.model = model
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.usd = usd


class _Result:
    def __init__(self, usd: float | None, prompt: int = 100, completion: int = 50) -> None:
        self.model = "m/model"
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self.usd = usd
        self.tool_names = ["read_file"]


class _Unit:
    def __init__(self, value: Any) -> None:
        self.value = value


def test_record_spend_writes_a_row_the_cost_screen_can_read(tmp_path: Path) -> None:
    record_spend(
        tmp_path,
        session_id="orchestration:run_7",
        model="t/model",
        prompt_tokens=1000,
        completion_tokens=400,
        usd=0.0123,
        route_kind="hierarchy",
    )

    rows = load_usage(tmp_path / "usage.jsonl")
    assert len(rows) == 1
    assert rows[0].session_id == "orchestration:run_7"
    assert rows[0].prompt_tokens == 1000 and rows[0].usd == 0.0123
    assert rows[0].ts, "a row with no timestamp cannot be placed on a spend-over-time chart"


def test_a_failure_to_log_never_takes_the_run_down(tmp_path: Path) -> None:
    """The answer is the product. A full disk must not lose a run that already cost money."""
    blocked = tmp_path / "usage.jsonl"
    blocked.mkdir()  # a directory where the log wants a file: append will raise

    record_spend(blocked.parent, session_id="x")  # must not raise

    assert blocked.is_dir()


def test_an_orchestration_run_lands_on_the_cost_screen(tmp_path: Path) -> None:
    from chimera.api.orchestration_api import _record_run_spend

    class _Outcome:
        receipts = [
            _Receipt("t/model", 1000, 200, 0.01),
            _Receipt("m/model", 500, 300, 0.002),
        ]

    _record_run_spend(tmp_path, "run_7", _Outcome())

    rows = load_usage(tmp_path / "usage.jsonl")
    assert len(rows) == 1
    assert rows[0].prompt_tokens == 1500 and rows[0].completion_tokens == 500
    assert rows[0].usd == 0.012
    assert rows[0].route_kind == "hierarchy"


def test_one_unpriced_delegation_makes_the_total_unknown_not_smaller(tmp_path: Path) -> None:
    """A partial sum presented as the total is the failure this accounting exists to avoid.

    Same rule the spend ceiling follows, and the same one `FusionReceipt.total_usd` follows: a
    missing number never masquerades as a smaller one.
    """
    from chimera.api.orchestration_api import _record_run_spend

    class _Outcome:
        receipts = [_Receipt("t/model", 1000, 200, 0.01), _Receipt("who/knows", 500, 300, None)]

    _record_run_spend(tmp_path, "run_8", _Outcome())

    row = load_usage(tmp_path / "usage.jsonl")[0]
    assert row.usd is None
    # The tokens ARE measured, so they are still reported. Only the dollars are unknown.
    assert row.prompt_tokens == 1500


def test_a_run_that_delegated_nothing_writes_no_row(tmp_path: Path) -> None:
    from chimera.api.orchestration_api import _record_run_spend

    class _Outcome:
        receipts: list[Any] = []

    _record_run_spend(tmp_path, "run_9", _Outcome())

    assert load_usage(tmp_path / "usage.jsonl") == []


def test_an_agents_batch_is_one_row_not_one_per_task(tmp_path: Path) -> None:
    """The Cost screen groups by session, and N rows for one click would read as N pieces of work."""
    from chimera.api.app import _record_batch_spend

    class _Batch:
        results = [_Unit(_Result(0.01)), _Unit(_Result(0.02)), _Unit(None)]

    _record_batch_spend(tmp_path, "batch_3", _Batch())

    rows = load_usage(tmp_path / "usage.jsonl")
    assert len(rows) == 1
    # Two tasks reported; the third crashed with no result and contributes nothing rather than zero.
    assert rows[0].prompt_tokens == 200 and rows[0].usd == 0.03
    assert rows[0].session_id == "agents:batch_3"


def test_a_batch_where_nothing_ran_writes_no_row(tmp_path: Path) -> None:
    from chimera.api.app import _record_batch_spend

    class _Batch:
        results = [_Unit(None)]

    _record_batch_spend(tmp_path, "batch_4", _Batch())

    assert load_usage(tmp_path / "usage.jsonl") == []
