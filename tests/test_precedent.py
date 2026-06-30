"""Tests for the guarded precedent store + kernel precedent-RAG (AgentTrust v2)."""

from __future__ import annotations

from pathlib import Path

from chimera.governance import Decision, PrecedentStore, RuleSet, TrustKernel, Verdict


def test_observe_confirms_after_min_agreement() -> None:
    store = PrecedentStore(min_agreement=2)
    assert store.observe("delete the temp file", Decision.ALLOW) is False  # 1st
    assert store.observe("delete the temp file", Decision.ALLOW) is True  # 2nd -> confirmed
    assert store.confirmed() == 1


def test_conflicting_verdict_resets_agreement() -> None:
    store = PrecedentStore(min_agreement=2)
    store.observe("do X", Decision.ALLOW)
    assert store.observe("do X", Decision.BLOCK) is False  # different verdict resets
    assert store.confirmed() == 0


def test_recall_matches_similar_confirmed_precedent() -> None:
    store = PrecedentStore(min_agreement=2, min_overlap=0.5)
    store.observe("rename the old log files", Decision.WARN)
    store.observe("rename the old log files", Decision.WARN)  # confirmed
    assert store.recall("rename the old log files now") == Decision.WARN  # similar
    assert store.recall("launch a rocket") is None  # dissimilar


def test_unconfirmed_precedent_is_not_recalled() -> None:
    store = PrecedentStore(min_agreement=2)
    store.observe("only seen once", Decision.WARN)  # 1 agreement
    assert store.recall("only seen once") is None


def test_precedent_persist_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "p.json"
    first = PrecedentStore(path, min_agreement=2)
    first.observe("alpha beta gamma", Decision.ALLOW)
    first.observe("alpha beta gamma", Decision.ALLOW)
    assert PrecedentStore(path, min_agreement=2).recall("alpha beta gamma") == Decision.ALLOW


def test_kernel_uses_precedent_to_skip_the_judge() -> None:
    calls = {"n": 0}

    def judge(action: str) -> Verdict:
        calls["n"] += 1
        return Verdict(Decision.WARN, "judged", "judge")

    store = PrecedentStore(min_agreement=2, min_overlap=0.5)
    kernel = TrustKernel(RuleSet(use_defaults=False), judge=judge, precedents=store)

    kernel.evaluate("deploy the staging branch")  # judge #1, 1 agreement
    kernel.evaluate("deploy the staging branch")  # judge #2, confirmed
    assert calls["n"] == 2

    verdict = kernel.evaluate("deploy the staging branch please")  # similar -> recall
    assert calls["n"] == 2  # the judge was NOT consulted again
    assert verdict.rule == "precedent" and verdict.decision == Decision.WARN
