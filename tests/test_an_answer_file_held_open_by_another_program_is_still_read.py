"""An approval is not refused because some other program had the answer file open for a moment.

On Windows the antivirus (and the search indexer) opens a file the moment a rename puts it in
place, and while it holds it an open by anybody else fails with a sharing violation — a
``PermissionError``. `pending.answer` already retried its ``os.replace`` for exactly this
(`core/filelock.retrying`); the asker's poll in `_wait_for_answer` read the file with a bare
``read_text``, so the sharing violation fell into the ``unreadable`` branch and the question ended
as a refusal the person never gave.

It showed up as `test_the_real_clock_gap_is_milliseconds` failing on a Windows CI runner: the ask
returned False 1.0055 s into a 5 s wait — the moment the answerer wrote its approval — with no
``seconds_to_answer`` on the record, which is what an ``unreadable`` outcome writes. Run alone on a
quiet machine it passed every time, because nothing scanned the file.

The first test makes the collision certain with a real exclusive handle (Windows only); the second
makes it certain everywhere by failing the first read.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from chimera.governance import pending


class _Clock:
    """A clock the asker sleeps on without waiting: every poll advances it by its interval."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _outcome(home: Path, request_id: str) -> str:
    return str([r for r in pending.history(home) if r["id"] == request_id][-1]["outcome"])


@pytest.mark.skipif(sys.platform != "win32", reason="a sharing violation is a Windows behaviour")
def test_an_answer_held_open_exclusively_for_a_moment_still_approves(tmp_path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    generic_read, open_existing, no_sharing = 0x80000000, 3, 0
    invalid = wintypes.HANDLE(-1).value
    asked: dict[str, Any] = {}

    def on_asked(q: pending.PendingApproval) -> None:
        asked["id"] = q.id
        assert pending.answer(tmp_path, q.id, True)
        target = pending._dir(tmp_path) / f"{q.id}.answer.json"
        # What the scanner does: open the new file and share it with nobody, briefly.
        handle = kernel32.CreateFileW(str(target), generic_read, no_sharing, None, open_existing, 0, None)
        assert handle not in (None, invalid), f"could not hold the file: {ctypes.get_last_error()}"
        threading.Timer(0.3, kernel32.CloseHandle, args=(handle,)).start()

    outcome = pending.ask_durably(
        tmp_path, "run_shell: make deploy", "held open", on_asked=on_asked,
        wait_seconds=5.0, poll_seconds=0.05,
    )

    assert _outcome(tmp_path, asked["id"]) == "approved"
    assert outcome is True


def test_a_read_that_fails_with_a_sharing_violation_is_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: dict[str, Any] = {}
    real_read_text = Path.read_text
    refused_reads: list[Path] = []

    def read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        if self.name.endswith(".answer.json") and not refused_reads:
            refused_reads.append(self)
            raise PermissionError(13, "The process cannot access the file because it is being used")
        return real_read_text(self, *args, **kwargs)

    def on_asked(q: pending.PendingApproval) -> None:
        asked["id"] = q.id
        assert pending.answer(tmp_path, q.id, True)
        monkeypatch.setattr(Path, "read_text", read_text)

    clock = _Clock()
    outcome = pending.ask_durably(
        tmp_path, "run_shell: make deploy", "busy file", on_asked=on_asked,
        wait_seconds=60, poll_seconds=1, clock=clock, sleep=clock.sleep,
    )
    monkeypatch.undo()

    assert refused_reads, "precondition: the first read of the answer failed as a busy file does"
    assert _outcome(tmp_path, asked["id"]) == "approved"
    assert outcome is True
