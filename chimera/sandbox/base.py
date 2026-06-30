"""Sandbox interface: a uniform way to run a command and capture its result."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class SandboxResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False

    @property
    def output(self) -> str:
        return (self.stdout or "") + (self.stderr or "")


class Sandbox(Protocol):
    """A backend that runs a shell command in some isolation and returns its result."""

    def run(self, command: str, *, timeout: int = 60, cwd: Path | None = None) -> SandboxResult: ...
