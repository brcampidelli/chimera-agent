"""The self-improving trust kernel.

Every action passes through ``evaluate`` -> allow / warn / review / block. Lexical
rules (deterministic) decide fixed-signature threats; a semantic judge (optional)
decides intent-dependent ones. The kernel can *distill* repeated judge verdicts into
cheap lexical rules, getting faster over time. It never hard-blocks a benign action:
with no matching rule and no judge, the default is ALLOW.
"""

from __future__ import annotations

from collections.abc import Callable

from chimera.governance.audit import AuditLog
from chimera.governance.policy import Decision, Rule, RuleSet, Verdict, more_severe
from chimera.telemetry import get_logger

_log = get_logger("governance.kernel")

JudgeFn = Callable[[str], Verdict]


class TrustKernel:
    """Decides allow/warn/review/block for actions, learning over time."""

    def __init__(
        self,
        ruleset: RuleSet | None = None,
        *,
        judge: JudgeFn | None = None,
        audit: AuditLog | None = None,
        default: Decision = Decision.ALLOW,
    ) -> None:
        self.ruleset = ruleset or RuleSet()
        self.learned = RuleSet(use_defaults=False)
        self.judge = judge
        self.audit = audit
        self.default = default

    def evaluate(self, action: str, *, context: str = "") -> Verdict:
        verdict = more_severe(self.ruleset.evaluate(action), self.learned.evaluate(action))
        source = "lexical"
        if verdict is None and self.judge is not None:
            verdict = self.judge(action)
            source = "judge"
        if verdict is None:
            verdict = Verdict(self.default, "no rule matched; default policy", "default")
            source = "default"

        if self.audit is not None:
            self.audit.record(
                "governance",
                {
                    "action": action[:200],
                    "decision": verdict.decision.value,
                    "rule": verdict.rule,
                    "reason": verdict.reason,
                    "source": source,
                },
            )
        return verdict

    def distill_rule(self, rule: Rule) -> None:
        """Add a learned lexical rule (e.g. distilled from repeated judge verdicts)."""
        self.learned.add(rule)
        _log.debug("distilled rule %s -> %s", rule.name, rule.decision.value)
