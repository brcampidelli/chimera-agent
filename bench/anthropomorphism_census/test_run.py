"""Tests for the offline anthropomorphism census."""

from __future__ import annotations

import json
import re
from pathlib import Path

from run import CATEGORIES, analyze, load_answers

HERE = Path(__file__).resolve().parent


def test_fixture_census_shape_and_one_hit_per_category() -> None:
    fixture = json.loads((HERE / "fixtures" / "tiny.json").read_text(encoding="utf-8"))
    answers = [row["answer"] for row in fixture["rows"]]
    records = [
        {"answer_id": f"fixture-{index}", "answer": answer, "source": "fixture", "location": f"/{index}"}
        for index, answer in enumerate(answers)
    ]
    sample = {
        name: {record["answer_id"]: True for record in records if any(re.search(pattern, record["answer"], re.IGNORECASE) for pattern in patterns)}
        for name, patterns in CATEGORIES.items()
    }
    result = analyze(records, hand_read=sample)
    assert result["answer_count"] == 6
    expected_hit = {
        "validation_openers": 2,
        "affective_first_person": 1,
        "relationship_claims": 1,
        "completion_claims": 1,
    }
    assert set(result["categories"]) == set(expected_hit)
    for name, hit_count in expected_hit.items():
        category = result["categories"][name]
        assert category["hit_count"] == hit_count
        assert category["denominator"] == 6
        assert 0 <= category["rate"] <= 1
        assert len(category["wilson_95"]) == 2
        assert category["hand_read"]["reviewed"] == hit_count
        assert category["hand_read"]["correct"] == hit_count
    assert result["completion_false_success_overlap"]["status"] == "unavailable"


def test_corpus_reader_uses_only_answer_fields() -> None:
    fixture_path = HERE / "fixtures" / "tiny.json"
    records, manifest = load_answers([fixture_path])
    assert records == []  # fixtures outside results are excluded from corpus ingestion
    assert manifest == []  # fixture is not a results corpus file
    assert all(set(record) == {"answer_id", "answer", "source", "location"} for record in records)
