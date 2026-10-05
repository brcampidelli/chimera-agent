"""The agent never answers an approval question — not even from the shell.

A question waiting for a person is a file, `<home>/approvals/<id>.ask.json`, and it is answered by
another file beside it, `<id>.answer.json`. `pending.py` says so of the one-time chat code: whoever
can read that folder can write an answer there, "including the local agent itself when it has
run_shell". #775 closed the folder to `write_file` and `edit_file` (by file identity, every Windows
spelling included) and left the shell, the two code tools and `chimera approve` itself open. A run
whose REVIEW is pending in one worker could answer it from another, or from a background job it
started earlier, and approve itself (study 30, critic's missed item).

What is refused here, before anything runs: a command (or program) that names the approval folder
by any spelling the file system resolves to it — absolute, relative to the workspace or to a folder
the command changes into, through an environment variable or `~`, a glob, a Windows device or 8.3
spelling — or names a question or answer file, the app's approval route, the queue's own code, or
runs `chimera approve`. The owner's own `chimera approve`, the app's card and the chat code are not
the agent's tools and are untouched (`tests/test_approve_from_anywhere.py` and the app tests).

It is a lexical fence over what the command SAYS, read also as a shell joins it once quoting is
removed (`appro""vals` is `approvals` to bash). That REDUCES the ways in and does not close them,
recorded rather than hidden: a program that builds the path at run time from pieces
(`"appr" + "ovals"`), `appro${X:-}vals`, brace expansion and command substitution are not seen.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from chimera.config import get_settings
from chimera.governance import pending
from chimera.sandbox.base import SandboxResult
from chimera.tools.code import CodeInterpreterTool, ExecuteCodeTool
from chimera.tools.shell import RunShellTool

QUESTION = "0123456789ab"


class _Recording:
    """A sandbox that runs nothing and keeps every command it was handed."""

    def __init__(self) -> None:
        self.commands: list[str] = []

    def is_isolated(self) -> bool:
        return True

    def run(self, command: str, **_: Any) -> SandboxResult:
        self.commands.append(command)
        return SandboxResult(exit_code=0)


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """A workspace that contains the data folder (an app started from the home directory)."""
    monkeypatch.chdir(tmp_path)
    home = tmp_path / "home"
    queue = home / "approvals"
    queue.mkdir(parents=True)
    (queue / f"{QUESTION}.ask.json").write_text(json.dumps({"id": QUESTION}), encoding="utf-8")
    (home / "scripts").mkdir()
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    get_settings.cache_clear()
    return tmp_path, home


@pytest.fixture(autouse=True)
def _clear_settings() -> Any:
    yield
    get_settings.cache_clear()


def _refused(out: str) -> bool:
    return "did NOT run" in out and "approval" in out


def _spellings(home: Path) -> list[str]:
    queue = home / "approvals"
    target = queue / "x.json"
    out = [
        f"copy evil.json {target}",
        f'cp evil.json "{target}"',
        f"cp evil.json {str(target).replace(os.sep, '/')}",
        "cp evil.json home/approvals/",
        "cp evil.json ./home/sub/../approvals",
        "cd home && cd approvals && cp ../../evil.json .",
        "cd home; cp evil.json approvals",
        "cp evil.json $CHIMERA_HOME/approvals",
        "cp evil.json ${CHIMERA_HOME}/approvals",
        "copy evil.json %CHIMERA_HOME%\\approvals",
        "Copy-Item evil.json $env:CHIMERA_HOME\\approvals",
        "cp evil.json ~/home/approvals",
        "cp evil.json hom*/appr*",
        "cp evil.json home/approva?s",
        f"echo {{}} > {QUESTION}.answer.json",
        f"cat home/approvals/{QUESTION}.ask.json",
        f"chimera approve {QUESTION} --yes",
        f"python -m chimera approve {QUESTION} --yes",
        f"uv run chimera approve {QUESTION} --yes",
        f"curl -X POST http://127.0.0.1:8765/api/approvals/{QUESTION} -d yes",
    ]
    if sys.platform == "win32":
        out += [
            f"copy evil.json {str(target).upper()}",
            f"copy evil.json \\\\?\\{target}",
            f"copy evil.json \\\\localhost\\{str(target)[0]}$\\{str(target)[3:]}",
            f"copy evil.json {queue}.\\x.json",
            f'copy evil.json "{queue} \\x.json"',  # a trailing space Windows drops on open
        ]
        short = _short_name(queue)
        if short and short.lower() != str(queue).lower():
            out.append(f"copy evil.json {short}\\x.json")
    return out


def _short_name(path: Path) -> str:
    import ctypes

    buffer = ctypes.create_unicode_buffer(1024)
    n = ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, 1024)  # type: ignore[attr-defined]  # Windows-only API
    return buffer.value if n else ""


def test_every_spelling_of_the_queue_is_refused_by_run_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws, home = _setup(tmp_path, monkeypatch)
    for command in _spellings(home):
        sandbox = _Recording()
        out = RunShellTool(ws, sandbox).run(command=command)
        assert _refused(out), f"ran: {command!r} -> {out!r}"
        assert sandbox.commands == [], f"reached the sandbox: {command!r}"


def test_a_spelling_the_shell_joins_by_removing_quotes_is_the_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Measured bypass of the first version (S30-04 review): the fence split words at a quote and
    read `appro` and `vals`; bash removes the quotes and opens `approvals`. Same for an escape."""
    ws, home = _setup(tmp_path, monkeypatch)
    for command in [
        f"echo '{{\"approved\": true}}' > \"$CHIMERA_HOME\"/appro\"\"vals/{QUESTION}.ans\"\"wer.json",
        "echo x > \"$CHIMERA_HOME\"/appro''vals/y",
        "echo x > \"$CHIMERA_HOME\"/approval\\s/y",
        "echo x > $CHIMERA_HOME/appro$''vals/y",
        f"echo x > home/approvals/{QUESTION}.ans''wer.json",
        "Set-Content $env:CHIMERA_HOME\\appro`vals\\y x",
        "echo x > %CHIMERA_HOME%\\appro^vals\\y",
    ]:
        sandbox = _Recording()
        out = RunShellTool(ws, sandbox).run(command=command)
        assert _refused(out), f"ran: {command!r} -> {out!r}"
        assert sandbox.commands == [], f"reached the sandbox: {command!r}"


def test_a_quoted_answer_written_by_the_real_shell_does_not_approve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end, the exact command the review ran in Git Bash, handed to a real bash: without the
    fix bash creates `approvals/<id>.answer.json`, which `ask_durably` reads as a yes."""
    import shutil
    import subprocess

    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash on this machine")

    class _Bash(_Recording):
        def run(self, command: str, **_: Any) -> SandboxResult:
            self.commands.append(command)
            done = subprocess.run([bash, "-c", command], cwd=ws, capture_output=True, timeout=30)
            return SandboxResult(exit_code=done.returncode)

    ws, home = _setup(tmp_path, monkeypatch)
    command = (
        "echo '{\"approved\": true}' > "
        f"\"$CHIMERA_HOME\"/appro\"\"vals/{QUESTION}.ans\"\"wer.json"
    )
    sandbox = _Bash()
    out = RunShellTool(ws, sandbox).run(command=command)
    assert not (home / "approvals" / f"{QUESTION}.answer.json").exists(), "the shell answered"
    assert _refused(out), out
    assert sandbox.commands == []


def test_a_command_quoted_for_a_nested_shell_is_read_the_way_the_inner_shell_reads_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`bash -c "... appro\\"\\"vals ..."`: the outer shell turns `\\"` into `"`, the inner removes
    them. Read with the escapes removed first and the quotes second."""
    ws, _ = _setup(tmp_path, monkeypatch)
    command = 'bash -c "echo x > \\"$CHIMERA_HOME\\"/appro\\"\\"vals/y"'
    sandbox = _Recording()
    assert _refused(RunShellTool(ws, sandbox).run(command=command))
    assert sandbox.commands == []


def test_working_on_chimeras_own_source_is_not_answering_a_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The agent works on Chimera's own repository: a search for the queue's function names, a test
    filter, and a `chimera run` whose TASK says "approve" answer nothing and must run."""
    ws, _ = _setup(tmp_path, monkeypatch)
    for command in [
        "rg answer_with_code chimera/",
        "python -m pytest -q -k pending.answer",
        'grep -rn "answer.json" chimera/',
        'chimera run "approve the dependency PR"',
    ]:
        sandbox = _Recording()
        out = RunShellTool(ws, sandbox).run(command=command)
        assert sandbox.commands == [command], f"refused: {command!r} -> {out!r}"


def test_a_link_to_the_queue_is_the_queue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A link made earlier (by the owner, a tool, anything) is followed: the test is the folder the
    path OPENS, compared by file identity, not the text."""
    ws, home = _setup(tmp_path, monkeypatch)
    try:
        (ws / "inbox").symlink_to(home / "approvals", target_is_directory=True)
    except OSError:
        pytest.skip("this machine does not let an unprivileged process make a link")
    sandbox = _Recording()
    assert _refused(RunShellTool(ws, sandbox).run(command="cp evil.json inbox/x.json"))
    assert sandbox.commands == []


def test_padding_the_command_does_not_push_the_folder_out_of_sight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No cap on the words read: a cap would be a bypass, five thousand words and then the path."""
    ws, home = _setup(tmp_path, monkeypatch)
    padding = " ".join(f"src/file_{i}.py" for i in range(5000))
    sandbox = _Recording()
    out = RunShellTool(ws, sandbox).run(command=f"cat {padding} > {home / 'approvals' / 'x'}")
    assert _refused(out)
    assert sandbox.commands == []


def test_ordinary_work_and_the_rest_of_the_data_folder_still_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fence is the queue, not the data folder: on the VPS the agent's helper scripts live in
    `<home>/scripts`, and a commit message that says "approve" is just text."""
    ws, home = _setup(tmp_path, monkeypatch)
    for command in [
        "npm test",
        f"python {home / 'scripts' / 'report.py'} --count",
        "ls home",
        'git commit -m "chimera: approve the plan before merging"',
        "python -m pytest -q tests/test_approvals_screen.py",
    ]:
        sandbox = _Recording()
        out = RunShellTool(ws, sandbox).run(command=command)
        assert sandbox.commands == [command], f"refused: {command!r} -> {out!r}"


def test_the_two_code_tools_are_fenced_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws, home = _setup(tmp_path, monkeypatch)
    answer = home / "approvals" / f"{QUESTION}.answer.json"
    program = f"open(r'{home / 'approvals'}' + '/' + 'x', 'w').write('{{}}')"
    sandbox = _Recording()
    assert _refused(ExecuteCodeTool(ws, sandbox).run(code=program))
    assert sandbox.commands == []

    interpreter = CodeInterpreterTool()
    assert _refused(interpreter.run(code=program))
    assert _refused(interpreter.run(
        code=f"from chimera.governance import pending\npending.answer(r'{home}', '{QUESTION}', True)"
    ))
    assert not answer.exists()
    assert not (home / "approvals" / "x").exists()


def test_a_waiting_question_is_not_answered_by_the_agents_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end on the real shell: a question is asked, the agent's shell writes the answer file
    the way `chimera approve` would, and the question must still end unanswered."""
    ws, home = _setup(tmp_path, monkeypatch)
    writer = ws / "write_answer.py"
    writer.write_text(
        "import json, pathlib, sys, time\n"
        "folder = pathlib.Path(sys.argv[1])\n"
        "for _ in range(100):\n"
        "    asks = sorted(folder.glob('*.ask.json'))\n"
        f"    asks = [a for a in asks if a.name != '{QUESTION}.ask.json']\n"
        "    if asks:\n"
        "        rid = asks[0].name.split('.')[0]\n"
        "        (folder / (rid + '.answer.json')).write_text(json.dumps({'approved': True}))\n"
        "        break\n"
        "    time.sleep(0.05)\n",
        encoding="utf-8",
    )
    result: dict[str, bool] = {}

    def ask() -> None:
        result["approved"] = pending.ask_durably(
            home, "run_shell: make deploy", "needs a person", wait_seconds=3.0, poll_seconds=0.05
        )

    asker = threading.Thread(target=ask)
    asker.start()
    out = RunShellTool(ws).run(command=f'"{sys.executable}" write_answer.py "{home / "approvals"}"')
    asker.join()

    assert result["approved"] is False, "the agent approved its own question"
    assert _refused(out), out
