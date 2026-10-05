"""Tests for step-level failure attribution (SkillAdaptor)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import chimera.evolution as evolution
from chimera.evolution import Fault, attribute, localize_fault
from chimera.evolution import attribution as attribution_module


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


def test_no_qualification_gate_exists_that_nothing_calls() -> None:
    # `qualify()` was documented as the gate that rejects a misdirected revision, exported, and
    # called only by this file (study 30, S30-21(k)). A gate with no production caller is a
    # guarantee in prose. So: either it exists AND something under chimera/ calls it, or it does
    # not exist. Today it does not; reintroducing it without a caller turns this red.
    package = Path(attribution_module.__file__).resolve().parents[1]
    defined = hasattr(attribution_module, "qualify") or "qualify" in evolution.__all__
    callers = [
        path for path in package.rglob("*.py")
        if path.name != "attribution.py"
        and re.search(r"qualify\(", path.read_text(encoding="utf-8"))
    ]
    assert not defined or callers, "qualify() is exported but no production code calls it"
