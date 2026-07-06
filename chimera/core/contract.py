"""Completion contracts — machine-checkable success conditions, declared up front.

A model (especially a weak one) is prone to declaring victory early: it *narrates* success it
didn't achieve. A completion contract is the antidote — a set of deterministic checks stated
before the run that must ALL hold before the run may be called done. It is an AND gate on top
of verify-or-revert: even a verified/approved attempt fails if the contract isn't met, and the
unmet clauses are fed back verbatim so the next attempt fixes exactly what's missing.

Checks are intentionally deterministic (no LLM, no shell): an output file exists, a file
contains a pattern, or the final answer matches a pattern. Command-exit checks are already
covered by the ``--verify`` verifier; the contract adds the declarative artifact checks that
catch "I'm done" when the artifact says otherwise.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from chimera.tools.workspace import resolve_in_workspace


class Check(Protocol):
    """A single contract clause: does the workspace/answer satisfy it?"""

    def evaluate(self, workspace: Path, answer: str) -> tuple[bool, str]:
        """Return (satisfied, reason-if-not)."""
        ...


@dataclass
class FileExists:
    path: str

    def evaluate(self, workspace: Path, answer: str) -> tuple[bool, str]:
        target = resolve_in_workspace(workspace, self.path)
        return (target.is_file(), "" if target.is_file() else f"expected file {self.path!r} to exist")


@dataclass
class FileContains:
    path: str
    pattern: str

    def evaluate(self, workspace: Path, answer: str) -> tuple[bool, str]:
        target = resolve_in_workspace(workspace, self.path)
        if not target.is_file():
            return False, f"expected file {self.path!r} to exist and contain /{self.pattern}/"
        text = target.read_text(encoding="utf-8", errors="replace")
        if re.search(self.pattern, text):
            return True, ""
        return False, f"expected {self.path!r} to contain /{self.pattern}/"


@dataclass
class AnswerMatches:
    pattern: str

    def evaluate(self, workspace: Path, answer: str) -> tuple[bool, str]:
        if re.search(self.pattern, answer):
            return True, ""
        return False, f"expected the final answer to match /{self.pattern}/"


@dataclass
class ContractResult:
    satisfied: bool
    failures: list[str] = field(default_factory=list)


def parse_check(spec: str) -> Check:
    """Parse a ``kind:arg`` spec into a Check. Raises ValueError on an unknown/invalid spec.

    Forms: ``file_exists:PATH`` · ``file_contains:PATH:REGEX`` · ``answer_matches:REGEX``.
    Paths are workspace-relative.
    """
    kind, sep, rest = spec.partition(":")
    kind = kind.strip()
    if not sep or not rest:
        raise ValueError(f"malformed contract clause {spec!r} (expected 'kind:arg')")
    if kind == "file_exists":
        return FileExists(rest)
    if kind == "answer_matches":
        _validate_regex(rest)
        return AnswerMatches(rest)
    if kind == "file_contains":
        path, psep, pattern = rest.partition(":")
        if not psep or not pattern:
            raise ValueError(f"file_contains needs 'PATH:REGEX', got {rest!r}")
        _validate_regex(pattern)
        return FileContains(path, pattern)
    raise ValueError(f"unknown contract clause kind {kind!r}")


def _validate_regex(pattern: str) -> None:
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"invalid regex {pattern!r}: {exc}") from exc


class CompletionContract:
    """A conjunction of checks the run must satisfy before it may be declared done."""

    def __init__(self, checks: list[Check], workspace: Path | None = None) -> None:
        self.checks = checks
        self.workspace = (workspace or Path.cwd()).resolve()

    @classmethod
    def from_specs(cls, specs: list[str], workspace: Path | None = None) -> CompletionContract:
        """Build a contract from ``kind:arg`` strings (e.g. from repeated ``--contract``)."""
        return cls([parse_check(s) for s in specs if s.strip()], workspace)

    def __bool__(self) -> bool:
        return bool(self.checks)

    def evaluate(self, answer: str) -> ContractResult:
        """Check every clause against the workspace + answer; collect all failures."""
        failures = [reason for ok, reason in (c.evaluate(self.workspace, answer) for c in self.checks) if not ok]
        return ContractResult(satisfied=not failures, failures=failures)
