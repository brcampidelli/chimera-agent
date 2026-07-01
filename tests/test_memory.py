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


def test_profile_consolidates_persona_facts(tmp_path: Path) -> None:
    manager = _manager(tmp_path)
    manager.remember("prefers concise answers", "persona")
    manager.remember("uses Python and async", "persona")
    manager.remember("the deploy runs on Fridays", "semantic")  # not persona
    profile = manager.profile()
    assert "concise answers" in profile and "uses Python and async" in profile
    assert "deploy runs on Fridays" not in profile
    assert _manager(tmp_path / "empty").profile() == ""  # no persona facts -> empty


def test_cluster_groups_similar_facts() -> None:
    from chimera.memory.consolidate import cluster

    items = [
        MemoryItem(id="a", content="the deploy uses github actions ci"),
        MemoryItem(id="b", content="deploy runs on github actions ci pipeline"),
        MemoryItem(id="c", content="lunch is at noon downtown"),
    ]
    groups = cluster(items, threshold=0.3)
    assert sorted(len(g) for g in groups) == [1, 2]  # a+b cluster, c alone


def test_consolidate_merges_clusters(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.remember("the deploy uses github actions ci", "semantic")
    mgr.remember("deploy runs on github actions ci pipeline", "semantic")
    mgr.remember("lunch is at noon downtown", "semantic")
    removed = mgr.consolidate(lambda facts: "MERGED: " + " / ".join(facts), threshold=0.3)
    assert removed == 1  # two merged into one, singleton untouched
    contents = [item.content for item in mgr.store.all()]
    assert any(c.startswith("MERGED:") for c in contents)
    assert "lunch is at noon downtown" in contents


def test_consolidate_skips_empty_summary(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.remember("alpha beta gamma delta", "semantic")
    mgr.remember("alpha beta gamma epsilon", "semantic")
    removed = mgr.consolidate(lambda facts: "   ", threshold=0.3)  # blank summary -> skip
    assert removed == 0
    assert len(mgr.store) == 2  # nothing removed when the summariser yields nothing


def test_nudges_suggests_unstored_preferences(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.remember("Bruno uses ruff for linting", "persona")  # already known
    suggestions = mgr.nudges(
        ["I prefer async code", "also I use ruff for linting", "what is 2+2?"]
    )
    assert any("prefer async code" in s for s in suggestions)  # new preference surfaced
    assert not any("ruff" in s for s in suggestions)  # already stored -> not re-suggested


def test_nudges_dedupes_and_caps(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    texts = [
        "I prefer dark mode",
        "I prefer dark mode",  # duplicate
        "I like strong types",
        "I need fast feedback",
        "I require green tests",  # 4th distinct -> beyond the cap of 3
    ]
    suggestions = mgr.nudges(texts, max_suggestions=3)
    assert len(suggestions) == 3
    assert len(set(suggestions)) == 3  # no duplicates


def test_nudges_ignores_non_preference_statements(tmp_path: Path) -> None:
    from chimera.memory.nudges import detect_nudges

    # "is"/"has" relations aren't preferences — they shouldn't nudge.
    assert detect_nudges(["the sky is blue", "the repo has tests"], []) == []


def test_prune_keeps_highest_value(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.add("a low working scratch note", kind="working")
    mgr.add("Alex prefers TypeScript strict and absolute imports", kind="persona", key="alex-prefs")
    mgr.add("another working scratch", kind="working")
    removed = mgr.prune(max_items=1)
    assert removed == 2
    remaining = mgr.store.all()
    assert len(remaining) == 1
    assert "Alex prefers" in remaining[0].content  # the persona/keyed fact survived


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
