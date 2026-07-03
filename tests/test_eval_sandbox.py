"""Tests for state-based, side-effect-aware grading (chimera.eval.sandbox, no network)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from chimera.eval.sandbox import StatefulTask, demo_stateful_tasks, run_stateful


class WriterRunner:
    """A fake agent that writes/deletes fixed files in the sandbox when run."""

    def __init__(self, ws: Path, writes: Mapping[str, str], deletes: Sequence[str] = ()) -> None:
        self.ws = Path(ws)
        self.writes = writes
        self.deletes = deletes

    def run(self, prompt: str) -> object:
        for name, content in self.writes.items():
            (self.ws / name).write_text(content, encoding="utf-8")
        for name in self.deletes:
            (self.ws / name).unlink(missing_ok=True)
        return "done"


def _create_report() -> list[StatefulTask]:
    return [demo_stateful_tasks()[0]]  # goal: report.txt contains DONE; allowed: report.txt


def _edit_target() -> list[StatefulTask]:
    return [demo_stateful_tasks()[1]]  # setup keep.txt+target.txt; allowed: target.txt


def test_clean_pass_has_no_side_effects(tmp_path: Path) -> None:
    report = run_stateful(lambda ws: WriterRunner(ws, {"report.txt": "DONE"}), _create_report(), tmp_path)
    outcome = report.outcomes[0]
    assert outcome.passed and not outcome.harmful
    assert report.summary() == {"tasks": 1.0, "pass_rate": 1.0, "side_effect_rate": 0.0}


def test_passed_but_harmful_write_is_flagged(tmp_path: Path) -> None:
    report = run_stateful(
        lambda ws: WriterRunner(ws, {"report.txt": "DONE", "stray.txt": "oops"}),
        _create_report(),
        tmp_path,
    )
    outcome = report.outcomes[0]
    assert outcome.passed  # the state goal was met
    assert outcome.harmful and "stray.txt" in outcome.side_effects  # but it damaged state


def test_missed_goal(tmp_path: Path) -> None:
    report = run_stateful(lambda ws: WriterRunner(ws, {}), _create_report(), tmp_path)
    assert not report.outcomes[0].passed


def test_clobbering_a_protected_file_is_a_side_effect(tmp_path: Path) -> None:
    # Updates target.txt (allowed) but also clobbers keep.txt (not allowed; setup wrote it).
    report = run_stateful(
        lambda ws: WriterRunner(ws, {"target.txt": "original\nUPDATED\n", "keep.txt": "clobbered"}),
        _edit_target(),
        tmp_path,
    )
    outcome = report.outcomes[0]
    assert outcome.harmful and "keep.txt" in outcome.side_effects
    assert not outcome.passed  # goal requires keep.txt intact


def test_allowed_edit_alone_passes_cleanly(tmp_path: Path) -> None:
    report = run_stateful(
        lambda ws: WriterRunner(ws, {"target.txt": "original\nUPDATED\n"}), _edit_target(), tmp_path
    )
    outcome = report.outcomes[0]
    assert outcome.passed and not outcome.harmful


def test_empty_report_summary(tmp_path: Path) -> None:
    report = run_stateful(lambda ws: WriterRunner(ws, {}), [], tmp_path)
    assert report.summary() == {"tasks": 0.0}
