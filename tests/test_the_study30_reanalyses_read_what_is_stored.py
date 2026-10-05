"""`bench/study30_reanalyses/reanalyze.py` — four US$ 0 reanalyses of stored results (study 30, S30-38).

The published JSON must be what the script computes from the files in `bench/` today, and the one
decision rule it applies (a group "carries" confident misses when its Wilson lower bound is above the
pooled rate) is pinned on cases built to sit on either side of it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from bench.study30_reanalyses import reanalyze  # noqa: E402

# The stored rows are committed; a checkout that leaves `bench/*/results*` out (the WSL gate copies
# without them) has nothing to reanalyse, which says nothing about the code. Skipped there only.
needs_results = pytest.mark.skipif(
    not (reanalyze.BENCH / "verified_cascade" / "results" / "run" / "calls.jsonl").exists()
    or not reanalyze.OUT.exists(),
    reason="bench results are not in this checkout",
)


@needs_results
def test_the_published_reanalyses_are_what_the_script_computes_now() -> None:
    published = json.loads(reanalyze.OUT.read_text(encoding="utf-8"))
    assert published == json.loads(json.dumps(reanalyze.build(), sort_keys=True))


def test_a_group_carries_confident_misses_only_when_its_interval_clears_the_pooled_rate() -> None:
    pooled = {"m": reanalyze.rate(10, 200)}  # 5%
    groups = {
        "concentrated": {"m": reanalyze.rate(8, 10)},  # Wilson lower ~0.49
        "ordinary": {"m": reanalyze.rate(1, 20)},  # 5%, the pooled rate itself
        "empty": {"m": reanalyze.rate(0, 0)},
    }
    out = reanalyze.carriers(groups, pooled, "m")
    assert "concentrated" in out and "ordinary" not in out and "empty" not in out
    assert out == [g for g in groups if g in out]  # order kept


@needs_results
def test_the_url_check_and_the_content_check_are_counted_separately() -> None:
    cit = reanalyze.citations()
    for arm in ("A", "B"):
        ok, bad = cit[arm]["url_valid_content_invalid"], cit[arm]["url_invalid_content_valid"]
        assert ok["n"] + bad["n"] <= cit[arm]["turns"]


@needs_results
def test_the_verifier_slice_acceptance_reproduces_the_published_rates() -> None:
    # bench/verified_cascade/RESULTS.md: Jev accepts 1.2% of the 686 unsupported constructions, the
    # local verifier 22.2%. Reading the noise-floor rereads (`replay|`) in place of the originals gave
    # 7/686 for Jev — the cross-check that caught it.
    vc = reanalyze.vc_misses()
    assert (vc["jev"]["pooled"]["accepted"]["k"], vc["jev"]["pooled"]["accepted"]["n"]) == (8, 686)
    assert (vc["local"]["pooled"]["accepted"]["k"], vc["local"]["pooled"]["accepted"]["n"]) == (152, 686)


def test_only_a_verification_result_next_to_an_outcome_counts_as_a_verify_row() -> None:
    # The reviewer's aggregate `{"abstained": 0}` is what the stored files hold; it is not a row.
    assert reanalyze.verify_rows({"other": {"abstained": 0}, "rows": 3}) == []
    assert reanalyze.verify_rows([{"abstained": True, "passed": True}]) == []  # no outcome next to it
    row = {"abstained": True, "passed": True, "resolved": False}
    assert reanalyze.verify_rows({"runs": [{"verify": row}]}) == [row]


def test_an_input_that_differs_from_its_pin_stops_the_reanalysis(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "a.json").write_text('{"x": 1}\n', encoding="utf-8", newline="\n")
    monkeypatch.setattr(reanalyze, "INPUTS", {"a.json": reanalyze.sha256_lf(tmp_path / "a.json")})
    reanalyze.check_inputs(tmp_path)  # matches: no exit
    # The same bytes with CRLF are the same input (a Windows checkout); a changed value is not.
    (tmp_path / "a.json").write_bytes(b'{"x": 1}\r\n')
    reanalyze.check_inputs(tmp_path)
    (tmp_path / "a.json").write_text('{"x": 2}\n', encoding="utf-8", newline="\n")
    with pytest.raises(SystemExit, match="a.json"):
        reanalyze.check_inputs(tmp_path)
    (tmp_path / "a.json").unlink()
    with pytest.raises(SystemExit, match="missing"):
        reanalyze.check_inputs(tmp_path)


@needs_results
def test_an_unrelated_new_bench_file_does_not_move_the_published_reanalyses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The inputs used to be live globs over `bench/`: a later commit storing an "abstained" field, a
    script importing VerificationResult or a new hierarchy run changed the numbers (and flipped
    "computable") although nothing in this analysis changed. Only the pinned files are read now."""
    import shutil

    bench = tmp_path / "bench"
    for rel in reanalyze.INPUTS:
        (bench / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(reanalyze.BENCH / rel, bench / rel)
    (bench / "new_review" / "results").mkdir(parents=True)
    (bench / "new_review" / "results" / "rows.jsonl").write_text('{"abstained": true}\n', encoding="utf-8")
    (bench / "new_review" / "run.py").write_text("from chimera.core.verify import VerificationResult\n", encoding="utf-8")
    shutil.copyfile(bench / "hierarchy_equal_calls" / "results" / "pilot.jsonl",
                    bench / "hierarchy_equal_calls" / "results" / "2026-12-01.jsonl")
    monkeypatch.setattr(reanalyze, "BENCH", bench)
    published = json.loads(reanalyze.OUT.read_text(encoding="utf-8"))
    assert json.loads(json.dumps(reanalyze.build(), sort_keys=True)) == published


@needs_results
def test_the_reanalysis_refuses_to_compute_on_a_changed_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A rerun on new data updates the pins in the same commit as the numbers; a silent change is refused.
    import shutil

    bench = tmp_path / "bench"
    for rel in reanalyze.INPUTS:
        (bench / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(reanalyze.BENCH / rel, bench / rel)
    pilot = bench / "hierarchy_equal_calls" / "results" / "pilot.jsonl"
    pilot.write_text(pilot.read_text(encoding="utf-8") + pilot.read_text(encoding="utf-8").splitlines()[0] + "\n",
                     encoding="utf-8", newline="\n")
    monkeypatch.setattr(reanalyze, "BENCH", bench)
    with pytest.raises(SystemExit, match="pilot.jsonl"):
        reanalyze.build()
