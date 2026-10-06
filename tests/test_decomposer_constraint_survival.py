"""Offline contract test for the S30-60 constraint-survival measurement harness."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

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


def test_harness_reports_one_survived_and_one_lost_constraint(tmp_path: Path) -> None:
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
    assert readout["corpus_size"] == 1
    assert readout["total_constraints"] == 2
    assert readout["survived_constraints"] == 1
    assert readout["lost_constraints"] == 1
    assert readout["survival_rate"] == 0.5
    task = readout["tasks"][0]
    assert task["status"] == "ok"
    assert task["constraints"] == [
        {
            "constraint": "Do not include private keys.",
            "survived": True,
            "matches": [{"task_id": "sub-1", "field": "boundaries"}],
        },
        {
            "constraint": "Keep the header row unchanged.",
            "survived": False,
            "matches": [],
        },
    ]
    assert isinstance(task["specs"][0], dict)
    # The registration promises the decomposer's raw reply is kept for audit.
    assert task["raw_responses"] == [StubBackend().complete([]).content]
