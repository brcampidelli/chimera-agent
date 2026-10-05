"""An answer file approves only when it carries the code the owner was given (study 30, S30-30).

Before this, a question was approved by ANY file named ``<id>.answer.json`` holding
``{"approved": true}``. The agent's own shell can write files, so the phase-0 fence
(`core/queue_fence.py`) refused commands that name the folder — lexically, and the review listed
what a lexical reading misses: ``appro${X:-}vals``, brace expansion, ``$(...)``, ANSI-C ``$'..'``,
and a program assembling the path at run time. Each of those wrote an answer file and approved.

Now the asker holds a code in its MEMORY — never on disk, not even hashed (a six-digit code under
sha256 inverts in a loop, and the chat code's hash used to sit in the question file) — and accepts
an approval only if the answer carries it. The owner's surfaces get it two ways: the ones in the
asker's own process (the app's card, the chat bot) look it up in memory; ``chimera approve`` is
told it in the delivered message (or, with no screen and no channel, on the asking process's
stderr) and passes ``--code``. A refusal needs no code: an agent refusing its own question harms
nothing. A wrong or missing code refuses the question — one attempt, so guessing is a 1-in-a-million
shot that costs the guesser its question and leaves an ``unverified`` line in the history.

What this does not close, said once: code that runs INSIDE the asking process (``code_interpreter``
runs in-process) can read that memory, and the app's ``/api/approvals`` route answers in-process
too — without ``CHIMERA_SERVER_TOKEN`` a ``curl`` to localhost reaches it. Both keep the lexical
fence; neither is made structural here.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from chimera.config import get_settings
from chimera.core.queue_fence import reaches_queue
from chimera.governance import pending
from chimera.tools.code import ExecuteCodeTool
from chimera.tools.shell import RunShellTool

WAIT = 20.0


class _Asked:
    """``ask_durably`` on a thread, with the delivered text captured — the owner's channel."""

    def __init__(self, home: Path, *, chat: bool = False, wait: float = WAIT) -> None:
        self.home = home
        self.texts: list[str] = []
        self.result: bool | None = None

        def run() -> None:
            self.result = pending.ask_durably(
                home, "run_shell\ngit push --force", "policy: force push",
                deliver=_Channel(self.texts, chat), wait_seconds=wait, poll_seconds=0.05,
            )

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + WAIT
        while time.monotonic() < deadline and not (pending.pending(home) and self.texts):
            time.sleep(0.01)
        assert pending.pending(home), "precondition: the question was written"
        self.id = pending.pending(home)[0].id

    @property
    def code(self) -> str:
        match = re.search(r"--code (\d{6})", "\n".join(self.texts))
        assert match, f"the delivered question carries no code: {self.texts!r}"
        return match[1]

    def outcome(self) -> bool | None:
        self.thread.join(WAIT)
        assert not self.thread.is_alive(), "the asker never resolved"
        return self.result

    def last_record(self) -> dict[str, Any]:
        lines = (self.home / "approvals" / pending.HISTORY).read_text(encoding="utf-8").splitlines()
        return dict(json.loads(lines[-1]))


class _Channel:
    def __init__(self, texts: list[str], chat: bool) -> None:
        self.texts = texts
        self.offers_chat_code = chat

    def __call__(self, text: str) -> None:
        self.texts.append(text)


def _forge(home: Path, request_id: str, body: dict[str, Any]) -> None:
    (home / "approvals" / f"{request_id}.answer.json").write_text(json.dumps(body), encoding="utf-8")


# ------------------------------------------------------------------ the forged file


def test_an_answer_file_without_the_code_does_not_approve(tmp_path: Path) -> None:
    asked = _Asked(tmp_path)

    _forge(tmp_path, asked.id, {"approved": True, "answered_at": time.time()})

    assert asked.outcome() is False
    assert asked.last_record()["outcome"] == "unverified"


def test_an_answer_file_with_a_wrong_code_does_not_approve_and_is_not_retried(tmp_path: Path) -> None:
    asked = _Asked(tmp_path)
    wrong = "000000" if asked.code != "000000" else "111111"

    _forge(tmp_path, asked.id, {"approved": True, "code": wrong})

    assert asked.outcome() is False
    assert asked.last_record()["outcome"] == "unverified"


def test_a_refusal_needs_no_code(tmp_path: Path) -> None:
    asked = _Asked(tmp_path)

    _forge(tmp_path, asked.id, {"approved": False})

    assert asked.outcome() is False
    assert asked.last_record()["outcome"] == "refused"


def test_the_code_is_on_disk_nowhere_not_even_hashed(tmp_path: Path) -> None:
    import hashlib

    asked = _Asked(tmp_path, chat=True)
    code = asked.code
    text = (tmp_path / "approvals" / f"{asked.id}.ask.json").read_text(encoding="utf-8")
    pending.answer(tmp_path, asked.id, False, via="test")
    asked.outcome()

    assert code not in text
    assert hashlib.sha256(f"{asked.id}:{code}".encode()).hexdigest() not in text
    assert code not in repr(pending.pending(tmp_path))


# ------------------------------------------------------------------ the owner's surfaces still work


def test_a_surface_in_the_asking_process_approves_without_being_handed_the_code(tmp_path: Path) -> None:
    """The app's card and the chat bot run in the asking process: they read the code from memory."""
    asked = _Asked(tmp_path)

    assert pending.answer(tmp_path, asked.id, True, via="app") is True

    assert asked.outcome() is True


def test_another_process_cannot_approve_without_the_code(tmp_path: Path) -> None:
    asked = _Asked(tmp_path)
    pending._forget_code(asked.id)  # as `chimera approve` in another process sees it: no memory

    assert pending.answer(tmp_path, asked.id, True, via="cli") is False
    assert pending.answer(tmp_path, asked.id, True, via="cli", code=asked.code) is True

    assert asked.outcome() is True


def _cli(home: Path, monkeypatch: pytest.MonkeyPatch, *args: str) -> Any:
    from chimera.cli.main import app

    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    try:
        return CliRunner().invoke(app, ["approve", *args])
    finally:
        get_settings.cache_clear()


def test_chimera_approve_yes_asks_for_the_code_and_approves_with_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = _Asked(tmp_path)

    without = _cli(tmp_path, monkeypatch, asked.id, "--yes")
    assert without.exit_code == 1 and "--code" in without.output
    assert not (tmp_path / "approvals" / f"{asked.id}.answer.json").exists()

    with_code = _cli(tmp_path, monkeypatch, asked.id, "--yes", "--code", asked.code)
    assert with_code.exit_code == 0, with_code.output
    assert asked.outcome() is True


def test_chimera_approve_no_needs_no_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _Asked(tmp_path)

    result = _cli(tmp_path, monkeypatch, asked.id, "--no")

    assert result.exit_code == 0, result.output
    assert asked.outcome() is False and asked.last_record()["outcome"] == "refused"


def test_the_delivered_message_carries_the_whole_approve_line(tmp_path: Path) -> None:
    asked = _Asked(tmp_path)
    pending.answer(tmp_path, asked.id, False)
    asked.outcome()

    assert f"chimera approve {asked.id} --yes --code {asked.code}" in "\n".join(asked.texts)


def test_with_no_screen_and_no_channel_the_code_goes_to_the_askers_own_stderr(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    result: list[bool] = []
    thread = threading.Thread(
        target=lambda: result.append(
            pending.ask_durably(tmp_path, "a", "b", wait_seconds=WAIT, poll_seconds=0.05)
        ),
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + WAIT
    while time.monotonic() < deadline and not pending.pending(tmp_path):
        time.sleep(0.01)
    request_id = pending.pending(tmp_path)[0].id
    pending.answer(tmp_path, request_id, False)
    thread.join(WAIT)

    assert re.search(rf"chimera approve {request_id} --yes --code \d{{6}}", capsys.readouterr().err)


def test_the_chat_bot_in_another_process_forwards_and_the_asker_checks(tmp_path: Path) -> None:
    right = _Asked(tmp_path, chat=True)
    pending._forget_code(right.id)
    assert pending.answer_with_code(tmp_path, right.id, right.code, True, via="discord:1") == "forwarded"
    assert right.outcome() is True

    wrong = _Asked(tmp_path, chat=True)
    pending._forget_code(wrong.id)
    bad = "000000" if wrong.code != "000000" else "111111"
    assert pending.answer_with_code(tmp_path, wrong.id, bad, True, via="discord:1") == "forwarded"
    assert wrong.outcome() is False and wrong.last_record()["outcome"] == "unverified"


def _app_client(home: Path, workspace: Path) -> Any:
    from fastapi.testclient import TestClient

    from chimera.api import build_api_app
    from chimera.config import Settings
    from chimera.interface import ChatSession

    settings = Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json")
    return TestClient(build_api_app(lambda: ChatSession(None), workspace=workspace, settings=settings))  # type: ignore[arg-type]  # no turn runs here


def test_the_apps_card_approves_a_question_this_process_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    asked = _Asked(home)

    with _app_client(home, tmp_path) as client:
        answered = client.post(f"/api/approvals/{asked.id}", json={"approved": True}).json()

    assert answered == {"ok": True}
    assert asked.outcome() is True
    get_settings.cache_clear()


def test_the_apps_card_says_how_to_approve_a_question_another_process_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    asked = _Asked(home)
    pending._forget_code(asked.id)  # asked by a terminal run: this app never held its code

    with _app_client(home, tmp_path) as client:
        approve = client.post(f"/api/approvals/{asked.id}", json={"approved": True}).json()
        refuse = client.post(f"/api/approvals/{asked.id}", json={"approved": False}).json()

    assert approve["ok"] is False and approve["outcome"] == "needs_code"
    assert f"chimera approve {asked.id} --yes --code" in approve["detail"]
    assert refuse == {"ok": True}, "a refusal needs no code, from anywhere"
    assert asked.outcome() is False and asked.last_record()["outcome"] == "refused"
    get_settings.cache_clear()


# ------------------------------------------------------------------ the bypasses the review listed


def _bypasses(answers: Path) -> list[tuple[str, str]]:
    """Commands that write ``<answers>/<id>.answer.json`` and that the lexical fence does not read.

    ``{ID}`` is filled in per question. The home path is written plainly; only the two words the
    fence looks for (``approvals``, ``answer``) are disguised, each the way the review named.
    """
    home = answers.parent.as_posix()
    body = '{"approved": true}'
    return [
        ("default-expansion", f"printf '%s' '{body}' > {home}/appro${{X:-}}vals/{{ID}}.ans${{X:-}}wer.json"),
        ("brace-expansion", f"printf '%s' '{body}' | tee {home}/appro{{vals,}}/{{ID}}.ans{{wer,}}.json"),
        ("command-substitution", f"printf '%s' '{body}' > {home}/$(echo appro)vals/{{ID}}.$(echo ans)wer.json"),
        ("ansi-c-escape", f"printf '%s' '{body}' > {home}/$'appro\\x76als'/{{ID}}.$'\\x61nswer'.json"),
    ]


def _bash_or_skip() -> None:
    if os.name != "posix" or shutil.which("bash") is None:
        pytest.skip("run_shell runs a POSIX shell only on POSIX; Windows runs cmd.exe")


@pytest.mark.parametrize("label", ["default-expansion", "brace-expansion", "command-substitution", "ansi-c-escape"])
def test_a_shell_bypass_of_the_lexical_fence_writes_a_file_that_approves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, label: str
) -> None:
    _bash_or_skip()
    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("SHELL", shutil.which("bash") or "/bin/bash")
    get_settings.cache_clear()
    asked = _Asked(home)
    command = dict(_bypasses(home / "approvals"))[label].replace("{ID}", asked.id)
    # The evidence that this is a bypass and not a refusal: the fence reads nothing in it.
    assert reaches_queue(command, home=home, cwd=tmp_path) is None
    workspace = tmp_path / "ws"
    workspace.mkdir()

    out = RunShellTool(workspace=workspace).run(command=f"bash -c {shlex.quote(command)}")

    assert "did NOT run" not in out, out
    assert asked.outcome() is False, "a file the agent's shell wrote approved its own question"
    assert asked.last_record()["outcome"] == "unverified"
    get_settings.cache_clear()


def test_a_program_that_assembles_the_path_writes_a_file_that_approves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    asked = _Asked(home)
    program = (
        "import json, os\n"
        f"folder = os.path.join({str(home)!r}, 'appr' + 'ovals')\n"
        f"name = {asked.id!r} + '.ans' + 'wer.json'\n"
        "open(os.path.join(folder, name), 'w').write(json.dumps({'approved': True}))\n"
    )
    assert reaches_queue(program, home=home, cwd=tmp_path) is None, "precondition: unfenced"
    workspace = tmp_path / "ws"
    workspace.mkdir()

    out = ExecuteCodeTool(workspace=workspace).run(code=program)

    assert out.startswith("[exit 0]"), out
    assert asked.outcome() is False
    assert asked.last_record()["outcome"] == "unverified"
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _no_leftover_codes() -> Any:
    yield
    pending._OWNER_CODES.clear()
