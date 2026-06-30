"""Tests for step-level failure attribution (SkillAdaptor)."""

from __future__ import annotations

from typing import Any

from chimera.evolution import Fault, attribute, localize_fault, qualify


def _assistant_call(tool: str) -> dict[str, Any]:
    return {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": tool}}]}


def test_localize_finds_first_failed_tool_step() -> None:
    transcript = [
        {"role": "user", "content": "do it"},
        _assistant_call("read_file"),
        {"role": "tool", "content": "ok: contents"},
        _assistant_call("run_shell"),
        {"role": "tool", "content": "error: command exited 1"},
        _assistant_call("write_file"),
        {"role": "tool", "content": "error: too late"},
    ]
    fault = localize_fault(transcript)
    assert fault is not None
    assert fault.tool == "run_shell"  # the FIRST failed step, not the later one
    assert "exited 1" in fault.error


def test_localize_returns_none_when_all_steps_ok() -> None:
    transcript = [
        _assistant_call("read_file"),
        {"role": "tool", "content": "ok"},
        {"role": "assistant", "content": "done"},
    ]
    assert localize_fault(transcript) is None


def test_attribute_links_fault_to_most_overlapping_skill() -> None:
    fault = Fault(tool="run_shell", error="error: pytest failed: 1 test failed", step_index=4)
    candidates = {
        "format_text": "reformat a block of prose",
        "run_tests": "run the pytest suite and report failures",
    }
    assert attribute(fault, candidates) == "run_tests"


def test_attribute_returns_none_without_overlap() -> None:
    fault = Fault(tool="xyz", error="error: zzz", step_index=0)
    assert attribute(fault, {"alpha": "beta gamma"}) is None


def test_qualify_accepts_only_non_regression() -> None:
    assert qualify(0.5, 0.7) is True
    assert qualify(0.5, 0.5) is True
    assert qualify(0.7, 0.5) is False
