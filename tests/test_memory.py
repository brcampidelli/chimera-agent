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


def test_autoconsolidate_skips_when_under_budget(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.remember("alpha beta gamma", "semantic")
    mgr.remember("alpha beta gamma delta", "semantic")  # would cluster, but under budget
    calls: list[list[str]] = []

    def summarizer(facts: list[str]) -> str:
        calls.append(facts)
        return "MERGED"

    removed = mgr.autoconsolidate(summarizer, max_items=10, threshold=0.3)
    assert removed == 0
    assert calls == []  # summariser never called under budget (no wasted model calls)
    assert len(mgr.store) == 2


def test_autoconsolidate_runs_when_over_budget(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.remember("the deploy uses github actions ci", "semantic")
    mgr.remember("deploy runs on github actions ci pipeline", "semantic")
    mgr.remember("lunch is at noon downtown", "semantic")
    removed = mgr.autoconsolidate(
        lambda facts: "MERGED: " + " / ".join(facts), max_items=2, threshold=0.3
    )
    assert removed == 1  # over budget -> the cluster of 2 is merged into 1


def test_prune_protects_persona_and_budgets_only_prunable(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.add("a low working scratch note", kind="working")
    mgr.add("Alex prefers TypeScript strict and absolute imports", kind="persona", key="alex-prefs")
    mgr.add("another working scratch", kind="working")
    # Budget of 1 applies only to the 2 prunable (working) items — persona is never pruned.
    removed = mgr.prune(max_items=1)
    assert removed == 1  # one of the two working notes; the persona fact is untouchable
    remaining = mgr.store.all()
    assert len(remaining) == 2
    assert any("Alex prefers" in item.content for item in remaining)  # persona survived


def test_prune_dry_run_deletes_nothing(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.add("a low working scratch note", kind="working")
    mgr.add("another working scratch", kind="working")
    would = mgr.prune(max_items=1, dry_run=True)
    assert would == 1  # reports what WOULD go
    assert len(mgr.store.all()) == 2  # ...but nothing was deleted


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


def test_memory_ids_are_full_length_uuids(tmp_path: Path) -> None:
    # Full uuid4 hex (32 chars), not an 8-char slice that collides and silently overwrites.
    mgr = _manager(tmp_path)
    _, item = mgr.remember("a fact")
    assert len(item.id) == 32


def test_blank_query_returns_nothing(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.remember("some fact")
    assert mgr.search("   ") == []  # tokenless query matches nothing on every path


def test_store_load_skips_a_malformed_record(tmp_path: Path) -> None:
    import json

    from chimera.memory import MemoryStore

    path = tmp_path / "mem.json"
    good = MemoryItem(id="a", content="a good memory")
    path.write_text(json.dumps([good.model_dump(), {"id": "bad"}]), encoding="utf-8")  # 2nd is invalid
    store = MemoryStore(path)
    assert [i.content for i in store.all()] == ["a good memory"]  # the good one survives


def test_store_save_is_atomic(tmp_path: Path) -> None:
    from chimera.memory import MemoryStore

    path = tmp_path / "mem.json"
    store = MemoryStore(path)
    store.add(MemoryItem(id="a", content="x"))
    assert not (tmp_path / "mem.json.tmp").exists()  # no stray temp file


def test_consolidate_preserves_tainted_provenance(tmp_path: Path) -> None:
    # Merging a cluster that includes a tainted member must not launder it to clean.
    mgr = _manager(tmp_path)
    mgr.add("deploy uses github actions ci pipeline", kind="semantic", provenance="tainted")
    mgr.add("deploy uses github actions ci pipeline daily", kind="semantic")  # clusters with it
    mgr.consolidate(lambda parts: "deploy: github actions ci", kinds=("semantic",))
    items = mgr.store.all()
    assert len(items) == 1
    assert items[0].provenance == "tainted"


def test_merge_preserves_tainted_provenance(tmp_path: Path) -> None:
    # Importing another agent's memories must NOT launder a tainted fact to clean — the merge
    # has to carry provenance through, or a poisoned import is recalled as verified.
    mgr = _manager(tmp_path)
    mgr.merge([MemoryItem(id="p", content="a poisoned fact from an untrusted store", provenance="tainted")])
    stored = mgr.store.all()
    assert len(stored) == 1
    assert stored[0].provenance == "tainted"


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


def test_search_reports_the_winning_layer_via_on_layer(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)  # default JSON store: no semantic, no FTS -> keyword layer
    mgr.remember("Alex prefers TypeScript strict", "persona")
    seen: list[str] = []
    hits = mgr.search("what does Alex prefer", k=3, on_layer=seen.append)
    assert hits  # something matched
    assert seen == ["keyword"]  # the layer that actually produced the hits


def test_search_does_not_report_a_layer_when_nothing_matches(tmp_path: Path) -> None:
    mgr = _manager(tmp_path)
    mgr.remember("Alex prefers TypeScript strict", "persona")
    seen: list[str] = []
    assert mgr.search("completely unrelated zzzptxq", k=3, on_layer=seen.append) == []
    assert seen == []  # no layer contributed -> nothing reported (never guessed)
