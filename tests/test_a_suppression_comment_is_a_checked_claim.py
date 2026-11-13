"""`# noqa` and `# type: ignore` are claims; these settings are what make the tools check them.

At the audit (2026-11) the repository carried 643 `# noqa` naming a rule that was not enabled and
about two dozen `# type: ignore` that silenced nothing, which read as reviewed exceptions and were
not. The scan itself runs in CI (`ruff check .`, `mypy chimera`); this keeps the two settings from
being switched off again, which would make that scan pass for the wrong reason.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

PYPROJECT = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))


def test_ruff_reports_a_noqa_that_silences_nothing() -> None:
    assert "RUF100" in PYPROJECT["tool"]["ruff"]["lint"]["select"]


def test_mypy_reports_an_ignore_that_silences_nothing() -> None:
    assert PYPROJECT["tool"]["mypy"]["warn_unused_ignores"] is True


def test_mypy_checks_for_the_platform_ci_checks_for() -> None:
    """Otherwise the `ctypes.windll` ignores are "unused" on a Windows machine and "needed" on CI."""
    assert PYPROJECT["tool"]["mypy"]["platform"] == "linux"
