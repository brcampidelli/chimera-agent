"""What `record` does when the file lock cannot be taken.

`locked()` degrades rather than raising — on Windows `msvcrt.locking` retries for about ten seconds
and then gives up, and the helper writes unlocked so a 24/7 process is never wedged. It yields
whether a real lock was taken *"so a caller that wants to record degraded operation can"*, and the
one caller that most needs that signal was discarding it.

The consequence was measured, not theorised: an intermittent CI failure on Windows,
``seq duplicado: [0, 1, 2, 1, 2, 3, ...]`` — two processes reading the same tail, claiming the same
``seq`` and chaining onto the same ``prev``. It passed on a rerun of the identical commit, which is
exactly what made it easy to wave away.

**These tests drive that path deterministically**, by making the lock fail on demand. The Windows
race is not reproducible on purpose and a guard that needs one is not a guard.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

import chimera.governance.audit as audit_mod
from chimera.governance.audit import AuditLog


@pytest.fixture
def lock_falha(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Make the file lock unobtainable, and count how many times it was asked for."""
    pedidos: list[int] = []

    def _recusa(handle: object) -> None:
        pedidos.append(1)
        raise OSError("lock unavailable (test)")

    # Patched where `locked()` reads it, so the degraded path inside the real helper runs — the
    # point is to exercise `locked()`, not to replace it with a stand-in that only looks similar.
    monkeypatch.setattr("chimera.core.filelock._acquire", _recusa)
    monkeypatch.setattr(audit_mod, "_LOCK_BACKOFF_S", 0.0)  # the wait is not what is under test
    return pedidos


def test_it_tries_more_than_once(tmp_path: Path, lock_falha: list[int]) -> None:
    """The whole point: one failure to lock is not a reason to write unlocked."""
    AuditLog(tmp_path / "audit.jsonl").record("test", {})

    assert len(lock_falha) == audit_mod._LOCK_ATTEMPTS, (
        f"asked for the lock {len(lock_falha)} times, expected {audit_mod._LOCK_ATTEMPTS}"
    )


def test_the_entry_is_still_written(tmp_path: Path, lock_falha: list[int]) -> None:
    """An audit log that drops entries under contention is worse than one with a visible break.

    A missing entry is never honest about itself; a break at least is, and `verify` names it.
    """
    log = AuditLog(tmp_path / "audit.jsonl")
    entry = log.record("test", {"a": 1})

    assert entry["seq"] == 0
    assert log.entries() == [entry]
    assert log.verify().ok


def test_it_says_so_at_error_level(tmp_path: Path, lock_falha: list[int], caplog: pytest.LogCaptureFixture) -> None:
    """Loud, because the helper's own warning is one line in a log nobody reads during a run.

    `locked()` logs at WARNING when it degrades, which is right for callers that can live with it.
    This caller cannot: the next concurrent writer breaks the chain, and the Security screen reports
    that break as tampering on a file nobody touched.
    """
    with caplog.at_level(logging.ERROR, logger="chimera.governance.audit"):
        AuditLog(tmp_path / "audit.jsonl").record("test", {})

    assert any("WITHOUT the file lock" in r.getMessage() for r in caplog.records), (
        f"nothing said the write was unlocked; got {[r.getMessage() for r in caplog.records]}"
    )


def test_a_lock_that_works_is_asked_for_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Guarding the guard: without this, a `record` that always retried would pass the tests above.

    The retry must be a response to failure, not a fixed cost paid by every append — this file
    writes one entry per governed action and the ordinary path has to stay one acquisition.
    """
    pedidos: list[int] = []
    real = audit_mod.locked

    from contextlib import contextmanager

    @contextmanager
    def _contando(path: Path):  # type: ignore[no-untyped-def]
        pedidos.append(1)
        with real(path) as got:
            yield got

    monkeypatch.setattr(audit_mod, "locked", _contando)
    AuditLog(tmp_path / "audit.jsonl").record("test", {})

    assert len(pedidos) == 1, f"took the lock {len(pedidos)} times when it was available"
