"""The shipped default used to be the host, and the only thing in front of it was a question.

`SECURITY.md` said it plainly: the `local` sandbox is not isolated. So the boundary between a
command the model chose and the machine was the governance kernel plus a confirmation prompt — and
a prompt is a boundary a tired person waves through and an injected instruction routes around.

Both comparable products enforce a *kernel* boundary per command, with the network off by default.
This is that boundary where the platform offers one, and an explicit, loud absence where it does
not.

**The rule the whole module is built on:** `is_isolated()` is what the host-exec gate reads, so it
may never be more optimistic than the enforcement. A machine that cannot sandbox keeps its prompt.
A machine that can may skip it. The claim and the enforcement move together or the claim is a lie.

⚠️ **Coverage stated rather than implied.** These tests run everywhere, but neither `bwrap` nor
`sandbox-exec` exists on the machine this was written on — no sudo to install one. So the argv and
the profile are asserted by **content**, not by execution, and the seam is exercised with a real
wrapper that does exist. What is NOT proven here: that bubblewrap and Seatbelt accept these exact
flags. The mitigation is in the code rather than in a promise — availability is probed with the
*same* argv builder the real command uses, so a rejected flag set reports unavailable instead of
reporting a sandbox that then fails every command.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

import chimera.sandbox as sandbox_pkg
from chimera.sandbox import LocalSandbox, get_sandbox
from chimera.sandbox.os_sandbox import (
    OsSandbox,
    _bwrap_argv,
    _seatbelt_profile,
    _writable_roots,
    bubblewrap_available,
    os_sandbox_available,
    seatbelt_available,
    unavailable_reason,
)


@pytest.fixture(autouse=True)
def _clean_probe_cache() -> Any:
    """The availability probes are cached for the process; a test that fakes one must not leak."""
    for fn in (seatbelt_available, bubblewrap_available):
        fn.cache_clear()
    sandbox_pkg._warned = False
    yield
    for fn in (seatbelt_available, bubblewrap_available):
        fn.cache_clear()
    sandbox_pkg._warned = False


class _Settings:
    def __init__(self, sandbox: str) -> None:
        self.sandbox = sandbox


# --------------------------------------------------------------------------- the claim


def test_an_unavailable_sandbox_never_claims_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    """The invariant the host-exec gate depends on. If this is ever True while the wrapper does not
    apply, the confirmation prompt disappears in front of a command running on the bare host."""
    monkeypatch.setattr("chimera.sandbox.os_sandbox.seatbelt_available", lambda: False)
    monkeypatch.setattr("chimera.sandbox.os_sandbox.bubblewrap_available", lambda: False)

    assert os_sandbox_available() is False
    assert OsSandbox.is_isolated() is False
    assert unavailable_reason() != ""


def test_an_available_sandbox_claims_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    """The control. A module that always answered False would pass the test above and be useless."""
    monkeypatch.setattr("chimera.sandbox.os_sandbox.bubblewrap_available", lambda: True)

    assert os_sandbox_available() is True
    assert OsSandbox.is_isolated() is True
    assert unavailable_reason() == ""


def test_the_reason_names_the_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """A refusal a person cannot act on is a refusal they will switch off. Each platform gets its
    own sentence with the next step in it."""
    monkeypatch.setattr("chimera.sandbox.os_sandbox.seatbelt_available", lambda: False)
    monkeypatch.setattr("chimera.sandbox.os_sandbox.bubblewrap_available", lambda: False)

    monkeypatch.setattr("chimera.sandbox.os_sandbox.platform.system", lambda: "Windows")
    assert "docker" in unavailable_reason().lower()

    monkeypatch.setattr("chimera.sandbox.os_sandbox.platform.system", lambda: "Linux")
    monkeypatch.setattr("chimera.sandbox.os_sandbox.shutil.which", lambda _: None)
    assert "bubblewrap" in unavailable_reason().lower()


# --------------------------------------------------------------------------- the factory


def test_auto_is_the_default_and_prefers_the_kernel(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("chimera.sandbox.os_sandbox.bubblewrap_available", lambda: True)
    assert isinstance(get_sandbox(_Settings("auto")), OsSandbox)  # type: ignore[arg-type]


def test_the_shipped_default_is_auto_not_the_host() -> None:
    """The whole point of this change is the value a user gets without choosing anything. Every
    test above passes `auto` explicitly, so none of them would notice the default going back."""
    from chimera.config import Settings

    assert Settings.model_fields["sandbox"].default == "auto"


def test_auto_falls_back_to_the_host_and_says_so(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Falling back is allowed. Falling back quietly is what this replaces."""
    monkeypatch.setattr("chimera.sandbox.os_sandbox.seatbelt_available", lambda: False)
    monkeypatch.setattr("chimera.sandbox.os_sandbox.bubblewrap_available", lambda: False)

    with caplog.at_level("WARNING"):
        chosen = get_sandbox(_Settings("auto"))  # type: ignore[arg-type]

    assert type(chosen) is LocalSandbox
    assert chosen.is_isolated() is False
    assert "WITHOUT an OS sandbox" in caplog.text


def test_the_warning_is_said_once_not_once_per_command(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr("chimera.sandbox.os_sandbox.seatbelt_available", lambda: False)
    monkeypatch.setattr("chimera.sandbox.os_sandbox.bubblewrap_available", lambda: False)

    with caplog.at_level("WARNING"):
        for _ in range(5):
            get_sandbox(_Settings("auto"))  # type: ignore[arg-type]

    assert caplog.text.count("WITHOUT an OS sandbox") == 1


def test_local_is_still_reachable_deliberately(monkeypatch: pytest.MonkeyPatch) -> None:
    """Someone who wants the host must still be able to say so — and get no warning for choosing
    it on purpose, which is different from being given it by default."""
    monkeypatch.setattr("chimera.sandbox.os_sandbox.bubblewrap_available", lambda: True)
    assert type(get_sandbox(_Settings("local"))) is LocalSandbox  # type: ignore[arg-type]


# --------------------------------------------------------------------------- what gets built


def test_bubblewrap_argv_carries_every_isolation_flag() -> None:
    """Asserted by content because this machine has no bwrap to run it against — and named as such
    in the module docstring. Each flag below is load-bearing: without `--unshare-net` the sandbox
    still reaches the network, without `--cap-drop ALL` it keeps capabilities, and `--ro-bind / /`
    before the writable binds is what makes the rest of the disk read-only."""
    ws = Path.cwd()
    argv = _bwrap_argv([ws], ws)
    joined = " ".join(argv)

    assert argv[0] == "bwrap"
    assert "--ro-bind / /" in joined
    assert f"--bind {ws} {ws}" in joined
    for flag in ("--unshare-net", "--unshare-pid", "--unshare-ipc", "--die-with-parent"):
        assert flag in argv, flag
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    # The read-only bind must come before the writable one, or it would mount over it.
    assert joined.index("--ro-bind / /") < joined.index(f"--bind {ws} {ws}")


def test_the_seatbelt_profile_is_closed_by_default() -> None:
    """`(deny default)` is what makes the omissions safe: the network is denied because nothing
    allows it, so a rule that fails to render cannot open it by accident."""
    ws = Path.cwd()
    profile = _seatbelt_profile([ws])

    assert "(deny default)" in profile
    assert f'(allow file-write* (subpath "{ws.as_posix()}"))' in profile
    assert "network" not in profile, "a network allowance appeared in a profile that must have none"


def test_writes_are_confined_to_the_workspace_and_temp() -> None:
    """Temp is deliberate, not a leak: compilers, package managers and test runners all write
    there, and a sandbox that forbids it is a sandbox people switch off."""
    ws = Path.cwd()
    roots = _writable_roots(ws)

    assert ws.resolve() in roots
    assert len(roots) >= 1
    assert all(r.is_dir() for r in roots)


# --------------------------------------------------------------------------- the seam


def test_the_wrapper_actually_reaches_the_process(tmp_path: Path) -> None:
    """Mechanism to wiring, and the half that would rot silently: every assertion above is about a
    string that no one runs unless `LocalSandbox.run` consults the hook. Exercised with a wrapper
    that exists on every platform — the interpreter running these tests — so this is a real
    execution, not a mock."""

    class _WrappedSandbox(OsSandbox):
        def _command_argv(self, command: str, cwd: Path | None) -> tuple[list[str] | str, bool]:
            return ([sys.executable, "-c", "print('through-the-wrapper')"], False)

    result = _WrappedSandbox().run("echo this-string-must-not-appear", cwd=tmp_path)

    assert result.exit_code == 0
    assert "through-the-wrapper" in result.stdout
    assert "this-string-must-not-appear" not in result.output


def test_streaming_never_steps_around_the_wrapper(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The most dangerous line in this change, pinned.

    `run_streamed` spawns its OWN process to deliver output line by line. That is equivalent to the
    configured sandbox only when the sandbox adds nothing to a plain host process. `OsSandbox`
    subclasses `LocalSandbox` to reuse its process handling, so an `isinstance` check there would
    stream — running the command outside the kernel wrapper the user is relying on, silently, while
    every screen still says "sandboxed".
    """
    from chimera.api.exec_stream import run_streamed
    from chimera.config import Settings

    monkeypatch.setattr("chimera.sandbox.os_sandbox.bubblewrap_available", lambda: True)
    # The wrapper is faked to something harmless that exists, so the branch is exercised for real.
    monkeypatch.setattr(
        OsSandbox,
        "_command_argv",
        lambda self, command, cwd: ([sys.executable, "-c", "print('via-the-backend')"], False),
    )
    settings = Settings(CHIMERA_HOME=str(tmp_path), CHIMERA_SANDBOX="auto")  # type: ignore[call-arg]

    lines: list[str] = []
    code = run_streamed(
        "echo must-not-be-streamed-directly",
        workspace=tmp_path,
        cwd=None,
        timeout=30,
        on_line=lines.append,
        settings=settings,
    )
    joined = "\n".join(lines)

    assert code == 0
    assert "via-the-backend" in joined, "the sandbox backend was bypassed"
    assert "must-not-be-streamed-directly" not in joined
    assert "output shown on completion" in joined


def test_streaming_still_streams_on_an_unsandboxed_host(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The control, and the regression this change caused once already: flipping the default to
    `auto` sent every machine down the one-shot path and cost live output and Stop."""
    from chimera.api.exec_stream import run_streamed
    from chimera.config import Settings

    monkeypatch.setattr("chimera.sandbox.os_sandbox.seatbelt_available", lambda: False)
    monkeypatch.setattr("chimera.sandbox.os_sandbox.bubblewrap_available", lambda: False)
    settings = Settings(CHIMERA_HOME=str(tmp_path), CHIMERA_SANDBOX="auto")  # type: ignore[call-arg]

    lines: list[str] = []
    code = run_streamed(
        f'{sys.executable} -c "print(1); print(2)"',
        workspace=tmp_path,
        cwd=None,
        timeout=30,
        on_line=lines.append,
        settings=settings,
    )

    assert code == 0
    assert "output shown on completion" not in "\n".join(lines)
    assert any("1" in ln for ln in lines)


def test_the_plain_sandbox_still_runs_the_command_as_typed(tmp_path: Path) -> None:
    """The control for the seam. Adding the hook must not change what `local` does."""
    result = LocalSandbox().run("echo hello-from-the-host", cwd=tmp_path)

    assert result.exit_code == 0
    assert "hello-from-the-host" in result.stdout


def test_an_unsandboxable_host_runs_the_command_gated_rather_than_wrapped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The pair that must hold together: no wrapper AND no claim. Wrapping with something that is
    not there would break every command; claiming isolation without it would remove the prompt."""
    monkeypatch.setattr("chimera.sandbox.os_sandbox.seatbelt_available", lambda: False)
    monkeypatch.setattr("chimera.sandbox.os_sandbox.bubblewrap_available", lambda: False)

    target, use_shell = OsSandbox()._command_argv("echo x", tmp_path)

    assert (target, use_shell) == ("echo x", True)
    assert OsSandbox.is_isolated() is False
