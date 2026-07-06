"""Tests for completion contracts (M13 B2) — declared, machine-checkable success clauses."""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.core.agent import AgentResult
from chimera.core.autonomous import AutonomousAgent, AutonomousConfig
from chimera.core.contract import (
    AnswerMatches,
    CompletionContract,
    FileContains,
    FileExists,
    parse_check,
)

# --- parsing ----------------------------------------------------------------------------


def test_parse_each_kind() -> None:
    assert isinstance(parse_check("file_exists:out.txt"), FileExists)
    assert isinstance(parse_check("answer_matches:DONE"), AnswerMatches)
    fc = parse_check("file_contains:a.py:def foo")
    assert isinstance(fc, FileContains) and fc.path == "a.py" and fc.pattern == "def foo"


def test_parse_rejects_unknown_and_malformed() -> None:
    for bad in ("nope:x", "file_exists", "file_contains:only_path", "answer_matches:[bad(regex"):
        with pytest.raises(ValueError):
            parse_check(bad)


# --- individual checks ------------------------------------------------------------------


def test_file_exists(tmp_path: Path) -> None:
    (tmp_path / "out.txt").write_text("hi", encoding="utf-8")
    assert FileExists("out.txt").evaluate(tmp_path, "")[0] is True
    ok, reason = FileExists("missing.txt").evaluate(tmp_path, "")
    assert ok is False and "exist" in reason


def test_file_contains(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    assert FileContains("a.py", r"def foo").evaluate(tmp_path, "")[0] is True
    assert FileContains("a.py", r"def bar").evaluate(tmp_path, "")[0] is False
    assert FileContains("ghost.py", r"x").evaluate(tmp_path, "")[0] is False  # missing file


def test_answer_matches(tmp_path: Path) -> None:
    assert AnswerMatches(r"\bDONE\b").evaluate(tmp_path, "all DONE now")[0] is True
    assert AnswerMatches(r"\bDONE\b").evaluate(tmp_path, "still working")[0] is False


# --- contract aggregation ---------------------------------------------------------------


def test_contract_collects_all_failures(tmp_path: Path) -> None:
    contract = CompletionContract.from_specs(
        ["file_exists:out.txt", "answer_matches:SHIPPED"], tmp_path
    )
    result = contract.evaluate("nope")
    assert result.satisfied is False and len(result.failures) == 2


def test_contract_satisfied_when_all_pass(tmp_path: Path) -> None:
    (tmp_path / "out.txt").write_text("SHIPPED", encoding="utf-8")
    contract = CompletionContract.from_specs(
        ["file_exists:out.txt", "file_contains:out.txt:SHIPPED"], tmp_path
    )
    assert contract.evaluate("done").satisfied is True


def test_empty_contract_is_falsey_and_satisfied(tmp_path: Path) -> None:
    contract = CompletionContract.from_specs([], tmp_path)
    assert not contract  # __bool__ False so the loop skips it
    assert contract.evaluate("anything").satisfied is True


# --- integration with the solve loop ----------------------------------------------------


class _OkWorker:
    def run(self, task: str) -> AgentResult:
        return AgentResult(answer="I finished it.", steps=1, transcript=[], stopped_reason="done")


class _PassVerifier:
    def verify(self) -> object:
        from chimera.core.verify import VerificationResult

        return VerificationResult(passed=True, output="ok")


def test_verified_attempt_still_fails_unmet_contract(tmp_path: Path) -> None:
    # Verifier passes, but the contract requires an artifact the worker never created.
    contract = CompletionContract.from_specs(["file_exists:deliverable.md"], tmp_path)
    agent = AutonomousAgent(
        _OkWorker(),
        verifier=_PassVerifier(),  # type: ignore[arg-type]
        contract=contract,
        config=AutonomousConfig(max_attempts=1, use_planner=False, use_manager=False),
    )
    result = agent.run("produce deliverable.md")
    assert result.success is False
    assert "Completion contract not met" in result.attempts[0].feedback
    assert "deliverable.md" in result.attempts[0].feedback


def test_verified_attempt_passes_when_contract_met(tmp_path: Path) -> None:
    (tmp_path / "deliverable.md").write_text("content", encoding="utf-8")
    contract = CompletionContract.from_specs(["file_exists:deliverable.md"], tmp_path)
    agent = AutonomousAgent(
        _OkWorker(),
        verifier=_PassVerifier(),  # type: ignore[arg-type]
        contract=contract,
        config=AutonomousConfig(max_attempts=1, use_planner=False, use_manager=False),
    )
    assert agent.run("produce deliverable.md").success is True
