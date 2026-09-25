"""A failure the tool reports in its own answer is still a failure (study 24, 2026-09-24).

The breaker stops "the same failure under different args", but it only knew a call had failed when the
loop said so: an ``error:`` answer or a refusal. A command that ran and failed is a success to the loop.
The replay in `bench/tool_loop_near_args` found three walls nobody stopped:

- ``run_shell`` given its command as a list: ``[exit 127] /bin/sh: 1: [bash,: not found``, whatever the
  command in the list (twice);
- ``code_interpreter`` raising the same ``ZeroDivisionError`` on four different pieces of code.

Then the things the wider notion of failure must not swallow.
"""

from __future__ import annotations

from typing import Any

from chimera.core.tool_loop import ToolLoopDetector, _answered_failure

NOT_FOUND = "[exit 127]\n/bin/sh: 1: [bash,: not found"


def _run(calls: list[tuple[str, dict[str, Any], str]]) -> list[tuple[str, str]]:
    det = ToolLoopDetector()
    out = []
    for name, args, obs in calls:
        v = det.record(name, args, obs, ok=True)  # the loop saw each of these as a success
        out.append((v.level, v.reason))
    return out


def test_a_command_passed_as_a_list_fails_the_same_way_whatever_it_holds() -> None:
    # bench/tool_loop_near_args: brk-legacy-weak 041 r8 and m6f-weak 041 r66.
    commands = [["bash", "-lc", c] for c in ("node test.js", "node -v", "which node", "echo test")]
    verdicts = _run([("run_shell", {"command": c, "timeout": 100000}, NOT_FOUND) for c in commands])
    assert verdicts[-1][0] == "break"
    assert "failed the same way" in verdicts[-1][1]


def test_the_same_exception_from_different_code_is_a_wall() -> None:
    # bench/tool_loop_near_args: System One weak 089 r2.
    codes = [f"rate = {i} / 0" for i in range(4)]
    verdicts = _run([("code_interpreter", {"code": c}, "ZeroDivisionError: float division by zero")
                     for c in codes])
    assert verdicts[-1][0] == "break"


# --- what it must not swallow ---------------------------------------------------------------------


def test_searches_that_find_nothing_are_exploring() -> None:
    # grep with no match exits 1 and prints nothing; four patterns that miss are four questions.
    verdicts = _run([("run_shell", {"command": f"grep -rn {p} src"}, "[exit 1]")
                     for p in ("TODO", "FIXME", "XXX", "HACK", "BUG")])
    assert all(level != "break" for level, _ in verdicts)


def test_a_command_that_succeeded_is_not_a_failure_for_repeating_its_text() -> None:
    verdicts = _run([("run_shell", {"command": f"make target{i}"}, "[exit 0]\nnothing to be done")
                     for i in range(5)])
    assert all(level != "break" for level, _ in verdicts)


def test_different_failures_are_different_answers() -> None:
    verdicts = _run([("run_shell", {"command": f"ls missing{i}"},
                      f"[exit 2]\nls: cannot access 'missing{i}': No such file or directory")
                     for i in range(6)])
    assert all(level != "break" for level, _ in verdicts)


def test_what_reads_as_a_failure() -> None:
    assert _answered_failure(NOT_FOUND)
    assert _answered_failure("[exit 2]\nusage: tool [-h]")
    assert _answered_failure("[exit -9]\nKilled")
    assert _answered_failure("partial output\nValueError: bad literal")
    assert _answered_failure("ZeroDivisionError: float division by zero")
    assert not _answered_failure("[exit 1]")
    assert not _answered_failure("[exit 0]\nerror: this is only text the command printed")
    assert not _answered_failure("ValueError is raised when the value is bad.")
    assert not _answered_failure("no files match")
    assert not _answered_failure(None)
