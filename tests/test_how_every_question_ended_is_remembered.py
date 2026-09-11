"""A configured approver whose questions nobody answers is no approver, and nothing said so.

`ask_durably` wrote the question and, on resolution, deleted it — together with the answer file that
was the only evidence of how long a person had taken. So a deployment could not be asked the two
numbers that decide whether the mechanism is worth anything: how often somebody answers, and how
fast. A night with nobody on call produced a run of refusals indistinguishable from careful ones,
and the block rate read perfect throughout (the point made under the LLMDevs post of 2026-09-11).

Every resolution now appends one line to ``approvals/history.jsonl``: the outcome in four values
(``timeout`` kept apart from ``refused``), the seconds the person took by *their* clock, and the
seconds the run waited. ``chimera approve`` prints the rate and the wait.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chimera.governance import pending
from chimera.interface.render import approval_stats_line


class _Clock:
    """A monotonic clock the test advances by hand, so a timeout costs no wall time."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def _ask(home: Path, clock: _Clock, *, on_asked: Any = None, wait: float = 30.0) -> bool:
    return pending.ask_durably(
        home, "run_shell: rm -rf build", "tainted run", wait_seconds=wait, poll_seconds=1.0,
        clock=clock, sleep=clock.sleep, on_asked=on_asked,
    )


def _answer_when_asked(home: Path, approved: bool, delay: float = 0.0) -> Any:
    """Answer the question the moment it is announced, as a person at a screen would."""

    def on_asked(question: pending.PendingApproval) -> None:
        assert pending.answer(home, question.id, approved)
        if delay:
            path = pending._dir(home) / f"{question.id}.answer.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["answered_at"] = question.asked_at + delay
            path.write_text(json.dumps(data), encoding="utf-8")

    return on_asked


def test_an_answered_question_leaves_its_outcome_and_the_persons_time(tmp_path: Path) -> None:
    clock = _Clock()
    assert _ask(tmp_path, clock, on_asked=_answer_when_asked(tmp_path, True, delay=42.0)) is True

    rows = pending.history(tmp_path)
    assert [r["outcome"] for r in rows] == ["approved"]
    assert rows[0]["seconds_to_answer"] == 42.0, "the person's clock, not the poller's"
    assert rows[0]["action"] == "run_shell: rm -rf build"
    # The question files themselves are still cleaned up — only the record persists.
    assert not list((tmp_path / "approvals").glob("*.ask.json"))
    assert not list((tmp_path / "approvals").glob("*.answer.json"))


def test_a_timeout_is_recorded_as_a_timeout_not_as_a_refusal(tmp_path: Path) -> None:
    """The distinction this file exists for: nobody reachable is not somebody saying no."""
    clock = _Clock()
    assert _ask(tmp_path, clock, wait=30.0) is False

    rows = pending.history(tmp_path)
    assert [r["outcome"] for r in rows] == ["timeout"]
    assert rows[0]["seconds_to_answer"] is None
    assert rows[0]["waited_seconds"] >= 0.0


def test_a_no_from_a_person_is_a_refusal(tmp_path: Path) -> None:
    clock = _Clock()
    assert _ask(tmp_path, clock, on_asked=_answer_when_asked(tmp_path, False)) is False
    assert [r["outcome"] for r in pending.history(tmp_path)] == ["refused"]


def test_the_stats_separate_answered_from_timed_out(tmp_path: Path) -> None:
    clock = _Clock()
    _ask(tmp_path, clock, on_asked=_answer_when_asked(tmp_path, True, delay=10.0))
    _ask(tmp_path, clock, on_asked=_answer_when_asked(tmp_path, False, delay=30.0))
    _ask(tmp_path, clock, wait=5.0)  # nobody

    stats = pending.answer_stats(tmp_path)
    assert stats["asked"] == 3
    assert stats["answered"] == 2
    assert stats["timeouts"] == 1
    assert stats["answer_rate"] == 2 / 3
    assert stats["p50_seconds"] == 10.0 and stats["p90_seconds"] == 30.0
    assert stats["max_seconds"] == 30.0


def test_a_home_that_never_asked_reports_the_fact_not_a_rate(tmp_path: Path) -> None:
    stats = pending.answer_stats(tmp_path)
    assert stats["asked"] == 0 and stats["answer_rate"] is None and stats["p50_seconds"] is None
    assert "no question has been asked" in approval_stats_line(stats)


def test_the_line_a_person_reads_names_both_numbers(tmp_path: Path) -> None:
    clock = _Clock()
    _ask(tmp_path, clock, on_asked=_answer_when_asked(tmp_path, True, delay=12.0))
    _ask(tmp_path, clock, wait=5.0)

    line = approval_stats_line(pending.answer_stats(tmp_path))
    assert "1 of 2 question(s) answered (50%)" in line
    assert "1 timed out into a refusal" in line
    assert "p50 12s" in line


def test_a_record_that_cannot_be_written_does_not_change_the_decision(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The decision stands either way; only the record is lost, and it is lost loudly."""
    clock = _Clock()
    real_open = Path.open

    def failing_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.name == pending.HISTORY:
            raise OSError("disk full")
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", failing_open)
    assert _ask(tmp_path, clock, on_asked=_answer_when_asked(tmp_path, True)) is True
    assert pending.history(tmp_path) == []
