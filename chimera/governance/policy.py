"""Governance policy: decisions, verdicts, and a lexical rule set.

The trust kernel decides allow / warn / block / review for every action. Lexical
rules handle the *fixed-signature* threats (``rm -rf /``, hardcoded secrets, force
pushes) deterministically and cheaply; the semantic layer (a judge model) handles
intent-dependent cases. Invariant: a benign action is never hard-blocked.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class Decision(StrEnum):
    ALLOW = "allow"
    WARN = "warn"
    REVIEW = "review"
    BLOCK = "block"


_SEVERITY = {Decision.ALLOW: 0, Decision.WARN: 1, Decision.REVIEW: 2, Decision.BLOCK: 3}


@dataclass
class Verdict:
    """The outcome of a governance evaluation."""

    decision: Decision
    reason: str = ""
    rule: str | None = None

    @property
    def allowed(self) -> bool:
        """True if the action may proceed without human review (allow/warn)."""
        return self.decision in (Decision.ALLOW, Decision.WARN)


@dataclass
class Rule:
    """A lexical rule: a regex that maps a matching action to a decision."""

    name: str
    pattern: re.Pattern[str]
    decision: Decision
    reason: str


def _default_rules() -> list[Rule]:
    return [
        Rule("rm_rf_root", re.compile(r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\s+(/|~|\*|\.\s*$)"), Decision.BLOCK, "recursive force delete of a root/home/glob path"),
        Rule("disk_destroy", re.compile(r"\bmkfs\b|\bdd\s+if=.*\bof=/dev/"), Decision.BLOCK, "disk format/overwrite"),
        Rule("fork_bomb", re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"), Decision.BLOCK, "fork bomb"),
        Rule("chmod_777_root", re.compile(r"\bchmod\s+-R\s+777\s+/"), Decision.BLOCK, "world-writable root"),
        Rule("curl_pipe_shell", re.compile(r"\b(curl|wget)\b[^\n|]*\|\s*(sudo\s+)?(bash|sh|zsh)\b"), Decision.REVIEW, "piping a remote script straight into a shell"),
        Rule("git_force_push", re.compile(r"\bgit\s+push\b[^\n]*(--force\b|--force-with-lease\b|\s-f\b)"), Decision.REVIEW, "force push"),
        Rule("sudo_rm", re.compile(r"\bsudo\s+rm\b"), Decision.WARN, "privileged delete"),
        Rule("secret_material", re.compile(r"sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), Decision.WARN, "possible secret/credential in the action"),
    ]


class RuleSet:
    """An evaluable collection of lexical rules (most-severe match wins)."""

    def __init__(self, rules: list[Rule] | None = None, *, use_defaults: bool = True) -> None:
        self.rules: list[Rule] = list(rules) if rules is not None else []
        if use_defaults and rules is None:
            self.rules = _default_rules()

    def add(self, rule: Rule) -> None:
        self.rules.append(rule)

    def evaluate(self, action: str) -> Verdict | None:
        """Return the most severe matching rule's verdict, or None if no match."""
        best: Verdict | None = None
        for rule in self.rules:
            if rule.pattern.search(action):
                candidate = Verdict(rule.decision, rule.reason, rule.name)
                if best is None or _SEVERITY[candidate.decision] > _SEVERITY[best.decision]:
                    best = candidate
        return best


def more_severe(a: Verdict | None, b: Verdict | None) -> Verdict | None:
    """Return whichever verdict is more severe (or the non-None one)."""
    if a is None:
        return b
    if b is None:
        return a
    return a if _SEVERITY[a.decision] >= _SEVERITY[b.decision] else b
