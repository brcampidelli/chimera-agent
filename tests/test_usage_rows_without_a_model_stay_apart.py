"""A usage row with no model is shown as one honest group, never folded into a named model.

The Usage screen's "By model" panel had a row with an empty name and 104 turns. Its source is an
autonomous run whose attempt receipt carries no model slug (`AttemptReceipt.model` defaults to ""),
so the spend is real and priced while the model that earned it is not recorded. The aggregate must
keep that group apart: merging it into a named model would invent a model, and dropping it would
under-count the bill, which is the direction this file never gets to take.
"""

from __future__ import annotations

import json
from pathlib import Path

from chimera.api.usage import UsageRecord, load_usage, summarize_usage, usage_from_runs


def _write_run(path: Path, attempts: list[dict[str, object]]) -> None:
    row = {"ts": "2026-10-20T10:00:00+00:00", "attempts": attempts}
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")


def test_an_attempt_with_no_recorded_model_becomes_a_row_with_an_empty_model(tmp_path: Path) -> None:
    runs = tmp_path / "runs.jsonl"
    _write_run(
        runs,
        [{"run_id": "r1", "usd": 0.25, "prompt_tokens": 1000, "completion_tokens": 50}],
    )

    (record,) = usage_from_runs(runs)

    assert record.model == ""
    assert record.usd == 0.25


def test_the_unrecorded_model_is_its_own_group_next_to_a_named_one(tmp_path: Path) -> None:
    runs = tmp_path / "runs.jsonl"
    _write_run(
        runs,
        [
            {"run_id": "r1", "usd": 0.25, "prompt_tokens": 1000, "completion_tokens": 50},
            {"run_id": "r2", "usd": 0.5, "model": "openrouter/deepseek/deepseek-chat",
             "prompt_tokens": 200, "completion_tokens": 20},
        ],
    )
    records = [
        *load_usage(tmp_path / "missing.jsonl"),
        *usage_from_runs(runs),
    ]

    summary = summarize_usage(records)
    by_model = {row["model"]: row for row in summary["by_model"]}

    assert set(by_model) == {"", "openrouter/deepseek/deepseek-chat"}
    assert by_model[""]["turns"] == 1
    assert by_model[""]["usd"] == 0.25
    assert by_model["openrouter/deepseek/deepseek-chat"]["usd"] == 0.5
    # Nothing is invented: the total is the sum of both, not a price for a model nobody recorded.
    assert summary["totals"]["usd"] == 0.75


def test_a_row_with_no_price_in_the_unrecorded_group_stays_unknown_not_zero() -> None:
    records = [
        UsageRecord(ts="2026-10-20T10:00:00+00:00", session_id="run:r9", model="", usd=None,
                    prompt_tokens=10, completion_tokens=1),
    ]

    summary = summarize_usage(records)
    (row,) = summary["by_model"]

    assert row["model"] == ""
    assert row["unpriced"] == 1
    assert summary["totals"]["unpriced_turns"] == 1
