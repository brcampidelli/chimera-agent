"""Tests for memory store + Memory Manager (ADD/UPDATE/NOOP, merge, search)."""

from __future__ import annotations

from pathlib import Path

from chimera.memory import MemoryItem, MemoryManager, MemoryStore


def _manager(tmp_path: Path) -> MemoryManager:
    return MemoryManager(MemoryStore(tmp_path / "mem.json"))


def test_remember_add_then_noop(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    op1, _ = mgr.remember("Alex prefers PT-BR")
    op2, _ = mgr.remember("alex prefers pt-br")  # same content, normalized
    assert op1 == "ADD"
    assert op2 == "NOOP"
    assert len(mgr.store) == 1


def test_remember_update_by_key(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.remember("default model is X", key="default-model")
    op, item = mgr.remember("default model is Y", key="default-model")
    assert op == "UPDATE"
    assert item.content == "default model is Y"
    assert len(mgr.store) == 1


def test_merge_counts(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.remember("existing fact")
    items = [
        MemoryItem(id="a", content="existing fact", source="hermes"),  # NOOP
        MemoryItem(id="b", content="brand new fact", source="hermes"),  # ADD
        MemoryItem(id="c", content="another new one", source="hermes"),  # ADD
    ]
    counts = mgr.merge(items)
    assert counts == {"ADD": 2, "UPDATE": 0, "NOOP": 1}
    assert len(mgr.store) == 3


def test_delete(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    _, item = mgr.remember("temporary")
    mgr.delete(item.id)
    assert len(mgr.store) == 0


def test_search(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.remember("the trading hard rules: stop loss always")
    mgr.remember("the user likes sharp UI corners")
    hits = mgr.search("trading stop", k=5)
    assert hits
    assert "trading" in hits[0].content


def test_persistence(tmp_path: Path) -> None:
    path = tmp_path / "mem.json"
    MemoryManager(MemoryStore(path)).remember("durable fact", key="k")
    reopened = MemoryManager(MemoryStore(path))
    assert len(reopened.store) == 1
    op, _ = reopened.remember("durable fact", key="k")
    assert op == "NOOP"
