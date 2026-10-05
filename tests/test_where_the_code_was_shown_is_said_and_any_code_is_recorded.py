"""Where an approval's code was shown is said, and any code — even a nonsense one — is recorded.

Three defects a review found in the answer-file code (study 30, S30-30):

- **A non-ASCII code escaped the record.** ``hmac.compare_digest`` on two ``str`` raises TypeError for
  any non-ASCII character. An answer carrying ``"code": "é"`` raised out of the wait: the call failed
  closed, but no history line was written and the files stayed behind, so "one attempt, recorded"
  did not hold. The chat path compared the same way.
- **With no channel, the code went to stderr whatever stderr was.** A daemon started as
  ``chimera serve --cron >> serve.log 2>&1`` writes stderr to a FILE, and a second run in the same
  daemon can read it while the first waits — the bypass the code exists to close. The code is now
  printed only to a terminal; elsewhere the line says it was withheld.
- **A question the app asked with no channel had its code nowhere a person could read**, and
  ``chimera approve`` sent them looking for it in "the message that asked". The question now says
  where its code was shown, and the command says when ``--yes`` cannot work.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from chimera.config import get_settings
from chimera.governance import pending

WAIT = 20.0


def _ask(home: Path, **kwargs: Any) -> tuple[threading.Thread, list[bool], str]:
    result: list[bool] = []
    thread = threading.Thread(
        target=lambda: result.append(
            pending.ask_durably(home, "a", "b", wait_seconds=WAIT, poll_seconds=0.05, **kwargs)
        ),
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + WAIT
    while time.monotonic() < deadline and not pending.pending(home):
        time.sleep(0.01)
    assert pending.pending(home), "precondition: the question was written"
    return thread, result, pending.pending(home)[0].id


def _last_record(home: Path) -> dict[str, Any]:
    lines = (home / "approvals" / pending.HISTORY).read_text(encoding="utf-8").splitlines()
    return dict(json.loads(lines[-1]))


def _cli(home: Path, monkeypatch: pytest.MonkeyPatch, *args: str) -> Any:
    from chimera.cli.main import app

    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("COLUMNS", "400")  # one line per sentence: the asserts read whole phrases
    get_settings.cache_clear()
    try:
        return CliRunner().invoke(app, ["approve", *args])
    finally:
        get_settings.cache_clear()


# ------------------------------------------------------------------ any code is recorded


def test_an_answer_with_a_non_ascii_code_is_refused_and_recorded_as_unverified(tmp_path: Path) -> None:
    thread, result, request_id = _ask(tmp_path)

    (tmp_path / "approvals" / f"{request_id}.answer.json").write_text(
        json.dumps({"approved": True, "code": "é"}), encoding="utf-8"
    )
    thread.join(WAIT)

    assert not thread.is_alive() and result == [False]
    assert _last_record(tmp_path)["outcome"] == "unverified"
    assert not (tmp_path / "approvals" / f"{request_id}.answer.json").exists(), "cleaned up"


def test_a_non_ascii_code_typed_into_the_chat_is_a_wrong_code(tmp_path: Path) -> None:
    class Channel:
        offers_chat_code = True

        def __call__(self, _text: str) -> None:
            pass

    thread, result, request_id = _ask(tmp_path, deliver=Channel())

    assert pending.answer_with_code(tmp_path, request_id, "１２３４５６", True, via="discord:1") == "wrong_code"
    pending.answer(tmp_path, request_id, False)
    thread.join(WAIT)
    assert result == [False]


# ------------------------------------------------------------------ where the code went


def test_a_terminal_is_shown_the_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pending, "_stderr_is_a_terminal", lambda: True)
    thread, _result, request_id = _ask(tmp_path)

    assert pending.code_shown(tmp_path, request_id) == "terminal"
    pending.answer(tmp_path, request_id, False)
    thread.join(WAIT)
    assert re.search(rf"--yes --code \d{{6}}", capsys.readouterr().err)


def test_stderr_that_is_not_a_terminal_is_never_shown_the_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pending, "_stderr_is_a_terminal", lambda: False)
    thread, _result, request_id = _ask(tmp_path)

    shown = pending.code_shown(tmp_path, request_id)
    err = capsys.readouterr().err
    pending.answer(tmp_path, request_id, False)
    thread.join(WAIT)

    assert shown == "nowhere"
    assert request_id in err, "the question is still announced"
    assert not re.search(r"--code \d{6}", err), err
    assert "CHIMERA_APPROVAL_WEBHOOK" in err


def test_chimera_approve_says_a_question_whose_code_went_nowhere_cannot_be_approved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pending, "_stderr_is_a_terminal", lambda: False)
    thread, result, request_id = _ask(tmp_path)

    approve = _cli(tmp_path, monkeypatch, request_id, "--yes", "--code", "123456")
    listed = _cli(tmp_path, monkeypatch)
    refuse = _cli(tmp_path, monkeypatch, request_id, "--no")
    thread.join(WAIT)

    assert approve.exit_code == 1 and "shown nowhere" in approve.output
    assert f"{request_id}:" in listed.output and "shown nowhere" in listed.output
    assert refuse.exit_code == 0 and result == [False]
    assert _last_record(tmp_path)["outcome"] == "refused", "nothing was written by the --yes"


def test_chimera_approve_says_a_question_the_app_asked_is_approved_on_the_apps_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    announced: list[Any] = []
    thread, result, request_id = _ask(tmp_path, on_asked=announced.append)

    assert pending.code_shown(tmp_path, request_id) == "screen"
    approve = _cli(tmp_path, monkeypatch, request_id, "--yes")
    pending.answer(tmp_path, request_id, False)
    thread.join(WAIT)

    assert approve.exit_code == 1
    assert "only the app's own card" in approve.output
    assert "message that asked" not in approve.output


def test_a_question_with_a_channel_still_asks_for_its_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    thread, _result, request_id = _ask(tmp_path, deliver=lambda _text: None)

    assert pending.code_shown(tmp_path, request_id) == "message"
    without = _cli(tmp_path, monkeypatch, request_id, "--yes")
    pending.answer(tmp_path, request_id, False)
    thread.join(WAIT)

    assert without.exit_code == 1 and "--code <code>" in without.output
