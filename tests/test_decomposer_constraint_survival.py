"""Offline contract test for the S30-60 constraint-survival measurement harness."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from chimera.orchestration.spec import TaskSpec
from chimera.providers.gateway import CompletionResult, MessageLike

RUN_PATH = Path(__file__).resolve().parents[1] / "bench" / "decomposer_survival" / "run.py"
_spec = importlib.util.spec_from_file_location("decomposer_survival_run", RUN_PATH)
if _spec is None or _spec.loader is None:
    raise RuntimeError("could not load decomposition survival harness")
harness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harness)


class StubBackend:
    """Return a fixed valid decomposition without making network or model calls."""

    def complete(
        self,
        messages: list[MessageLike],
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> CompletionResult:
        return CompletionResult(
            content=(
                '[{"objective":"Add the export", "output_format":"CSV", '
                '"boundaries":"Do not include private keys."}]'
            ),
            model=model or "stub",
            prompt_tokens=1,
            completion_tokens=1,
        )


def test_the_scorer_counts_one_survived_and_one_lost_constraint() -> None:
    """The scorer alone: a literal match survives, an absent sentence is lost."""
    spec = TaskSpec(
        task_id="sub-1", objective="Add the export", output_format="CSV",
        boundaries="Do not include private keys.",
    )
    scored, survived = harness.score_specs(
        ["Do not include private keys.", "Keep the header row unchanged."], [spec]
    )
    assert survived == 1
    assert scored == [
        {
            "constraint": "Do not include private keys.",
            "survived": True,
            "matches": [{"task_id": "sub-1", "field": "boundaries"}],
        },
        {"constraint": "Keep the header row unchanged.", "survived": False, "matches": []},
    ]


def test_a_constraint_the_decomposer_drops_still_reaches_the_worker(tmp_path: Path) -> None:
    """The stub decomposition keeps one constraint and drops the other. Before the pass-through
    the harness read 1 of 2 here (0 of 60 on qwen3:4b); the request now rides along verbatim."""
    corpus = [
        {
            "id": "stub-case",
            "request": "Add the export.",
            "constraints": [
                "Do not include private keys.",
                "Keep the header row unchanged.",
            ],
        }
    ]
    readout = harness.run(StubBackend(), model="stub", corpus=corpus)

    assert set(readout) == {
        "schema_version",
        "preregistration",
        "model",
        "corpus_size",
        "total_constraints",
        "survived_constraints",
        "lost_constraints",
        "survival_rate",
        "tasks",
    }
    assert readout["schema_version"] == 1
    assert readout["total_constraints"] == 2
    assert readout["survived_constraints"] == 2
    assert readout["survival_rate"] == 1.0
    task = readout["tasks"][0]
    assert task["status"] == "ok"
    assert [c["survived"] for c in task["constraints"]] == [True, True]
    # The registration promises the decomposer's raw reply is kept for audit.
    assert task["raw_responses"] == [StubBackend().complete([]).content]
