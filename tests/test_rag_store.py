"""The index and the fusion (:mod:`chimera.rag.store`, :mod:`chimera.rag.hybrid`).

The embedder is a deterministic fake: a bag-of-words vector over a fixed vocabulary. That is enough
to make "the same words land close together" true, which is the only property the ranking depends
on — and it makes these tests run without a provider, a key, or a network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.rag.chunks import Chunk
from chimera.rag.hybrid import reciprocal_rank_fusion
from chimera.rag.store import ChunkStore, Hit

VOCAB = ["verify", "revert", "keep", "change", "token", "budget", "search", "index", "cat", "dog"]


def fake_embed(texts: list[str]) -> list[list[float]]:
    """A bag-of-words vector. Deterministic, offline, and enough for cosine to mean something."""
    out = []
    for text in texts:
        low = text.lower()
        out.append([float(low.count(word)) for word in VOCAB])
    return out


def chunk(path: str, symbol: str, text: str, start: int = 1) -> Chunk:
    return Chunk(path, symbol, "function", start, start + 3, text)


CORPUS = [
    chunk("core/verify.py", "verify", "def verify(): decide whether to keep or revert the change"),
    chunk("core/budget.py", "spend", "def spend(): count the token budget for a run", 20),
    chunk("core/search.py", "find", "def find(): search the index for a string", 40),
    chunk("pets.py", "adopt", "def adopt(): the cat and the dog go home", 60),
]


@pytest.fixture()
def store(tmp_path: Path) -> ChunkStore:
    s = ChunkStore(tmp_path / "index.sqlite3")
    s.replace_all(CORPUS)
    return s


# --- storing --------------------------------------------------------------------------------


def test_the_index_reports_what_is_in_it(store: ChunkStore) -> None:
    stats = store.stats()
    assert stats["chunks"] == 4
    assert stats["files"] == 4
    assert stats["embedded"] == 0  # nothing embedded yet, and it says so rather than implying it


def test_rebuilding_keeps_the_embeddings_it_already_paid_for(store: ChunkStore) -> None:
    """The difference between a cache and a bill.

    A rebuild is wholesale because a stale row answers confidently about code that has moved — but
    re-embedding an unchanged chunk costs money for a vector we already have. Chunk ids are stable
    across runs precisely so this is possible.
    """
    store.embed_missing(fake_embed)
    assert store.stats()["embedded"] == 4

    store.replace_all(CORPUS)  # same code, rebuilt

    assert store.stats()["embedded"] == 4, "an unchanged chunk was re-embedded"


def test_a_changed_chunk_loses_its_vector_and_is_re_embedded(store: ChunkStore) -> None:
    # The other direction: a chunk whose lines moved is a different id, so it must NOT inherit a
    # vector describing code that is no longer there.
    store.embed_missing(fake_embed)
    moved = [chunk("core/verify.py", "verify", "def verify(): now it says something else", 99)]

    store.replace_all(moved)

    assert store.stats()["embedded"] == 0


def test_embedding_is_resumable(store: ChunkStore) -> None:
    """A provider that dies halfway leaves what it wrote, and the next call picks up the rest."""
    calls = {"n": 0}

    def flaky(texts: list[str]) -> list[list[float]]:
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("provider down")
        return fake_embed(texts)

    from chimera.rag import store as store_module

    original = store_module.EMBED_BATCH
    store_module.EMBED_BATCH = 2
    try:
        store.embed_missing(flaky)
        assert store.stats()["embedded"] == 2  # the first batch survived the failure
        store.embed_missing(fake_embed)
        assert store.stats()["embedded"] == 4
    finally:
        store_module.EMBED_BATCH = original


def test_an_embedder_that_returns_the_wrong_count_is_refused(store: ChunkStore) -> None:
    # Zipping mismatched lists would attach vectors to the wrong chunks — an index that is silently
    # wrong is worse than one that is visibly incomplete.
    store.embed_missing(lambda texts: fake_embed(texts)[:-1])
    assert store.stats()["embedded"] == 0


# --- keyword retrieval ------------------------------------------------------------------------


def test_keyword_finds_the_exact_identifier(store: ChunkStore) -> None:
    hits = store.search_keyword("revert", k=5)
    assert hits and hits[0].chunk.path == "core/verify.py"
    assert hits[0].source == "fts"


def test_keyword_misses_the_paraphrase(store: ChunkStore) -> None:
    """The gap the vectors exist to close, asserted rather than assumed.

    Nothing in the corpus contains "discard", so a keyword search cannot reach the function that
    decides whether to revert — no shared token, no hit.
    """
    assert store.search_keyword("discard edits", k=5) == []
    assert store.search_keyword("throw away what was written", k=5) == []


def test_a_match_on_a_word_in_every_chunk_is_not_a_hit(store: ChunkStore) -> None:
    """Since the query is an OR of its words, "discard THE edits" matches every chunk containing
    "the" — and bm25 scores those 0, because a term in every document carries no information.

    Returning them anyway hands the fusion four confident non-answers to rank, which is how a
    retriever that found nothing produces a top result.
    """
    assert store.search_keyword("discard the edits", k=5) == []


def test_a_search_box_query_cannot_break_the_fts_syntax(store: ChunkStore) -> None:
    """`AND`, a stray quote and `NEAR(` are things people type, not FTS operators they meant."""
    for query in ['verify AND revert', 'say "hello', "NEAR(", "*"]:
        store.search_keyword(query, k=5)  # must not raise


def test_an_empty_query_finds_nothing(store: ChunkStore) -> None:
    assert store.search_keyword("", k=5) == []


# --- vector retrieval -------------------------------------------------------------------------


def test_vectors_bridge_the_paraphrase(store: ChunkStore) -> None:
    store.embed_missing(fake_embed)
    # "keep or revert the change" — the words are in the target and not in the query's phrasing of
    # the question; the fake embedder makes the overlap the ranking signal.
    query = fake_embed(["should we keep the change or revert it"])[0]

    hits = store.search_vector(query, k=2)

    assert hits and hits[0].chunk.path == "core/verify.py"
    assert hits[0].source == "vector"


def test_vector_search_over_an_unembedded_index_returns_nothing(store: ChunkStore) -> None:
    # Rather than raising. A machine with no embedder gets keyword retrieval, not an error.
    assert store.search_vector(fake_embed(["anything"])[0], k=5) == []


def test_zero_similarity_is_not_a_hit(store: ChunkStore) -> None:
    store.embed_missing(fake_embed)
    # A query sharing no vocabulary with anything: cosine 0 everywhere. Returning the corpus in
    # arbitrary order would present noise as the four best answers.
    assert store.search_vector([0.0] * len(VOCAB), k=5) == []


# --- fusion ----------------------------------------------------------------------------------


def _hit(path: str, source: str, score: float) -> Hit:
    return Hit(chunk(path, "x", "text"), score, source)


def test_fusion_ranks_by_position_not_by_score() -> None:
    """A bm25 score and a cosine similarity are not comparable numbers.

    Any weighted sum needs a per-corpus weight that somebody will tune once, on one repository. RRF
    keeps only the order — so a keyword score of 40 does not out-shout a cosine of 0.9.
    """
    keyword = [_hit("a.py", "fts", 40.0), _hit("b.py", "fts", 39.0)]
    vector = [_hit("b.py", "vector", 0.91), _hit("c.py", "vector", 0.90)]

    fused = reciprocal_rank_fusion([keyword, vector], limit=3)

    # b.py is second in both lists and first overall: agreement beats a single confident opinion.
    assert fused[0].chunk.path == "b.py"


def test_a_hit_both_retrievers_found_is_labelled_hybrid() -> None:
    fused = reciprocal_rank_fusion([[_hit("a.py", "fts", 1)], [_hit("a.py", "vector", 1)]])
    assert fused[0].source == "hybrid"


def test_a_hit_only_one_retriever_found_keeps_that_name() -> None:
    # Calling a keyword-only hit "hybrid" would make the label useless for working out why a result
    # is wrong.
    fused = reciprocal_rank_fusion([[_hit("a.py", "fts", 1)], []])
    assert fused[0].source == "fts"


def test_the_prior_never_overrules_the_retriever() -> None:
    """Importance is not a retrieval opinion.

    A first draft multiplied the fused score by the prior and this test caught it: consecutive RRF
    scores are 1/61 and 1/62, a 1.6% gap that any useful prior flips. The multiplying version let a
    central file outrank an exact match — the case retrieval is most useful for.
    """
    ranked = [_hit("core/verify.py", "fts", 1), _hit("scripts/tiny.py", "fts", 1)]
    prior = {"scripts/tiny.py": 0.9, "core/verify.py": 0.1}

    fused = reciprocal_rank_fusion([ranked], prior=prior, limit=2)

    assert fused[0].chunk.path == "core/verify.py", "a prior overruled the retriever's own ranking"


def test_the_prior_decides_when_the_retrievers_do_not() -> None:
    """The other half: on a genuine tie it is the only signal there is.

    Both chunks are rank 1 of their own list, so RRF scores them identically — and answering an
    arbitrary one of the two would make the same query return different answers between runs.
    """
    fused = reciprocal_rank_fusion(
        [[_hit("core/verify.py", "fts", 1)], [_hit("scripts/tiny.py", "vector", 1)]],
        prior={"core/verify.py": 0.9, "scripts/tiny.py": 0.1},
        limit=2,
    )

    assert fused[0].chunk.path == "core/verify.py"


def test_fusion_of_nothing_is_nothing() -> None:
    assert reciprocal_rank_fusion([[], []]) == []
