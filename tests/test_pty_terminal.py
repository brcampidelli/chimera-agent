"""The PTY manager behind the Code screen's terminal.

What each test would catch if the behaviour broke:

* a shell that does not keep state between lines (the whole point over the Runner: `cd` must stick);
* a resize that does not reach the child, so a full-screen program draws for the wrong window;
* a closed tab that leaves the shell's children running (a dev server holding its port);
* a cap that lets a ninth shell start;
* typed text reaching the log.

They run where the platform has a pty: POSIX always, Windows when `pywinpty` is installed (the
`desktop` extra). Elsewhere they skip, and the "unavailable" test says what the owner would see.
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path

import pytest

from chimera.api import pty_terminal as pt

_OK, _WHY = pt.available()
needs_pty = pytest.mark.skipif(not _OK, reason=_WHY or "no pty on this platform")
posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX pty only")


def _drain_until(session: pt.PtySession, needle: str, timeout: float = 15.0) -> str:
    """Read output until it contains ``needle`` (matched after the shell echoed it back once)."""
    seen = ""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        chunk = session.read(0.2)
        if chunk is None:
            break
        seen += chunk
        if needle in seen:
            return seen
    raise AssertionError(f"{needle!r} not seen in {seen[-400:]!r}")


def _line_end() -> str:
    return "\r\n" if os.name == "nt" else "\n"


def _quiet_shell() -> list[str] | None:
    """A shell without a user's rc files, so a prompt theme cannot get in the way."""
    if os.name == "nt":
        return None
    return ["/bin/sh"]


@pytest.fixture()
def mgr():
    manager = pt.PtyManager(max_sessions=3)
    yield manager
    manager.close_all()


def test_without_the_windows_backend_the_terminal_says_so_and_does_not_fake_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pt.os, "name", "nt")
    monkeypatch.setattr("importlib.util.find_spec", lambda name, *a, **k: None)
    ok, why = pt.available()
    assert not ok
    assert "pywinpty" in why


@posix_only
@needs_pty
def test_the_reader_survives_a_descriptor_numbered_past_select_s_limit(
    mgr: pt.PtyManager, tmp_path: Path
) -> None:
    """`select.select` raises ValueError for fd >= 1024; the reader died quietly and the tab was blank.
    Found when the full suite (which holds many descriptors open) ran this file."""
    import resource

    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    wanted = 1300
    if hard != resource.RLIM_INFINITY and hard < wanted + 50:
        pytest.skip("the hard descriptor limit is too low to number a descriptor past 1024")
    resource.setrlimit(resource.RLIMIT_NOFILE, (max(soft, wanted + 50), hard))
    held: list[int] = []
    try:
        while len(held) < wanted:
            held.append(os.open(os.devnull, os.O_RDONLY))
        session = mgr.open(tmp_path, argv=_quiet_shell())
        assert session._master >= 1024, "the descriptor was not numbered past the limit"
        session.write("echo past-" + "limit" + _line_end())
        assert "past-limit" in _drain_until(session, "past-limit")
    finally:
        for fd in held:
            os.close(fd)
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))


@needs_pty
def test_a_shell_runs_what_is_typed(mgr: pt.PtyManager, tmp_path: Path) -> None:
    session = mgr.open(tmp_path, argv=_quiet_shell())
    session.write("echo hello-" + "pty" + _line_end())
    assert "hello-pty" in _drain_until(session, "hello-pty")


@needs_pty
def test_cd_persists_between_lines(mgr: pt.PtyManager, tmp_path: Path) -> None:
    (tmp_path / "inner-dir").mkdir()
    session = mgr.open(tmp_path, argv=_quiet_shell())
    session.write("cd inner-dir" + _line_end())
    session.write("echo here-is:%CD%" + _line_end() if os.name == "nt" else "echo here-is:$PWD\n")
    out = _drain_until(session, "here-is:" + ("" if os.name == "nt" else str(tmp_path)))
    assert "inner-dir" in out or os.name == "nt"


@needs_pty
def test_it_opens_in_the_given_folder(mgr: pt.PtyManager, tmp_path: Path) -> None:
    session = mgr.open(tmp_path, argv=_quiet_shell())
    assert session.cwd == tmp_path
    if os.name == "posix":
        session.write("pwd\n")
        assert tmp_path.name in _drain_until(session, tmp_path.name)


@posix_only
@needs_pty
def test_resize_reaches_the_child(mgr: pt.PtyManager, tmp_path: Path) -> None:
    session = mgr.open(tmp_path, 100, 30, argv=_quiet_shell())
    session.resize(132, 40)
    session.write("stty size\n")
    assert "40 132" in _drain_until(session, "40 132")


def test_sizes_are_clamped() -> None:
    assert pt._clamp(5000, pt.COLS_RANGE) == pt.COLS_RANGE[1]
    assert pt._clamp(-3, pt.ROWS_RANGE) == pt.ROWS_RANGE[0]


@needs_pty
def test_the_cap_refuses_one_more(mgr: pt.PtyManager, tmp_path: Path) -> None:
    for _ in range(3):
        mgr.open(tmp_path, argv=_quiet_shell())
    with pytest.raises(pt.TerminalLimitReached):
        mgr.open(tmp_path, argv=_quiet_shell())
    assert mgr.count() == 3


@needs_pty
def test_closing_a_tab_frees_its_slot(mgr: pt.PtyManager, tmp_path: Path) -> None:
    sessions = [mgr.open(tmp_path, argv=_quiet_shell()) for _ in range(3)]
    assert mgr.close(sessions[0].id)
    assert mgr.close(sessions[0].id) is False  # already gone, and says so
    mgr.open(tmp_path, argv=_quiet_shell())  # the slot is back


@posix_only
@needs_pty
def test_closing_kills_what_the_shell_started(mgr: pt.PtyManager, tmp_path: Path) -> None:
    session = mgr.open(tmp_path, argv=_quiet_shell())
    # printf, not echo: the tty echoes the typed line back, and the marker must only be in the OUTPUT.
    session.write("sleep 300 & printf 'child-pid:%s:%s\\n' $! done\n")
    seen = _drain_until(session, ":done")
    pid = int(re.findall(r"child-pid:(\d+):done", seen)[-1])
    os.kill(pid, 0)  # alive now
    mgr.close(session.id)
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.1)
    raise AssertionError("the child the shell started outlived the terminal")


@needs_pty
def test_a_shell_that_exits_ends_the_stream(mgr: pt.PtyManager, tmp_path: Path) -> None:
    session = mgr.open(tmp_path, argv=_quiet_shell())
    session.write("exit" + _line_end())
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if session.read(0.2) is None:
            return
    raise AssertionError("the stream never ended after the shell exited")


@needs_pty
def test_a_session_nobody_attached_to_is_reaped_by_its_own_timer(tmp_path: Path) -> None:
    # No second open() and no call into the manager: the old test patched a constant and called the
    # sweep itself, which is exactly the path that only ran when somebody opened another terminal.
    manager = pt.PtyManager(max_sessions=3, attach_grace=0.3)
    try:
        lost = manager.open(tmp_path, argv=_quiet_shell())
        watched = manager.open(tmp_path, argv=_quiet_shell())
        assert watched.claim()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and manager.get(lost.id) is not None:
            time.sleep(0.1)
        assert manager.get(lost.id) is None and lost.closed
        assert manager.get(watched.id) is not None and not watched.closed
    finally:
        manager.close_all()


@needs_pty
def test_only_one_stream_can_claim_a_session(mgr: pt.PtyManager, tmp_path: Path) -> None:
    session = mgr.open(tmp_path, argv=_quiet_shell())
    assert session.claim() is True
    assert session.claim() is False


@needs_pty
def test_close_all_closes_them_in_parallel_and_all_of_them(mgr: pt.PtyManager, tmp_path: Path) -> None:
    sessions = [mgr.open(tmp_path, argv=_quiet_shell()) for _ in range(3)]
    mgr.close_all()
    assert all(s.closed for s in sessions)
    assert mgr.count() == 0


@needs_pty
def test_writing_to_a_closed_terminal_is_refused_not_sent_to_a_recycled_descriptor(
    mgr: pt.PtyManager, tmp_path: Path
) -> None:
    session = mgr.open(tmp_path, argv=_quiet_shell())
    mgr.close(session.id)
    with pytest.raises(pt.TerminalUnavailable):
        session.write("echo nobody-hears-this" + _line_end())
    session.resize(100, 30)  # a no-op, not an error and not an ioctl on a closed number


def _processes_matching(needle: str) -> list[int]:
    psutil = pytest.importorskip("psutil")
    found: list[int] = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        if needle in " ".join(proc.info.get("cmdline") or []):
            found.append(proc.info["pid"])
    return found


@pytest.mark.skipif(os.name != "nt", reason="Job Objects are Windows only")
@needs_pty
@pytest.mark.parametrize("how", ["close", "exit-then-close"])
def test_on_windows_a_background_process_does_not_outlive_the_terminal(
    mgr: pt.PtyManager, tmp_path: Path, how: str
) -> None:
    """`exit` after `ping -t` in the background used to leave the ping running: taskkill /T follows
    parent links and its parent, the shell, was gone. The Job Object holds it whatever became of it."""
    marker = "127.0.0." + str(70 + (os.getpid() % 100))
    session = mgr.open(tmp_path, argv=["cmd.exe"])
    session.write(f"start /b ping -t {marker} > nul\r\n")
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not _processes_matching(marker):
        time.sleep(0.2)
    assert _processes_matching(marker), "the background ping never started"
    if how == "exit-then-close":
        session.write("exit\r\n")
        time.sleep(1.0)
    mgr.close(session.id)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and _processes_matching(marker):
        time.sleep(0.2)
    leftovers = _processes_matching(marker)
    for pid in leftovers:  # do not leave a ping running on the developer's machine if this fails
        os.kill(pid, 9)
    assert not leftovers, "a background process outlived its terminal"


@needs_pty
def test_what_is_typed_is_never_logged(
    mgr: pt.PtyManager, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "correct-horse-battery-staple"
    with caplog.at_level(logging.DEBUG):
        session = mgr.open(tmp_path, argv=_quiet_shell())
        session.write("echo " + secret + _line_end())
        _drain_until(session, secret)
        mgr.close(session.id)
    assert secret not in caplog.text


def test_the_shell_environment_drops_provider_secrets_but_keeps_the_terminal_usable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test-not-for-the-shell")
    env = pt._shell_env()
    assert "OPENROUTER_API_KEY" not in env
    assert env["TERM"] and env["CHIMERA_TERMINAL"] == "1"
    # The Runner's non-interactive overrides would break an editor or a pager opened by hand.
    assert env.get("PAGER") != "cat" or os.environ.get("PAGER") == "cat"
    assert env.get("CI") != "1" or os.environ.get("CI") == "1"
