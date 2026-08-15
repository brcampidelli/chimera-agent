"""The two logs the agent learns from, and the ways they used to lose everything at once.

``experience.json`` and ``trajectories.jsonl`` are the only places an autonomous run's history
outlives the process. Both were written the direct way, and both shared a failure family the
memory store next door had already been fixed for: **data disappears and nothing complains**.

Three shapes, in descending order of how much they cost:

1. ``ExperienceBuffer.save`` was a bare ``write_text``, which truncates before it fills. It runs
   from ``AutonomousLoop`` after every attempt, so a kill at the wrong moment leaves a file that is
   neither the old contents nor the new — and because the buffer is one JSON array, that is *every*
   lesson, not the last one.
2. Both loaders validated without a guard. One record with a field from another version made the
   whole history unreadable, and the failure surfaced in the ``chimera evolve`` commands you would
   reach for to look at it.
3. Neither had a ceiling, on disk or in memory, and ``relevant()`` scores every entry on every
   planning step.

Each test below fails against the pre-fix implementation. The ordering here follows the cost, not
the effort.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chimera.ecosystem.trajectory import TrajectoryCollector
from chimera.evolution.experience import ExperienceBuffer

# --- the whole file, gone -------------------------------------------------------------------------


def test_a_crash_mid_save_does_not_erase_every_lesson(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The one that costs the most. A non-atomic write that dies halfway leaves a truncated JSON
    array — unparseable — where forty attempts' worth of history used to be."""
    import chimera.core.filelock as filelock

    path = tmp_path / "experience.json"
    buffer = ExperienceBuffer(path)
    for i in range(40):
        buffer.record(f"task {i}", "success" if i % 2 else "failure")
    before = path.read_text(encoding="utf-8")

    def die(src: object, dst: object) -> None:
        raise OSError("killed mid-write")

    monkeypatch.setattr(filelock.os, "replace", die)
    with pytest.raises(OSError):
        buffer.record("the attempt that crashes", "failure")
    monkeypatch.undo()

    assert path.read_text(encoding="utf-8") == before, "the buffer was damaged by a failed write"
    assert len(ExperienceBuffer(path)) == 40
    assert not list(tmp_path.glob("*.tmp"))


def test_the_buffer_file_is_never_opened_for_truncation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The structural guarantee behind the test above, asserted directly.

    A mid-write crash cannot be injected portably — the corruption happens inside a syscall. But the
    property that makes it impossible can be: the live file is only ever *renamed onto*, never
    opened in a mode that truncates it. ``write_text`` opens it ``"w"``, which empties it before a
    single byte of the new contents exists, and that is the whole bug in one flag.

    Both ``io.open`` and ``builtins.open`` are watched, and the first draft of this test watched
    only the second — so it passed against the very implementation it exists to catch, because
    ``pathlib`` reaches ``io.open`` by its own reference.
    """
    import io

    path = tmp_path / "experience.json"
    ExperienceBuffer(path).record("first", "success")
    opened: list[tuple[str, str]] = []
    real_open = io.open

    def watch(file: object, mode: str = "r", *args: object, **kwargs: object) -> object:
        opened.append((str(file), mode))
        return real_open(file, mode, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(io, "open", watch)
    monkeypatch.setattr("builtins.open", watch)
    ExperienceBuffer(path).record("second", "success")
    monkeypatch.undo()

    truncating = [
        (name, mode)
        for name, mode in opened
        if Path(name) == path and ("w" in mode or "+" in mode)
    ]
    assert not truncating, f"the live buffer was opened in a truncating mode: {truncating}"
    assert len(ExperienceBuffer(path)) == 2


def test_a_second_writer_does_not_erase_the_first_buffer(tmp_path: Path) -> None:
    """``chimera serve`` runs the autonomous work in one process; an operator can run ``chimera`` in
    another. Whichever wrote second used to republish a snapshot from before the other started."""
    path = tmp_path / "experience.json"
    first = ExperienceBuffer(path)
    second = ExperienceBuffer(path)

    first.record("built the thing", "success")
    second.record("broke the other thing", "failure")

    assert {e.task for e in ExperienceBuffer(path).all()} == {
        "built the thing",
        "broke the other thing",
    }


# --- one bad record ---------------------------------------------------------------------------


def test_one_malformed_experience_does_not_hide_the_rest(tmp_path: Path) -> None:
    """Skip the record, keep the history, and *count* the skip — reporting 2 as though that were
    all there ever was is how a silent loss becomes a wrong conclusion."""
    path = tmp_path / "experience.json"
    path.write_text(
        json.dumps(
            [
                {"seq": 0, "task": "good one", "outcome": "success"},
                {"seq": 1, "task": "from a later version", "outcome": "maybe", "extra": 1},
                {"seq": 2, "task": "another good one", "outcome": "failure"},
            ]
        ),
        encoding="utf-8",
    )

    buffer = ExperienceBuffer(path)

    assert [e.task for e in buffer.all()] == ["good one", "another good one"]
    assert buffer.skipped == 1


def test_a_file_that_is_not_json_reports_empty_rather_than_refusing_to_start(tmp_path: Path) -> None:
    """The alternative is an autonomous loop that will not boot because of a file it only reads."""
    path = tmp_path / "experience.json"
    path.write_text("{ this was hand-edited and left brok", encoding="utf-8")

    buffer = ExperienceBuffer(path)

    assert buffer.all() == []
    buffer.record("carry on", "success")
    assert len(ExperienceBuffer(path)) == 1


def test_one_malformed_trajectory_does_not_take_the_history_with_it(tmp_path: Path) -> None:
    """Same shape, other file. A process killed between the write and the newline leaves exactly
    this, and every ``chimera evolve`` command used to raise on it."""
    path = tmp_path / "traj.jsonl"
    good = '{"seq": 0, "prompt": "p", "response": "r", "outcome": "success"}'
    truncated = '{"seq": 1, "prompt": "p2", "resp'
    later = '{"seq": 2, "prompt": "p3", "response": "r3", "outcome": "failure"}'
    path.write_text("\n".join([good, truncated, later]) + "\n", encoding="utf-8")

    collector = TrajectoryCollector(path)

    assert [t.prompt for t in collector.all()] == ["p", "p3"]
    assert collector.skipped == 1


def test_a_blank_line_is_not_counted_as_damage(tmp_path: Path) -> None:
    """A trailing newline is normal. Reporting it as a skipped record would make every healthy file
    look slightly corrupt, and a warning nobody can act on is one nobody reads."""
    path = tmp_path / "traj.jsonl"
    path.write_text(
        '{"seq": 0, "prompt": "p", "response": "r"}\n\n\n', encoding="utf-8"
    )

    collector = TrajectoryCollector(path)

    assert len(collector) == 1
    assert collector.skipped == 0


# --- the ceilings -------------------------------------------------------------------------------


def test_the_experience_buffer_stops_growing(tmp_path: Path) -> None:
    """``relevant()`` scores every entry on every planning step, so this is a latency ceiling as
    much as a memory one."""
    path = tmp_path / "experience.json"
    buffer = ExperienceBuffer(path, max_items=10)
    for i in range(25):
        buffer.record(f"task {i}", "success")

    assert len(buffer) == 10
    assert [e.task for e in buffer.all()][0] == "task 15", "the wrong end was dropped"
    assert len(json.loads(path.read_text(encoding="utf-8"))) == 10


def test_dropping_the_head_does_not_reissue_a_sequence_number(tmp_path: Path) -> None:
    """``relevant()`` breaks ties on ``seq`` to prefer newer attempts. Numbering from the list
    length restarts once the head is dropped, so an old attempt starts winning that tiebreak."""
    path = tmp_path / "experience.json"
    buffer = ExperienceBuffer(path, max_items=5)
    for i in range(12):
        buffer.record(f"task {i}", "success")

    seqs = [e.seq for e in buffer.all()]

    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs), f"seq reused: {seqs}"
    assert seqs[-1] == 11
    assert ExperienceBuffer(path, max_items=5).record("next", "success").seq == 12


def test_the_trajectory_log_keeps_only_the_tail_in_memory(tmp_path: Path) -> None:
    """The prefix stays on disk — this is about what a long-lived process carries, not about
    deleting history."""
    path = tmp_path / "traj.jsonl"
    collector = TrajectoryCollector(path, max_resident=8)
    for i in range(30):
        collector.record(f"p{i}", "r")

    assert len(collector) == 8
    assert collector.all()[-1].prompt == "p29"
    on_disk = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(on_disk) == 30, "the log itself was truncated; only memory should be capped"

    assert TrajectoryCollector(path, max_resident=8).record("next", "r").seq == 30


def test_the_trajectory_log_rotates_instead_of_growing_without_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rename, not trim: rewriting the file to drop old lines has to read all of it, and a crash
    mid-rewrite loses the whole history instead of its oldest part."""
    import chimera.ecosystem.trajectory as module

    monkeypatch.setattr(module, "MAX_TRAJECTORY_BYTES", 200)
    path = tmp_path / "traj.jsonl"
    collector = TrajectoryCollector(path)
    for i in range(12):
        collector.record(f"prompt number {i}", "a response of some length")

    assert (tmp_path / "traj.jsonl.1").exists(), "nothing was rotated"
    assert path.exists() and path.stat().st_size < 400
