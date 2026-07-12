"""Tests for the graph memory layer (entity-relation extraction)."""

from __future__ import annotations

from pathlib import Path

from chimera.memory import MemoryGraph, Relation, build_graph, extract_relations


def test_extracts_simple_relations() -> None:
    relations = extract_relations("Alex prefers TypeScript strict. PassaPro uses Supabase.")
    assert Relation("Alex", "prefers", "TypeScript strict") in relations
    assert Relation("PassaPro", "uses", "Supabase") in relations


def test_multiword_relation_wins_over_is() -> None:
    relations = extract_relations("The frontend depends on the API")
    assert relations == [Relation("The frontend", "depends_on", "the API")]


def test_graph_entities_and_neighbors() -> None:
    graph = build_graph(["PassaPro uses Supabase", "PassaPro depends on Stripe"])
    assert "PassaPro" in graph.entities()
    assert graph.neighbors("PassaPro") == {"Supabase", "Stripe"}
    assert len(graph.relations_of("supabase")) == 1  # case-insensitive


def test_graph_dedups_relations() -> None:
    graph = MemoryGraph()
    graph.add(Relation("a", "uses", "b"))
    graph.add(Relation("a", "uses", "b"))
    assert len(graph) == 1


def test_related_facts_by_query() -> None:
    graph = build_graph(["PassaPro uses Supabase", "LeFran uses Stripe"])
    assert graph.related_facts("how does PassaPro store data?") == ["PassaPro uses Supabase"]


def test_related_facts_matches_whole_words_only() -> None:
    # A short entity ("Go", "AI", "C") must NOT match inside an unrelated word, or recall gets
    # polluted. "Go uses goroutines" should surface only when "Go" appears as its own word.
    graph = build_graph(["Go uses goroutines"])
    assert graph.related_facts("recommend a good approach") == []  # "Go" not inside "good"
    assert graph.related_facts("is Go fast?") == ["Go uses goroutines"]  # standalone word matches


def test_from_dict_skips_malformed_relations() -> None:
    graph = MemoryGraph.from_dict(
        {"relations": [{"source": "a", "relation": "uses", "target": "b"}, {"source": "x"}, "junk"]}  # type: ignore[list-item]
    )
    assert graph.relations() == [Relation("a", "uses", "b")]  # the bad entries are dropped


def test_load_corrupt_file_is_empty(tmp_path: Path) -> None:
    path = tmp_path / "g.json"
    path.write_text("{ this is not valid json", encoding="utf-8")
    assert MemoryGraph.load(path).relations() == []  # truncated/corrupt file -> empty, not a crash


def test_graph_persist_roundtrip(tmp_path: Path) -> None:
    graph = build_graph(["X requires Y"])
    path = tmp_path / "g.json"
    graph.save(path)
    assert MemoryGraph.load(path).relations() == graph.relations()
    assert MemoryGraph.load(tmp_path / "missing.json").relations() == []
