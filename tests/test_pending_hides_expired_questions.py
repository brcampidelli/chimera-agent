"""A question whose wait ended is not a question a person can answer, and the card must not say it is.

The bug this pins: the app showed "Chimera needs a decision" fourteen hours after the process that
asked had died. Its question file still held the wait it was given (300 s), nothing ever consumed
it, and `pending()` listed every file until the 24 h sweep. Clicking Refuse wrote an answer file no
one read, so the card never went away either.

Time is passed in as `now`; nothing here sleeps or patches the clock.
"""

from __future__ import annotations

import json
from pathlib import Path

from chimera.governance.pending import EXPIRY_GRACE_SECONDS, answer, pending

ASKED_AT = 1_793_053_055.34
WAIT = 300.0
REQUEST_ID = "20e05e425d44"
CODE = "123456"


def _ask(
    home: Path,
    *,
    wait_seconds: float | None = WAIT,
    asked_at: float = ASKED_AT,
    **extra: object,
) -> Path:
    directory = home / "approvals"
    directory.mkdir(parents=True, exist_ok=True)
    data = {
        "id": REQUEST_ID,
        "action": "run_shell rm -rf build",
        "reason": "review required",
        "asked_at": asked_at,
        "wait_seconds": wait_seconds,
        **extra,
    }
    (directory / f"{REQUEST_ID}.ask.json").write_text(json.dumps(data), encoding="utf-8")
    return directory


def _ids(home: Path, now: float) -> list[str]:
    return [question.id for question in pending(home, now=now)]


def test_a_question_whose_wait_is_over_is_not_listed(tmp_path: Path) -> None:
    _ask(tmp_path)
    after_grace = ASKED_AT + WAIT + EXPIRY_GRACE_SECONDS + 1

    assert _ids(tmp_path, after_grace) == []


def test_listing_an_expired_question_deletes_nothing(tmp_path: Path) -> None:
    """The sweep owns deletion; a read of the queue must not race the asker's own cleanup."""
    directory = _ask(tmp_path)

    pending(tmp_path, now=ASKED_AT + WAIT + EXPIRY_GRACE_SECONDS + 1)

    assert (directory / f"{REQUEST_ID}.ask.json").exists()


def test_a_question_inside_its_wait_is_listed(tmp_path: Path) -> None:
    _ask(tmp_path)

    assert _ids(tmp_path, ASKED_AT + 10) == [REQUEST_ID]


def test_a_question_at_the_grace_boundary_is_still_listed(tmp_path: Path) -> None:
    """The asker's last poll can land exactly here; the card must still be answerable."""
    _ask(tmp_path)

    assert _ids(tmp_path, ASKED_AT + WAIT + EXPIRY_GRACE_SECONDS) == [REQUEST_ID]


def test_a_question_with_no_wait_limit_is_listed_however_old(tmp_path: Path) -> None:
    """No recorded wait means the asker waited without a limit; only the 24 h sweep may clear it."""
    _ask(tmp_path, wait_seconds=None)

    assert _ids(tmp_path, ASKED_AT + 10 * 24 * 3600) == [REQUEST_ID]


def test_an_approval_to_an_expired_question_is_refused_and_writes_nothing(tmp_path: Path) -> None:
    directory = _ask(tmp_path)
    expired = ASKED_AT + WAIT + EXPIRY_GRACE_SECONDS + 1

    assert answer(tmp_path, REQUEST_ID, True, code=CODE, now=expired) is False
    assert not (directory / f"{REQUEST_ID}.answer.json").exists()


def test_a_refusal_to_an_expired_question_is_refused_too(tmp_path: Path) -> None:
    """Nobody is waiting, so a refusal would be a file no one reads, the same as an approval."""
    directory = _ask(tmp_path)
    expired = ASKED_AT + WAIT + EXPIRY_GRACE_SECONDS + 1

    assert answer(tmp_path, REQUEST_ID, False, now=expired) is False
    assert not (directory / f"{REQUEST_ID}.answer.json").exists()


def test_an_approval_inside_the_grace_is_still_recorded(tmp_path: Path) -> None:
    """The asker is still in its last poll here, and it honours an approval written now."""
    directory = _ask(tmp_path)

    assert answer(tmp_path, REQUEST_ID, True, code=CODE, now=ASKED_AT + WAIT + 1) is True
    assert (directory / f"{REQUEST_ID}.answer.json").exists()
