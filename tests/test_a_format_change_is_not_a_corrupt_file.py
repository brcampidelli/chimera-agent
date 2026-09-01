"""One bad record is tolerance working. Every record bad is a file the next `save()` will erase.

Five readers here skip what they cannot validate and carry on, each with a comment saying why: a
hand-edit or a truncated last object must not cost every other entry. The rule is right and the
severity was flat — the same warning either way, and the same empty collection returned.

Empty is the dangerous part. The scheduler's store is read by a daemon every tick and written back
by the next `add` or `remove`, so a `CronJob` field that becomes required in a release turns forty
jobs into forty warnings nobody reads and then into `[]` on disk. The memory store has the same
shape with a slower fuse.

Two other readers had the opposite defect: a bare `json.loads` outside any handler, so a single
truncated line took down the whole kanban board or the whole audit log — a lesson `memory/store.py`
already learned and wrote down in its own docstring.
"""

from __future__ import annotations

import json
from pathlib import Path

from chimera.core.state_format import looks_like_a_format_change
from chimera.governance.audit import AuditLog
from chimera.kanban.board import KanbanBoard
from chimera.memory.store import MemoryStore
from chimera.scheduler.store import CronStore

# ------------------------------------------------------------------ the rule


def test_one_bad_record_among_many_is_not_a_format_change() -> None:
    assert looks_like_a_format_change(kept=49, skipped=1) is False


def test_every_record_bad_is() -> None:
    assert looks_like_a_format_change(kept=0, skipped=40) is True


def test_an_empty_file_is_neither() -> None:
    """A file with no records is not a format change — it is a file with no records, and refusing to
    write to it would make a fresh install unusable."""
    assert looks_like_a_format_change(kept=0, skipped=0) is False


# ------------------------------------------------------------------ the crontab


def _jobs(path: Path, *entries: dict[str, object]) -> None:
    path.write_text(json.dumps(list(entries)), encoding="utf-8")


VALIDO = {
    "id": "a1", "name": "relatório", "trigger": "cron", "schedule": "* * * * *",
    "action": "escreva", "enabled": True,
}


def test_a_crontab_from_another_version_is_not_read_as_empty(tmp_path: Path) -> None:
    """The whole point. Two jobs that no longer validate are two jobs, not zero."""
    caminho = tmp_path / "jobs.json"
    _jobs(caminho, {"id": "a1", "campo_que_sumiu": 1}, {"id": "b2", "campo_que_sumiu": 2})

    loja = CronStore(caminho)

    assert loja.list() == []  # nothing loaded, which is honest
    assert loja.stale is True  # and the store knows the load did not happen


def test_it_refuses_to_overwrite_a_file_it_could_not_read(tmp_path: Path) -> None:
    """The consequence that makes this worth a mechanism rather than a log line.

    `save()` is called by the next `add` or `remove`, and the daemon reloads every tick. Without
    this, an upgrade that adds a required field empties the crontab of a machine nobody is watching.
    """
    caminho = tmp_path / "jobs.json"
    antes = json.dumps([{"id": "a1", "campo_que_sumiu": 1}])
    caminho.write_text(antes, encoding="utf-8")

    loja = CronStore(caminho)
    loja.save()

    assert caminho.read_text(encoding="utf-8") == antes


def test_one_bad_job_among_good_ones_still_loads_the_good_ones(tmp_path: Path) -> None:
    """Tolerance still works, and the store is not stale — this is the case the skip exists for."""
    caminho = tmp_path / "jobs.json"
    _jobs(caminho, VALIDO, {"id": "b2", "lixo": True})

    loja = CronStore(caminho)

    assert [j.id for j in loja.list()] == ["a1"]
    assert loja.stale is False


def test_a_healthy_store_still_saves(tmp_path: Path) -> None:
    """The guard against a fix that makes the store read-only for everyone."""
    caminho = tmp_path / "jobs.json"
    _jobs(caminho, VALIDO)

    loja = CronStore(caminho)
    loja.remove("a1")

    assert json.loads(caminho.read_text(encoding="utf-8")) == []


def test_an_absent_file_is_not_stale(tmp_path: Path) -> None:
    """A fresh install has no file, and a store that refused to write there would never start."""
    loja = CronStore(tmp_path / "jobs.json")

    assert loja.stale is False


# ------------------------------------------------------------------ memory


def test_memory_from_another_version_is_not_read_as_empty(tmp_path: Path) -> None:
    caminho = tmp_path / "m.json"
    caminho.write_text(json.dumps([{"conteudo": "sem id"}, {"conteudo": "outro"}]), encoding="utf-8")

    loja = MemoryStore(caminho)

    assert loja.stale is True


def test_memory_refuses_to_overwrite_what_it_could_not_read(tmp_path: Path) -> None:
    caminho = tmp_path / "m.json"
    antes = json.dumps([{"conteudo": "sem id"}])
    caminho.write_text(antes, encoding="utf-8")

    loja = MemoryStore(caminho)
    loja.save()

    assert caminho.read_text(encoding="utf-8") == antes


# ------------------------------------------------------------------ the two that crashed


def test_a_truncated_kanban_file_does_not_take_the_board_down(tmp_path: Path) -> None:
    """`json.loads` sat outside the handler, so the per-card tolerance below it was unreachable for
    the one failure that actually happens: a write cut short."""
    caminho = tmp_path / "kanban.json"
    caminho.write_text('[{"id": "c1", "title": "meio', encoding="utf-8")

    board = KanbanBoard(caminho)

    assert board.cards() == []
    assert board.stale is True


def test_a_truncated_audit_log_still_reads_the_lines_before_it(tmp_path: Path) -> None:
    """An append-only log truncated mid-line is the ordinary crash outcome, and the whole file was
    unreadable because of it — including the entries written before the crash, which are the ones
    an incident needs."""
    caminho = tmp_path / "audit.jsonl"
    caminho.write_text('{"type": "a", "prev": ""}\n{"type": "b", "pre', encoding="utf-8")

    entradas = AuditLog(caminho).entries()

    assert [e["type"] for e in entradas] == ["a"]
