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


@dataclass
class VerificationResult:
    """Outcome of a verification."""

    passed: bool
    output: str = ""


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
        output = ((proc.stdout or "") + (proc.stderr or ""))[:_MAX_OUTPUT_CHARS]
        return VerificationResult(proc.returncode == 0, output)


class NullVerifier:
    """Always passes — used when no verification command is configured."""

    def verify(self) -> VerificationResult:
        return VerificationResult(True, "no verification configured")
