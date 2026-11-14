"""The suggestion ledger is trimmed through the atomic writer, and stays made of whole events.

The trim used to be a plain ``write_text``, which truncates the file before it writes. A crash in
that window, or a second writer, left the ledger empty or half-written. The trim now goes through
``atomic_write_text``, the same helper the other stores use for this reason.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.api import suggestion_log


def test_the_trim_goes_through_the_atomic_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []
    real = suggestion_log.atomic_write_text

    def spy(path: Path, text: str, **kwargs: object) -> None:
        calls.append(path)
        real(path, text)

    monkeypatch.setattr(suggestion_log, "atomic_write_text", spy)
    # The trim runs once the file exceeds MAX_LINES * 80 bytes; with 0 it runs on every event.
    monkeypatch.setattr(suggestion_log, "MAX_LINES", 0)
    suggestion_log.record(tmp_path, event="shown", kind="fix")
    assert calls, "the trim never ran, so the test proves nothing"
    assert calls[0] == suggestion_log._path(tmp_path)


def test_the_trim_keeps_the_ledger_made_of_whole_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(suggestion_log, "MAX_LINES", 3)
    for _ in range(12):
        suggestion_log.record(tmp_path, event="shown", kind="continue")
    lines = suggestion_log._path(tmp_path).read_text(encoding="utf-8").splitlines()
    assert 0 < len(lines) < 12, "the ledger was never trimmed"
    assert all(json.loads(line)["event"] == "shown" for line in lines)
