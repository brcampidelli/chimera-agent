"""What a whole-file store does when the file lock cannot be taken.

``locked()`` degrades: when the OS refuses the lock it runs the body **unlocked** and says so by
yielding ``False``. That is deliberate, and right for the audit log, which takes it loudly because a
broken hash chain it can complain about beats a wedged 24/7 process.

It is the wrong answer for a store, and this was measured rather than argued. Twelve processes each
adding twenty-five learned skills, on Windows:

    entered without the lock ... 2–3 per run
    skills lost ................ 2–5 per run
    warnings the operator sees . none

Every caller discarded the boolean, so the read-modify-write ran with no exclusion at all and
another process's record went away. The CI failure that started this read ``assert 49 == 50``, and
it passed on a rerun of the identical commit — which is exactly what makes this kind of thing easy
to wave away.

After the change, the same twenty-process probe lands 500 of 500, three runs running.

**These tests drive the failure deterministically**, by making the lock fail on demand — the same
approach ``test_audit_lock_degrades`` takes, and for the same reason: the Windows race is not
reproducible on purpose, and a guard that needs one is not a guard.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

import chimera.core.filelock as filelock_mod
from chimera.core.filelock import LOCK_ATTEMPTS, LockUnavailable, exclusively
from chimera.evolution.learned_skill import LearnedSkill
from chimera.evolution.skill_store import SkillStore
from chimera.memory.manager import MemoryManager
from chimera.memory.store import MemoryStore


@pytest.fixture
def lock_sempre_falha(monkeypatch):
    """`locked` yielding False every time — the degraded path, on demand.

    Patched where `exclusively` reads it, so the real helper runs rather than a stand-in that only
    looks similar.
    """
    tentativas: list[Path] = []

    @contextmanager
    def falso(path: Path):
        tentativas.append(path)
        yield False

    monkeypatch.setattr(filelock_mod, "locked", falso)
    monkeypatch.setattr("chimera.evolution.skill_store.exclusively", exclusively)
    monkeypatch.setattr("chimera.memory.store.exclusively", exclusively)
    monkeypatch.setattr(filelock_mod, "LOCK_BACKOFF_S", 0.0)  # no waiting in a unit test
    return tentativas


def test_it_retries_before_giving_up(tmp_path: Path, lock_sempre_falha) -> None:
    """One refusal is a moment of contention; the budget is what tells that from a stuck lock."""
    with pytest.raises(LockUnavailable), exclusively(tmp_path / "x.json"):
        pytest.fail("the body must not run without the lock")

    assert len(lock_sempre_falha) == LOCK_ATTEMPTS, (
        f"tried {len(lock_sempre_falha)} times, expected {LOCK_ATTEMPTS}"
    )


def test_the_body_never_runs_unlocked(tmp_path: Path, lock_sempre_falha) -> None:
    """The whole point. Running the body is what loses the other process's record."""
    rodou = False

    with pytest.raises(LockUnavailable), exclusively(tmp_path / "x.json"):
        rodou = True

    assert not rodou


def test_the_message_says_nothing_was_written(tmp_path: Path, lock_sempre_falha) -> None:
    """An operator reading this needs to know whether to retry the operation or not."""
    with pytest.raises(LockUnavailable) as erro, exclusively(tmp_path / "skills.json"):
        pass

    texto = str(erro.value)
    assert "skills.json" in texto
    assert "Nothing was written" in texto


def test_a_skill_store_refuses_rather_than_losing_somebody_elses_skill(
    tmp_path: Path, lock_sempre_falha
) -> None:
    """The measured failure, at the level a user meets it.

    Before: `add` wrote unlocked and a concurrent writer's skill disappeared, silently. Now the
    write refuses — and an error the operator can read is a better outcome than a skill that was
    learned and is not there.
    """
    caminho = tmp_path / "skills.json"
    caminho.write_text('[{"name": "ja-existia", "description": "x"}]', encoding="utf-8")
    store = SkillStore(caminho)

    with pytest.raises(LockUnavailable):
        store.add(LearnedSkill(name="nova", description="x"))

    # And the file it could not lock is untouched: refusing must not be a half-write.
    assert "ja-existia" in caminho.read_text(encoding="utf-8")
    assert "nova" not in caminho.read_text(encoding="utf-8")


def test_a_memory_store_refuses_too(tmp_path: Path, lock_sempre_falha) -> None:
    """Same helper, same failure, same silence — memory was losing facts the same way."""
    mgr = MemoryManager(MemoryStore(tmp_path / "m.json"))

    with pytest.raises(LockUnavailable):
        mgr.remember("um fato que nao pode sumir")


def test_locked_itself_still_degrades(tmp_path: Path, monkeypatch) -> None:
    """The contract that was NOT changed, asserted so nobody widens the fix into the audit log.

    `locked` degrading is a deliberate choice with its own tests: the audit log would rather write a
    chain it can complain about than wedge a process that runs all day. This adds a stricter helper
    beside it; it does not replace it.
    """
    def recusa(handle):
        raise OSError("no lock for you")

    monkeypatch.setattr(filelock_mod, "_acquire", recusa)

    with filelock_mod.locked(tmp_path / "y.json") as got_lock:
        assert got_lock is False, "locked() started raising — the audit log depends on it not doing that"
