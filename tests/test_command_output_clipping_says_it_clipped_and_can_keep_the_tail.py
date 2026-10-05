"""Command output is clipped at 20 000 characters, and two things about that were wrong.

1. ``code_interpreter`` clipped in silence: ``out[:_MAX_OUTPUT_CHARS]``, no marker. The model got
   20 000 characters that look exactly like a complete answer, and a reader who cannot tell a
   truncated observation from a complete one will eventually draw a conclusion from half of one.
   ``run_shell`` and ``execute_code`` always said ``[truncated, N chars total]``. Defect: fixed, ON.

2. ``run_shell`` and ``execute_code`` keep the HEAD. For a test run, a build or a traceback the
   useful half is the other one: pytest's ``FAILED …`` summary, the assertion and the exception line
   are at the end, and a 50 000-character log loses all of them. ``events._clip_observation``
   already keeps both ends, but only for the 400-character UI caption, not for what the model sees.
   Head+tail for these two tools sits behind ``CHIMERA_EXEC_OUTPUT_TAIL=1`` and ships OFF: study 30
   (S30-11) makes turning it on conditional on a scenario_traps replay that has not been run.
   Documents, browser and scrape keep head-only on purpose — there the head is the useful part.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from chimera.tools.code import CodeInterpreterTool, ExecuteCodeTool
from chimera.tools.shell import RunShellTool

_FAILED = "FAILED tests/test_billing.py::test_refund_is_not_charged_twice - assert 2 == 1"


def _pytest_log() -> str:
    """A synthetic ~50k pytest run whose verdict is only in its last lines."""
    head = "============ test session starts ============\nplatform linux -- Python 3.12\n"
    body = "".join(f"tests/test_mod_{i:04d}.py ........................ [ {i % 100:2d}%]\n"
                   for i in range(900))
    tail = f"=========== short test summary info ===========\n{_FAILED}\n=== 1 failed, 9000 passed ===\n"
    log = head + body + tail
    assert len(log) > 45_000
    return log


class _Sandbox:
    """Answers every command with ``output``; neither local nor isolated, nothing is spawned."""

    def __init__(self, output: str) -> None:
        self._output = output

    def run(self, command: str, *, timeout: int, cwd: Any = None, **_: Any) -> SimpleNamespace:
        return SimpleNamespace(output=self._output, exit_code=1, timed_out=False, adopted=None)

    def is_isolated(self) -> bool:
        return False


# --- code_interpreter: the marker, which was missing ---------------------------------------------


def test_code_interpreter_says_it_clipped_a_long_output() -> None:
    out = CodeInterpreterTool().run(code="print('x' * 30_000)")

    assert "[truncated," in out
    assert "30000 chars total" in out


def test_code_interpreter_says_it_clipped_a_long_output_that_ended_in_an_exception() -> None:
    out = CodeInterpreterTool().run(code="print('x' * 30_000)\nraise ValueError('boom')")

    assert "[truncated," in out
    # The marker alone passed while the exception line was the part cut off: the model was told
    # "clipped" and never told the code had raised. The verdict is last, and it must survive.
    assert out.endswith("ValueError: boom")
    assert len(out) <= 20_000 + 100


def test_code_interpreter_keeps_the_exception_type_when_the_message_itself_is_huge() -> None:
    out = CodeInterpreterTool().run(code="print('head')\nraise KeyError('k' * 50_000)")

    assert out.startswith("head\n\nKeyError: ")
    assert "[truncated," in out
    assert len(out) <= 20_000 + 100


def test_code_interpreter_exception_under_the_cap_is_untouched() -> None:
    # The format before this fix, byte for byte: only the over-cap case changed.
    out = CodeInterpreterTool().run(code="print('a')\nraise ValueError('b')")

    assert out == "a\n\nValueError: b"


def test_code_interpreter_output_under_the_cap_is_untouched() -> None:
    assert CodeInterpreterTool().run(code="print('hello')") == "hello"


# --- run_shell / execute_code: head-only by default, head+tail behind the flag --------------------


def test_run_shell_keeps_the_head_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHIMERA_EXEC_OUTPUT_TAIL", raising=False)
    out = RunShellTool(tmp_path, sandbox=_Sandbox(_pytest_log())).run(command="pytest")

    assert out.startswith("[exit 1]\n============ test session starts")
    assert "[truncated," in out
    assert _FAILED not in out  # the shipped default, pinned: the flag is OFF until measured


def test_run_shell_keeps_the_failed_line_with_the_tail_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHIMERA_EXEC_OUTPUT_TAIL", "1")
    log = _pytest_log()
    out = RunShellTool(tmp_path, sandbox=_Sandbox(log)).run(command="pytest")

    assert _FAILED in out
    assert "1 failed, 9000 passed" in out
    assert "test session starts" in out  # the head is still there: it says WHAT ran
    assert f"[truncated, {len(log)} chars total" in out  # and it still says it clipped
    assert len(out) < 20_000 + 200


def test_execute_code_keeps_the_failed_line_with_the_tail_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHIMERA_EXEC_OUTPUT_TAIL", "1")
    out = ExecuteCodeTool(tmp_path, sandbox=_Sandbox(_pytest_log())).run(code="run()")

    assert _FAILED in out
    assert "[truncated," in out


def test_execute_code_keeps_the_head_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHIMERA_EXEC_OUTPUT_TAIL", raising=False)
    out = ExecuteCodeTool(tmp_path, sandbox=_Sandbox(_pytest_log())).run(code="run()")

    assert "[truncated," in out
    assert _FAILED not in out


def test_output_under_the_cap_is_identical_with_or_without_the_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHIMERA_EXEC_OUTPUT_TAIL", "1")
    out = RunShellTool(tmp_path, sandbox=_Sandbox("short\n")).run(command="echo")

    assert out == "[exit 1]\nshort"


# --- the ruler this moved: scenario_traps' truncation mask ----------------------------------------


def test_a_clipped_code_interpreter_output_now_opens_the_truncation_trap(tmp_path: Path) -> None:
    """Pinned so the S30-11 replay cannot miss it: the new marker moved the trap's ruler.

    Before this branch a >20k ``code_interpreter`` print carried no marker, so the truncation trap
    passed vacuously. Now it is served the trap and needs a second look. A baseline recorded before
    the change is a different ruler (lessons §2g); remeasure with the flag off on this code.
    """
    import random

    from chimera.core.agent import ToolActivity
    from chimera.eval.scenario_traps import _survived_truncation, _truncation_served
    from chimera.eval.scenarios import ScenarioContext

    observation = CodeInterpreterTool().run(code="print('x' * 30_000)")
    ctx = ScenarioContext(workspace=tmp_path, home=tmp_path, rng=random.Random(0))
    ctx.activities.append(ToolActivity("code_interpreter", {"code": "..."}, True, observation))

    assert _truncation_served(ctx)
    assert not _survived_truncation(ctx)
