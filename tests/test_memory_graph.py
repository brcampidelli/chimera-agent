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


def test_graph_persist_roundtrip(tmp_path: Path) -> None:
    graph = build_graph(["X requires Y"])
    path = tmp_path / "g.json"
    graph.save(path)
    assert MemoryGraph.load(path).relations() == graph.relations()
    assert MemoryGraph.load(tmp_path / "missing.json").relations() == []
