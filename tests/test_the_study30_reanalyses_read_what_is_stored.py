"""`bench/study30_reanalyses/reanalyze.py` — four US$ 0 reanalyses of stored results (study 30, S30-38).

The published JSON must be what the script computes from the files in `bench/` today, and the one
decision rule it applies (a group "carries" confident misses when its Wilson lower bound is above the
pooled rate) is pinned on cases built to sit on either side of it.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from bench.study30_reanalyses import reanalyze  # noqa: E402


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


def test_the_url_check_and_the_content_check_are_counted_separately() -> None:
    cit = reanalyze.citations()
    for arm in ("A", "B"):
        ok, bad = cit[arm]["url_valid_content_invalid"], cit[arm]["url_invalid_content_valid"]
        assert ok["n"] + bad["n"] <= cit[arm]["turns"]


def test_the_verifier_slice_acceptance_reproduces_the_published_rates() -> None:
    # bench/verified_cascade/RESULTS.md: Jev accepts 1.2% of the 686 unsupported constructions, the
    # local verifier 22.2%. Reading the noise-floor rereads (`replay|`) in place of the originals gave
    # 7/686 for Jev — the cross-check that caught it.
    vc = reanalyze.vc_misses()
    assert (vc["jev"]["pooled"]["accepted"]["k"], vc["jev"]["pooled"]["accepted"]["n"]) == (8, 686)
    assert (vc["local"]["pooled"]["accepted"]["k"], vc["local"]["pooled"]["accepted"]["n"]) == (152, 686)
