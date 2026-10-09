"""A decision that misses its deadline is a halt that says so, and the band reads it as REVIEW.

Study 22's I8 (`bench/PLAN-study22-system-one.md`): "an unreadable or halted decision means uncertain →
escalate, never pass". arXiv 2609.23136 measured decision models against a 1 s budget; until this
change a decision here had no latency budget at all — the local backend waits up to 30 s, and a tool
call waits with it. The deadline is an option, OFF by default. What is held here:

* past the deadline the answer is a halt with ``deadline_missed`` on the receipt and in the decision
  log, so a miss can be counted apart from a server that was down;
* the band turns that miss into REVIEW — and the sabotage test shows the check would fail if the miss
  were read as an ordinary halt, which goes to the default (ALLOW);
* a fast answer under a deadline is the same answer as without one; with no deadline the call runs
  on the caller's thread, exactly as before;
* a late answer is never cached or applied after the fact.
"""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from chimera.config import Settings
from chimera.decisions import CalibrationMaps, Decider, PlattMap, Reading
from chimera.decisions.contract import Answer, DecisionCache
from chimera.decisions.governance import DANGER, DECISION
from chimera.decisions.local import LocalLogprobBackend
from chimera.decisions.log import DecisionLog
from chimera.decisions.maps import SHIPPED_MAPS
from chimera.decisions.spec import REGISTRY, DecisionSpec, Escalation
from chimera.governance.audit import AuditLog
from chimera.governance.band import DecisionBand, build_band
from chimera.governance.kernel import TrustKernel
from chimera.governance.policy import Decision, Verdict


class _Backend:
    """Answers ``p`` after ``delay`` seconds — or, when slow, after the test releases it, so a
    straggler left on a worker thread ends with the test instead of outliving it."""

    name = "local_logprob"
    model = "qwen3:4b"

    def __init__(self, p: float, *, slow: bool = False) -> None:
        self.p = p
        self.slow = slow
        self.release = threading.Event()
        self.threads: list[str] = []
        self._instrument = LocalLogprobBackend("http://127.0.0.1:11434", "qwen3:4b").instrument(DANGER)

    def instrument(self, question: Any) -> str:
        return self._instrument

    def ask(self, state: str, question: Any) -> Reading:
        self.threads.append(threading.current_thread().name)
        if self.slow:
            self.release.wait(timeout=5.0)
        return Reading(
            choice="BLOCK" if self.p >= 0.5 else "ALLOW", shares=None, p=self.p, mass=0.99,
            logprobs_came=True, resolved_model="qwen3:4b@Q4_K_M",
        )


def _identity_map() -> CalibrationMaps:
    shipped = SHIPPED_MAPS[0]
    return CalibrationMaps([PlattMap(**{**shipped.to_dict(), "id": "identity", "a": 1.0, "b": 0.0})])


def _last(path: Path) -> dict[str, Any]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()][-1]


ACTION = "make deploy"


def _assert_failed_toward_review(verdict: Verdict, line: dict[str, Any]) -> None:
    """What a missed deadline must look like at the kernel. Shared with the sabotage test, which
    proves this check rejects the behaviour it exists to forbid."""
    assert verdict.decision is Decision.REVIEW, f"a missed deadline went to {verdict.decision}"
    assert verdict.rule == "decision_band" and "did not answer within" in verdict.reason
    assert line["band"] == "deadline" and line["deadline_missed"] is True


def _run_slow(tmp_path: Path, decider: Decider) -> tuple[Verdict, dict[str, Any]]:
    audit = AuditLog(tmp_path / "audit.jsonl")
    band = DecisionBand(decider, deadline_s=0.05)
    verdict = TrustKernel(audit=audit, band=band).evaluate(ACTION)
    return verdict, _last(audit.path)


def test_a_slow_answer_past_the_deadline_is_review_and_the_miss_is_recorded(tmp_path: Path) -> None:
    # p = 0.05 would be a confident ALLOW had it arrived: the verdict must not depend on the answer
    # the model gives after the deadline.
    backend = _Backend(0.05, slow=True)
    log = DecisionLog.for_home(tmp_path)
    try:
        verdict, line = _run_slow(tmp_path, Decider(backend, _identity_map(), log=log))
    finally:
        backend.release.set()
    _assert_failed_toward_review(verdict, line)
    assert line["deadline_s"] == pytest.approx(0.05)
    logged = _last(log.path)
    assert logged["deadline_missed"] is True and logged["halt"].startswith("DeadlineMissed")


def test_sabotage_a_miss_read_as_an_ordinary_halt_would_be_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The fallback the invariant forbids: the miss loses its flag and is read like a server that was
    # down, which goes on to the kernel's default — ALLOW. The shared check must reject that.
    original = Decider.decide

    def forgetful(self: Decider, *args: Any, **kwargs: Any) -> Answer:
        return replace(original(self, *args, **kwargs), deadline_missed=False)

    monkeypatch.setattr(Decider, "decide", forgetful)
    backend = _Backend(0.05, slow=True)
    try:
        verdict, line = _run_slow(tmp_path, Decider(backend, _identity_map()))
    finally:
        backend.release.set()
    assert verdict.decision is Decision.ALLOW  # the sabotage really produced the forbidden fallback
    with pytest.raises(AssertionError):
        _assert_failed_toward_review(verdict, line)


def test_a_fast_answer_under_a_deadline_is_unchanged(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    band = DecisionBand(Decider(_Backend(0.80), _identity_map()), deadline_s=2.0)
    verdict = TrustKernel(audit=audit, band=band).evaluate(ACTION)
    assert verdict.decision is Decision.REVIEW and verdict.confidence == pytest.approx(0.80)
    line = _last(audit.path)
    assert line["band"] == "review" and "deadline_missed" not in line

    answer = Decider(_Backend(0.05), _identity_map()).decide(DECISION, ACTION, DANGER, deadline_s=2.0)
    assert not answer.halt and not answer.deadline_missed and answer.p == pytest.approx(0.05)
    assert answer.receipt()["deadline_s"] == 2.0 and "deadline_missed" not in answer.receipt()


def test_without_a_deadline_nothing_changes() -> None:
    backend = _Backend(0.05)
    answer = Decider(backend, _identity_map()).decide(DECISION, ACTION, DANGER)
    assert backend.threads == [threading.current_thread().name]  # no worker thread in between
    assert answer.deadline_s is None and not answer.deadline_missed
    assert "deadline_s" not in answer.receipt() and "deadline_missed" not in answer.receipt()
    assert DecisionBand(Decider(backend)).deadline_s is None


def test_a_late_answer_is_never_cached() -> None:
    backend = _Backend(0.05, slow=True)
    cache = DecisionCache()
    try:
        answer = Decider(backend, _identity_map(), cache=cache).decide(DECISION, ACTION, DANGER, deadline_s=0.05)
    finally:
        backend.release.set()
    assert answer.deadline_missed and answer.halt and answer.p is None
    assert len(cache) == 0


def test_a_spec_may_declare_the_deadline_and_a_call_may_override_it() -> None:
    name = "test.deadline_spec"
    REGISTRY[name] = DecisionSpec(
        name=name, questions=(DANGER,), escalation=Escalation.REVIEW,
        bench="bench/jev_decisions/RESULTS.md", deadline_s=0.05,
    )
    backend = _Backend(0.05, slow=True)
    try:
        assert Decider(backend).decide(name, ACTION, DANGER).deadline_missed
        backend.release.set()
        assert not Decider(backend).decide(name, ACTION, DANGER, deadline_s=5.0).deadline_missed
    finally:
        backend.release.set()
        REGISTRY.pop(name, None)
    with pytest.raises(ValueError):
        DecisionSpec(name="x", questions=(DANGER,), escalation=Escalation.REVIEW, bench="b", deadline_s=0.0)
    with pytest.raises(ValueError):
        Decider(_Backend(0.1)).decide(DECISION, ACTION, DANGER, deadline_s=-1.0)
    with pytest.raises(ValueError):
        DecisionBand(Decider(_Backend(0.1)), deadline_s=0.0)


def test_the_deadline_is_off_by_default_and_read_from_the_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import chimera.decisions.factory as factory

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(factory, "build_decider", lambda settings: Decider(_Backend(0.1)))
    assert Settings(_env_file=None).governance_band_deadline_s is None
    assert build_band(Settings(_env_file=None)).deadline_s is None
    settings = Settings(_env_file=None, CHIMERA_GOVERNANCE_BAND_DEADLINE_S="1.0")
    assert build_band(settings).deadline_s == 1.0
    with pytest.raises(ValueError):
        Settings(_env_file=None, CHIMERA_GOVERNANCE_BAND_DEADLINE_S="0")
