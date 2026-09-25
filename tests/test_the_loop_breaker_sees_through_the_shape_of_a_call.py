"""A call that differs only in its shape asks the same question (study 24, 2026-09-24).

The fix that made a spin need the same call AND the same answer (#577) left one known miss, seen in
`bench/tool_loop_fork`: the weak executor listing the workspace root four times, answered
`in/\nout/` each time, with depth 2, 3, 2, 1, or with and without `max_results`. The args differed,
so the fixed rule read four questions; they were one. These are those calls, then the things the
wider notion of "the same call" must not swallow.
"""

from __future__ import annotations

from typing import Any

from chimera.core.tool_loop import ToolLoopDetector, _target_sig

ROOT = "in/\nout/"


def _run(calls: list[tuple[str, dict[str, Any], str]]) -> list[str]:
    det = ToolLoopDetector()
    return [det.record(name, args, obs, ok=True).level for name, args, obs in calls]


def test_the_fork_listing_at_four_depths_is_one_question() -> None:
    # bench/tool_loop_fork, 082 weak r44: depth 3, 4, 2, 2 — the same listing each time.
    levels = _run([("list_dir", {"depth": d, "path": ""}, ROOT) for d in (3, 4, 2, 2)])
    assert levels[-1] == "break"


def test_a_limit_that_comes_and_goes_is_one_question() -> None:
    # bench/tool_loop_fork, 043 weak r35: max_results 200, absent, absent, 200.
    shapes: list[dict[str, Any]] = [{"max_results": 200}, {}, {}, {"max_results": 200}]
    levels = _run([("list_dir", {**s, "path": ""}, ROOT) for s in shapes])
    assert levels[-1] == "break"


def test_a_depth_sent_as_a_string_is_still_shape() -> None:
    # bench/tool_loop_fork, 042 weak r58: the model sent depth "0".
    levels = _run([("list_dir", {"depth": d, "path": ""}, ROOT) for d in (1, "0", 2, "3")])
    assert levels[-1] == "break"


def test_three_spellings_of_the_root_are_one_place() -> None:
    levels = _run([("list_dir", {"path": p}, ROOT) for p in ("", ".", "./", " . ")])
    assert levels[-1] == "break"
    assert _target_sig("read_file", {"path": "in/db/"}) == _target_sig("read_file", {"path": "./in/db"})
    assert _target_sig("read_file", {"path": "in\\db"}) == _target_sig("read_file", {"path": "in/db"})


# --- what it must not swallow ------------------------------------------------------------------------


def test_the_same_listing_of_two_different_folders_is_two_questions() -> None:
    levels = _run([("list_dir", {"path": p}, "README.md") for p in ("a", "b", "c", "d", "e")])
    assert "break" not in levels


def test_pages_of_a_log_that_differ_only_in_their_numbers_are_new_pages() -> None:
    # The gist flattens digits; this rule compares the answer exactly, so paging a log whose lines
    # differ only in their timestamps is reading, not spinning.
    calls = [
        ("read_file", {"path": "app.log", "offset": 100 * i, "limit": 100},
         f"12:00:{i:02d} INFO request ok\n12:00:{i:02d} INFO request ok")
        for i in range(8)
    ]
    assert "break" not in _run(calls)


def test_edits_with_different_text_and_the_same_confirmation_stay_work() -> None:
    calls = [
        ("edit_file", {"path": "db/migration.sql", "old_string": f"col{i}", "new_string": f"c{i}",
                       "replace_all": False}, "edited db/migration.sql: replaced 1 occurrence")
        for i in range(8)
    ]
    assert "break" not in _run(calls)


def test_a_path_that_is_only_a_number_is_a_place_not_a_shape() -> None:
    assert _target_sig("list_dir", {"path": "2024"}) != _target_sig("list_dir", {"path": "2025"})
    assert _target_sig("list_dir", {"path": "2024"}) != _target_sig("list_dir", {})


def test_the_shape_is_numbers_booleans_and_nulls_and_nothing_else() -> None:
    base = _target_sig("search", {"pattern": "TODO", "path": "src"})
    assert _target_sig("search", {"pattern": "TODO", "path": "src", "limit": 5, "regex": True,
                                  "context": None}) == base
    assert _target_sig("search", {"pattern": "FIXME", "path": "src"}) != base
    assert _target_sig("search", {"pattern": "TODO", "path": "src", "globs": ["*.py"]}) != base
