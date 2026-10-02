"""bench/manager_advisory/census.py — two defects an adversarial review found after it was published.

1. C1 scanned every tracked `.json` under `bench/`, which came to include the census's own output, so a
   rerun scanned 1,057 files where the published run said 1,056: the headline could not be reproduced
   from the committed tree.
2. C2 called the regime "Manager on, no executable verifier" the one where "the Manager decides every
   attempt". In the code that produced those runs other gates decide beside it (the diff gate in
   tool_defer; `--checklist` and `--require-diff` in the SWE-bench treatment arms), so that label
   blamed the Manager for reverts the stored rows cannot attribute. Each row must now name those gates,
   and cite its runner at the commit that wrote the result, not a line in today's tree.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "bench" / "manager_advisory" / "census.py"


def _census() -> ModuleType:
    spec = importlib.util.spec_from_file_location("manager_advisory_census", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_receipt_scan_does_not_count_the_census_own_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    census = _census()
    own = tmp_path / "bench" / "manager_advisory" / "results" / "census.json"
    other = tmp_path / "bench" / "tool_defer" / "results.json"
    for path in (own, other):
        path.parent.mkdir(parents=True)
        path.write_text('{"evidence": "manager"}', encoding="utf-8")
    monkeypatch.setattr(census, "REPO", tmp_path)
    monkeypatch.setattr(census, "_tracked", lambda pattern: [own, other])

    c1 = census.c1_receipts()

    assert c1["files_scanned"] == 1
    assert list(c1["files_with_labels"]) == [str(other.relative_to(tmp_path))]


def test_every_manager_without_verifier_row_names_the_gates_beside_it() -> None:
    c2 = _census().c2_regimes()
    regime = [d for d in c2["datasets"] if d["manager"] is True and not d["verifier"]]

    assert regime, "the regime is reached in stored runs; an empty list means the classifier broke"
    for row in regime:
        assert row.get("gates_beside_manager"), f"{row['bench']} hides the gates deciding beside the Manager"
    # The label that claimed the Manager decided alone must not come back under its old name.
    assert "manager_decides" not in c2
    assert c2["manager_llm_gate_no_verifier"] == sum(d["solves"] for d in regime)


def test_every_regime_is_cited_at_the_commit_that_wrote_its_results() -> None:
    for row in _census().c2_regimes()["datasets"]:
        assert re.match(r"^[0-9a-f]{8}:bench/", row["source"]), (
            f"{row['bench']} cites {row['source']!r}, a line in today's tree that drifts with the runner"
        )
