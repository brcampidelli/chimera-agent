"""The installed app hung forever the first time the agent chose to run a command.

Not slow — stopped. No frame, no error, no timeout, and `POST /api/runs/{id}/cancel` answering
`{"ok": true}` indefinitely about a run that could not be stopped. Three threads of the shipped
build were parked in the same place:

    confirm    (click/termui.py)        <- reading stdin, blocked
    _prompt    (chimera/sandbox/confirm.py)
    run        (chimera/tools/shell.py)
    ...
    work       (chimera/api/app.py)

`resolve_host_exec_confirm` decided between prompting and refusing with
``sys.stdin.isatty()``. The desktop shell spawns the frozen backend with ``CREATE_NO_WINDOW``, and
Windows answers that by giving the child a console **with no window**: stdin is a character device,
`isatty()` says True, and there is nobody there. So the gate drew a confirmation prompt somewhere
no human could ever see and waited for an answer that could never come.

`chimera/api/code_api.py` had already written the correct invariant down — *"this surface has no
terminal, so its `ask` has always been a refusal"* — while the code went on inferring it from a file
descriptor and getting the opposite answer. A comment asserting what the code does not do.

Three things now hold, and each is tested below because each fails differently: the API declares
that no human is present instead of leaving it to be guessed; the desktop shell hands the backend a
stdin that tells the truth; and a prompt that is somehow still reached cannot block forever.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path

import pytest

import chimera.sandbox.confirm as confirm_mod
from chimera.sandbox.confirm import (
    _answer_or_refuse,
    _human_can_answer,
    declare_no_human_here,
    resolve_host_exec_confirm,
)


class _Settings:
    def __init__(self, sandbox: str = "local", host_exec: str = "ask") -> None:
        self.sandbox = sandbox
        self.host_exec = host_exec


class _FakeStdin:
    """A stdin that claims to be a terminal — exactly what the packaged sidecar sees."""

    def isatty(self) -> bool:
        return True


# --------------------------------------------------------------------------- the declaration


def test_a_declared_surface_refuses_even_when_stdin_claims_a_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The defect, reproduced and closed. `isatty()` is True here, as it is in the shipped app."""
    monkeypatch.setattr(confirm_mod.sys, "stdin", _FakeStdin())
    assert _human_can_answer() is True  # the control: without the declaration, it would prompt

    declare_no_human_here("test")
    assert _human_can_answer() is False
    gate = resolve_host_exec_confirm(_Settings("local", "ask"))
    assert gate is not confirm_mod._prompt
    assert gate is not None and gate("rm -rf /") is False


def test_a_real_terminal_still_gets_its_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    """The control. Someone running `chimera solve` in their own terminal is the case the gate was
    written for, and a fix that refuses everywhere would be a different bug, not a fix."""
    monkeypatch.setattr(confirm_mod.sys, "stdin", _FakeStdin())
    # By behaviour, not identity: the gate is wrapped now — a command that can be PROVED to change
    # nothing is approved without asking — so `is _prompt` would fail for a change that has nothing
    # to do with what this test is about. What it must still show is that a command needing a
    # decision reaches the terminal.
    perguntado: list[str] = []
    monkeypatch.setattr(confirm_mod, "_prompt", lambda cmd: perguntado.append(cmd) or True)
    gate = resolve_host_exec_confirm(_Settings("local", "ask"))

    assert gate is not None and gate("rm -rf /tmp/x") is True
    assert perguntado == ["rm -rf /tmp/x"]


def test_the_declaration_does_not_override_allow(monkeypatch: pytest.MonkeyPatch) -> None:
    """`allow` is an explicit choice by the person who installed this; a headless surface is not a
    reason to overrule it. Refusing here would break every unattended install that opted in."""
    monkeypatch.setattr(confirm_mod.sys, "stdin", _FakeStdin())
    declare_no_human_here("test")
    assert resolve_host_exec_confirm(_Settings("local", "allow")) is None


# --------------------------------------------------------------------------- the API declares it


def test_building_the_api_declares_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Mechanism to wiring — the half that actually shipped broken. Serving HTTP means the caller is
    a client, not somebody watching this process's terminal, and one declaration at the top of
    `build_api_app` is what keeps every surface under it from having to remember."""
    monkeypatch.setattr(confirm_mod.sys, "stdin", _FakeStdin())
    assert _human_can_answer() is True

    from chimera.api import build_api_app
    from chimera.config import Settings

    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))  # type: ignore[call-arg]
    build_api_app(lambda: None, settings=settings)  # type: ignore[arg-type,return-value]
    assert _human_can_answer() is False


# --------------------------------------------------------------------------- the prompt is bounded


def test_a_prompt_that_never_answers_refuses_instead_of_hanging() -> None:
    """The backstop, and the property that was missing: the old prompt could not fail, only stall.

    `except Exception` cannot catch this — blocking is not an exception. A read from a console with
    no window simply never returns.
    """
    started, release = threading.Event(), threading.Event()

    def _never_answers() -> bool:
        started.set()
        # Bounded, and released in the `finally` below. A literal `sleep(3600)` would model the
        # real hang more faithfully and leave an hour-long thread behind on every run — which is
        # exactly the kind of debris that turns a sabotage run into a stall instead of a failure.
        release.wait(30)
        return True

    original = confirm_mod.PROMPT_TIMEOUT_SECONDS
    confirm_mod.PROMPT_TIMEOUT_SECONDS = 0.2
    try:
        began = time.monotonic()
        assert _answer_or_refuse(_never_answers, "the prompt nobody could see") is False
        elapsed = time.monotonic() - began
    finally:
        confirm_mod.PROMPT_TIMEOUT_SECONDS = original
        release.set()

    assert started.is_set(), "the prompt never ran, so this proved nothing about waiting for it"
    assert elapsed < 15, f"refused, but only after {elapsed:.1f}s — that is still a hang"


def test_the_waiting_thread_cannot_keep_the_process_alive() -> None:
    """A daemon, deliberately. The stuck prompt is unkillable — nothing can interrupt a blocking
    console read — so the only question left is whether it also prevents shutdown. It must not."""
    release = threading.Event()
    original = confirm_mod.PROMPT_TIMEOUT_SECONDS
    confirm_mod.PROMPT_TIMEOUT_SECONDS = 0.1
    try:
        _answer_or_refuse(lambda: bool(release.wait(30)), "x")
        stuck = [t for t in threading.enumerate() if t.name == "host-exec-confirm"]
        assert stuck, "control failed: no waiting thread was left, so daemon-ness is untested here"
        assert all(t.daemon for t in stuck)
    finally:
        confirm_mod.PROMPT_TIMEOUT_SECONDS = original
        release.set()


def test_an_answer_that_arrives_in_time_is_returned_faithfully() -> None:
    """The control for the two above. A timeout that swallowed real answers would refuse everything
    a human approved, which is the same class of harm pointing the other way."""
    assert _answer_or_refuse(lambda: True, "ls") is True
    assert _answer_or_refuse(lambda: False, "ls") is False


def test_a_prompt_that_raises_still_refuses() -> None:
    """Fail-safe survives being moved onto another thread — an exception there is invisible to the
    caller's `except`, so the refusal has to be decided by the absence of an answer."""

    def _explodes() -> bool:
        raise RuntimeError("no console")

    assert _answer_or_refuse(_explodes, "ls") is False


# --------------------------------------------------------------------------- the launcher's half


def test_the_desktop_shell_hands_the_backend_a_stdin_that_tells_the_truth() -> None:
    """The other half, in the other language, and the reason it is asserted as text: this file
    cannot spawn a Tauri shell, and leaving the Rust side untested would leave the fix depending on
    a line nobody would notice being deleted. Read together with the comment above it, which says
    why `CREATE_NO_WINDOW` and an inherited stdin cannot both stand.
    """
    main_rs = Path(__file__).resolve().parents[1] / "apps/desktop/src-tauri/src/main.rs"
    source = main_rs.read_text(encoding="utf-8")
    spawn = source[source.index("let mut cmd = Command::new(&exe);") :][:1500]
    assert re.search(r"\.stdin\(Stdio::null\(\)\)", spawn), (
        "the backend is spawned with an inherited stdin again; under CREATE_NO_WINDOW that is a "
        "console with no window, and isatty() will lie to the host-exec gate"
    )


def test_the_code_surface_no_longer_asserts_what_it_cannot_enforce() -> None:
    """The comment that was true as intent and false as description. It is allowed to say the ask
    resolves to a refusal only because something now makes that so."""
    code_api = Path(__file__).resolve().parents[1] / "chimera/api/code_api.py"
    text = code_api.read_text(encoding="utf-8")
    assert "declare_no_human_here" in text, (
        "code_api still explains the refusal without naming whatever now produces it"
    )
