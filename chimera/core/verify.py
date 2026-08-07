"""Verification for the autonomous loop.

A ``Verifier`` answers one question: did the attempt succeed? The canonical verifier
runs a command (tests, a build, a linter) and treats exit code 0 as success — the
"executable evidence" gate that lets the agent keep a change instead of reverting it.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

_MAX_OUTPUT_CHARS = 20_000

#: Exit codes that mean "this command reached no verdict", not "the work is bad".
#:
#: 127 — the shell could not find the command at all.
#: 5   — pytest's "no tests collected". Deliberately included even though it is one tool's
#:       convention: pytest is what the inference reaches for most often, and a repository whose
#:       tests live outside the inferred path would otherwise have every change reverted by a
#:       verifier that ran nothing.
_NO_VERDICT = frozenset({5, 127})


@dataclass
class VerificationResult:
    """Outcome of a verification."""

    passed: bool
    output: str = ""
    abstained: bool = False
    """True = the verifier had nothing runnable to check (e.g. spec-test generation produced no
    tests). A ``passed=True, abstained=True`` result is NOT positive evidence — the caller must fall
    back to its other gates (Manager review, coverage checklist) rather than accept on it."""


class Verifier(Protocol):
    """Anything that can verify the current workspace state."""

    def verify(self) -> VerificationResult: ...


class CommandVerifier:
    """Runs a shell command; success == exit code 0."""

    def __init__(self, command: str, workspace: Path, *, timeout: int = 120) -> None:
        self.command = command
        self.workspace = Path(workspace)
        self.timeout = timeout

    def verify(self) -> VerificationResult:
        try:
            proc = subprocess.run(
                self.command,
                shell=True,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            return VerificationResult(False, f"verification timed out after {self.timeout}s")
        except OSError as exc:
            # e.g. the cwd was removed, or the command binary is missing — report a failed/unverifiable
            # attempt instead of letting it propagate and abort the whole run.
            return VerificationResult(False, f"verification could not run: {exc}")
        output = ((proc.stdout or "") + (proc.stderr or ""))[:_MAX_OUTPUT_CHARS]
        if proc.returncode in _NO_VERDICT:
            # The command produced no verdict, which is NOT the same as a verdict of "bad".
            #
            # 127 is the shell saying the command does not exist; 5 is pytest saying it collected no
            # tests. Both used to be reported as a failed verification, so the attempt was reverted
            # and the receipt recorded a test failure that never happened. That was survivable while
            # a human typed the command and could see the mistake. It stops being survivable when the
            # command is INFERRED from the project, because then a repository whose tests live
            # somewhere the inference did not look would have every change silently thrown away.
            #
            # Abstaining hands the decision back to the other gates (the Manager review and the diff
            # gate) exactly as if no verifier had been configured — `verifier_active` in the
            # autonomous loop already demotes an abstention to the no-verifier path, so `evidence`
            # correctly stops being "verifier". We could not check it; we do not claim we did, and we
            # do not punish the work for our own inability.
            return VerificationResult(True, output, abstained=True)
        return VerificationResult(proc.returncode == 0, output)


class NullVerifier:
    """Always passes — used when no verification command is configured."""

    def verify(self) -> VerificationResult:
        return VerificationResult(True, "no verification configured")
