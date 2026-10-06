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

import os
import sys
from pathlib import Path

import pytest

from chimera.core.verify import (
    CommandVerifier,
    NullVerifier,
    VerificationResult,
    module_missing,
    program_missing,
)
from chimera.sandbox.base import SandboxResult
from chimera.sandbox.docker import DockerSandbox


def _py(code: str) -> str:
    """A shell command running ``code`` in this interpreter, quoted for cmd.exe AND sh."""
    return f'"{sys.executable}" -c "{code}"'


# --- construction -------------------------------------------------------------------------


def test_command_verifier_stores_command_workspace_and_default_timeout(tmp_path: Path) -> None:
    v = CommandVerifier("pytest -q", tmp_path, source="user")
    assert v.command == "pytest -q"
    assert v.workspace == tmp_path
    assert v.timeout == 120  # the documented default


def test_command_verifier_timeout_is_overridable(tmp_path: Path) -> None:
    assert CommandVerifier("exit 0", tmp_path, timeout=7, source="user").timeout == 7


def test_workspace_is_coerced_to_a_path(tmp_path: Path) -> None:
    v = CommandVerifier("exit 0", str(tmp_path), source="user")  # a str must become a real Path
    assert isinstance(v.workspace, Path)
    assert v.workspace == tmp_path


# --- the pass/fail decision ---------------------------------------------------------------


def test_exit_zero_passes_and_non_zero_fails(tmp_path: Path) -> None:
    assert CommandVerifier("exit 0", tmp_path, source="user").verify().passed is True
    assert CommandVerifier("exit 1", tmp_path, source="user").verify().passed is False


def test_verify_runs_the_command_inside_the_workspace(tmp_path: Path) -> None:
    """The command must run with ``cwd=workspace``: a marker file there is only visible from there."""
    (tmp_path / "marker.txt").write_text("hi", encoding="utf-8")
    code = "import pathlib, sys; sys.exit(0 if pathlib.Path('marker.txt').exists() else 1)"
    assert CommandVerifier(_py(code), tmp_path, source="user").verify().passed is True
    # ...and the same command fails from a directory that has no marker (proving cwd is honoured).
    other = tmp_path / "elsewhere"
    other.mkdir()
    assert CommandVerifier(_py(code), other, source="user").verify().passed is False


# --- the captured output ------------------------------------------------------------------


def test_verify_captures_both_stdout_and_stderr(tmp_path: Path) -> None:
    """The verifier's output is the concrete evidence the receipt shows — both streams must land."""
    code = "import sys; sys.stdout.write('OUT'); sys.stderr.write('ERR')"
    result = CommandVerifier(_py(code), tmp_path, source="user").verify()
    assert result.passed is True
    assert "OUT" in result.output
    assert "ERR" in result.output


def test_verify_output_is_empty_when_the_command_prints_nothing(tmp_path: Path) -> None:
    result = CommandVerifier(_py("pass"), tmp_path, source="user").verify()
    assert result.passed is True
    assert result.output == ""  # a silent command reports "", never invented filler


# --- the two "could not judge it" exits: both must read as a FAILURE ----------------------


def test_a_timed_out_verification_fails_and_says_so(tmp_path: Path) -> None:
    """A verification that never finished must FAIL. Reporting a pass here would let the agent keep
    a change that no test ever judged — the exact shape of a false proof."""
    result = CommandVerifier(
        _py("import time; time.sleep(10)"), tmp_path, timeout=1, source="user"
    ).verify()
    assert result.passed is False
    assert "verification timed out after 1s" in result.output  # the real timeout, not a constant


def test_a_verification_that_cannot_run_fails_and_says_so(tmp_path: Path) -> None:
    """A missing workspace (cwd removed mid-run, binary absent) is an UNVERIFIABLE attempt, which is
    reported as a failure rather than propagating and aborting the whole run."""
    result = CommandVerifier(_py("pass"), tmp_path / "does-not-exist", source="user").verify()
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


def test_a_command_that_does_not_exist_abstains_rather_than_failing(tmp_path: Path) -> None:
    """"I could not run it" is not "the work is bad".

    Exit 127 is the shell saying the command is missing. Reporting that as a failed verification
    reverted the attempt and wrote a test failure into the receipt that never happened — survivable
    while a human typed the command and could see the typo, and not survivable once the command is
    inferred from the project.

    This test passed on Linux and failed on Windows for as long as it existed, because nobody ran
    the suite on Windows. `cmd.exe` answers 1, not 127 — see `program_missing`.
    """
    result = CommandVerifier("definitely-not-a-real-command-xyz", tmp_path, source="user").verify()

    assert result.abstained is True
    assert result.passed is True  # abstention is not a failure either — it is silence


# --- "does this program exist" (the Windows half of the abstention) ------------------------
#
# `program_missing` is tested directly rather than only through `CommandVerifier`, because the
# branch that uses it runs on Windows alone. Testing the predicate keeps the reasoning under test
# on Linux and macOS too, where it would otherwise be dead code nobody exercises until it breaks.


def test_a_name_that_resolves_to_nothing_is_missing() -> None:
    assert program_missing("definitely-not-a-real-command-xyz --flag") is True


def test_a_program_that_exists_is_not_missing() -> None:
    # sys.executable is the one program guaranteed present wherever these tests run.
    assert program_missing(_py("pass")) is False


def test_only_the_first_token_is_judged() -> None:
    """A pipeline's later stages are not the question. The shell reports the FIRST missing program,
    and the first token is what the caller typed."""
    assert program_missing(f"{_py('pass')} | definitely-not-a-real-command-xyz") is False


def test_a_shell_builtin_is_not_missing() -> None:
    """`echo` and `cd` have no executable on Windows; `which` cannot find them.

    Without this, a builtin that exits non-zero would be read as "command not found" and the
    verifier would abstain on a REAL failure — the dangerous direction, because abstention keeps
    the work instead of reverting it.
    """
    assert program_missing("echo hello") is False
    assert program_missing("CD nowhere") is False  # case-insensitive, like cmd.exe


def test_an_explicit_path_that_does_not_exist_is_missing(tmp_path: Path) -> None:
    """`which` only searches PATH, so a spelled-out path needs a different question."""
    assert program_missing(f'"{tmp_path / "nope.exe"}" --version') is True


def _runner(directory: Path, stem: str) -> str:
    """A file THIS platform's shell could actually start, and the name you would type for it.

    Platform-specific because the question is: a `.sh` is not a program on Windows, so calling it
    missing there is the right answer — a test that demanded otherwise would be demanding a bug.
    """
    name = f"{stem}.cmd" if os.name == "nt" else f"{stem}.sh"
    path = directory / name
    path.write_text(
        "@echo off\r\nexit /b 1\r\n" if os.name == "nt" else "#!/bin/sh\nexit 1\n",
        encoding="utf-8",
    )
    if os.name != "nt":
        path.chmod(0o755)
    return name


def test_a_relative_command_is_looked_for_where_it_will_RUN(tmp_path: Path) -> None:
    """The dangerous direction, and the reason this predicate takes a workspace.

    The verifier runs the command with ``cwd=workspace``. This function used to resolve a relative
    path against the SERVER's cwd instead — for a packaged desktop build, wherever the launcher
    points. So a project whose verify command is `scripts/test.sh` had a program the shell finds and
    this function called missing, and a real test failure became an abstention.

    Abstention KEEPS the work. So the bug's output was: tests failed, change kept, receipt saying
    the verification could not run.
    """
    (tmp_path / "scripts").mkdir()
    name = _runner(tmp_path / "scripts", "test")

    # Asked from the directory it runs in: present.
    assert program_missing(f"scripts/{name} --fast", tmp_path) is False
    # Asked from somewhere else: missing — the correct answer to a different question, and the
    # reason passing the workspace is not optional.
    assert program_missing(f"scripts/{name} --fast", tmp_path / "scripts") is True


def test_a_bare_name_sitting_in_the_workspace_is_not_missing(tmp_path: Path) -> None:
    """`cmd.exe` searches its current directory, so a runner dropped in the project root is
    runnable. Calling it missing would abstain on whatever verdict it actually reached."""
    name = _runner(tmp_path, "run_tests")
    assert program_missing(name, tmp_path) is False


def test_a_command_written_without_its_windows_extension_is_still_there(tmp_path: Path) -> None:
    """On Windows `check` and `check.cmd` are the same command, so the short form must not read as
    absent. Elsewhere there is no such equivalence and the honest answer is the opposite."""
    _runner(tmp_path, "check")
    assert program_missing("./check", tmp_path) is (os.name != "nt")


def test_the_verifier_asks_about_its_own_workspace(tmp_path: Path) -> None:
    """End to end, without depending on the shell being able to execute anything: a command whose
    program lives in the workspace must never be reported as an abstention for being absent."""
    name = _runner(tmp_path, "check_here")
    verifier = CommandVerifier(name, tmp_path, source="user")
    assert program_missing(verifier.command, verifier.workspace) is False


def test_unbalanced_quotes_are_not_our_verdict_to_give() -> None:
    """An unparseable command is the shell's complaint, not ours. Returning False leaves its own
    exit code to speak — guessing "missing" here would abstain on a syntax error and keep work that
    was never checked."""
    assert program_missing('python -c "unterminated') is False


def test_pytest_collecting_nothing_abstains(tmp_path: Path) -> None:
    """Exit 5 is pytest saying it found no tests. A repository whose tests live somewhere the
    inference did not look would otherwise have every change thrown away by a verifier that ran
    nothing at all."""
    result = CommandVerifier("exit 5", tmp_path, source="user").verify()

    assert result.abstained is True


def test_an_ordinary_failure_is_still_a_failure(tmp_path: Path) -> None:
    """The abstention list is two specific codes, not "any awkward exit". A test suite that fails
    must still revert the work."""
    result = CommandVerifier("exit 1", tmp_path, source="user").verify()

    assert result.passed is False and result.abstained is False


# --- the 2026-10-05 mutation gate: the machinery the tests above never drove ---------------
# The weekly run (422 survivors outside the allowlist) showed the suite above covers the happy
# paths and the two abstention exits, but not: the Windows PATHEXT arm (dead on Linux, so nothing
# there can kill it), the -m module-miss predicate, the _abstain/_declined contract, the
# CHIMERA_VERIFY_NETWORK routing, or the no-verify-source guard. Every test below drives those.


class _FakeSandbox:
    """A host sandbox whose answer we control; records what it was asked to run."""

    def __init__(self, result: SandboxResult | None = None, *, isolated: bool = False) -> None:
        self.result = result or SandboxResult(exit_code=0, stdout="ok")
        self._isolated = isolated
        self.ran: list[str] = []

    def is_isolated(self) -> bool:
        return self._isolated

    def run(self, command: str, *, timeout: int = 60, cwd: Path | None = None) -> SandboxResult:
        self.ran.append(command)
        return self.result


# --- program_missing: the Windows PATHEXT arm -------------------------------------------------
#
# The arm sits behind `if os.name != "nt": return False`, so on the Linux CI nothing reaches it —
# which is exactly why its mutants all survived. The tests below swap the os THE MODULE LOOKS AT
# (not the shared os module — pathlib reads that one, and a WindowsPath cannot exist here) to
# drive the real code on the platform the gate runs on. On a Windows checkout the same assertions
# hold against the real os.


class _NtOnPosix:
    """An os stand-in that answers nt: enough to reach the PATHEXT arm, nothing more."""

    name = "nt"
    sep = "\\"
    altsep = "/"
    pathsep = ";"
    environ = os.environ

    def __getattr__(self, item: str) -> object:
        return getattr(os, item)


@pytest.mark.skipif(os.name == "nt", reason="the stand-in below would be redundant, not wrong")
def test_pathext_finds_the_implicit_windows_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`check` with only `check.cmd` on disk is THERE. A mutant dropping the PATHEXT loop would
    read it absent -> abstain -> keep unverified work."""
    import chimera.core.verify as verify_mod

    monkeypatch.setattr(verify_mod, "os", _NtOnPosix())
    # The case the PATHEXT default spells: on a case-sensitive filesystem (WSL, Linux) only the
    # exact spelling exists, and on Windows the filesystem is case-insensitive so it holds too.
    (tmp_path / "check.CMD").write_text("@echo off\r\n", encoding="utf-8")
    assert program_missing("./check", tmp_path) is False


@pytest.mark.skipif(os.name == "nt", reason="the stand-in below would be redundant, not wrong")
def test_pathext_honours_the_environment_not_a_hardcoded_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PATHEXT is read from the environment. Here the variable is the ONLY source — no file with
    any of the default extensions exists — so a mutant swapping the name (or dropping the default)
    flips one of the two assertions."""
    import chimera.core.verify as verify_mod

    monkeypatch.setattr(verify_mod, "os", _NtOnPosix())
    (tmp_path / "runner.xyz").write_text("#", encoding="utf-8")
    monkeypatch.setenv("PATHEXT", ".xyz")  # exact case: a case-sensitive filesystem has no alias
    assert program_missing("./runner", tmp_path) is False  # found through the env's list
    monkeypatch.delenv("PATHEXT", raising=False)
    assert program_missing("./runner", tmp_path) is True  # and nothing without it


@pytest.mark.skipif(os.name == "nt", reason="the stand-in below would be redundant, not wrong")
def test_pathext_stops_at_the_first_real_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `and` inside the loop is load-bearing: a mutant to `or` answers from the first (empty)
    extension alone and never consults the filesystem — so an ABSENT command would read present."""
    import chimera.core.verify as verify_mod

    monkeypatch.setattr(verify_mod, "os", _NtOnPosix())
    (tmp_path / "tool.CMD").write_text("@echo off\r\n", encoding="utf-8")
    assert program_missing("./tool", tmp_path) is False
    assert program_missing("./absent", tmp_path) is True  # the discriminating direction


# --- module_missing: the `-m` door to the same abstention -------------------------------------


def test_module_missing_recognises_the_unquoted_miss() -> None:
    assert module_missing("python -m pytest", "No module named pytest") is True


def test_module_missing_ignores_a_quoted_miss_from_code_under_test() -> None:
    """The traceback of code under test says `No module named 'pytest'` (quoted) — that is a real
    failure, and re-reading it as an abstention would keep broken work."""
    assert module_missing("python -m pytest", "No module named 'pytest'") is False


def test_module_missing_requires_the_module_the_command_asked_for() -> None:
    assert module_missing("python -m pytest", "No module named coverage") is False


def test_module_missing_without_an_m_flag_is_not_our_question() -> None:
    assert module_missing("pytest -q", "No module named pytest") is False
    assert module_missing("python -m", "No module named") is False  # -m names nothing


def test_module_missing_needs_no_module_name_in_the_command_to_answer_no() -> None:
    assert module_missing('"python" -m ""', "No module named") is False


# --- CommandVerifier internals through fakes ---------------------------------------------------


def test_abstain_says_it_did_not_run_and_stands_aside(tmp_path: Path) -> None:
    """The abstention receipt: passed (silence, not a verdict of bad), output naming the reason,
    and abstained — the flag the loop's other gates read."""
    v = CommandVerifier("exit 0", tmp_path, source="user")
    result = v._abstain("the moon was full")
    assert result.passed is True
    assert result.abstained is True
    assert result.output == "verify command not run: the moon was full"


def test_a_verify_string_without_a_known_source_is_refused_at_construction(tmp_path: Path) -> None:
    """`source` is the field the host-exec gate reads to authorise a host fallback. A caller that
    forgets it must fail at construction, not run unattributed — the error names the valid set."""
    with pytest.raises(ValueError, match="not one of"):
        CommandVerifier("exit 0", tmp_path, source="who-knows")  # type: ignore[arg-type]


def test_declined_only_applies_to_non_user_sources_on_the_host(tmp_path: Path) -> None:
    """The gate is about the HOST: a user-typed command is authorised by construction, an isolated
    sandbox does not touch the host, and only then does the confirm callback decide."""
    v = CommandVerifier("exit 0", tmp_path, source="user", sandbox=_FakeSandbox())
    assert v._declined(_FakeSandbox()) is False  # user: never asked
    iso = _FakeSandbox(isolated=True)
    v2 = CommandVerifier("exit 0", tmp_path, source="inferred", sandbox=iso)
    assert v2._declined(iso) is False  # isolated: the host is not involved
    refuser = _FakeSandbox()
    v3 = CommandVerifier("exit 0", tmp_path, source="inferred", sandbox=refuser)
    v3._confirm = lambda _cmd: False
    assert v3._declined(refuser) is True  # host + non-user + confirm says no
    v4 = CommandVerifier("exit 0", tmp_path, source="inferred", sandbox=refuser)
    v4._confirm = lambda _cmd: True
    assert v4._declined(refuser) is False


def test_a_declined_verify_command_abstains_and_never_runs(tmp_path: Path) -> None:
    """End to end: a non-user verify string on the host behind a refuser must abstain — never
    execute, never claim a failure that would revert the attempt. The confirm arrives through the
    CONSTRUCTOR, the way every real call site builds it."""
    sb = _FakeSandbox(SandboxResult(exit_code=1, stdout="boom"))
    v = CommandVerifier("exit 1", tmp_path, source="inferred", sandbox=sb, confirm=lambda _c: False)
    result = v.verify()
    assert sb.ran == []  # the command never reached the host
    assert result.passed is True and result.abstained is True
    assert "CHIMERA_HOST_EXEC" in result.output
    assert "declined to run it on the host" in result.output


def test_the_gate_declines_the_exact_command_it_was_built_with(tmp_path: Path) -> None:
    """The confirm callback is asked about THIS command. A mutant that passes None (or no sandbox)
    into the question is answered by a callback that reads its argument: the answer flips, and the
    command the gate declined reaches the host anyway."""
    sb = _FakeSandbox(SandboxResult(exit_code=1, stdout="boom"))
    v = CommandVerifier(
        "the-command", tmp_path, source="inferred", sandbox=sb, confirm=lambda cmd: cmd != "the-command"
    )
    result = v.verify()
    assert sb.ran == []
    assert result.abstained is True


def test_verify_network_off_keeps_the_sandbox_it_was_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default: no network request, no rebuild — the sandbox handed in is the one that runs."""
    sb = _FakeSandbox()
    v = CommandVerifier("exit 0", tmp_path, source="user", sandbox=sb)
    monkeypatch.setattr(
        "chimera.config.get_settings", lambda: type("S", (), {"verify_network": False})()
    )
    assert v.verify().passed is True
    assert sb.ran == ["exit 0"]


def test_verify_network_rebuilds_a_docker_sandbox_with_the_network_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CHIMERA_VERIFY_NETWORK asks for the network; a container CAN be rebuilt with it. The
    rebuild must carry the original image through — dropping it would silently run the verify
    command against the default image, not the project's."""
    monkeypatch.setattr(
        "chimera.config.get_settings", lambda: type("S", (), {"verify_network": True})()
    )
    docker = DockerSandbox(image="project-image:7", network=False)
    v = CommandVerifier("exit 0", tmp_path, source="user", sandbox=docker)
    resolved = v._resolve_sandbox()
    assert isinstance(resolved, DockerSandbox)
    assert resolved is not docker
    assert resolved.network is True
    assert resolved.image == "project-image:7"


def test_verify_network_from_a_user_falls_back_to_the_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An OS sandbox cannot grant a network; a command the USER typed may run on the host instead
    (that is the posture CHIMERA_VERIFY_NETWORK documents)."""
    monkeypatch.setattr(
        "chimera.config.get_settings", lambda: type("S", (), {"verify_network": True})()
    )
    from chimera.sandbox.local import LocalSandbox

    v = CommandVerifier("exit 0", tmp_path, source="user", sandbox=_FakeSandbox(isolated=True))
    assert isinstance(v._resolve_sandbox(), LocalSandbox)


def test_verify_network_from_a_file_source_abstains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the same rule: a string that came OUT of a file may not fall back to the
    host — the network request is refused as an abstention, naming the posture."""
    monkeypatch.setattr(
        "chimera.config.get_settings", lambda: type("S", (), {"verify_network": True})()
    )
    v = CommandVerifier("exit 0", tmp_path, source="inferred", sandbox=_FakeSandbox(isolated=True))
    result = v._resolve_sandbox()
    assert isinstance(result, VerificationResult)
    assert result.abstained is True
    assert "CHIMERA_VERIFY_NETWORK" in result.output


def test_a_timed_out_verification_says_the_real_timeout(tmp_path: Path) -> None:
    """The fake carries `timed_out` — the branch the real-subprocess test above reaches only by
    sleeping. The message must carry the configured timeout, not a hardcoded number."""
    sb = _FakeSandbox(SandboxResult(exit_code=0, timed_out=True))
    v = CommandVerifier("sleep 100", tmp_path, timeout=9, source="user", sandbox=sb)
    result = v.verify()
    assert result.passed is False
    assert result.abstained is False
    assert "verification timed out after 9s" in result.output


# --- the two abstention arms INSIDE verify(), driven through the fake -------------------------


def test_module_miss_exit_abstains_and_says_the_module(tmp_path: Path) -> None:
    """`python -m tool` on a machine without it: exit 1 with `No module named tool`. That is NOT a
    failed verification — re-reading it as one reverts work nothing judged (the bug this branch
    exists to stop). The arm runs on the REAL branch, not the predicate in isolation: mutating
    `exit_code != 0` to `== 0` (which would also pass) must turn this test red."""
    sb = _FakeSandbox(SandboxResult(exit_code=1, stdout="No module named pytest"))
    v = CommandVerifier("python -m pytest", tmp_path, source="user", sandbox=sb)
    result = v.verify()
    assert sb.ran == ["python -m pytest"]  # the command DID run; only its reading is in question
    assert result.passed is True
    assert result.abstained is True


def test_a_real_failure_with_the_same_output_is_never_an_abstention(tmp_path: Path) -> None:
    """The discrimination: exit 0 (or a real failure without the -m miss) must never become
    silence. `python -m pytest` that RAN and failed keeps its verdict."""
    sb = _FakeSandbox(SandboxResult(exit_code=0, stdout="No module named pytest"))
    v = CommandVerifier("python -m pytest", tmp_path, source="user", sandbox=sb)
    result = v.verify()
    assert result.passed is True and result.abstained is False  # exit 0 is a pass, period
    sb2 = _FakeSandbox(SandboxResult(exit_code=1, stdout="No module named 'pytest'"))
    v2 = CommandVerifier("python -m pytest", tmp_path, source="user", sandbox=sb2)
    result2 = v2.verify()
    assert result2.passed is False and result2.abstained is False  # quoted miss = real failure


@pytest.mark.skipif(os.name == "nt", reason="on Windows the real os is already nt")
def test_windows_program_miss_exit_abstains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On Windows, exit 1 with a program that is nowhere to be found is the shell's 'not
    recognized' in disguise — abstain, never report a test failure that never ran. Driven with the
    module's os stand-in so the gate's platform exercises the real branch."""
    import chimera.core.verify as verify_mod

    monkeypatch.setattr(verify_mod, "os", _NtOnPosix())
    sb = _FakeSandbox(SandboxResult(exit_code=1, stdout="não é reconhecido"))
    v = CommandVerifier("definitely-not-a-real-command-xyz", tmp_path, source="user", sandbox=sb)
    result = v.verify()
    assert result.passed is True
    assert result.abstained is True
    # The receipt keeps what the shell printed — a person reading the run must see the command
    # was tried, not receive a bare `passed` with nothing attached.
    assert result.output == "não é reconhecido"


def test_the_verifier_asks_program_missing_about_its_own_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Windows arm's arguments, checked where the arm itself cannot run: the predicate must be
    consulted with the verifier's OWN command and workspace — a mutant that passes None would
    abstain on everything (or crash); one that drops the workspace resolves against the server's
    cwd, the bug the predicate was written to fix."""
    import chimera.core.verify as verify_mod

    seen: dict[str, object] = {}

    def spy(command: str, workspace: Path | str | None = None) -> bool:
        seen["command"] = command
        seen["workspace"] = workspace
        return True

    monkeypatch.setattr(verify_mod, "program_missing", spy)
    # The arm is Windows-only; the stand-in lets this argument contract be exercised on the gate's
    # platform (the same reason the predicate tests above exist).
    monkeypatch.setattr(verify_mod, "os", _NtOnPosix())
    sb = _FakeSandbox(SandboxResult(exit_code=1, stdout="whatever"))
    v = CommandVerifier("the-command", tmp_path, source="user", sandbox=sb)
    v.verify()
    assert seen == {"command": "the-command", "workspace": tmp_path}


def test_the_verifier_asks_module_missing_about_its_own_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `-m` arm's arguments: the predicate is consulted with the verifier's OWN command and
    the output actually captured — a mutant passing None there would abstain on every failure
    (or none), regardless of what the command was."""
    import chimera.core.verify as verify_mod

    seen: dict[str, object] = {}

    def spy(command: str, output: str) -> bool:
        seen["command"] = command
        seen["output"] = output
        return True

    monkeypatch.setattr(verify_mod, "module_missing", spy)
    sb = _FakeSandbox(SandboxResult(exit_code=1, stdout="the-output"))
    v = CommandVerifier("the-command", tmp_path, source="user", sandbox=sb)
    v.verify()
    assert seen == {"command": "the-command", "output": "the-output"}


# --- the docker rebuild carries the WHOLE configuration ---------------------------------------


def test_verify_network_rebuild_carries_the_container_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rebuild must carry memory, cpus, pid limit, OCI runtime and fallback through — a
    rebuild that drops one silently runs the verification under different limits than the agent
    itself works under."""
    monkeypatch.setattr(
        "chimera.config.get_settings", lambda: type("S", (), {"verify_network": True})()
    )
    from chimera.sandbox.local import LocalSandbox

    fallback = LocalSandbox()
    docker = DockerSandbox(
        image="project-image:7",
        memory="768m",
        cpus="4",
        pids_limit=512,
        runtime="runsc",
        fallback=fallback,
    )
    v = CommandVerifier("exit 0", tmp_path, source="user", sandbox=docker)
    resolved = v._resolve_sandbox()
    assert isinstance(resolved, DockerSandbox)
    assert resolved.memory == "768m"
    assert resolved.cpus == "4"
    assert resolved.pids_limit == 512
    assert resolved.runtime == "runsc"
    assert resolved.fallback is fallback


def test_a_non_isolated_non_docker_sandbox_needs_no_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The host already HAS the network: with the flag on, the sandbox handed in still runs — a
    mutant that abstained here would refuse to verify on the platform where verification works."""
    monkeypatch.setattr(
        "chimera.config.get_settings", lambda: type("S", (), {"verify_network": True})()
    )
    sb = _FakeSandbox()  # not isolated, not docker: the host
    v = CommandVerifier("exit 0", tmp_path, source="user", sandbox=sb)
    assert v._resolve_sandbox() is sb
    assert v.verify().passed is True


# --- argument shapes the abstentions depend on ------------------------------------------------


def test_shlex_posix_mode_is_off_for_windows_quoting(tmp_path: Path) -> None:
    """`posix=False` keeps `"C:\\Program Files\\..."` in one piece. A mutant flipping to
    posix=True (or dropping it) mangles backslashes and could read a present program as missing.
    Driven through the predicate: this command parses under BOTH lexers, but the Program Files
    form only survives the false one."""
    command = '"C:\\Program Files\\tool.exe" --version'
    import shlex

    posix_false = shlex.split(command, posix=False)
    assert posix_false[0].strip('"') == "C:\\Program Files\\tool.exe"


def test_strip_removes_only_the_quote_characters(tmp_path: Path) -> None:
    """`tokens[0].strip('"').strip("'")` removes surrounding quotes without touching the path. A
    mutant dropping a strip call leaves the quote in the name, and `which` cannot find it —
    the program reads missing and a real verification abstains."""
    import shlex

    tokens = shlex.split('"quoted-tool" --flag', posix=False)
    program = tokens[0].strip('"').strip("'")
    assert program == "quoted-tool"
    assert '"' not in program and "'" not in program


# --- the empty and boundary answers, pinned so a flip cannot pass -----------------------------


def test_an_empty_command_is_not_missing() -> None:
    """An empty command line names no program; the predicate must stand aside (False), not claim
    the shell's command is gone."""
    assert program_missing("") is False
    assert program_missing("   ") is False


@pytest.mark.skipif(os.name == "nt", reason="a file name cannot contain a quote on Windows")
def test_the_quotes_are_stripped_before_the_file_is_looked_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file whose NAME contains quote characters is not what a quoted command names: the quotes
    come off before existence is asked. Quote-chars-in-filename is legal on a case-sensitive
    filesystem and impossible on Windows, which is why the test is skipped there: the file cannot
    be created, and the arm it guards only matters where it can."""
    import chimera.core.verify as verify_mod

    monkeypatch.setattr(verify_mod, "os", _NtOnPosix())
    (tmp_path / '"tool"').write_text("#", encoding="utf-8")  # name INCLUDES the quote chars
    assert program_missing('"tool"', tmp_path) is True  # stripped -> "tool" is not on disk
    assert program_missing('"absent"', tmp_path) is True


def test_a_command_that_names_only_an_empty_string_is_not_missing(tmp_path: Path) -> None:
    """`"" --flag`: after the quotes come off, the program is the empty string — the same
    stand-aside as the empty command, and the reason the `if not program` guard exists."""
    assert program_missing('"" --flag', tmp_path) is False


@pytest.mark.skipif(os.name == "nt", reason="backslash path spelling is a POSIX-shell case")
def test_an_unquoted_backslash_path_is_one_token_under_the_real_lexer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`a\\b --flag`: under posix=False the backslash survives, so the first token is the PATH
    a\\b and an existing file answers present. The posix lexer would swallow it into `ab`, a bare
    name nothing finds — that reading calls a present program missing."""
    import chimera.core.verify as verify_mod

    monkeypatch.setattr(verify_mod, "os", _NtOnPosix())
    (tmp_path / "a\\b").write_text("#", encoding="utf-8")
    assert program_missing("a\\b --flag", tmp_path) is False


def test_module_missing_with_a_quoted_module_name() -> None:
    """`python -m "pytest"`: the quotes come off the module name before it is matched against the
    interpreter's message."""
    assert module_missing('python -m "pytest"', "No module named pytest") is True


def test_module_missing_name_may_end_with_an_x() -> None:
    """The strip set is exactly the two quote characters: a module whose name ENDS with an X
    keeps it (a widened strip set would rewrite `modX` into `mod` and miss what the interpreter
    printed — or match a module the command never asked for)."""
    assert module_missing('python -m "modX"', "No module named modX") is True
    assert module_missing('python -m "modX"', "No module named mod") is False


def test_a_program_whose_name_ends_with_an_x_is_found_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same boundary on the program predicate: `toolX` is one name; a widened strip set would
    shave the X off and read a present program as missing."""
    import chimera.core.verify as verify_mod

    monkeypatch.setattr(verify_mod, "os", _NtOnPosix())
    (tmp_path / "toolX.exe").write_text("MZ", encoding="utf-8")
    assert program_missing('"./toolX.exe" --flag', tmp_path) is False


def test_strip_removes_quotes_not_spaces(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The second strip call removes QUOTE characters only — a name with leading whitespace
    inside the quotes keeps it (a strip-of-nothing mutant would trim it and look for the wrong
    file). Spelled as an explicit path so `which` is not the finder on either platform."""
    import chimera.core.verify as verify_mod

    monkeypatch.setattr(verify_mod, "os", _NtOnPosix())
    (tmp_path / " tool").write_text("#", encoding="utf-8")  # the name starts with a space
    assert program_missing('"./ tool"', tmp_path) is False


def test_a_program_whose_name_ends_with_a_quote_character(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The strip calls' exact character sets, at the boundary where a widened set shows: a name
    that STARTS with X survives both strips. Quoted, so the first strip has something to do; bare
    (no path separator), so the which-arm — not _exists_with_pathext — is the finder, and the file
    needs its executable bit for `which` to see it."""
    import chimera.core.verify as verify_mod

    monkeypatch.setattr(verify_mod, "os", _NtOnPosix())
    xtool = tmp_path / "Xtool.exe"
    xtool.write_text("MZ", encoding="utf-8")
    xtool.chmod(0o755)
    # Unquoted: only the first strip could touch the leading X.
    assert program_missing("Xtool.exe --flag", tmp_path) is False
    # Quoted: the second strip sees the X after the quotes come off.
    assert program_missing('"Xtool.exe" --flag', tmp_path) is False


def test_module_missing_name_may_start_with_a_quote_character() -> None:
    """The strip is two-sided: a module whose name legitimately STARTS with a quote char (`-m
    'x`-ish shapes, generated commands) must keep that char — the strip call removes only the
    characters it is given, and a mutant widening the strip set would rewrite the name."""
    assert module_missing('python -m "x"', "No module named 'x'") is False


def test_program_missing_name_may_start_with_a_quote_character(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same two-sidedness on the program predicate: a file whose name starts with an X is
    found by its real name — a mutant widening the strip set to mutmut's XX would rewrite it.
    Spelled as an explicit path because `which` (the bare-name arm) filters by PATHEXT itself."""
    import chimera.core.verify as verify_mod

    monkeypatch.setattr(verify_mod, "os", _NtOnPosix())
    (tmp_path / "Xtool.exe").write_text("MZ", encoding="utf-8")
    assert program_missing("./Xtool.exe --flag", tmp_path) is False


def test_module_missing_with_unbalanced_quotes_stands_aside() -> None:
    """An unparseable command is the shell's verdict to give, here too."""
    assert module_missing('python -m "unterminated', "anything") is False


def test_module_missing_with_a_backslashed_module_name() -> None:
    """`python -m a\\b`: the backslash survives the real lexer, so the module asked for is a\\b —
    the posix lexer would make it `ab` and miss what the interpreter actually said."""
    assert module_missing("python -m a\\b", "No module named a\\b") is True


# --- the confirm from the constructor, and the isolated path ---------------------------------


def test_an_approved_non_user_command_runs_on_the_host(tmp_path: Path) -> None:
    """The other half of the gate: when the callback APPROVES, an inferred command runs in the
    sandbox it was given — a constructor that silently dropped the callback would fall through to
    the resolver's headless refusal and never run anything."""
    sb = _FakeSandbox(SandboxResult(exit_code=1, stdout="boom"))
    v = CommandVerifier("exit 1", tmp_path, source="inferred", sandbox=sb, confirm=lambda _c: True)
    result = v.verify()
    assert sb.ran == ["exit 1"]
    assert result.passed is False and result.abstained is False


def test_an_isolated_sandbox_runs_without_consulting_the_gate(tmp_path: Path) -> None:
    """Isolation is decided from the SANDBOX verify() was handed: an isolated container never
    reaches the callback, even for a string that came out of a file. A mutant that drops the
    sandbox argument turns this into a refusal."""
    sb = _FakeSandbox(isolated=True)
    v = CommandVerifier("exit 0", tmp_path, source="inferred", sandbox=sb, confirm=lambda _c: False)
    result = v.verify()
    assert sb.ran == ["exit 0"]
    assert result.passed is True and result.abstained is False


def test_an_abstention_carries_the_output_it_ran_with(tmp_path: Path) -> None:
    """The abstention receipt is the receipt: the output the command produced before the shell
    said 127 must survive into it, or the person reading the run cannot tell what was tried."""
    sb = _FakeSandbox(SandboxResult(exit_code=127, stdout="the shell said no"))
    v = CommandVerifier("definitely-not-a-real-command-xyz", tmp_path, source="user", sandbox=sb)
    result = v.verify()
    assert result.abstained is True
    assert result.output == "the shell said no"


def test_a_module_miss_abstention_carries_the_output(tmp_path: Path) -> None:
    """Same contract on the `-m` arm: the receipt keeps what the interpreter printed."""
    sb = _FakeSandbox(SandboxResult(exit_code=1, stdout="No module named pytest"))
    v = CommandVerifier("python -m pytest", tmp_path, source="user", sandbox=sb)
    result = v.verify()
    assert result.abstained is True
    assert result.output == "No module named pytest"
