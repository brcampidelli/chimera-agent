"""What a trace line has to carry to be worth writing, and what it must never carry.

The trace was keyed by a truncated task plus a stop reason. That collides exactly where the file is
used: a bench runs the same task dozens of times, one run with three attempts writes three
indistinguishable lines, and every downstream question — how did success vary with context size,
what did this cron job actually do at 3 a.m., which attempt produced the bad diff — needs to tell two
runs apart before it can be asked at all.

The other half is that this file now gets written on the 24/7 path, where the tool text came from a
broker, a database and a payment processor. Redaction and the size cap are here rather than in a
later commit because they are properties of putting a trace on disk, not of producing one.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chimera.core.redact import MASK, redact
from chimera.core.steplog import MAX_TRACE_BYTES, StepLog, StepRecord


def _log_with_step(observation: str = "fine", arguments: str = "{}") -> StepLog:
    from chimera.core.steplog import ToolRecord

    log = StepLog()
    log.add(
        StepRecord(
            index=1,
            prompt_tokens=100,
            completion_tokens=10,
            model="m",
            tools=[ToolRecord(name="t", arguments=arguments, observation=observation, ok=True)],
        )
    )
    return log


def _lines(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# --- identity ----------------------------------------------------------------------------------


def test_two_runs_of_the_same_task_are_distinguishable(tmp_path: Path) -> None:
    """The collision the old key produced. A bench repeats one task; without an id the lines are the
    same line twice, and nothing can be attributed to either run."""
    path = tmp_path / "traces.jsonl"

    first = _log_with_step().write(path, task="fix the bug", stopped_reason="final")
    second = _log_with_step().write(path, task="fix the bug", stopped_reason="final")

    ids = [row["run_id"] for row in _lines(path)]
    assert first != second
    assert ids == [first, second]
    assert len(set(ids)) == 2


def test_every_line_is_timestamped(tmp_path: Path) -> None:
    # Ordering inside a file is not a time: an appended log survives restarts and copies, and "when"
    # is the first question asked of any 24/7 trace.
    path = tmp_path / "traces.jsonl"

    _log_with_step().write(path, task="t", stopped_reason="final")

    assert _lines(path)[0]["ts"].startswith("20")


def test_a_caller_can_supply_the_id_it_already_has(tmp_path: Path) -> None:
    # So a receipt, a usage row and a trace line can share one key rather than three.
    path = tmp_path / "traces.jsonl"

    returned = _log_with_step().write(path, task="t", stopped_reason="final", run_id="abc123")

    assert returned == "abc123"
    assert _lines(path)[0]["run_id"] == "abc123"


# --- redaction ---------------------------------------------------------------------------------


def test_a_configured_secret_never_reaches_the_file(tmp_path: Path, monkeypatch: Any) -> None:
    """The guarantee this can actually give: a secret THIS process knows about is replaced verbatim.

    On the cron path the observation is whatever a broker or a payment API returned, and the file
    outlives the incident by weeks.
    """
    monkeypatch.setenv("SOME_PROVIDER_API_KEY", "super-secret-value-1234")
    path = tmp_path / "traces.jsonl"

    _log_with_step(observation="auth ok with super-secret-value-1234").write(
        path, task="t", stopped_reason="final"
    )

    body = path.read_text(encoding="utf-8")
    assert "super-secret-value-1234" not in body
    assert MASK in body


def test_the_task_itself_is_redacted_too(tmp_path: Path, monkeypatch: Any) -> None:
    # A job's action is written by a person and is exactly where a token gets pasted "just once".
    monkeypatch.setenv("X_TOKEN", "paste-me-nowhere-9999")
    path = tmp_path / "traces.jsonl"

    _log_with_step().write(path, task="call the API with paste-me-nowhere-9999", stopped_reason="final")

    assert "paste-me-nowhere-9999" not in path.read_text(encoding="utf-8")


def test_a_short_env_value_does_not_mask_the_whole_file(monkeypatch: Any) -> None:
    """`CI=1` is an env var whose name ends in nothing suspicious, but plenty of credential-named
    vars hold short placeholders. Redacting an 8-character floor keeps a `1` from erasing every
    digit in the trace — which would make the file useless and the redaction look thorough."""
    monkeypatch.setenv("SHORT_TOKEN", "1")

    assert redact("step 1 of 3") == "step 1 of 3"


def test_credential_shapes_are_caught_without_being_configured() -> None:
    # The second net, for secrets this process never held. Narrow on purpose.
    assert "ghp_" not in redact("token ghp_abcdefghijklmnopqrstuvwxyz0123")
    assert redact("a normal sentence about a key") == "a normal sentence about a key"


def test_redaction_prefers_the_longest_secret(monkeypatch: Any) -> None:
    """A base token and the same token plus a suffix: masking the short one first leaves the tail
    behind, which reads as redacted and is not."""
    monkeypatch.setenv("A_TOKEN", "abcdefgh")
    monkeypatch.setenv("B_TOKEN", "abcdefgh-with-suffix")

    assert "with-suffix" not in redact("value abcdefgh-with-suffix here")


# --- the size cap ------------------------------------------------------------------------------


def test_the_trace_rotates_instead_of_growing_forever(tmp_path: Path, monkeypatch: Any) -> None:
    """A daemon runs for weeks. An uncapped trace stops being a diagnostic and becomes the reason
    the disk is full — and the first symptom is a daemon that can no longer write anything."""
    monkeypatch.setattr("chimera.core.steplog.MAX_TRACE_BYTES", 200)
    path = tmp_path / "traces.jsonl"

    for _ in range(6):
        _log_with_step().write(path, task="t" * 50, stopped_reason="final")

    assert path.with_suffix(".jsonl.1").exists(), "nothing was rotated aside"
    assert path.stat().st_size < 200 * 4, "the live file kept growing past the cap"


def test_the_cap_is_a_real_number() -> None:
    # Guards against a future edit that sets it to something a single run could exceed.
    assert MAX_TRACE_BYTES >= 1024 * 1024
