"""Tests for the memory-layers read-model (by-kind + provenance + by-source aggregation).

Load-bearing properties: all four canonical kinds appear even when empty; an unknown kind is folded
in (never dropped); ``tainted`` is counted separately from ``clean``; ``by_source`` is sorted desc;
the semantic-embeddings flag is passed straight through (it is a UI note, not an index count).
"""

from __future__ import annotations

from dataclasses import dataclass

from chimera.api.memory_layers import summarize_memory_layers


@dataclass
class _FakeItem:
    """Minimal MemoryItem-like stand-in (duck-typed on .kind/.provenance/.source)."""

    kind: str
    provenance: str = "clean"
    source: str = "chimera"


def test_summarize_empty_reports_full_taxonomy() -> None:
    s = summarize_memory_layers([], semantic_embeddings_enabled=False)
    assert s["total"] == 0 and s["clean"] == 0 and s["tainted"] == 0
    assert [layer["kind"] for layer in s["layers"]] == [
        "working",
        "episodic",
        "semantic",
        "persona",
    ]
    assert all(layer["count"] == 0 for layer in s["layers"])
    assert s["by_source"] == []
    assert s["semantic_embeddings_enabled"] is False


def test_summarize_counts_clean_and_tainted() -> None:
    items = [
        _FakeItem("semantic", "clean"),
        _FakeItem("semantic", "tainted"),
        _FakeItem("persona", "clean"),
    ]
    s = summarize_memory_layers(items, semantic_embeddings_enabled=True)
    assert s["total"] == 3 and s["clean"] == 2 and s["tainted"] == 1
    by_kind = {layer["kind"]: layer for layer in s["layers"]}
    assert by_kind["semantic"] == {"kind": "semantic", "count": 2, "clean": 1, "tainted": 1}
    assert by_kind["persona"] == {"kind": "persona", "count": 1, "clean": 1, "tainted": 0}
    # 0-count kinds are still present and honest.
    assert by_kind["working"]["count"] == 0 and by_kind["episodic"]["count"] == 0
    assert s["semantic_embeddings_enabled"] is True


def test_summarize_folds_unknown_kind_trailing() -> None:
    items = [_FakeItem("mystery", "clean"), _FakeItem("semantic", "clean")]
    s = summarize_memory_layers(items, semantic_embeddings_enabled=False)
    kinds = [layer["kind"] for layer in s["layers"]]
    # Four canonical kinds first, unknown kind folded in at the end — never dropped.
    assert kinds[:4] == ["working", "episodic", "semantic", "persona"]
    assert "mystery" in kinds[4:]
    assert next(la for la in s["layers"] if la["kind"] == "mystery")["count"] == 1


def test_summarize_by_source_sorted_desc_and_blank_key() -> None:
    items = [
        _FakeItem("semantic", "clean", "hermes"),
        _FakeItem("semantic", "clean", "chimera"),
        _FakeItem("semantic", "clean", "chimera"),
        _FakeItem("semantic", "clean", ""),  # blank source -> key ""
    ]
    s = summarize_memory_layers(items, semantic_embeddings_enabled=False)
    assert [row["source"] for row in s["by_source"]][0] == "chimera"  # 2 wins the top slot
    assert {row["source"] for row in s["by_source"]} == {"chimera", "hermes", ""}
    assert next(r for r in s["by_source"] if r["source"] == "chimera")["count"] == 2


def test_provenance_defaults_to_clean_when_absent() -> None:
    class _NoProvenance:
        kind = "semantic"
        source = "chimera"

    s = summarize_memory_layers([_NoProvenance()], semantic_embeddings_enabled=False)
    assert s["total"] == 1 and s["clean"] == 1 and s["tainted"] == 0
