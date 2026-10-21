"""Regression tests for the read/write mixed-batch trace census."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.mixed_batch_census import collect, report


def _write_run(path: Path, steps: list[dict[str, object]], model: str = "model-a") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"steps": [{"model": model, **step} for step in steps]}
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record) + "\n")


def test_census_counts_mixed_batches_per_model_and_keeps_unknown_visible(tmp_path: Path) -> None:
    trace = tmp_path / "traces.jsonl"
    _write_run(
        trace,
        [
            {"tools": [{"name": "read_file"}, {"name": "write_file"}]},
            {"tools": [{"name": "grep"}, {"name": "glob"}]},
            {"tools": [{"name": "write_file"}]},
            {"tools": [{"name": "new_tool"}, {"name": "read_file"}]},
        ],
    )
    _write_run(
        trace,
        [{"tools": [{"name": "apply_patch"}, {"name": "edit_file"}]}],
        model="model-b",
    )

    census = collect(tmp_path)

    assert census.total_steps == 5
    assert census.batch_steps == 4
    assert census.mixed_steps == 1
    assert census.models["model-a"].steps == 4
    assert census.models["model-a"].batch_steps == 3
    assert census.models["model-a"].mixed_steps == 1
    assert census.models["model-b"].mixed_steps == 0
    assert census.unknown_tools == {"new_tool": 1}
    assert census.files_read == 1
    assert census.unreadable_files == census.unreadable_lines == 0
    output = report(census, tmp_path)
    assert "new_tool: 1" in output
    assert "Mixed / all steps: 20.00%" in output


def test_corrupt_jsonl_line_is_counted_not_silently_dropped(tmp_path: Path) -> None:
    trace = tmp_path / "traces.jsonl"
    trace.write_text(
        '{"steps":[{"model":"m","tools":[{"name":"read_file"},{"name":"edit_file"}]}]}\n'
        '{bad json\n'
        '\n',
        encoding="utf-8",
    )

    census = collect(tmp_path)

    assert census.total_steps == 1
    assert census.mixed_steps == 1
    assert census.unreadable_lines == 1
    assert census.unreadable_files == 0


def test_steps_with_less_than_two_calls_are_not_batches(tmp_path: Path) -> None:
    trace = tmp_path / "traces.jsonl"
    _write_run(
        trace,
        [
            {"tools": []},
            {"tools": [{"name": "read_file"}]},
            {"tools": [{"name": "unknown"}, {"name": "write_file"}]},
        ],
    )

    census = collect(tmp_path)

    assert census.total_steps == 3
    assert census.batch_steps == 1
    assert census.mixed_steps == 0
    assert census.unknown_tools == {"unknown": 1}


def test_rotated_and_nested_trace_files_are_included(tmp_path: Path) -> None:
    _write_run(
        tmp_path / "nested" / "traces.jsonl.1",
        [{"tools": [{"name": "read_file"}, {"name": "edit_file"}]}],
    )
    _write_run(
        tmp_path / "scheduler" / "cron_traces.jsonl",
        [{"tools": [{"name": "grep"}, {"name": "write_file"}]}],
    )

    census = collect(tmp_path)

    assert census.files_read == 2
    assert census.total_steps == census.mixed_steps == 2


def test_file_path_root_is_supported(tmp_path: Path) -> None:
    trace = tmp_path / "traces.jsonl"
    _write_run(trace, [{"tools": [{"name": "read_file"}, {"name": "edit_file"}]}])

    census = collect(trace)

    assert census.files_read == 1
    assert census.mixed_steps == 1
