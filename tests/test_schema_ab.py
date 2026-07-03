"""Tests for the tool-schema compaction bench (no network)."""

from __future__ import annotations

from chimera.eval.schema_ab import SchemaABReport, demo_bloated_schemas, run_schema_ab

MODEL = "gpt-4o-mini"  # a model litellm can tokenize locally; falls back to a heuristic


def test_bench_reduces_tokens_on_bloated_schemas() -> None:
    report = run_schema_ab(demo_bloated_schemas(), model=MODEL)
    assert len(report.rows) == 2
    for row in report.rows:
        assert row.compact_tokens < row.full_tokens  # each verbose tool shrinks
    summary = report.summary()
    assert summary["compact_tokens"] < summary["full_tokens"]
    assert summary["reduction_pct"] > 0


def test_bench_never_grows_a_terse_schema() -> None:
    terse = [
        {
            "type": "function",
            "function": {
                "name": "echo",
                "description": "Echo text.",
                "parameters": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            },
        }
    ]
    report = run_schema_ab(terse, model=MODEL)
    row = report.rows[0]
    assert row.compact_tokens <= row.full_tokens  # compaction never inflates


def test_demo_schemas_present() -> None:
    schemas = demo_bloated_schemas()
    assert schemas and all(s["function"]["name"] for s in schemas)


def test_empty_report_summary() -> None:
    assert SchemaABReport().summary()["reduction_pct"] == 0.0
