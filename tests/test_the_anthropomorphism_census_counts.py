"""The anthropomorphism census (study 31, A31-06) counts what it says it counts.

Moved here from `bench/anthropomorphism_census/test_run.py`, where pytest never collected it
(`testpaths = ["tests"]`): a test in that folder passes forever because it never runs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from bench.anthropomorphism_census import run as census

FIXTURE = Path(census.__file__).resolve().parent / "fixtures" / "tiny.json"


def test_the_fixture_yields_one_hit_per_category_and_the_readout_shape() -> None:
    rows = json.loads(FIXTURE.read_text(encoding="utf-8"))["rows"]
    records = [
        {"answer_id": f"fixture-{i}", "answer": row["answer"], "source": "fixture", "location": f"/{i}"}
        for i, row in enumerate(rows)
    ]
    agreed = {
        name: {
            r["answer_id"]: True
            for r in records
            if any(re.search(p, r["answer"], re.IGNORECASE) for p in patterns)
        }
        for name, patterns in census.CATEGORIES.items()
    }
    result = census.analyze(records, hand_read=agreed)
    assert result["answer_count"] == 6
    expected = {"validation_openers": 2, "affective_first_person": 1,
                "relationship_claims": 1, "completion_claims": 1}
    for name, hits in expected.items():
        category = result["categories"][name]
        assert category["hit_count"] == hits
        assert category["denominator"] == 6
        assert len(category["wilson_95"]) == 2
    assert result["completion_false_success_overlap"]["status"] == "unavailable"


def test_the_census_reads_the_committed_answers_and_only_those() -> None:
    """A rate of 0% over an empty corpus would read as a clean result: the census must find the
    thousands of committed answers (3206 when written) and take every one from a results file."""
    records, manifest = census.load_answers()
    assert len(records) > 1000
    assert manifest and all("results" in Path(m).parts for m in manifest)
    assert {r["source"] for r in records} <= set(manifest)
