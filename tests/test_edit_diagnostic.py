"""Tests for the structural-vs-parametric AST edit classifier (EvoPolicyGym)."""

from __future__ import annotations

from chimera.evolution.edit_diagnostic import classify_edit, topology_key


def test_noop_identical() -> None:
    assert classify_edit("x = 1", "x = 1") == "noop"


def test_noop_whitespace_only() -> None:
    # Same AST including constants — only formatting differs.
    assert classify_edit("x=1", "x = 1") == "noop"


def test_parametric_numeric_tweak() -> None:
    assert classify_edit("threshold = 5", "threshold = 7") == "parametric"
    assert classify_edit("def f():\n    return 0.5", "def f():\n    return 0.9") == "parametric"


def test_structural_new_control_flow() -> None:
    assert classify_edit("def f():\n    return 1", "def f():\n    if x:\n        return 1\n    return 2") == "structural"


def test_structural_new_expression() -> None:
    assert classify_edit("x = 1", "x = 1 + y") == "structural"


def test_string_change_is_structural() -> None:
    # Only NUMERIC literals are parameters; strings carry structural meaning.
    assert classify_edit('k = "a"', 'k = "b"') == "structural"


def test_unknown_for_prose() -> None:
    # Prose skill-card templates don't parse as Python -> topology is meaningless.
    assert classify_edit("Summarize the {text}.", "Summarize the {text} briefly.") == "unknown"
    assert topology_key("please write a haiku about the sea, thanks!") is None


def test_topology_key_ignores_numeric_constants() -> None:
    assert topology_key("a = 1") == topology_key("a = 999")
    assert topology_key("a = 1") != topology_key("a = 1 + 2")
