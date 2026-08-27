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
    # M19 Track B (project orchestrator): optional task-graph metadata a spec author can declare.
    # ``depends_on`` lists requirement ids that must be satisfied before this one's card is ready;
    # ``risk="high"`` makes the project orchestrator pause for human approval before running its card
    # (deploy/migration/delete). Ignored by the plain drift gate — it only reads check/target.
    depends_on: list[str] = Field(default_factory=list)
    risk: str = ""


class Spec(BaseModel):
    name: str
    requirements: list[Requirement] = Field(default_factory=list)
    #: Where this spec was read from, when it was read from a file. Carried on the model rather
    #: than passed to :func:`check_drift`, because it exists to be EXCLUDED from the scan and a
    #: caller who forgets to pass it gets the bug back. Measured: a spec stored in the folder it
    #: judges satisfies itself — every ``contains`` regex is searched across every text file, and
    #: the spec's own ``text`` fields repeat the words those regexes look for, so an empty project
    #: reports aligned with nothing written. Excluded from serialization: it is a local path, not
    #: part of the spec.
    source: Path | None = Field(default=None, exclude=True)


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


def _scannable(workspace: Path, skip: Path | None) -> Iterator[Path]:
    """Every file the checks are allowed to read. One place, so ``skip`` cannot be honoured by one
    check and forgotten by another — the positive and negative scans disagreeing about what counts
    as evidence would be worse than either rule alone."""
    ignore = skip.resolve() if skip else None
    for path in workspace.rglob("*"):
        if path.is_dir():
            continue
        if any(part in _IGNORE_DIRS for part in path.relative_to(workspace).parts):
            continue
        if ignore is not None:
            try:
                if path.resolve() == ignore:
                    continue
            except OSError:
                pass
        yield path


def _iter_text(workspace: Path, skip: Path | None = None) -> Iterator[str]:
    for path in _scannable(workspace, skip):
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                continue
            yield path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue


def _present(workspace: Path, pattern: str, skip: Path | None = None) -> bool:
    regex = re.compile(pattern)
    return any(regex.search(text) for text in _iter_text(workspace, skip))


def _scan_absent(workspace: Path, pattern: str, skip: Path | None = None) -> tuple[bool, list[str]]:
    """For a negative (``absent``) check: return (pattern_found, unscannable_files).

    ``_iter_text`` silently skips oversized (> 1 MB) and undecodable files. For a POSITIVE check
    that only risks a conservative false-'missing'; for a NEGATIVE (security) check it fails OPEN —
    a forbidden pattern hiding in a skipped file would report 'absent'. So a negative check must
    treat an unscannable file as un-verifiable, not as clean.
    """
    regex = re.compile(pattern)
    found = False
    unscannable: list[str] = []
    for path in _scannable(workspace, skip):
        try:
            if path.stat().st_size > _MAX_FILE_BYTES:
                unscannable.append(str(path.relative_to(workspace)))
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            unscannable.append(str(path.relative_to(workspace)))
            continue
        if regex.search(text):
            found = True
    return found, unscannable


def _defined(workspace: Path, name: str, skip: Path | None = None) -> bool:
    regex = re.compile(rf"^\s*(def|class)\s+{re.escape(name)}\b", re.MULTILINE)
    return any(regex.search(text) for text in _iter_text(workspace, skip))


def _check(requirement: Requirement, workspace: Path, skip: Path | None = None) -> tuple[bool, str]:
    if requirement.check == "defines":
        ok = _defined(workspace, requirement.target, skip)
        return ok, "" if ok else f"'{requirement.target}' is not defined"
    if requirement.check == "contains":
        ok = _present(workspace, requirement.target, skip)
        return ok, "" if ok else f"missing /{requirement.target}/"
    if requirement.check == "absent":
        found, unscannable = _scan_absent(workspace, requirement.target, skip)
        if found:
            return False, f"found /{requirement.target}/ (should be absent)"
        if unscannable:
            # Fail closed: the pattern could be hiding in a file we couldn't read.
            sample = ", ".join(unscannable[:3])
            return False, (
                f"cannot verify /{requirement.target}/ is absent: "
                f"{len(unscannable)} unscannable file(s) (oversized/binary): {sample}"
            )
        return True, ""
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
        ok, detail = _check(requirement, root, spec.source)
        results.append(RequirementResult(requirement.id, ok, detail))
        if requirement.required and not ok:
            aligned = False
    return DriftReport(spec.name, aligned, results)


def load_spec(path: str | Path) -> Spec:
    import yaml

    source = Path(path)
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    spec = Spec.model_validate(data)
    spec.source = source
    return spec
