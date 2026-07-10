"""Tests for TraceProbe anti-pattern detectors (arXiv 2607.06184)."""

from __future__ import annotations

from chimera.evolution.trace_probe import anti_pattern_hint, detect_anti_patterns


def _ev(*tools: str) -> list[dict[str, object]]:
    return [{"tool": t, "ok": True} for t in tools]


def test_search_loop_flagged() -> None:
    events = _ev("read_file", "grep", "list_dir", "search", "read_file")
    kinds = {p.kind for p in detect_anti_patterns(events)}
    assert "search-loop" in kinds


def test_search_run_broken_by_edit_is_not_a_loop() -> None:
    # read, read, EDIT, read, read — no run reaches the threshold of 4.
    events = _ev("read_file", "grep", "edit_file", "read_file", "list_dir")
    kinds = {p.kind for p in detect_anti_patterns(events)}
    assert "search-loop" not in kinds


def test_verification_skip_flagged() -> None:
    events = _ev("read_file", "write_file", "edit_file")  # wrote, never checked
    kinds = {p.kind for p in detect_anti_patterns(events)}
    assert "verification-skip" in kinds


def test_write_then_check_is_not_a_skip() -> None:
    events = _ev("edit_file", "run_shell", "pytest")  # wrote then verified
    kinds = {p.kind for p in detect_anti_patterns(events)}
    assert "verification-skip" not in kinds


def test_clean_trace_has_no_anti_patterns() -> None:
    events = _ev("read_file", "edit_file", "run_shell")
    assert detect_anti_patterns(events) == []
    assert anti_pattern_hint(events) == ""


def test_empty_trace_is_clean() -> None:
    assert detect_anti_patterns([]) == []
    assert anti_pattern_hint([]) == ""


def test_other_steps_are_neutral_in_search_run() -> None:
    # 'calc' (other) between reads neither extends nor breaks; four reads still reach the threshold.
    events = _ev("read_file", "calc", "grep", "search", "http_get")
    kinds = {p.kind for p in detect_anti_patterns(events)}
    assert "search-loop" in kinds


def test_hint_lists_detected_patterns() -> None:
    events = _ev("read_file", "grep", "search", "list_dir", "write_file")
    hint = anti_pattern_hint(events)
    assert "search-loop" in hint and "verification-skip" in hint
    assert hint.startswith("Process check")
