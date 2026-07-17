"""Tests for the verify-or-revert authority (:mod:`chimera.core.verify`).

A ``Verifier`` decides whether a change is KEPT or rolled back, so every exit it has needs pinning
down — pass, fail, timed out, could-not-run. The dangerous direction is a FALSE PASS: if a
verification that timed out (or never ran at all) reported ``passed=True``, the agent would keep a
change that nothing ever judged, and the receipt would claim evidence that does not exist.

These tests drive real subprocesses. Commands are built with :func:`_py` so they behave identically
under ``cmd.exe`` (Windows) and ``/bin/sh`` (Linux/macOS/CI) — the payloads deliberately avoid shell
metacharacters and use single quotes only.
"""

from __future__ import annotations

import sys
from pathlib import Path

from chimera.core.verify import CommandVerifier, NullVerifier, VerificationResult


def _py(code: str) -> str:
    """A shell command running ``code`` in this interpreter, quoted for cmd.exe AND sh."""
    return f'"{sys.executable}" -c "{code}"'


# --- construction -------------------------------------------------------------------------


def test_command_verifier_stores_command_workspace_and_default_timeout(tmp_path: Path) -> None:
    v = CommandVerifier("pytest -q", tmp_path)
    assert v.command == "pytest -q"
    assert v.workspace == tmp_path
    assert v.timeout == 120  # the documented default


def test_command_verifier_timeout_is_overridable(tmp_path: Path) -> None:
    assert CommandVerifier("exit 0", tmp_path, timeout=7).timeout == 7


def test_workspace_is_coerced_to_a_path(tmp_path: Path) -> None:
    v = CommandVerifier("exit 0", str(tmp_path))  # a str must become a real Path
    assert isinstance(v.workspace, Path)
    assert v.workspace == tmp_path


# --- the pass/fail decision ---------------------------------------------------------------


def test_exit_zero_passes_and_non_zero_fails(tmp_path: Path) -> None:
    assert CommandVerifier("exit 0", tmp_path).verify().passed is True
    assert CommandVerifier("exit 1", tmp_path).verify().passed is False


def test_verify_runs_the_command_inside_the_workspace(tmp_path: Path) -> None:
    """The command must run with ``cwd=workspace``: a marker file there is only visible from there."""
    (tmp_path / "marker.txt").write_text("hi", encoding="utf-8")
    code = "import pathlib, sys; sys.exit(0 if pathlib.Path('marker.txt').exists() else 1)"
    assert CommandVerifier(_py(code), tmp_path).verify().passed is True
    # ...and the same command fails from a directory that has no marker (proving cwd is honoured).
    other = tmp_path / "elsewhere"
    other.mkdir()
    assert CommandVerifier(_py(code), other).verify().passed is False


# --- the captured output ------------------------------------------------------------------


def test_verify_captures_both_stdout_and_stderr(tmp_path: Path) -> None:
    """The verifier's output is the concrete evidence the receipt shows — both streams must land."""
    code = "import sys; sys.stdout.write('OUT'); sys.stderr.write('ERR')"
    result = CommandVerifier(_py(code), tmp_path).verify()
    assert result.passed is True
    assert "OUT" in result.output
    assert "ERR" in result.output


def test_verify_output_is_empty_when_the_command_prints_nothing(tmp_path: Path) -> None:
    result = CommandVerifier(_py("pass"), tmp_path).verify()
    assert result.passed is True
    assert result.output == ""  # a silent command reports "", never invented filler


# --- the two "could not judge it" exits: both must read as a FAILURE ----------------------


def test_a_timed_out_verification_fails_and_says_so(tmp_path: Path) -> None:
    """A verification that never finished must FAIL. Reporting a pass here would let the agent keep
    a change that no test ever judged — the exact shape of a false proof."""
    result = CommandVerifier(_py("import time; time.sleep(10)"), tmp_path, timeout=1).verify()
    assert result.passed is False
    assert "verification timed out after 1s" in result.output  # the real timeout, not a constant


def test_a_verification_that_cannot_run_fails_and_says_so(tmp_path: Path) -> None:
    """A missing workspace (cwd removed mid-run, binary absent) is an UNVERIFIABLE attempt, which is
    reported as a failure rather than propagating and aborting the whole run."""
    result = CommandVerifier(_py("pass"), tmp_path / "does-not-exist").verify()
    assert result.passed is False
    assert "verification could not run" in result.output


# --- NullVerifier + the result shape ------------------------------------------------------


def test_null_verifier_passes_with_an_explicit_message() -> None:
    result = NullVerifier().verify()
    assert result.passed is True
    assert result.output == "no verification configured"
    assert result.abstained is False


def test_verification_result_defaults_are_not_an_abstention() -> None:
    # `abstained` must be opt-in: a plain result is real evidence, not an "I had nothing to run".
    result = VerificationResult(True)
    assert result.output == ""
    assert result.abstained is False
