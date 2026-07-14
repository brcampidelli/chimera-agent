"""Tests for the maturity scorecard: the snapshot builder + the live/snapshot/unavailable resolver.

Load-bearing properties: ``build_snapshot`` is derived from real test-file evidence (proven <= total);
``maturity_report`` reads the repo's live tests dir here (``source == "live"``); and when pointed at a
tests-less dir with a written snapshot, it falls back to that snapshot (``source == "snapshot"``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chimera.api.maturity_api import maturity_report
from chimera.eval.maturity_snapshot import build_snapshot


def test_build_snapshot_shape_and_bounds() -> None:
    snap = build_snapshot(Path("tests"))

    assert 0 <= snap["proven"] <= snap["total"]
    assert snap["total"] > 0
    assert snap["surfaces"], "the taxonomy has surfaces, so the snapshot must too"
    for s in snap["surfaces"]:
        assert 0 <= s["proven"] <= s["total"]
        assert s["level"] in {"GA", "Beta", "Alpha"}
        assert isinstance(s["missing"], list)
    # weakest is None (all proven) or a {name, ratio} dict — never anything else.
    assert snap["weakest"] is None or set(snap["weakest"]) == {"name", "ratio"}
    assert isinstance(snap["generated_for"], str)


def test_maturity_report_live_on_this_repo() -> None:
    report = maturity_report()

    # This repo IS a source checkout with a tests dir, so the resolver reads it live.
    assert report["available"] is True
    assert report["source"] == "live"
    assert report["total"] > 0
    assert 0 <= report["proven"] <= report["total"]
    assert report["level"] in {"GA", "Beta", "Alpha"}


def test_maturity_report_falls_back_to_snapshot(tmp_path: Path) -> None:
    # A tests dir with no test files -> not a live evidence base -> fall through to the snapshot.
    empty_tests = tmp_path / "tests"
    empty_tests.mkdir()

    snap_file = tmp_path / "_maturity_snapshot.json"
    written = {
        "proven": 3,
        "total": 5,
        "ratio": 0.6,
        "level": "Beta",
        "surfaces": [
            {"name": "fusion", "proven": 3, "total": 5, "ratio": 0.6, "level": "Beta", "missing": ["x"]}
        ],
        "weakest": {"name": "fusion", "ratio": 0.6},
        "generated_for": "9.9.9",
    }
    snap_file.write_text(json.dumps(written), encoding="utf-8")

    report = maturity_report(tests_dirs=[empty_tests], snapshot_file=snap_file)
    assert report["available"] is True
    assert report["source"] == "snapshot"
    assert report["proven"] == 3 and report["total"] == 5
    assert report["generated_for"] == "9.9.9"
    assert report["weakest"] == {"name": "fusion", "ratio": 0.6}


def test_maturity_report_unavailable_when_nothing_readable(tmp_path: Path) -> None:
    empty_tests = tmp_path / "tests"
    empty_tests.mkdir()
    missing_snapshot = tmp_path / "does_not_exist.json"

    report: dict[str, Any] = maturity_report(
        tests_dirs=[empty_tests], snapshot_file=missing_snapshot
    )
    assert report["available"] is False
    assert report["source"] is None
    assert report["surfaces"] == []
    assert report["proven"] == 0 and report["total"] == 0
    assert report["weakest"] is None and report["generated_for"] is None
