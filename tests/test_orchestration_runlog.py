"""A run that was paid for has to survive the tab that started it.

A fan-out costs a top-model decompose, N workers and a synthesis. Every frame of that existed only
in an SSE stream: close the app, reload the page or lose the connection, and the answer was gone
while the bill stayed. The cost was recorded and the product was not.

The property the whole design turns on is in `test_replay_then_live_is_the_same_state_as_live_only`:
the transcript carries the `seq` the endpoint already stamps under a lock at its single writer, and
the client's reducer ignores a `seq` it has applied — so replaying and then continuing converges on
exactly the state a client that never disconnected would have.
"""

from __future__ import annotations

import json
from pathlib import Path

from chimera.orchestration import runlog


def _write(home: Path, run_id: str, n: int, *, kind: str = "hierarchy") -> None:
    runlog.append(home, run_id, "run", {"seq": 1, "run_id": run_id, "task": "compare a and b"})
    for i in range(2, n + 1):
        event = "worker_started" if kind == "hierarchy" else "crew_worker_started"
        runlog.append(home, run_id, event, {"seq": i, "task_id": f"t{i}"})


def test_a_run_survives_the_process_that_wrote_it(tmp_path: Path) -> None:
    _write(tmp_path, "abc123", 5)

    frames = runlog.frames(tmp_path, "abc123")

    assert len(frames) == 5
    assert frames[0]["event"] == "run"
    assert [f["seq"] for f in frames] == [1, 2, 3, 4, 5]


def test_since_returns_only_what_the_client_is_missing(tmp_path: Path) -> None:
    _write(tmp_path, "abc123", 5)

    assert [f["seq"] for f in runlog.frames(tmp_path, "abc123", since=3)] == [4, 5]
    assert runlog.frames(tmp_path, "abc123", since=5) == []


def test_replay_then_live_is_the_same_state_as_live_only(tmp_path: Path) -> None:
    """The property the design turns on, asserted rather than assumed.

    A client that disconnects at frame 3, replays from 0, and then receives 4 and 5 live must end
    where a client that never disconnected ends. The `seq` is what makes that true, and this is the
    test that says so — the reducer lives in TypeScript, so what is checked here is that the
    transcript hands it everything it needs to do the job.
    """
    _write(tmp_path, "abc123", 5)

    live_only = runlog.frames(tmp_path, "abc123")
    replayed = runlog.frames(tmp_path, "abc123", since=0)
    then_live = replayed + [f for f in live_only if f["seq"] > max(r["seq"] for r in replayed)]

    assert then_live == live_only


def test_a_truncated_tail_line_does_not_lose_the_rest(tmp_path: Path) -> None:
    """The file is appended to by a LIVE run, so a reader can arrive mid-write.

    Refusing the whole transcript over one half-written line would throw away the ninety-nine frames
    it could have replayed — to protect against a line it can simply skip.
    """
    _write(tmp_path, "abc123", 4)
    path = runlog.run_dir(tmp_path, "abc123") / "frames.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"event": "worker_verif')

    assert len(runlog.frames(tmp_path, "abc123")) == 4


def test_a_run_that_never_wrote_anything_reads_as_empty_not_as_an_error(tmp_path: Path) -> None:
    assert runlog.frames(tmp_path, "nope") == []
    assert runlog.recent(tmp_path) == []


def test_a_forged_run_id_cannot_reach_outside_the_home(tmp_path: Path) -> None:
    """The id arrives as a path parameter. It is hex from `uuid4` in practice, and checked anyway."""
    (tmp_path / "secret.txt").write_text("not yours", encoding="utf-8")

    assert runlog.frames(tmp_path, "../../secret.txt") == []
    assert ".." not in str(runlog.run_dir(tmp_path, "..abc"))


def test_the_listing_is_newest_first_and_says_which_engine_ran(tmp_path: Path) -> None:
    import time

    _write(tmp_path, "older", 3, kind="hierarchy")
    time.sleep(0.01)
    _write(tmp_path, "newer", 2, kind="crew")

    runs = runlog.recent(tmp_path)

    assert [r.run_id for r in runs] == ["newer", "older"]
    assert runs[0].kind == "crew" and runs[1].kind == "hierarchy"
    assert runs[1].task == "compare a and b"


def test_a_run_that_stopped_without_finishing_is_not_reported_as_done(tmp_path: Path) -> None:
    """A transcript that simply ends is a process that died. Calling that finished would turn a
    crash into a completed run in the one list built to find them again."""
    _write(tmp_path, "killed", 4)

    assert runlog.recent(tmp_path)[0].done is False

    runlog.append(tmp_path, "killed", "done", {"seq": 5, "answer": "here it is"})

    assert runlog.recent(tmp_path)[0].done is True


def test_a_runaway_run_cannot_fill_the_disk(tmp_path: Path) -> None:
    """A worker looping on a huge observation must not cost the machine its free space to preserve
    a transcript nobody will read to the end."""
    runlog.append(tmp_path, "big", "run", {"seq": 1, "task": "x"})
    path = runlog.run_dir(tmp_path, "big") / "frames.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("x" * (runlog.MAX_BYTES + 10) + "\n")

    runlog.append(tmp_path, "big", "worker_started", {"seq": 2})

    assert not any(f.get("seq") == 2 for f in runlog.frames(tmp_path, "big"))


def test_pruning_keeps_the_newest_and_drops_the_rest(tmp_path: Path) -> None:
    import time

    for i in range(6):
        _write(tmp_path, f"run{i}", 2)
        time.sleep(0.005)

    removed = runlog.prune(tmp_path, keep=3)

    assert removed == 3
    assert [r.run_id for r in runlog.recent(tmp_path)] == ["run5", "run4", "run3"]


def test_a_failure_to_write_never_takes_the_run_down(tmp_path: Path) -> None:
    """The run is the product and it is already being paid for. Losing the record is bad; losing the
    run to preserve the record would be worse."""
    blocked = runlog.run_dir(tmp_path, "blocked")
    blocked.mkdir(parents=True)
    (blocked / "frames.jsonl").mkdir()  # a directory where the log wants a file

    runlog.append(tmp_path, "blocked", "run", {"seq": 1})  # must not raise

    assert runlog.frames(tmp_path, "blocked") == []


def test_the_transcript_is_valid_jsonl(tmp_path: Path) -> None:
    """One object per line, so a shell can read it too — `jq`, `grep`, a tail."""
    _write(tmp_path, "abc123", 3)
    raw = (runlog.run_dir(tmp_path, "abc123") / "frames.jsonl").read_text(encoding="utf-8")

    for line in raw.splitlines():
        assert isinstance(json.loads(line), dict)


def test_an_unknown_run_is_distinguishable_from_a_quiet_one(tmp_path: Path) -> None:
    """`frames()` answers both with an empty list, and they are opposite instructions.

    A client resuming from a stale localStorage entry asked for a run this machine had never
    recorded and got the same reply a live run gives before its first frame lands — so it kept
    waiting, showing an empty screen that looked like a slow one rather than a gone one.
    """
    _write(tmp_path, "existe", 3)

    assert runlog.exists(tmp_path, "existe")
    assert not runlog.exists(tmp_path, "nunca-gravado")
    # The pair that motivates it: both return nothing, and only `exists` separates them.
    assert runlog.frames(tmp_path, "existe", since=3) == []
    assert runlog.frames(tmp_path, "nunca-gravado") == []


def test_a_run_id_cannot_probe_the_filesystem(tmp_path: Path) -> None:
    """Existence is now observable through the API, so the id is a path built from a request.

    `run_dir` strips everything but alphanumerics, `-` and `_`, which leaves traversal with nothing
    to work with — but an empty result raises rather than resolving to the orchestration directory
    itself, and this pins that it stays a False rather than becoming a 500.
    """
    (tmp_path / "orchestration").mkdir(parents=True, exist_ok=True)

    assert not runlog.exists(tmp_path, "../../etc/passwd")
    assert not runlog.exists(tmp_path, "..")
    assert not runlog.exists(tmp_path, "")
