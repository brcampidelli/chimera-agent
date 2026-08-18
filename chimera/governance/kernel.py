"""The self-improving trust kernel.

Every action passes through ``evaluate`` -> allow / warn / review / block. Lexical
rules (deterministic) decide fixed-signature threats; a semantic judge (optional)
decides intent-dependent ones. The kernel can *distill* repeated judge verdicts into
cheap lexical rules, getting faster over time. It never hard-blocks a benign action:
with no matching rule and no judge, the default is ALLOW.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import cast

from chimera.governance.audit import AuditLog
from chimera.governance.policy import Decision, Rule, RuleSet, Verdict, more_severe
from chimera.governance.precedent import PrecedentStore
from chimera.telemetry import get_logger

_log = get_logger("governance.kernel")

JudgeFn = Callable[[str], Verdict]
#: A judge that also gets *why* the action is being taken. ``rm -rf build/`` reads very differently
#: mid-build than it does inside a task about reading logs, and a judge handed only the command has
#: no way to tell those apart.
ContextJudgeFn = Callable[[str, str], Verdict]


def _accepts_context(judge: object) -> bool:
    """Whether ``judge`` can be called with ``(action, context)`` as well as ``(action)``.

    Inspected once, at construction, rather than guessed per call — and it fails *closed* to the
    one-argument form. A judge whose signature cannot be read (a C callable, a mock, a partial over
    ``*args``) keeps the old behaviour instead of getting a second positional argument it may not
    take, which would turn a governance decision into a TypeError at the worst moment.
    """
    if judge is None or not callable(judge):
        return False
    try:
        params = list(inspect.signature(judge).parameters.values())
    except (TypeError, ValueError):
        return False
    positional = [
        p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
    ]
    return len(positional) >= 2


class TrustKernel:
    """Decides allow/warn/review/block for actions, learning over time."""

    def __init__(
        self,
        ruleset: RuleSet | None = None,
        *,
        judge: JudgeFn | ContextJudgeFn | None = None,
        audit: AuditLog | None = None,
        audit_allows: bool = True,
        precedents: PrecedentStore | None = None,
        default: Decision = Decision.ALLOW,
    ) -> None:
        self.ruleset = ruleset or RuleSet()
        self.learned = RuleSet(use_defaults=False)
        self.judge = judge
        self.audit = audit
        # Whether an ALLOW — the overwhelmingly common verdict — earns a line. On cron that is a few
        # hundred a day and worth keeping. On an interactive coding turn it is one per tool call,
        # and the Security screen reads the newest 200, so about twenty-five turns would push every
        # taint and narrowing event, the rare ones that screen exists for, off the first page.
        # `assemble_registry` had already reached this conclusion for `restrict_registry` and
        # written it down: "a trail nobody can read is the same as no trail."
        #
        # The cost, named because it is the same shape as a bug this file's neighbours have fixed
        # twice: with it off and nothing refused, the log holds no governance line at all — so "the
        # kernel is installed and allowed everything" and "the kernel is not installed" look
        # identical to whoever reads the Security screen. Those are opposite claims. Distinguishing
        # them needs a line that says the kernel STARTED, written once per assembly rather than once
        # per call; that is not this parameter's job and it is not yet anyone's.
        self.audit_allows = audit_allows
        self.precedents = precedents
        self.default = default
        self._judge_takes_context = _accepts_context(judge)

    def evaluate(self, action: str, *, context: str = "", record_as: str | None = None) -> Verdict:
        """Decide on ``action``, optionally told *why* it is happening.

        ``context`` was declared here and read nowhere: the signature accepted it, the body never
        mentioned it, and the one production caller (``GovernedTool``) never passed it. A parameter
        that silently discards what you give it is worse than no parameter, because a caller can
        believe the kernel is judging with information it never received.

        It now reaches two places. The **judge** gets it when the judge can take it — arity is
        inspected once at construction, so a one-argument judge written before this keeps working.
        The **audit** gets it always, which is the cheaper half and the one that matters when
        somebody reads the log afterwards asking why an action was allowed.

        It deliberately does NOT reach the precedent store: precedents are keyed on the action, and
        folding context into that key would fragment the cache so finely that nothing would ever
        match twice — turning a cost optimisation into a cost multiplier.
        """
        verdict = more_severe(self.ruleset.evaluate(action), self.learned.evaluate(action))
        source = "lexical"
        # Precedent RAG: a confirmed precedent (2 judges agreed) answers a similar
        # action cheaply, before the expensive judge is consulted again.
        if verdict is None and self.precedents is not None:
            recalled = self.precedents.recall(action)
            if recalled is not None:
                verdict = Verdict(recalled, "matched a confirmed precedent", "precedent")
                source = "precedent"
        if verdict is None and self.judge is not None:
            if self._judge_takes_context:
                verdict = cast("ContextJudgeFn", self.judge)(action, context)
            else:
                verdict = cast("JudgeFn", self.judge)(action)
            source = "judge"
            if self.precedents is not None:
                self.precedents.observe(action, verdict.decision)
        if verdict is None:
            verdict = Verdict(self.default, "no rule matched; default policy", "default")
            source = "default"

        if self.audit is not None and (self.audit_allows or verdict.decision != Decision.ALLOW):
            self.audit.record(
                "governance",
                {
                    # `record_as` is the caller's audit-safe rendering of the same action — the
                    # rules judge the full text, the log keeps a version with document bodies
                    # elided. Falls back to `action` for the callers that have nothing to hide.
                    "action": (record_as or action)[:200],
                    "decision": verdict.decision.value,
                    "rule": verdict.rule,
                    "reason": verdict.reason,
                    "source": source,
                    # Truncated like ``action`` above, and omitted when empty so the log does not
                    # grow a column of empty strings for the callers that have no context to give.
                    **({"context": context[:200]} if context else {}),
                },
            )
        return verdict

    def distill_rule(self, rule: Rule) -> None:
        """Add a learned lexical rule (e.g. distilled from repeated judge verdicts)."""
        self.learned.add(rule)
        _log.debug("distilled rule %s -> %s", rule.name, rule.decision.value)
