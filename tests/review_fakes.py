"""Shared fakes for the `chimera review` tests: a model that answers by role, and a real git repo.

No network and no model: the finder's and the verifier's replies are fixed per test, and the
repository is a real one in ``tmp_path``, so the diff the pipeline reads is the one git prints.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from chimera.providers.gateway import CompletionResult
from chimera.review.finder import FINDER_SYSTEM
from chimera.review.verifier import VERIFIER_SYSTEM

BASE_CALC = "def total(xs):\n    s = 0\n    for x in xs:\n        s += x\n    return s\n"
CHANGED_CALC = (
    "def total(xs):\n    s = 0\n    for x in xs:\n        s += x\n    return s\n\n\n"
    "def mean(xs):\n    if not xs:\n        return 0\n    return total(xs) / (len(xs) - 1)\n"
)


def finding(file: str, line: int, priority: str = "P2", title: str = "a defect",
            confidence: float = 0.5) -> dict[str, Any]:
    return {"file": file, "line": line, "priority": priority, "title": title,
            "evidence": "quoted", "consequence": "it breaks", "confidence": confidence}


def finder_json(findings: list[dict[str, Any]], risks: list[str] | None = None,
                untested: list[str] | None = None) -> str:
    return json.dumps({"findings": findings, "residual_risks": risks or [],
                       "untested_paths": untested or []})


class FakeBackend:
    """Answers the finder with ``finder_reply``, and each verifier call with ``verdict(user)``."""

    def __init__(
        self,
        finder_reply: str,
        verdict: Callable[[str], str] = lambda _user: '{"reason": "fine", "verdict": "keep"}',
    ) -> None:
        self.finder_reply = finder_reply
        self.verdict = verdict
        self.calls: list[tuple[str, str, str | None]] = []  # (role, user text, model)

    def complete(
        self, messages: list[Any], *, model: str | None = None, **_kw: Any
    ) -> CompletionResult:
        system = messages[0].content if hasattr(messages[0], "content") else messages[0]["content"]
        user = messages[1].content if hasattr(messages[1], "content") else messages[1]["content"]
        if system == FINDER_SYSTEM:
            self.calls.append(("finder", user, model))
            content = self.finder_reply
        elif system == VERIFIER_SYSTEM:
            self.calls.append(("verifier", user, model))
            content = self.verdict(user)
        else:  # pragma: no cover - a prompt this fake does not know is a test bug
            raise AssertionError(f"unexpected system prompt: {system[:60]}")
        return CompletionResult(content=content, model=model or "fake", prompt_tokens=100,
                                completion_tokens=20)


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout


def repo_with_change(tmp_path: Path, *, commit_change: bool = False) -> Path:
    """A repo whose ``main`` holds ``calc.py`` and whose working tree (or a commit) changes it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "calc.py").write_text(BASE_CALC, encoding="utf-8")
    git(repo, "add", "calc.py")
    git(repo, "commit", "-qm", "base")
    git(repo, "checkout", "-q", "-b", "feature")
    (repo / "calc.py").write_text(CHANGED_CALC, encoding="utf-8")
    if commit_change:
        git(repo, "commit", "-qam", "add mean")
    return repo
