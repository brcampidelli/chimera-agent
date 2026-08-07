"""Find the command that already judges this project, so nobody has to type it.

The verify command is the only part of the loop that decides whether work was good without having
an opinion — everything else is a model. That is why ``evidence == "verifier"`` is the one value the
receipt treats as executable, and why the field could not simply be deleted from the UI when the
screen was simplified: removing it would have quietly demoted every run to "a model read the answer
and approved it", while the panel counting passes kept using the same word.

So the field goes and the capability stays, by reading the project instead of asking the user. Three
properties make that safe to trust:

**No model, no network, no execution.** This looks at files. An inferred command is a fact about the
repository ("`pyproject.toml` configures pytest"), not a guess about intent — and it never runs the
command to check, because a "validated" command would be a second definition of verified.

**It says where it looked.** The result carries the file the rule fired on, and the receipt records
``inferred:<file>`` rather than pretending the user chose it. A receipt that cannot distinguish a
command someone typed from one this module guessed is a receipt that will eventually be cited as
evidence of a choice nobody made.

**It returns None rather than a guess.** A project with no discoverable test command gets an honest
"this run is judged by a model reading the answer, not by tests" — which has always been true when
the box was left empty, and which the interface never said once.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

#: What `npm init` writes into a fresh package.json. It exits 1, so adopting it as the verify command
#: would fail every run of every scaffolded project — the inference's most likely way to be actively
#: harmful rather than merely absent.
_NPM_SCAFFOLD = 'echo "Error: no test specified" && exit 1'


@dataclass(frozen=True)
class InferredVerify:
    """A command to run, and the file that says so."""

    command: str
    source_file: str
    """Relative to the workspace. Goes into the receipt as ``inferred:<source_file>``, so the claim
    can be checked by opening the file it came from."""


def _package_manager(workspace: Path) -> str:
    """Which runner this project locks to. The lockfile is the fact; the field order is a tiebreak."""
    if (workspace / "pnpm-lock.yaml").is_file():
        return "pnpm"
    if (workspace / "yarn.lock").is_file():
        return "yarn"
    return "npm"


def _from_package_json(workspace: Path) -> InferredVerify | None:
    path = workspace / "package.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    scripts = data.get("scripts") if isinstance(data, dict) else None
    script = (scripts or {}).get("test") if isinstance(scripts, dict) else None
    if not isinstance(script, str) or not script.strip():
        return None
    if script.strip() == _NPM_SCAFFOLD:
        return None
    return InferredVerify(f"{_package_manager(workspace)} test", "package.json")


def _from_makefile(workspace: Path) -> InferredVerify | None:
    for name in ("Makefile", "makefile", "GNUmakefile"):
        path = workspace / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # A target at the start of a line, which is what make itself requires. A `test` appearing in
        # a recipe body or a variable is not a target and would produce a command that does nothing.
        for line in text.splitlines():
            if line.startswith("test:") or line.startswith("test :"):
                return InferredVerify("make test", name)
    return None


def _from_python(workspace: Path) -> InferredVerify | None:
    if (workspace / "pytest.ini").is_file():
        return InferredVerify("python -m pytest -q", "pytest.ini")
    pyproject = workspace / "pyproject.toml"
    if pyproject.is_file():
        try:
            if "[tool.pytest.ini_options]" in pyproject.read_text(encoding="utf-8", errors="replace"):
                return InferredVerify("python -m pytest -q", "pyproject.toml")
        except OSError:
            pass
    setup_cfg = workspace / "setup.cfg"
    if setup_cfg.is_file():
        try:
            if "[tool:pytest]" in setup_cfg.read_text(encoding="utf-8", errors="replace"):
                return InferredVerify("python -m pytest -q", "setup.cfg")
        except OSError:
            pass
    tests = workspace / "tests"
    if tests.is_dir():
        try:
            if any(tests.glob("test_*.py")) or any(tests.glob("*_test.py")):
                return InferredVerify("python -m pytest -q", "tests/")
        except OSError:
            pass
    return None


def _from_rust_or_go(workspace: Path) -> InferredVerify | None:
    if (workspace / "Cargo.toml").is_file():
        return InferredVerify("cargo test", "Cargo.toml")
    if (workspace / "go.mod").is_file():
        return InferredVerify("go test ./...", "go.mod")
    return None


def infer_verify(workspace: Path) -> InferredVerify | None:
    """The project's own test command, or ``None`` when there is nothing to find.

    Ordered by how specific the evidence is, not by language popularity: a ``package.json`` that
    names a test script is a statement about this project, while a bare ``tests/`` directory is an
    observation about its layout. The first rule that fires on a real file wins.
    """
    workspace = Path(workspace)
    for rule in (_from_package_json, _from_makefile, _from_python, _from_rust_or_go):
        found = rule(workspace)
        if found is not None:
            return found
    return None
