"""Two writers against one memory file — the update that used to disappear.

``save()`` has been atomic since it was written, and that covers the failure everyone thinks of: a
crash mid-write leaving an unreadable store. It does nothing for the other one. ``add()`` writes the
dict this object loaded when it was constructed, so a second writer that started from an older
snapshot republishes it and the first writer's record is gone — no exception, no log line, nothing
in the file to say a record was ever there.

Worth being exact about who the two writers are, because "concurrency bug" invites hand-waving.
``chimera serve`` builds one shared memory and runs the cron daemon in a background thread of the
same process, so those two share ``_items`` and do not race this way. The second *process* is the
ordinary one: any ``chimera memory add`` run over SSH while the gateway is up.

Every test here fails on the pre-fix implementation. The one that matters most is
``test_a_second_writer_does_not_erase_the_first``, which is that bug in six lines.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

import chimera.core.filelock as filelock
from chimera.core.filelock import atomic_write_text
from chimera.memory.models import MemoryItem
from chimera.memory.store import MemoryStore


def _item(ident: str) -> MemoryItem:
    return MemoryItem(id=ident, content=f"fact {ident}")


def _ids(path: Path) -> set[str]:
    return {row["id"] for row in json.loads(path.read_text(encoding="utf-8"))}


# --- the lost update ----------------------------------------------------------------------------


def test_a_second_writer_does_not_erase_the_first(tmp_path: Path) -> None:
    """The whole bug. Two stores open the same file, each adds one fact, both must survive.

    Pre-fix: ``second`` was constructed while the file was empty, so its save writes ``[b]`` over
    ``[a]``. The assertion below found one id where two were added.
    """
    path = tmp_path / "memory.json"
    first = MemoryStore(path)
    second = MemoryStore(path)  # a second process, holding the same empty snapshot

    first.add(_item("a"))
    second.add(_item("b"))

    assert _ids(path) == {"a", "b"}


def test_a_removal_does_not_resurrect_what_another_writer_deleted(tmp_path: Path) -> None:
    """The mirror, and the nastier direction: a stale writer can bring deleted data BACK.

    ``stale`` holds a snapshot from before the deletion. Under the old code its next write
    republished that snapshot, undoing someone else's ``memory forget``.
    """
    path = tmp_path / "memory.json"
    seed = MemoryStore(path)
    seed.add(_item("keep"))
    seed.add(_item("secret"))

    stale = MemoryStore(path)  # snapshot with both
    MemoryStore(path).remove("secret")  # somebody else forgets it
    stale.add(_item("new"))

    assert _ids(path) == {"keep", "new"}, "a deleted memory came back"


def test_interleaved_writers_keep_every_record(tmp_path: Path) -> None:
    """Alternating writers, which is what a daemon and a shell session actually look like."""
    path = tmp_path / "memory.json"
    left = MemoryStore(path)
    right = MemoryStore(path)

    for i in range(10):
        left.add(_item(f"L{i}"))
        right.add(_item(f"R{i}"))

    assert len(_ids(path)) == 20


# --- threads, and real processes ------------------------------------------------------------------


def test_threads_sharing_one_store_do_not_lose_records(tmp_path: Path) -> None:
    """One store, many threads — the `serve` shape. The risk here is not the stale snapshot but
    ``_write`` iterating the dict while another thread mutates it."""
    path = tmp_path / "memory.json"
    store = MemoryStore(path)

    def write(start: int) -> None:
        for i in range(start, start + 25):
            store.add(_item(f"t{i}"))

    threads = [threading.Thread(target=write, args=(base,)) for base in (0, 100, 200, 300)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(_ids(path)) == 100


_WRITER = """
import sys
from pathlib import Path
from chimera.memory.models import MemoryItem
from chimera.memory.store import MemoryStore

path, tag = Path(sys.argv[1]), sys.argv[2]
store = MemoryStore(path)
for i in range(40):
    store.add(MemoryItem(id=f"{tag}{i}", content="fact"))
"""


def test_two_real_processes_both_land(tmp_path: Path) -> None:
    """The configuration the fix is actually for: separate interpreters, separate file descriptors,
    genuine OS-level interleaving. The in-process tests cannot prove the *file* lock works, only the
    reload — a threading.Lock alone would pass those and still lose records here.
    """
    path = tmp_path / "memory.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    script = tmp_path / "writer.py"
    script.write_text(textwrap.dedent(_WRITER), encoding="utf-8")

    procs = [
        subprocess.Popen([sys.executable, str(script), str(path), tag], cwd=str(Path.cwd()))
        for tag in ("A", "B")
    ]
    for proc in procs:
        assert proc.wait(timeout=180) == 0

    assert len(_ids(path)) == 80


# --- the temp file ------------------------------------------------------------------------------


def test_a_temp_file_is_never_reused_between_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fixed ``<name>.tmp`` lets two writers that both got past the lock — the degraded path, or
    anything that never took it — interleave inside the same temp file, so the rename publishes a
    mixture of two serialisations rather than either one.

    Held still by stubbing out the rename: the temp files then accumulate instead of being consumed,
    and three writes have to leave three of them. With a shared name they leave one.
    """
    path = tmp_path / "memory.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(filelock, "retrying", lambda action: None)  # the rename never happens

    for payload in ("[]", '[{"a": 1}]', '[{"b": 2}]'):
        atomic_write_text(path, payload)

    leftovers = list(tmp_path.glob("*.tmp"))
    monkeypatch.undo()

    assert len(leftovers) == 3, f"temp file name was reused: {[p.name for p in leftovers]}"
    assert all(p.parent == tmp_path for p in leftovers), "os.replace is only atomic within a filesystem"


def test_no_temp_file_survives_an_ordinary_write(tmp_path: Path) -> None:
    path = tmp_path / "memory.json"
    store = MemoryStore(path)
    store.add(_item("a"))
    store.add(_item("b"))

    assert not list(tmp_path.glob("*.tmp"))


def test_a_failed_write_leaves_no_temp_file_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Otherwise a full disk seeds the directory with debris that the next `ls` reads as corruption
    — and the previous contents have to still be there, which is what the replace buys."""
    path = tmp_path / "memory.json"
    store = MemoryStore(path)
    store.add(_item("a"))

    def boom(src: object, dst: object) -> None:
        raise OSError("no space left on device")

    monkeypatch.setattr(filelock.os, "replace", boom)
    with pytest.raises(OSError):
        store.add(_item("b"))
    monkeypatch.undo()

    assert not list(tmp_path.glob("*.tmp"))
    assert _ids(path) == {"a"}, "the previous contents did not survive the failed write"


# --- what must not have changed -------------------------------------------------------------------


def test_the_lock_file_is_a_sibling_not_the_store(tmp_path: Path) -> None:
    """Locking the data file would be locking a descriptor that ``os.replace`` invalidates a moment
    later. And the lock must never end up parsed as memory."""
    path = tmp_path / "memory.json"
    MemoryStore(path).add(_item("a"))

    assert (tmp_path / "memory.json.lock").exists()
    assert _ids(path) == {"a"}


# --- the collision the lock cannot cover ---------------------------------------------------------


def test_a_busy_file_is_retried_and_then_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    """Readers take no lock, so on Windows a reader mid-``read_text`` makes an unrelated writer's
    ``os.replace`` raise ``PermissionError``. That is absorbed — but only for as long as a race
    plausibly lasts. A permission problem that outlives the retries is a real one and must surface,
    not turn into a hang or a silently skipped write.
    """
    monkeypatch.setattr(filelock, "RETRY_PAUSE", 0)
    calls = {"n": 0}

    def busy_twice() -> str:
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError("file in use")
        return "ok"

    assert filelock.retrying(busy_twice) == "ok"
    assert calls["n"] == 3

    def always_busy() -> str:
        raise PermissionError("genuinely not permitted")

    with pytest.raises(PermissionError):
        filelock.retrying(always_busy)


def test_a_reader_and_a_writer_do_not_take_each_other_down(tmp_path: Path) -> None:
    """The scenario in full: one thread writing while another reads as fast as it can.

    This is the shape the fix's own design creates — lock-free reads beside locked writes — so it
    is the one that has to hold. On Windows the pre-retry code raised ``PermissionError`` out of
    ``os.replace`` here; on POSIX it always passed, which is exactly why it needs asserting on both.
    """
    path = tmp_path / "memory.json"
    writer_store = MemoryStore(path)
    writer_store.add(_item("seed"))
    errors: list[BaseException] = []
    stop = threading.Event()

    def write() -> None:
        try:
            for i in range(60):
                writer_store.add(_item(f"w{i}"))
        except BaseException as exc:  # noqa: BLE001 - the point is to report it, not handle it
            errors.append(exc)
        finally:
            stop.set()

    def read() -> None:
        try:
            while not stop.is_set():
                assert MemoryStore(path).all(), "recall came back empty mid-write"
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=write), threading.Thread(target=read)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)

    assert not errors, f"reader/writer collision surfaced: {errors[0]!r}"
    assert len(_ids(path)) == 61


def test_reading_still_works_without_taking_a_lock(tmp_path: Path) -> None:
    """Reads stay lock-free on purpose: the atomic replace already guarantees a reader sees one
    complete version or the other. Making recall wait on a writer would be a real cost for a
    problem that does not exist."""
    path = tmp_path / "memory.json"
    store = MemoryStore(path)
    store.add(_item("a"))

    with open(str(path) + ".lock", "a+b") as held:
        filelock._acquire(held)
        try:
            assert MemoryStore(path).all()[0].id == "a"  # would hang if load() locked
        finally:
            filelock._release(held)


# --- how long a collision may last ---------------------------------------------------------------
#
# Found on 2026-10-01: the Windows CI job failed once in 40 runs on the reader/writer test above,
# with PermissionError out of `os.replace`. The retry budget was 20 attempts 10 ms apart, about
# 0.2 s in all. What holds a file open on Windows is not only our own reader mid-read: the
# antivirus scans a file the moment it is created or replaced, and a reader thread descheduled on a
# loaded machine keeps its handle for as long as it is not running. Either outlasts 0.2 s on a busy
# CI runner, and the write then failed outright, losing the memory being saved.


def test_a_file_busy_for_half_a_second_is_still_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Half a second of "in use", simulated so it holds on every platform: the write gets through."""
    path = tmp_path / "memory.json"
    store = MemoryStore(path)
    store.add(_item("a"))
    real_replace = filelock.os.replace
    busy_until = {"t": 0.0}

    def held_by_a_scanner(src: str, dst: object) -> None:
        if not busy_until["t"]:
            busy_until["t"] = time.monotonic() + 0.5
        if time.monotonic() < busy_until["t"]:
            raise PermissionError(13, "Access is denied")
        real_replace(src, dst)

    monkeypatch.setattr(filelock.os, "replace", held_by_a_scanner)

    store.add(_item("b"))

    assert _ids(path) == {"a", "b"}, "a file busy for half a second lost the write"


def test_a_permission_problem_that_does_not_clear_still_surfaces_in_bounded_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The longer budget is a wait, not a hang: a real permission problem is reported in seconds."""

    def always_busy() -> str:
        raise PermissionError("genuinely not permitted")

    started = time.monotonic()
    with pytest.raises(PermissionError):
        filelock.retrying(always_busy)
    assert time.monotonic() - started < 6.0, "a permanent error took too long to surface"


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows refuses to replace an open file")
def test_a_reader_holding_the_file_for_half_a_second_does_not_fail_the_writer(tmp_path: Path) -> None:
    """The real collision, on the platform that has it: a handle held open while another writes."""
    path = tmp_path / "memory.json"
    store = MemoryStore(path)
    store.add(_item("a"))
    opened = threading.Event()

    def hold() -> None:
        with open(path, "rb"):
            opened.set()
            time.sleep(0.5)

    holder = threading.Thread(target=hold)
    holder.start()
    assert opened.wait(5)
    try:
        store.add(_item("b"))
    finally:
        holder.join(5)

    assert _ids(path) == {"a", "b"}
