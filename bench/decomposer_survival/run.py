"""Measure verbatim survival of constraints through Chimera's shipped decomposer.

Run with ``python bench/decomposer_survival/run.py [--output PATH]``. The default
backend is local Ollama; tests inject a deterministic backend and never contact a model.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Protocol

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from chimera.orchestration.artifacts import ArtifactStore  # noqa: E402
from chimera.orchestration.hierarchy import HierarchicalOrchestrator  # noqa: E402
from chimera.orchestration.spec import TaskSpec  # noqa: E402
from chimera.providers.gateway import (  # noqa: E402
    CompletionResult,
    LLMGateway,
    MessageLike,
    SupportsComplete,
)

HERE = Path(__file__).resolve().parent
CORPUS_PATH = HERE / "corpus.json"
MODEL = "ollama_chat/qwen3:4b"
# The registration this harness measures against; committed before the corpus and this file.
PREREGISTRATION = {"path": "bench/decomposer_survival/PREREGISTRATION.md", "commit": "079a563a"}


class CompletionBackend(Protocol):
    """Minimal provider seam consumed by the real hierarchy decomposer."""

    def complete(
        self,
        messages: list[MessageLike],
        *,
        model: str | None = None,
        temperature: float = 0.3,
        max_tokens: int | None = None,
        tools: list[dict[str, object]] | None = None,
        **kwargs: object,
    ) -> CompletionResult: ...


class _Recording:
    """Pass-through that keeps every raw decomposer reply, as the registration promises."""

    def __init__(self, inner: SupportsComplete) -> None:
        self.inner = inner
        self.raw: list[str] = []

    def complete(self, messages: list[MessageLike], **kwargs: Any) -> CompletionResult:
        result = self.inner.complete(messages, **kwargs)
        self.raw.append(result.content or "")
        return result


def normalize(text: str) -> str:
    """Apply only the Unicode and whitespace normalization registered in advance."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def load_corpus(path: Path = CORPUS_PATH) -> list[dict[str, object]]:
    """Load and validate the frozen corpus shape."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("corpus must be a JSON array")
    for item in data:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("id"), str)
            or not isinstance(item.get("request"), str)
            or not isinstance(item.get("constraints"), list)
            or not all(isinstance(sentence, str) for sentence in item["constraints"])
        ):
            raise ValueError("each corpus item needs id, request, and string constraints")
    return data


def task_text(item: dict[str, object]) -> str:
    constraints = item["constraints"]
    if not isinstance(constraints, list):
        raise ValueError("constraints must be a list")
    return f"{item['request']}\n\nConstraints:\n" + "\n".join(str(x) for x in constraints)


def score_specs(
    constraints: list[str], specs: list[TaskSpec]
) -> tuple[list[dict[str, object]], int]:
    """Score literal constraint inclusion across all worker-facing spec fields."""
    outcomes: list[dict[str, object]] = []
    for constraint in constraints:
        target = normalize(constraint)
        matches = [
            {"task_id": spec.task_id, "field": field}
            for spec in specs
            for field, text in (
                ("objective", spec.objective),
                ("output_format", spec.output_format),
                ("boundaries", spec.boundaries),
            )
            if target in normalize(text)
        ]
        outcomes.append(
            {"constraint": constraint, "survived": bool(matches), "matches": matches}
        )
    return outcomes, sum(bool(outcome["survived"]) for outcome in outcomes)


def run(
    backend: CompletionBackend | None = None,
    *,
    model: str = MODEL,
    corpus: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Run the real decomposer and return an auditable per-constraint readout."""
    selected_corpus = load_corpus() if corpus is None else corpus
    recorder = _Recording(LLMGateway() if backend is None else backend)

    task_results: list[dict[str, object]] = []
    total = 0
    survived = 0
    with tempfile.TemporaryDirectory(prefix="chimera-decomposer-survival-") as temporary:
        orchestrator = HierarchicalOrchestrator(
            recorder,
            weak_model=model,
            mid_model=model,
            top_model=model,
            store=ArtifactStore(Path(temporary) / "artifacts"),
        )
        for item in selected_corpus:
            constraints = item["constraints"]
            if not isinstance(constraints, list) or not all(
                isinstance(sentence, str) for sentence in constraints
            ):
                raise ValueError("each corpus item must have string constraints")
            total += len(constraints)
            recorder.raw = []
            try:
                specs = orchestrator.decompose(task_text(item))
                scored, count = score_specs(constraints, specs)
                survived += count
                task_results.append(
                    {
                        "id": item["id"],
                        "status": "ok" if specs else "decomposition_failed",
                        "constraints": scored,
                        "specs": [spec.model_dump(mode="json") for spec in specs],
                        "raw_responses": list(recorder.raw),
                    }
                )
            except Exception as exc:  # keep failed items in the registered denominator
                task_results.append(
                    {
                        "id": item["id"],
                        "status": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                        "constraints": [
                            {"constraint": sentence, "survived": False, "matches": []}
                            for sentence in constraints
                        ],
                        "specs": [],
                        "raw_responses": list(recorder.raw),
                    }
                )
    return {
        "schema_version": 1,
        "preregistration": PREREGISTRATION,
        "model": model,
        "corpus_size": len(selected_corpus),
        "total_constraints": total,
        "survived_constraints": survived,
        "lost_constraints": total - survived,
        "survival_rate": survived / total if total else None,
        "tasks": task_results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="write JSON readout here (default: stdout)")
    args = parser.parse_args()
    readout = run()
    rendered = json.dumps(readout, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
