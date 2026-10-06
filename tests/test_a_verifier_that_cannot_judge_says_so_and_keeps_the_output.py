"""The verifier's "could not judge" paths — exact results, and the Windows branch run on any OS.

`chimera/core/verify.py` is the pass/fail authority of verify-or-revert. The mutation gate (study 30,
S30-37) found 63 live mutants there. Two clusters mattered: every abstention returned its result
without anything checking that the command's OUTPUT survived into it (the receipt then shows an
abstention with no evidence of why), and the Windows-only branch — `cmd.exe` answers 1 for a missing
program, so only "does the program exist?" can tell a missing tool from a failed test — never ran
on the Linux CI at all. The module's `os` is replaced with a stand-in here, so that branch is
exercised by its decision logic on every platform.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import chimera.core.verify as verify_mod
from chimera.core.verify import (
    CommandVerifier,
    _exists_with_pathext,
    module_missing,
    program_missing,
)
from chimera.sandbox.base import SandboxResult


class _Answer:
    def __init__(self, exit_code: int, out: str = "the output") -> None:
        self.exit_code = exit_code
        self.out = out

    def is_isolated(self) -> bool:
        return True

    def run(self, command: str, *, timeout: int = 60, cwd: Path | None = None) -> SandboxResult:
        return SandboxResult(exit_code=self.exit_code, stdout=self.out)


def _fake_os(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    environ: dict[str, str] | None = None,
    *,
    sep: str = "/",
    altsep: str | None = None,
) -> None:
    monkeypatch.setattr(
        verify_mod, "os",
        SimpleNamespace(name=name, environ=environ or {}, pathsep=";", sep=sep, altsep=altsep),
    )


def _verify(tmp_path: Path, command: str, exit_code: int, out: str = "the output") -> object:
    return CommandVerifier(command, tmp_path, source="user", sandbox=_Answer(exit_code, out)).verify()


def test_an_exit_code_with_no_verdict_abstains_and_keeps_the_output(tmp_path: Path) -> None:
    got = _verify(tmp_path, "tool", 127)
    assert (got.passed, got.output, got.abstained) == (True, "the output", True)


def test_a_missing_module_abstains_and_keeps_the_output(tmp_path: Path) -> None:
    out = "No module named pytest\n"
    got = _verify(tmp_path, "python -m pytest", 1, out)
    assert (got.passed, got.output, got.abstained) == (True, out, True)


def test_on_windows_a_missing_program_that_exits_1_is_an_abstention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_os(monkeypatch, "nt")
    got = _verify(tmp_path, "no-such-tool-anywhere --run", 1)
    assert (got.passed, got.output, got.abstained) == (True, "the output", True)
    # A run that passed stays a pass, and a present program that failed stays a failure.
    passed = _verify(tmp_path, "no-such-tool-anywhere --run", 0)
    assert (passed.passed, passed.abstained) == (True, False)
    failed = _verify(tmp_path, f'"{sys.executable}" -c "raise SystemExit(1)"', 1)
    assert (failed.passed, failed.abstained) == (False, False)


def test_off_windows_exit_1_is_a_failure_even_if_the_program_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 127 is the POSIX "not found"; 1 is "the tests failed". Reading 1 as missing there would keep work
    # nothing verified.
    _fake_os(monkeypatch, "posix")
    got = _verify(tmp_path, "no-such-tool-anywhere --run", 1)
    assert (got.passed, got.abstained) == (False, False)


def test_pathext_is_only_consulted_on_windows_and_read_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "check.COM").write_text("")
    (tmp_path / "other.PS1").write_text("")
    _fake_os(monkeypatch, "posix")
    assert _exists_with_pathext(tmp_path / "check") is False
    _fake_os(monkeypatch, "nt")  # no PATHEXT in the environment: the documented default list
    assert _exists_with_pathext(tmp_path / "check") is True
    assert _exists_with_pathext(tmp_path / "absent") is False
    _fake_os(monkeypatch, "nt", {"PATHEXT": ".BAT;.PS1"})
    assert _exists_with_pathext(tmp_path / "other") is True
    assert _exists_with_pathext(tmp_path / "check") is False


def test_a_program_named_in_quotes_is_looked_up_without_them(tmp_path: Path) -> None:
    runner = tmp_path / "runX"
    runner.write_text("")
    assert program_missing("./runX", tmp_path) is False
    assert program_missing("'./runX'", tmp_path) is False
    assert program_missing(f'"{runner}" --flag', tmp_path) is False


@pytest.mark.parametrize(
    "command", ['"', 'tool "unclosed', '""', "''", "", "   "],
    ids=["lone-quote", "unclosed", "empty-double", "empty-single", "empty", "blank"],
)
def test_a_command_that_cannot_be_split_or_names_nothing_is_not_called_missing(command: str) -> None:
    assert program_missing(command) is False


def test_module_missing_reads_the_module_the_command_named() -> None:
    assert module_missing('python -m "pytest"', "No module named pytest\n") is True
    assert module_missing("python -m modX", "No module named modX\n") is True
    assert module_missing('python -m pytest "', "No module named pytest\n") is False
    assert module_missing('python -m ""', "No module named x\n") is False


def test_the_network_bridge_keeps_the_docker_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from chimera.config import get_settings
    from chimera.sandbox import DockerSandbox, LocalSandbox

    monkeypatch.setenv("CHIMERA_VERIFY_NETWORK", "1")
    get_settings.cache_clear()
    fallback = LocalSandbox()
    configured = DockerSandbox("img:1", fallback=fallback)
    resolved = CommandVerifier("x", tmp_path, source="job", sandbox=configured)._resolve_sandbox()
    assert isinstance(resolved, DockerSandbox) and resolved.fallback is fallback


def test_a_windows_path_is_split_the_windows_way(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # `cmd.exe` reads backslashes literally; a POSIX split would eat them and look for "subrun.cmd".
    target = tmp_path / "sub\\run.cmd"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("")
    (tmp_path / "sub").mkdir(exist_ok=True)
    (tmp_path / "sub" / "fwd.cmd").write_text("")
    _fake_os(monkeypatch, "nt", sep="\\", altsep="/")
    assert program_missing("sub\\run.cmd --x", tmp_path) is False
    assert program_missing("sub/fwd.cmd --x", tmp_path) is False  # the alternative separator counts too


def test_on_windows_the_program_is_looked_for_where_the_command_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "check.cmd").write_text("")
    # The process cwd (the repository, here) is not the workspace. No chdir: mutmut cannot run a
    # test that changes directory (see the deselect list in pyproject.toml).
    _fake_os(monkeypatch, "nt", altsep="/")
    got = _verify(tmp_path, "sub/check.cmd", 1)
    assert (got.passed, got.abstained) == (False, False)  # it exists there: a real failure
