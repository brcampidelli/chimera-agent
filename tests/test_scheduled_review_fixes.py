"""The second review of the scheduled catch-up, history and worktree.

* a ``.pyc`` is junk only inside a cache folder and only when git does not track it;
* a commit already merged is work that is safe: the worktree goes and the branch goes with ``-d``;
* a failure while tidying never costs the run's outcome or masks its exception;
* a tick that took long is not an outage (the end of the tick is what is remembered);
* a history line with a field of the wrong shape cannot raise, and rotation does not run on every
  append.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from chimera.config import get_settings
from chimera.scheduler import Scheduler
from chimera.scheduler.daemon import CronDaemon, read_heartbeat
from chimera.scheduler.history import CronHistory, HistoryRow
from chimera.scheduler.job_worktree import run_in_job_worktree
from chimera.scheduler.models import CronJob, JobOutcome
from chimera.scheduler.store import CronStore


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd, capture_output=True, text=True, check=True,
    )
    return done.stdout


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIMERA_WORKTREE_DIR", str(tmp_path / "wts"))
    get_settings.cache_clear()
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    (root / "a.txt").write_text("one\n", encoding="utf-8")
    (root / "vendor").mkdir()
    (root / "vendor" / "lib.pyc").write_bytes(b"tracked")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _job() -> CronJob:
    return CronJob(id="j1", name="nightly", schedule="0 3 * * *", action="x", worktree=True)


def _hist(tmp_path: Path) -> CronHistory:
    return CronHistory(tmp_path / "history.jsonl")


# --------------------------------------------------------------------------- junk


def test_a_pyc_outside_a_cache_folder_is_work(repo: Path, tmp_path: Path) -> None:
    def run(root: Path) -> JobOutcome:
        (root / "dist").mkdir()
        (root / "dist" / "app.pyc").write_bytes(b"0")
        return JobOutcome("x")

    run_in_job_worktree(_job(), repo, _hist(tmp_path), run)

    rows = _hist(tmp_path).load()
    assert len(rows) == 1 and (Path(rows[0].worktree) / "dist" / "app.pyc").exists()


def test_a_tracked_pyc_is_never_deleted_by_the_cleaning(repo: Path, tmp_path: Path) -> None:
    """The repository versions `vendor/lib.pyc`; a cache folder holding a tracked file keeps it,
    and the tree is not made dirty by us."""
    (repo / "vendor" / "__pycache__").mkdir()
    (repo / "vendor" / "__pycache__" / "t.pyc").write_bytes(b"tracked too")
    _git(repo, "add", "-f", "-A")
    _git(repo, "commit", "-q", "-m", "tracked cache")

    def run(root: Path) -> JobOutcome:
        (root / "vendor" / "__pycache__" / "new.pyc").write_bytes(b"junk")
        return JobOutcome("x")

    run_in_job_worktree(_job(), repo, _hist(tmp_path), run)

    assert _hist(tmp_path).load() == [], "a worktree whose only change was a cache went unremoved"
    assert (repo / "vendor" / "__pycache__" / "t.pyc").exists()
    assert (repo / "vendor" / "lib.pyc").read_bytes() == b"tracked"


# --------------------------------------------------------------------------- merged


def test_a_commit_already_merged_lets_the_worktree_and_its_branch_go(
    repo: Path, tmp_path: Path
) -> None:
    argvs: list[list[str]] = []
    from chimera.core.pull_request import run_quiet

    def recording(argv, cwd):  # type: ignore[no-untyped-def]
        argvs.append(list(argv))
        return run_quiet(argv, cwd)

    seen: list[Path] = []

    def run(root: Path) -> JobOutcome:
        seen.append(root)
        branch = _git(root, "branch", "--show-current").strip()
        (root / "b.txt").write_text("new\n", encoding="utf-8")
        _git(root, "add", "-A")
        _git(root, "commit", "-q", "-m", "work")
        _git(repo, "merge", "--ff-only", branch)  # the work is on the owner's branch now
        return JobOutcome("merged")

    run_in_job_worktree(_job(), repo, _hist(tmp_path), run, runner=recording)

    assert _hist(tmp_path).load() == []
    assert not seen[0].exists()
    assert "chimera-cron" not in _git(repo, "branch")
    assert any("branch" in a and "-d" in a for a in argvs)
    assert all("-D" not in a and "--force" not in a for a in argvs)


# --------------------------------------------------------------------------- guarded tidying


def test_a_failing_tidy_step_does_not_lose_the_outcome(repo: Path, tmp_path: Path) -> None:
    from chimera.core.pull_request import run_quiet

    def failing_branch(argv, cwd):  # type: ignore[no-untyped-def]
        if "branch" in argv and "-d" in argv:
            raise OSError("disk says no")
        return run_quiet(argv, cwd)

    outcome = run_in_job_worktree(
        _job(), repo, _hist(tmp_path), lambda root: JobOutcome("answer"), runner=failing_branch
    )

    assert outcome.answer == "answer"


def test_a_failure_while_settling_does_not_mask_the_runs_own_exception(
    repo: Path, tmp_path: Path
) -> None:
    def broken_runner(argv, cwd):  # type: ignore[no-untyped-def]
        raise OSError("git vanished")

    def run(root: Path) -> JobOutcome:
        raise RuntimeError("the real failure")

    with pytest.raises(RuntimeError, match="the real failure"):
        run_in_job_worktree(_job(), repo, _hist(tmp_path), run, runner=broken_runner)


# --------------------------------------------------------------------------- a long tick


T0 = float(datetime(2026, 3, 10, 12, 0, tzinfo=UTC).timestamp())


class _Calls:
    def __init__(self) -> None:
        self.n = 0

    def __call__(self, job: CronJob) -> None:
        self.n += 1


def test_a_tick_that_took_forty_minutes_is_not_an_outage(tmp_path: Path) -> None:
    sched = Scheduler(CronStore(tmp_path / "scheduler" / "jobs.json"), jitter=False)
    job = sched.schedule_cron("five", "*/5 * * * *", "r", now=T0, catch_up="skip")
    due = job.next_run or 0.0
    start = due - 60.0
    calls = _Calls()
    times = iter([start, start + 40 * 60.0, start + 40 * 60.0 + 30.0, start + 40 * 60.0 + 31.0])
    daemon = CronDaemon(sched, calls, clock=lambda: next(times), sleep=lambda _s: None)

    daemon.tick()  # sequential jobs held it for 40 minutes: nothing due at its start
    daemon.tick()  # the next one starts 30 s after that, and the job is long overdue

    assert calls.n == 1, "skip discarded a job the live daemon was merely late with"
    beat = read_heartbeat(tmp_path / "scheduler" / "heartbeat.json")
    assert beat is not None


def test_the_heartbeat_carries_the_end_of_the_tick(tmp_path: Path) -> None:
    sched = Scheduler(CronStore(tmp_path / "scheduler" / "jobs.json"), jitter=False)
    times = iter([100.0, 100.0 + 2400.0])
    CronDaemon(sched, _Calls(), clock=lambda: next(times), sleep=lambda _s: None).tick()

    raw = json.loads((tmp_path / "scheduler" / "heartbeat.json").read_text(encoding="utf-8"))
    assert raw["at"] == 100.0 and raw["ended_at"] == 2500.0


# --------------------------------------------------------------------------- history robustness


def test_a_line_with_a_field_of_the_wrong_shape_is_skipped_not_raised(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    history = CronHistory(path)
    history.append(HistoryRow(at=1.0, job_id="j", name="j", outcome="ran", reason="on_time"))
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": "not-a-number", "job_id": "j", "outcome": "ran"}) + "\n")
        fh.write(json.dumps({"at": [1], "job_id": "j", "outcome": "ran"}) + "\n")
        fh.write(json.dumps({"at": 5, "job_id": "j", "outcome": "ran", "scheduled_for": "x"}) + "\n")
    history.append(HistoryRow(at=9.0, job_id="j", name="j", outcome="ran", reason="on_time"))

    rows = history.load(job_id="j")

    assert [r.at for r in rows] == [9.0, 5.0, 1.0]


def test_a_rotation_goes_well_below_the_limit_so_it_is_not_repeated(tmp_path: Path) -> None:
    path = tmp_path / "h.jsonl"
    limit = 30_000
    history = CronHistory(path, max_bytes=limit, keep_per_job=10_000)
    sizes: list[int] = []
    for i in range(400):
        history.append(HistoryRow(at=float(i), job_id="m", name="m", outcome="ran",
                                  reason="on_time", detail="x" * 100))
        sizes.append(path.stat().st_size)

    drops = [i for i in range(1, len(sizes)) if sizes[i] < sizes[i - 1]]
    assert drops, "the file was never rotated"
    assert sizes[drops[0]] <= limit * 0.75 + 400
    # Each rotation frees a quarter of the limit, so it can only come once per that many bytes.
    assert len(drops) <= int(400 * sizes[0] / (limit * 0.25)) + 1, "it rotated on nearly every append"
    assert max(sizes) <= limit + 400

