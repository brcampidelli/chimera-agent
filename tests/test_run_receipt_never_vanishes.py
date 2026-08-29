"""A run that cost money must leave a row saying so, even when its proof trail cannot be serialised.

Found by `bench/fusion_paired`: on one task, three solves in a row wrote **no row at all** to
`runs.jsonl`, and two of them had SUCCEEDED — the workspace still held the written module and its
test passed on a fresh check. `_persist_receipt` wrapped the whole write in `except Exception` and
logged at `debug`, so the loss was invisible at every level a user or a bench would ever look at.

It was also invisible in a way that biases: receipts vanished **by task** rather than at random, so
every consumer of these rows — the Cost screen included — undercounted systematically. A cost that
reads zero looks exactly like a cheap run.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from chimera.core.autonomous import AutonomousAgent


@dataclass
class _Attempt:
    """The fields `build_receipt` reads. The ones it reads DIRECTLY rather than through `getattr`
    are the interesting half: `verified`, `reverted`, `verify_output`, `diff_summary` and `feedback`
    have no default there, so an attempt missing any of them takes the whole receipt down with it."""

    index: int = 1
    verified: bool = True
    reverted: bool = False
    verify_output: str = "1 passed"
    diff_summary: str = "diff: +1 new"
    feedback: str = ""
    diffs: list = field(default_factory=list)
    success: bool = True
    prompt_tokens: int = 4321
    completion_tokens: int = 210
    usd: float | None = 0.05
    model: str = "openrouter/anthropic/claude-opus-5"


@dataclass
class _Result:
    """Likewise for the result: `paused` and `answer` are read directly by the builder."""

    success: bool = True
    paused: bool = False
    answer: str = "done"
    attempts: list[_Attempt] = field(default_factory=lambda: [_Attempt()])


def _agent(tmp_path: Path) -> Any:
    """An agent shell with only the fields `_persist_receipt` touches.

    Built with `__new__` rather than a real constructor on purpose: this file is about one method's
    failure path, and wiring a whole agent would make the test depend on everything else that method
    does not use.
    """
    agent = AutonomousAgent.__new__(AutonomousAgent)
    agent.run_log = tmp_path / "runs.jsonl"
    agent.workspace = tmp_path / "ws"
    agent.verifier = None
    agent.run_profile = None
    agent.verify_source = "user"
    agent.profile_source = "user"
    return agent


def _rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_a_receipt_that_cannot_be_built_still_records_the_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure that actually happened. `build_receipt` raises, and the run vanished entirely.

    The tokens are the part nothing else can reconstruct: the workspace survives a lost receipt and
    so does the answer, but what the run COST exists only here.
    """
    import chimera.api.runs as runs_module

    def _explode(*_args: object, **_kwargs: object) -> None:
        raise ValueError("a field would not serialise")

    monkeypatch.setattr(runs_module, "build_receipt", _explode)
    agent = _agent(tmp_path)

    agent._persist_receipt(_Result(), "make the thing work")

    rows = _rows(agent.run_log)
    assert len(rows) == 1, "the run left no trace at all"
    assert rows[0]["partial"] is True, "a fallback row must not pass for a complete one"
    assert rows[0]["attempts"][0]["prompt_tokens"] == 4321
    assert rows[0]["attempts"][0]["completion_tokens"] == 210
    assert rows[0]["workspace"].endswith("ws"), "without this the row cannot be joined to a cell"


def test_the_loss_is_reported_at_a_level_somebody_sees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """`debug` is what made this invisible for as long as it was. A bench that reads receipts and a
    user reading the Cost screen both see WARNING; neither sees debug."""
    import chimera.api.runs as runs_module

    monkeypatch.setattr(
        runs_module, "build_receipt", lambda *a, **k: (_ for _ in ()).throw(ValueError("nope"))
    )
    agent = _agent(tmp_path)

    with caplog.at_level("WARNING"):
        agent._persist_receipt(_Result(), "task")

    assert any("receipt" in record.message.lower() for record in caplog.records), caplog.text


def test_a_working_receipt_is_untouched(tmp_path: Path) -> None:
    """The fallback is a fallback. When the builder works, exactly one complete row is written and
    it carries no `partial` marker — otherwise every consumer would have to learn a second shape."""
    agent = _agent(tmp_path)

    agent._persist_receipt(_Result(), "task")

    rows = _rows(agent.run_log)
    assert len(rows) == 1
    assert "partial" not in rows[0]
    assert rows[0]["attempts"][0]["prompt_tokens"] == 4321


def test_a_fallback_that_itself_fails_does_not_break_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Persisting a receipt must never fail a run — that part of the original contract was right,
    and a fallback that can throw is not a fallback."""
    import chimera.api.runs as runs_module

    monkeypatch.setattr(
        runs_module, "build_receipt", lambda *a, **k: (_ for _ in ()).throw(ValueError("nope"))
    )
    agent = _agent(tmp_path)
    agent.run_log = tmp_path / "nested" / "runs.jsonl"
    monkeypatch.setattr(Path, "mkdir", lambda *a, **k: (_ for _ in ()).throw(OSError("read-only")))

    agent._persist_receipt(_Result(), "task")  # must not raise
