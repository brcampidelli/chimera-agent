"""Tests for the multi-factor memory value model + ranking."""

from __future__ import annotations

from chimera.memory import MemoryItem, rank, value
from chimera.memory.models import MemoryKind


def _item(content: str = "fact", kind: MemoryKind = "semantic", key: str | None = None, source: str = "chimera") -> MemoryItem:
    return MemoryItem(id="x", content=content, kind=kind, key=key, source=source)


def test_persona_beats_working() -> None:
    assert value(_item(kind="persona")) > value(_item(kind="working"))


def test_keyed_beats_unkeyed() -> None:
    assert value(_item(key="k")) > value(_item(key=None))


def test_reliable_source_beats_agent_derived() -> None:
    assert value(_item(source="user")) > value(_item(source="chimera"))


def test_specificity_rewards_richer_content() -> None:
    assert value(_item(content="x" * 150)) > value(_item(content="x"))


def test_rank_puts_the_high_value_item_first() -> None:
    items = [
        _item("a low working note", kind="working"),
        _item("a rich persona fact " * 10, kind="persona", key="k", source="user"),
    ]
    ranked = rank(items)
    assert ranked[0][1].kind == "persona"
    assert ranked[-1][1].kind == "working"
