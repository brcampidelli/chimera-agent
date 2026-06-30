"""Drift gate — keep a spec and the code aligned (Spec Growth Engine).

A *spec* is a small declarative artifact (YAML) listing requirements the code must
satisfy. The drift gate checks each requirement against the workspace; if any required
one fails, the spec and code have **drifted** and the change should be rejected.

Requirement check kinds (all deterministic except ``command``):
- ``defines``  — a function/class with this name must exist in the code.
- ``contains`` — this regex must appear somewhere in the code.
- ``absent``   — this regex must NOT appear (e.g. "no TODO left").
- ``command``  — this shell command must exit 0 (tests, a build, a linter).

Because the gate returns a non-zero exit on drift, ``chimera drift <spec>`` doubles as
a verifier: pass it to ``solve --verify`` to make the spec the executable ground truth.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from chimera.core.checkpoint import _IGNORE_DIRS

CheckKind = Literal["defines", "contains", "absent", "command"]
_MAX_FILE_BYTES = 1_000_000


class Requirement(BaseModel):
    id: str
    text: str = ""
    check: CheckKind
    target: str
    required: bool = True


class Spec(BaseModel):
    name: str
    requirements: list[Requirement] = Field(default_factory=list)


@dataclass
class RequirementResult:
    id: str
    satisfied: bool
    detail: str = ""


@dataclass
class DriftReport:
    name: str
    aligned: bool
    results: list[RequirementResult]


def _iter_text(workspace: Path) -> Iterator[str]:
    for path in workspace.rglob("*"):
        if path.is_dir():
            continue
        if any(part in _IGNORE_DIRS for part in path.relative_to(workspace).parts):
            continue
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
            yield path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue


def _present(workspace: Path, pattern: str) -> bool:
    regex = re.compile(pattern)
    return any(regex.search(text) for text in _iter_text(workspace))


def _defined(workspace: Path, name: str) -> bool:
    regex = re.compile(rf"^\s*(def|class)\s+{re.escape(name)}\b", re.MULTILINE)
    return any(regex.search(text) for text in _iter_text(workspace))


def _check(requirement: Requirement, workspace: Path) -> tuple[bool, str]:
    if requirement.check == "defines":
        ok = _defined(workspace, requirement.target)
        return ok, "" if ok else f"'{requirement.target}' is not defined"
    if requirement.check == "contains":
        ok = _present(workspace, requirement.target)
        return ok, "" if ok else f"missing /{requirement.target}/"
    if requirement.check == "absent":
        ok = not _present(workspace, requirement.target)
        return ok, "" if ok else f"found /{requirement.target}/ (should be absent)"
    # command
    from chimera.sandbox import LocalSandbox

    result = LocalSandbox().run(requirement.target, cwd=workspace)
    return result.exit_code == 0, "" if result.exit_code == 0 else f"exit {result.exit_code}"


def check_drift(spec: Spec, workspace: Path) -> DriftReport:
    """Check the workspace against the spec; ``aligned`` is False if anything drifted."""
    root = Path(workspace).resolve()
    results: list[RequirementResult] = []
    aligned = True
    for requirement in spec.requirements:
        ok, detail = _check(requirement, root)
        results.append(RequirementResult(requirement.id, ok, detail))
        if requirement.required and not ok:
            aligned = False
    return DriftReport(spec.name, aligned, results)


def load_spec(path: str | Path) -> Spec:
    import yaml

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return Spec.model_validate(data)
