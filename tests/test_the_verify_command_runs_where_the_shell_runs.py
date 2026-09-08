"""The verify command runs where the agent's own shell runs, not on a bare host.

`CommandVerifier.verify` ran its string with `subprocess.run(shell=True)` on the host — outside the
kernel sandbox, the taint ledger and the host-exec gate, which all wrap *tools*. The string is not
always typed by the person starting the run: `infer_verify` reads it out of the repository's own
`package.json` / `Makefile` / `pyproject.toml`, the cron dispatch reads it from `jobs.json`, a lane
from a kanban card, a workflow step from its YAML. `docs/audits/sleeper-channels.md` ranked that
the most exposed channel in the codebase: a shell string bound to a runtime event, the shape
arXiv 2609.03884 used against seven harnesses.

Now the verifier resolves the sandbox `run_shell` resolves and, when that sandbox is not isolated
and the string did not come from the user, consults the same host-exec confirmation. A declined
command is an **abstention** — the semantics this module already gives a program that is not
installed: we could not check it, we do not claim we did, the loop's other gates decide.

The fakes are the ones `tests/test_host_exec_confirm.py` uses for the shell tool, so the two gates
cannot drift: a sandbox that records whether it ran, a confirm that records whether it was asked.
The marker-file tests run a real interpreter through a real `LocalSandbox`, because "declined"
has to mean *nothing executed*, and only the filesystem can say that.
"""

from __future__ import annotations

import ast
import logging
import pathlib
import sys
from pathlib import Path

import pytest

from chimera.core.verify import VERIFY_SOURCES, CommandVerifier
from chimera.sandbox import DockerSandbox, LocalSandbox
from chimera.sandbox.base import SandboxResult

CHIMERA_DIR = pathlib.Path(__file__).resolve().parents[1] / "chimera"


def _py(code: str) -> str:
    """A shell command running ``code`` in this interpreter, quoted for cmd.exe AND sh."""
    return f'"{sys.executable}" -c "{code}"'


def _touch_marker(name: str) -> str:
    """A command whose only effect is a file — the proof of whether it ran."""
    return _py(f"import pathlib; pathlib.Path('{name}').write_text('ran')")


class _RecordingSandbox:
    """A sandbox that records whether it was asked to run, and answers with a chosen exit code."""

    def __init__(self, *, isolated: bool = False, exit_code: int = 0) -> None:
        self.ran: list[str] = []
        self._isolated = isolated
        self._exit_code = exit_code

    def is_isolated(self) -> bool:
        return self._isolated

    def run(self, command: str, *, timeout: int = 60, cwd: Path | None = None) -> SandboxResult:
        self.ran.append(command)
        return SandboxResult(exit_code=self._exit_code, stdout="ran")


class _Confirm:
    """A host-exec confirm that records what it was asked and answers as told."""

    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.asked: list[str] = []

    def __call__(self, command: str) -> bool:
        self.asked.append(command)
        return self.answer


# --- who typed it decides whether anyone is asked -------------------------------------------------


def test_a_command_the_user_typed_runs_on_the_host_without_asking(tmp_path: Path) -> None:
    """Authorised by construction: the person asked for the run and the check in one breath."""
    confirm = _Confirm(False)  # would refuse, if it were consulted
    result = CommandVerifier(
        _touch_marker("typed"), tmp_path, source="user", sandbox=LocalSandbox(), confirm=confirm
    ).verify()

    assert result.passed is True and result.abstained is False
    assert (tmp_path / "typed").exists(), "the typed command did not run"
    assert confirm.asked == [], "a typed command must not be prompted for"


def test_an_inferred_command_the_gate_declines_is_not_run_and_abstains(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The regression this file exists for: a string from the repository's own files, on a host
    with no isolated sandbox, refused by the gate — and NOTHING executed."""
    command = _touch_marker("inferred")
    confirm = _Confirm(False)
    with caplog.at_level(logging.WARNING):
        result = CommandVerifier(
            command, tmp_path, source="inferred", sandbox=LocalSandbox(), confirm=confirm
        ).verify()

    assert not (tmp_path / "inferred").exists(), "the declined command reached the host"
    assert confirm.asked == [command], "the gate must see the exact command"
    # An abstention, not a failure: the work is not punished for our refusal to check it.
    assert result.abstained is True and result.passed is True
    assert "inferred" in result.output and "CHIMERA_HOST_EXEC" in result.output
    # The decline is recorded — a log line, since no audit hook reaches the verifier.
    assert "verify command not run" in caplog.text and "inferred" in caplog.text


def test_an_inferred_command_the_gate_approves_runs(tmp_path: Path) -> None:
    """A gate that blocks the approved case is a regression, not a fix."""
    confirm = _Confirm(True)
    result = CommandVerifier(
        _touch_marker("approved"), tmp_path, source="inferred", sandbox=LocalSandbox(), confirm=confirm
    ).verify()

    assert (tmp_path / "approved").exists()
    assert result.passed is True and result.abstained is False
    assert len(confirm.asked) == 1


@pytest.mark.parametrize("source", sorted(VERIFY_SOURCES - {"user"}))
def test_every_source_but_the_user_is_gated_on_a_host_sandbox(tmp_path: Path, source: str) -> None:
    """One rule for the job, the card, the workflow, the spec runner, the crew, the bench and the
    inferred command: on a sandbox that does not isolate, the gate is asked, and no is no."""
    sandbox = _RecordingSandbox()
    confirm = _Confirm(False)
    result = CommandVerifier("pytest -q", tmp_path, source=source, sandbox=sandbox, confirm=confirm).verify()

    assert sandbox.ran == []
    assert result.abstained is True
    assert repr(source) in result.output, "the abstention must name the source"


def test_an_isolated_sandbox_never_consults_the_gate(tmp_path: Path) -> None:
    """The confirmation asks about running on the HOST. Inside a real container or kernel sandbox
    that question does not arise — exactly the rule `RunShellTool` follows."""
    sandbox = _RecordingSandbox(isolated=True)
    confirm = _Confirm(False)
    result = CommandVerifier("pytest -q", tmp_path, source="job", sandbox=sandbox, confirm=confirm).verify()

    assert sandbox.ran == ["pytest -q"]
    assert confirm.asked == []
    assert result.passed is True and result.abstained is False


@pytest.mark.parametrize(("posture", "ran"), [("allow", True), ("deny", False)])
def test_with_no_confirm_given_the_gate_is_resolved_from_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, posture: str, ran: bool
) -> None:
    """The production path: no caller hands the verifier a confirm, so it resolves one the way the
    shell tool's registry does — from ``CHIMERA_HOST_EXEC``. ``allow`` is no gate; ``deny`` refuses."""
    from chimera.config import get_settings

    monkeypatch.setenv("CHIMERA_HOST_EXEC", posture)
    get_settings.cache_clear()
    sandbox = _RecordingSandbox()
    result = CommandVerifier("pytest -q", tmp_path, source="card", sandbox=sandbox).verify()

    assert (sandbox.ran == ["pytest -q"]) is ran
    assert result.abstained is (not ran)


# --- the verdicts are unchanged on the way through the sandbox --------------------------------------


@pytest.mark.parametrize("code", [5, 127])
def test_no_verdict_exit_codes_still_abstain_through_the_sandbox(tmp_path: Path, code: int) -> None:
    """Exit 5 (pytest collected nothing) and 127 (no such command) reached no verdict before the
    sandbox and reach none after it."""
    sandbox = _RecordingSandbox(isolated=True, exit_code=code)
    result = CommandVerifier("pytest -q", tmp_path, source="user", sandbox=sandbox).verify()

    assert result.abstained is True and result.passed is True


def test_a_failure_is_still_a_failure_and_a_pass_still_a_pass(tmp_path: Path) -> None:
    # A command whose program EXISTS, so the Windows "is it even installed" abstention (which runs
    # after any non-zero exit) has nothing to say and the exit code alone decides.
    command = _py("pass")
    passed = CommandVerifier(
        command, tmp_path, source="user", sandbox=_RecordingSandbox(isolated=True, exit_code=0)
    ).verify()
    failed = CommandVerifier(
        command, tmp_path, source="user", sandbox=_RecordingSandbox(isolated=True, exit_code=1)
    ).verify()

    assert passed.passed is True and passed.abstained is False
    assert failed.passed is False and failed.abstained is False
    assert passed.output == "ran"  # the sandbox's output is the receipt's evidence, verbatim


def test_a_sandbox_timeout_is_reported_as_the_old_timeout_was(tmp_path: Path) -> None:
    class _Slow:
        def is_isolated(self) -> bool:
            return True

        def run(self, command: str, *, timeout: int = 60, cwd: Path | None = None) -> SandboxResult:
            return SandboxResult(exit_code=124, stderr="command timed out", timed_out=True)

    result = CommandVerifier("sleep 99", tmp_path, source="user", timeout=3, sandbox=_Slow()).verify()

    assert result.passed is False and result.abstained is False
    assert result.output == "verification timed out after 3s"


# --- the source is validated, and it is not optional ------------------------------------------------


def test_a_source_outside_the_allowed_set_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="verify source"):
        CommandVerifier("pytest -q", tmp_path, source="model")


def test_the_source_cannot_be_left_out(tmp_path: Path) -> None:
    """Keyword-only and required: a constructor that accepted silence would be one more site where
    a string from a file reaches a shell with nobody having said where it came from."""
    with pytest.raises(TypeError):
        CommandVerifier("pytest -q", tmp_path)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        CommandVerifier("pytest -q", tmp_path, 120, "user")  # type: ignore[misc]


def test_every_allowed_source_constructs(tmp_path: Path) -> None:
    for source in VERIFY_SOURCES:
        assert CommandVerifier("pytest -q", tmp_path, source=source).source == source


# --- CHIMERA_VERIFY_NETWORK -----------------------------------------------------------------------------


def _network_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.config import get_settings

    monkeypatch.setenv("CHIMERA_VERIFY_NETWORK", "1")
    get_settings.cache_clear()


def test_off_by_default_the_sandbox_is_used_as_resolved(tmp_path: Path) -> None:
    sandbox = _RecordingSandbox(isolated=True)
    assert CommandVerifier("x", tmp_path, source="job", sandbox=sandbox)._resolve_sandbox() is sandbox


def test_a_docker_verifier_is_given_the_bridge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Asserted on the rebuilt sandbox rather than on a container, for the reason `DockerSandbox`
    itself gives: CI has no daemon, and neither did the machine this was written on."""
    _network_on(monkeypatch)
    configured = DockerSandbox("img:1", memory="256m", cpus="1", pids_limit=64, runtime="runsc")
    resolved = CommandVerifier("x", tmp_path, source="job", sandbox=configured)._resolve_sandbox()

    assert isinstance(resolved, DockerSandbox)
    assert resolved.network is True
    assert (resolved.image, resolved.memory, resolved.cpus, resolved.pids_limit, resolved.runtime) == (
        "img:1", "256m", "1", 64, "runsc"
    )


def test_on_a_kernel_sandbox_only_the_user_may_fall_back_to_the_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """bubblewrap and Seatbelt cannot be asked for a network. The setting's help text says what
    happens next: a verifier that needs the network runs on the host, and only when you typed it."""
    _network_on(monkeypatch)
    kernel = _RecordingSandbox(isolated=True)

    typed = CommandVerifier("x", tmp_path, source="user", sandbox=kernel)._resolve_sandbox()
    assert isinstance(typed, LocalSandbox)

    inferred = CommandVerifier("x", tmp_path, source="inferred", sandbox=kernel).verify()
    assert inferred.abstained is True and inferred.passed is True
    assert "CHIMERA_VERIFY_NETWORK" in inferred.output and "inferred" in inferred.output
    assert kernel.ran == [], "the abstention must not run the command anyway"


def test_on_a_host_sandbox_the_setting_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The host has the network already; there is nothing to grant and nothing to refuse."""
    _network_on(monkeypatch)
    host = _RecordingSandbox()
    assert CommandVerifier("x", tmp_path, source="user", sandbox=host)._resolve_sandbox() is host


# --- the guard that keeps this true in a year -------------------------------------------------------


def _constructor_calls(tree: ast.AST) -> list[ast.Call]:
    """Every ``CommandVerifier(...)`` call in ``tree``, however the name was imported."""
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name == "CommandVerifier":
            calls.append(node)
    return calls


def _constructors_without_a_source() -> list[str]:
    """``file:line`` for every ``CommandVerifier(`` in ``chimera/`` that does not pass ``source=``."""
    offenders: list[str] = []
    for path in sorted(CHIMERA_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in _constructor_calls(tree):
            if not any(kw.arg == "source" for kw in call.keywords):
                offenders.append(f"{path.relative_to(CHIMERA_DIR.parent).as_posix()}:{call.lineno}")
    return offenders


def test_every_constructor_in_the_package_names_who_authored_the_command() -> None:
    """The structural half. A call site that leaves ``source`` out raises at runtime — but only when
    that path runs, and the cron, the card and the workflow paths run where nobody is watching."""
    missing = _constructors_without_a_source()

    assert not missing, (
        f"CommandVerifier built without source=: {missing} — say who authored the string "
        f"(one of {sorted(VERIFY_SOURCES)})"
    )


def test_the_constructor_guard_is_not_vacuous() -> None:
    """Proof the walk can still see an offender, over a fabricated call site with and without it."""
    without = ast.parse("v = CommandVerifier(cmd, ws)\nw = verify.CommandVerifier(cmd, ws, timeout=3)\n")
    with_it = ast.parse('v = CommandVerifier(cmd, ws, source="job")\n')

    assert len(_constructor_calls(without)) == 2
    assert all(not any(kw.arg == "source" for kw in c.keywords) for c in _constructor_calls(without))
    assert all(any(kw.arg == "source" for kw in c.keywords) for c in _constructor_calls(with_it))


def test_the_guard_covers_the_sites_the_audit_listed() -> None:
    """The audit named the constructors by file. The walk must at least see those, or a passing
    guard would be silence about files it never opened."""
    seen: set[str] = set()
    for path in sorted(CHIMERA_DIR.rglob("*.py")):
        if _constructor_calls(ast.parse(path.read_text(encoding="utf-8"))):
            seen.add(path.relative_to(CHIMERA_DIR.parent).as_posix())

    expected = {
        "chimera/api/app.py",
        "chimera/api/code_api.py",
        "chimera/cli/main.py",
        "chimera/core/spec_test.py",
        "chimera/eval/env.py",
        "chimera/kanban/lanes.py",
        "chimera/orchestration/crew.py",
        "chimera/orchestration/lifecycle.py",
        "chimera/scheduler/job_runner.py",
        "chimera/workflow/executors.py",
    }
    assert expected <= seen, f"constructor sites the walk did not see: {sorted(expected - seen)}"
