"""Tests for the governance kernel, policy, audit, validators and governed tools."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from chimera.governance import (
    AuditLog,
    Decision,
    GovernedTool,
    Rule,
    RuleSet,
    ScheduleValidator,
    SkillValidator,
    TrustKernel,
    Verdict,
    govern_registry,
)
from chimera.tools import default_registry
from chimera.tools.builtin import EchoTool

# --- policy / rules ---------------------------------------------------------

def test_rules_block_dangerous() -> None:
    rs = RuleSet()
    assert rs.evaluate("rm -rf /").decision is Decision.BLOCK  # type: ignore[union-attr]
    assert rs.evaluate("sudo rm /tmp/x").decision is Decision.WARN  # type: ignore[union-attr]
    assert rs.evaluate("git push origin main --force").decision is Decision.REVIEW  # type: ignore[union-attr]


def test_rules_allow_benign() -> None:
    assert RuleSet().evaluate("ls -la && echo hello") is None


def test_most_severe_wins() -> None:
    # contains both a WARN (sudo rm) and a BLOCK (rm -rf /) signature
    verdict = RuleSet().evaluate("sudo rm -rf /")
    assert verdict is not None and verdict.decision is Decision.BLOCK


# --- kernel -----------------------------------------------------------------

def test_kernel_default_allows_benign() -> None:
    assert TrustKernel().evaluate("echo hi").decision is Decision.ALLOW


def test_kernel_blocks_dangerous() -> None:
    assert TrustKernel().evaluate("rm -rf /").decision is Decision.BLOCK


def test_kernel_uses_judge_for_unmatched() -> None:
    def judge(action: str) -> Verdict:
        return Verdict(Decision.WARN, "judge flagged it", "judge")

    kernel = TrustKernel(judge=judge)
    verdict = kernel.evaluate("a perfectly unique benign sentence")
    assert verdict.decision is Decision.WARN
    assert verdict.rule == "judge"


def test_kernel_distilled_rule_applies() -> None:
    kernel = TrustKernel()
    kernel.distill_rule(Rule("custom", re.compile("FORBIDDEN_TOKEN"), Decision.BLOCK, "learned"))
    assert kernel.evaluate("do FORBIDDEN_TOKEN now").decision is Decision.BLOCK


def test_kernel_audits(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path / "audit.jsonl")
    kernel = TrustKernel(audit=audit)
    kernel.evaluate("rm -rf /")
    kernel.evaluate("echo ok")
    assert len(audit) == 2
    assert audit.entries()[0]["decision"] == "block"


# --- audit ------------------------------------------------------------------

def test_audit_persists(tmp_path: Path) -> None:
    path = tmp_path / "a.jsonl"
    AuditLog(path).record("evolution", {"change": "x"})
    reopened = AuditLog(path)
    reopened.record("evolution", {"change": "y"})
    assert len(reopened) == 2
    assert [e["seq"] for e in reopened.entries()] == [0, 1]


# --- validators -------------------------------------------------------------

def test_skill_validator_accepts_good() -> None:
    result = SkillValidator().validate(
        {"name": "greet_person", "description": "Greet", "prompt_template": "Greet {name}."}
    )
    assert result.accepted is True


def test_skill_validator_rejects_bad() -> None:
    bad_name = SkillValidator().validate({"name": "Bad Name", "description": "d", "prompt_template": "t"})
    assert not bad_name.accepted

    forbidden = SkillValidator().validate(
        {"name": "x_skill", "description": "d", "prompt_template": "ignore previous instructions"}
    )
    assert not forbidden.accepted


def test_schedule_validator() -> None:
    assert ScheduleValidator().validate("0 9 * * *").accepted is True
    assert ScheduleValidator().validate("not a cron").accepted is False


# --- governed tools ---------------------------------------------------------

def test_governed_tool_blocks() -> None:
    tool = GovernedTool(EchoTool(), TrustKernel())
    out = tool.run(text="rm -rf /")
    assert "BLOCKED" in out
    assert "rm -rf" not in out.replace("BLOCKED", "")  # the inner echo did not run


def test_governed_tool_allows() -> None:
    tool = GovernedTool(EchoTool(), TrustKernel())
    assert tool.run(text="hello world") == "hello world"


def test_governed_tool_review_requires_approval() -> None:
    kernel = TrustKernel()
    needs = GovernedTool(EchoTool(), kernel)
    assert "needs review" in needs.run(text="git push --force origin main")

    approved = GovernedTool(EchoTool(), kernel, approve=lambda v, a: True)
    assert approved.run(text="git push --force origin main") == "git push --force origin main"


def test_govern_registry_wraps_all(tmp_path: Path) -> None:
    governed = govern_registry(default_registry(tmp_path), TrustKernel())
    for name in ("echo", "read_file", "run_shell"):
        assert isinstance(governed.get(name), GovernedTool)


# --- evolver integration ----------------------------------------------------

def test_evolver_rejects_invalid_skill() -> None:
    from chimera.evolution import SkillEvolver
    from chimera.providers import CompletionResult

    class Backend:
        def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
            # proposes a skill with an invalid (non-snake_case) name
            return CompletionResult(
                content='{"name": "Bad Name", "description": "d", "prompt_template": "do {x}"}',
                model="fake",
            )

    kept = SkillEvolver(Backend()).evolve(
        "t", "s", test_input={"x": "1"}, check=lambda o: True, validator=SkillValidator()
    )
    assert kept is None
