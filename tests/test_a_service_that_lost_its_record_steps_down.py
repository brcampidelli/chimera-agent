"""A service keeps its record true, and leaves when the record names another live service (S1, D6).

The record is how every client finds the service. If it vanishes, a client started later finds
nothing and starts a second one; if another live service now owns it, this one answers nobody the
file sends and should not keep running bots and jobs in the background. And on the way out it may
delete only its OWN record: deleting unconditionally erases a successor that already published.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from chimera.core.service_record import new_record, pid_alive, read_record, write_record
from chimera.core.service_watch import RecordWatch
from tests.service_harness import ServiceSandbox, wait_dead


def _watch(tmp_path: Path, alive: Callable[[int], bool] = pid_alive) -> tuple[RecordWatch, list[int]]:
    calls: list[int] = []
    record = new_record(url="http://127.0.0.1:1", token="t", home=tmp_path)
    watch = RecordWatch(record, lambda: calls.append(1), path=tmp_path / "service-dev.json", alive=alive)
    watch.publish()
    return watch, calls


def _someone_else(tmp_path: Path, pid: int) -> None:
    other = replace(new_record(url="http://127.0.0.1:2", token="u", home=tmp_path), pid=pid)
    write_record(other, tmp_path / "service-dev.json")


def test_a_deleted_record_is_written_back(tmp_path: Path) -> None:
    watch, calls = _watch(tmp_path)
    watch.path.unlink()

    assert watch.check_once() == "rewrote"
    assert read_record(watch.path) == watch.record and not calls


def test_a_record_naming_another_live_service_makes_this_one_step_down(tmp_path: Path) -> None:
    # pid 4242 stands in for the other service; only `alive` decides whether it runs.
    watch, calls = _watch(tmp_path, alive=lambda pid: pid == 4242)
    _someone_else(tmp_path, 4242)

    assert watch.check_once() == "stepped_down"
    assert calls == [1]
    assert read_record(watch.path).pid == 4242, "the successor's record was overwritten"  # type: ignore[union-attr]
    assert watch.check_once() == "stepped_down" and calls == [1], "stepped down twice"


def test_a_record_naming_a_dead_service_is_replaced(tmp_path: Path) -> None:
    watch, calls = _watch(tmp_path, alive=lambda pid: False)
    _someone_else(tmp_path, 4242)

    assert watch.check_once() == "rewrote" and not calls
    assert read_record(watch.path) == watch.record


def test_closing_deletes_only_its_own_record(tmp_path: Path) -> None:
    watch, _ = _watch(tmp_path, alive=lambda pid: True)
    _someone_else(tmp_path, 4242)
    watch.close()
    assert read_record(watch.path) is not None, "close erased a successor's record"

    mine, _ = _watch(tmp_path)
    mine.close()
    assert not mine.path.exists(), "close left its own record behind"


def test_the_watch_runs_on_its_own(tmp_path: Path) -> None:
    stepped = threading.Event()
    record = new_record(url="http://127.0.0.1:1", token="t", home=tmp_path)
    watch = RecordWatch(
        record, stepped.set, path=tmp_path / "service-dev.json", interval=0.05, alive=lambda pid: pid == 4242
    )
    watch.publish()
    watch.start()
    try:
        _someone_else(tmp_path, 4242)
        assert stepped.wait(5), "the watch never noticed the record changed hands"
    finally:
        watch.close()


def test_a_running_service_exits_when_another_live_service_takes_its_record(tmp_path: Path) -> None:
    box = ServiceSandbox(tmp_path)
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        box.start()
        record = box.record()
        assert record is not None, box.log()

        write_record(replace(record, id="f" * 32, pid=holder.pid), box.record_path)

        assert wait_dead(record.pid, 30), "the displaced service kept running"
        left = read_record(box.record_path)
        assert left is not None and left.pid == holder.pid, "the displaced service erased the new record"
    finally:
        holder.kill()
        box.record_path.unlink(missing_ok=True)
        box.close()
