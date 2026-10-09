"""`pending.answer` publishes the answer file in one step — the asker never reads it half-written.

`ask_durably` polls for `<id>.answer.json` to EXIST and parses it at once. `answer()` used
`Path.write_text`, which creates the file empty and then fills it; a poll inside that window parsed
"" and recorded the approval as `unreadable`, i.e. a refusal the person never gave. On Linux a tight
reader saw the partial file in 1959 of 3000 writes, and the stale-consent probe (a real-clock thread
answering while the asker polls) failed on it now and then in the full suite.

A timing test would pass most of the time with the bug present, so this one watches the mechanism:
through an audit hook, the final path is never opened by the writer — it only appears by a rename.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from chimera.governance import pending

#: Events seen while a test is watching; None outside, so the (unremovable) hook is inert.
_WATCHING: list[tuple[str, tuple[Any, ...]]] | None = None


def _hook(event: str, args: tuple[Any, ...]) -> None:
    if _WATCHING is not None and event in ("open", "os.rename"):
        _WATCHING.append((event, args))


sys.addaudithook(_hook)


def _ask(home: Path) -> str:
    request_id = "0123456789ab"
    directory = pending._dir(home)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{request_id}.ask.json").write_text('{"id": "0123456789ab"}', encoding="utf-8")
    pending._remember_code(request_id, "123456")
    return request_id


def test_the_answer_file_appears_only_by_a_rename(tmp_path: Path) -> None:
    global _WATCHING
    request_id = _ask(tmp_path)
    target = pending._dir(tmp_path) / f"{request_id}.answer.json"
    _WATCHING = []
    try:
        assert pending.answer(tmp_path, request_id, True)
        seen = list(_WATCHING)
    finally:
        _WATCHING = None
        pending._forget_code(request_id)

    def same(value: Any) -> bool:
        return isinstance(value, (str, bytes, Path)) and Path(str(value)) == target

    opened_in_place = [args for event, args in seen if event == "open" and same(args[0])]
    renamed_onto = [args for event, args in seen if event == "os.rename" and same(args[1])]
    assert not opened_in_place, "the answer file was written in place — a poll can read it empty"
    assert renamed_onto, "the answer file did not arrive by a rename"
    assert '"approved": true' in target.read_text(encoding="utf-8")
