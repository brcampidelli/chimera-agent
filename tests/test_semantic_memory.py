"""Tests for opt-in semantic memory recall (M11b).

The fake embedder is a deterministic **concept embedder**: it maps each synonym pair in the
memory-bench corpus to a shared one-hot axis, so a paraphrase and its fact land on the same
vector. That's exactly the property a good real embedder has — and it lets us prove the
*wiring* lifts paraphrase recall from the keyword ceiling (0.0) without any network call.
"""

from __future__ import annotations

from pathlib import Path

from chimera.eval import run_memory_bench, synthetic_facts_and_probes
from chimera.eval.memory_bench import _SYNONYMS
from chimera.memory import MemoryManager, MemoryStore, SemanticIndex, cosine
from chimera.memory.models import MemoryItem

# One axis per synonym pair (+ a catch-all axis); both words in a pair map to the same axis.
_CONCEPT: dict[str, int] = {}
for _i, (_w, _s) in enumerate(_SYNONYMS):
    _CONCEPT[_w] = _i
    _CONCEPT[_s] = _i
_DIM = len(_SYNONYMS) + 1


def _concept_embed(texts: list[str]) -> list[list[float]]:
    vecs: list[list[float]] = []
    for text in texts:
        vec = [0.0] * _DIM
        lowered = text.lower()
        for word, axis in _CONCEPT.items():
            if word in lowered:
                vec[axis] += 1.0
        if sum(vec) == 0.0:  # distractor / unknown -> catch-all axis, far from any concept
            vec[-1] = 1.0
        vecs.append(vec)
    return vecs


def _manager(tmp_path: Path, *, semantic: bool) -> MemoryManager:
    embed = _concept_embed if semantic else None
    return MemoryManager(MemoryStore(tmp_path / "m.json"), embed=embed)


def test_cosine_basics() -> None:
    assert cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine([], [1.0]) == 0.0  # empty -> 0, never a crash
    assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0  # zero-norm -> 0


def test_semantic_index_ranks_synonym_first() -> None:
    index = SemanticIndex(_concept_embed)
    items = [
        MemoryItem(id="1", kind="semantic", content="The doctor for the archive is verified."),
        MemoryItem(id="2", kind="semantic", content="Log entry: routine status nominal."),
    ]
    hits = index.search("physician", items, k=1)
    assert [h.id for h in hits] == ["1"]  # bridged doctor<->physician with no shared token


def test_semantic_lifts_paraphrase_recall(tmp_path: Path) -> None:
    facts, probes = synthetic_facts_and_probes(200)
    keyword = run_memory_bench(_manager(tmp_path / "kw", semantic=False), facts, probes, k=5)
    semantic = run_memory_bench(_manager(tmp_path / "se", semantic=True), facts, probes, k=5)
    # Keyword search is stuck at the paraphrase ceiling...
    assert keyword.summary()["recall@k_paraphrase"] == 0.0
    # ...and semantic recall lifts it decisively (concept embedder -> synonyms co-locate).
    assert semantic.summary()["recall@k_paraphrase"] >= 0.9
    assert semantic.summary()["recall@k_lexical"] >= 0.9  # doesn't sacrifice lexical


def test_semantic_falls_back_to_keyword_on_embedder_error(tmp_path: Path) -> None:
    def boom(texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embeddings endpoint down")

    manager = MemoryManager(MemoryStore(tmp_path / "m.json"), embed=boom)
    manager.add("The doctor for the archive is verified.", key="k1")
    # A lexical query still works because search degrades to keyword when the embedder throws.
    hits = manager.search("doctor", k=3)
    assert any(h.key == "k1" for h in hits)


def test_no_embedder_is_pure_keyword(tmp_path: Path) -> None:
    manager = MemoryManager(MemoryStore(tmp_path / "m.json"))
    manager.add("The doctor for the archive is verified.", key="k1")
    assert manager.search("physician", k=3) == []  # no bridge without embeddings
    assert any(h.key == "k1" for h in manager.search("doctor", k=3))
