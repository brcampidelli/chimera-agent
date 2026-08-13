"""Tests for the long-lived child process layer (:mod:`chimera.proc.stdio`).

These drive REAL processes. A mocked ``Popen`` would test the mock: every behaviour that matters
here — a program that will not resolve, a child that ignores a closed stdin, a grandchild that
outlives its parent — exists precisely because the operating system does something the code did not
expect, and a fake cannot surprise anybody.

The child is always this interpreter (``sys.executable``), so the suite needs nothing installed and
behaves the same on every platform the project supports.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from chimera.proc.stdio import (
    StdioProcess,
    is_batch_launcher,
    kill_tree,
    reap_all,
    resolve_program,
    unsafe_windows_argument,
)

_POSIX = os.name == "posix"


def _child(code: str) -> list[str]:
    """An argv running ``code`` in this interpreter, unbuffered so lines arrive as they are written."""
    return [sys.executable, "-u", "-c", code]


#: Echoes every JSON line back with a marker, then exits when stdin closes.
ECHO = """
import sys, json
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    print(json.dumps({"echo": msg}), flush=True)
"""


class Collector:
    """Gathers messages off the reader thread and lets a test wait for the nth one."""

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self._arrived = threading.Event()

    def __call__(self, message: dict) -> None:
        self.messages.append(message)
        self._arrived.set()

    def wait(self, count: int = 1, timeout: float = 20.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if len(self.messages) >= count:
                return True
            self._arrived.wait(0.05)
            self._arrived.clear()
        return len(self.messages) >= count


# --- resolving a program ---------------------------------------------------------------------


def test_a_bare_name_resolves_to_its_path() -> None:
    """The Windows trap, asserted on every platform because the fix is cross-platform.

    `Popen(["npx", ...])` raises FileNotFoundError on Windows: CreateProcess does not consult
    PATHEXT, so `npx.CMD` is invisible under the name everyone types. Measured on this machine, and
    it reads exactly like "the adapter is not installed" — the wrong thing to tell someone who has
    installed it.
    """
    program = "python.exe" if not _POSIX else "sh"
    resolved = resolve_program(program)
    assert os.sep in resolved or resolved == program


def test_a_path_is_left_alone() -> None:
    # Already a path: whether it exists is the OS's answer to give, not ours to pre-empt.
    assert resolve_program(sys.executable) == sys.executable


def test_a_name_that_resolves_to_nothing_comes_back_unchanged() -> None:
    # So the caller can tell "not installed" from "installed but broken". Raising here would
    # collapse the two into one message.
    assert resolve_program("definitely-not-a-real-program-xyz") == "definitely-not-a-real-program-xyz"


# --- the Windows argument hazard -------------------------------------------------------------


@pytest.mark.skipif(_POSIX, reason="cmd.exe parses arguments only on Windows")
def test_shell_syntax_reaching_a_cmd_launcher_is_refused(tmp_path: Path) -> None:
    """Launching `npx` on Windows launches `npx.CMD`, which cmd.exe parses even with shell=False.

    So an argument containing `&` is a second command. Refused rather than escaped: quoting rules
    differ per launcher and a wrong guess runs the user's machine, not a program on it.

    Driven through a REAL `.cmd`, because the hazard belongs to the launcher and not to Windows —
    the first version of this guard applied to every program and refused ordinary launches.
    """
    launcher = tmp_path / "fake-adapter.cmd"
    launcher.write_text("@echo off\r\n", encoding="utf-8")

    proc = StdioProcess([str(launcher), "a&calc"], on_message=lambda _m: None)
    with pytest.raises(ValueError, match="shell syntax"):
        proc.start()

    assert unsafe_windows_argument("--flag=a&calc", str(launcher)) is True
    assert unsafe_windows_argument("--model=sonnet", str(launcher)) is False


@pytest.mark.skipif(_POSIX, reason="Windows-only launcher rule")
def test_an_exe_is_not_parsed_by_a_shell_so_its_arguments_are_left_alone() -> None:
    """`CreateProcess` involves no shell. Banning `&` for an .exe refuses a risk that cannot exist —
    which is what the over-broad first version did, breaking nine tests at once."""
    assert is_batch_launcher(sys.executable) is False
    assert unsafe_windows_argument("x = a & b", sys.executable) is False

    seen = Collector()
    child = StdioProcess(
        _child('import sys; print(\'{"argv":"%s"}\' % sys.argv[1].replace("&","and"))'),
        on_message=seen,
    )
    child.argv.append("a&b")
    child.start()
    try:
        assert seen.wait()
        assert seen.messages[0] == {"argv": "aandb"}
    finally:
        child.close()


@pytest.mark.skipif(not _POSIX, reason="there is no shell in the launch path on POSIX")
def test_an_ampersand_is_an_ordinary_argument_on_posix() -> None:
    assert unsafe_windows_argument("--flag=a&b") is False
    assert is_batch_launcher("/usr/bin/anything.cmd") is False


# --- talking to it ---------------------------------------------------------------------------


def test_a_message_goes_out_and_the_answer_comes_back() -> None:
    seen = Collector()
    child = StdioProcess(_child(ECHO), on_message=seen).start()
    try:
        child.send({"hello": "world"})
        assert seen.wait(), "no reply from the child"
        assert seen.messages[0] == {"echo": {"hello": "world"}}
    finally:
        child.close()


def test_messages_keep_their_order() -> None:
    # A protocol correlates replies by id, but a stream of notifications has no ids at all: if the
    # framing reorders them, a tool's "completed" can arrive before its "started".
    seen = Collector()
    child = StdioProcess(_child(ECHO), on_message=seen).start()
    try:
        for i in range(20):
            child.send({"n": i})
        assert seen.wait(20)
        assert [m["echo"]["n"] for m in seen.messages] == list(range(20))
    finally:
        child.close()


def test_non_json_on_stdout_is_kept_rather_than_thrown_away() -> None:
    """npm notices, adapter banners and tracebacks all arrive on stdout looking identical.

    Dropping them silently is how "the agent said nothing" hides "the agent printed a stack trace".
    """
    code = 'import sys; print("npm notice: new version"); print(\'{"real":1}\'); sys.stdout.flush()'
    seen = Collector()
    child = StdioProcess(_child(code), on_message=seen).start()
    try:
        assert seen.wait()
        assert seen.messages == [{"real": 1}]
        assert any("npm notice" in line for line in child.noise)
    finally:
        child.close()


def test_a_json_array_is_noise_and_not_a_message() -> None:
    # Every frame in these protocols is an object. A bare array is a child talking to somebody else.
    seen = Collector()
    child = StdioProcess(_child('print("[1,2,3]")'), on_message=seen).start()
    try:
        time.sleep(0.5)
        assert seen.messages == []
        assert any("[1,2,3]" in line for line in child.noise)
    finally:
        child.close()


def test_a_handler_that_raises_does_not_kill_the_stream() -> None:
    """One bad frame must not end the conversation — the next frame may be the one that matters."""
    seen: list[dict] = []

    def handler(message: dict) -> None:
        if message.get("boom"):
            raise RuntimeError("handler exploded")
        seen.append(message)

    code = 'print(\'{"boom":1}\'); print(\'{"fine":1}\')'
    child = StdioProcess(_child(code), on_message=handler).start()
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not seen:
            time.sleep(0.05)
        assert seen == [{"fine": 1}]
    finally:
        child.close()


def test_stderr_is_kept_for_the_failure_report() -> None:
    # An exit code alone cannot tell anyone what to do next. The child's last words can.
    code = 'import sys; sys.stderr.write("ANTHROPIC_API_KEY is not set\\n"); sys.exit(3)'
    child = StdioProcess(_child(code), on_message=lambda _m: None).start()
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and child.poll() is None:
            time.sleep(0.05)
        time.sleep(0.3)  # let the stderr reader drain after exit
        assert "ANTHROPIC_API_KEY is not set" in child.stderr_tail()
    finally:
        child.close()


def test_sending_to_a_child_that_has_exited_raises_rather_than_vanishing() -> None:
    child = StdioProcess(_child("pass"), on_message=lambda _m: None).start()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and child.poll() is None:
        time.sleep(0.05)
    child.close()
    with pytest.raises(BrokenPipeError):
        child.send({"anything": True})


def test_the_exit_callback_fires_when_the_child_dies_on_its_own() -> None:
    """An adapter that exits mid-turn must surface as a turn that ended, not as one that hangs."""
    codes: list[int] = []
    child = StdioProcess(_child("import sys; sys.exit(7)"), on_message=lambda _m: None,
                         on_exit=codes.append).start()
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not codes:
            time.sleep(0.05)
        assert codes == [7]
    finally:
        child.close()


def test_closing_normally_does_not_report_an_exit() -> None:
    # `close()` is us ending it. Reporting that back as "the agent died" would turn every clean
    # shutdown into an error on screen.
    codes: list[int] = []
    child = StdioProcess(_child(ECHO), on_message=lambda _m: None, on_exit=codes.append).start()
    child.close()
    time.sleep(0.3)
    assert codes == []


# --- the part that is actually about processes -------------------------------------------------


def test_closing_stdin_is_enough_for_a_polite_child() -> None:
    child = StdioProcess(_child(ECHO), on_message=lambda _m: None).start()
    child.close(timeout=15)
    assert child.poll() is not None


def test_a_child_that_ignores_a_closed_stdin_is_killed_anyway() -> None:
    """The one that matters. A polite shutdown that a child can decline is not a shutdown."""
    code = "import time\nwhile True: time.sleep(0.1)"
    child = StdioProcess(_child(code), on_message=lambda _m: None).start()
    started = time.monotonic()
    child.close(timeout=1.0)
    assert child.poll() is not None
    assert time.monotonic() - started < 20


def test_the_grandchild_dies_too(tmp_path: Path) -> None:
    """A coding agent is a launcher: npx starts node, node starts workers.

    Killing only the process we hold leaves the ones doing the work — still holding the workspace,
    still talking to a model, and invisible to the person wondering why their folder is locked.
    The grandchild here writes a file every 100ms; if it survives, the file keeps growing.
    """
    marker = tmp_path / "alive.txt"
    grandchild = (
        "import time, pathlib\n"
        f"p = pathlib.Path(r'{marker}')\n"
        "while True:\n"
        "    p.write_text(str(time.time()))\n"
        "    time.sleep(0.1)\n"
    )
    parent = (
        "import subprocess, sys, time\n"
        f"subprocess.Popen([sys.executable, '-u', '-c', {grandchild!r}])\n"
        "while True: time.sleep(0.1)\n"
    )
    child = StdioProcess(_child(parent), on_message=lambda _m: None).start()

    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not marker.exists():
        time.sleep(0.05)
    assert marker.exists(), "the grandchild never started"

    child.close(timeout=1.0)
    time.sleep(1.0)  # anything still alive writes again inside this window
    settled = marker.read_text()
    time.sleep(1.0)
    assert marker.read_text() == settled, "the grandchild outlived its parent"


def test_kill_tree_on_an_already_dead_process_is_silent() -> None:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    kill_tree(proc)  # must not raise: reaping races with a child exiting on its own


def test_reap_all_kills_what_is_still_running() -> None:
    """Registered with atexit, so a crash, a forgotten close, or the user quitting mid-turn all end
    here rather than in a process they have to hunt down in a task manager."""
    code = "import time\nwhile True: time.sleep(0.1)"
    child = StdioProcess(_child(code), on_message=lambda _m: None).start()
    reap_all()
    assert child.poll() is not None


def test_reap_all_is_safe_with_nothing_to_reap() -> None:
    reap_all()
    reap_all()


def test_an_empty_argv_is_refused_before_anything_starts() -> None:
    with pytest.raises(ValueError, match="argv"):
        StdioProcess([], on_message=lambda _m: None)


def test_a_program_that_does_not_exist_says_so() -> None:
    child = StdioProcess(["definitely-not-a-real-program-xyz"], on_message=lambda _m: None)
    with pytest.raises((FileNotFoundError, OSError)):
        child.start()
