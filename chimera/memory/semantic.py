"""Semantic recall over memory — cosine ranking on embeddings, with a content cache.

Keyword/FTS search finds a fact only when the query shares a token with it; a paraphrase
(``"physician"`` vs a fact about a ``"doctor"``) has no shared token and is missed entirely
— the exact gap :mod:`chimera.eval.memory_bench` measures. This index closes it by embedding
both fact and query into a vector space where synonyms land close together, then ranking by
cosine similarity.

The embedder is **injected** (``EmbedFn``), so this is unit-testable with a deterministic fake
and provider-agnostic in production (see :meth:`chimera.providers.gateway.LLMGateway.embed`).
Item vectors are cached by content, so repeated searches over a stable corpus embed only the
new query — one batched embed call for the corpus, one per distinct query thereafter.

The cache is per instance, and the app builds a manager per request, so on its own it lasted one
request: every search re-embedded the whole corpus. ``cache_path`` puts it on disk, keyed by the
SHA-256 of the text (plus a namespace naming the embedding model), so a vector is paid for once
across requests and processes. Disk errors never fail recall: the vector is simply embedded again.
"""

from __future__ import annotations

import contextlib
import hashlib
import math
import sqlite3
import threading
from array import array
from collections.abc import Callable, Sequence
from pathlib import Path

from chimera.memory.models import MemoryItem

# A batched embedder: a list of texts -> a list of equal-length vectors, order-preserving.
EmbedFn = Callable[[list[str]], list[list[float]]]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two vectors; 0.0 if either is empty or zero-norm."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


#: Rows kept in the on-disk cache. Queries land here too (a repeated search should be free), and
#: nothing else would ever evict a deleted fact's vector, so the oldest rows go past this.
_MAX_CACHED_VECTORS = 20_000


class VectorCache:
    """Embedding vectors on disk, keyed by the hash of the text they embed.

    SQLite in WAL mode with a busy timeout, because several processes (the app, the CLI, the bot)
    may hold the same home. Every method swallows ``sqlite3.Error``: the cache is an optimisation
    and a broken file must cost one re-embed, not a failed search.
    """

    def __init__(self, path: Path, namespace: str = "") -> None:
        self._namespace = namespace
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(path), check_same_thread=False, timeout=30)
            conn.execute("PRAGMA busy_timeout=30000")
            # A filesystem without shared memory refuses WAL; the default journal still works.
            with contextlib.suppress(sqlite3.Error):
                conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS vectors ("
                "key TEXT PRIMARY KEY, dim INTEGER NOT NULL, vec BLOB NOT NULL)"
            )
            conn.commit()
            self._conn = conn
        except (sqlite3.Error, OSError):
            self._conn = None

    def _key(self, text: str) -> str:
        return hashlib.sha256(f"{self._namespace}\x00{text}".encode()).hexdigest()

    def get_many(self, texts: list[str]) -> dict[str, list[float]]:
        """The vectors already stored for ``texts``, by text; absent ones are not in the result."""
        if self._conn is None or not texts:
            return {}
        found: dict[str, list[float]] = {}
        by_key = {self._key(text): text for text in texts}
        keys = list(by_key)
        try:
            with self._lock:
                # In chunks, one statement each: a row-by-row loop costs a round trip per fact, and
                # the corpus is the whole store. 500 stays under SQLite's variable limit.
                for start in range(0, len(keys), 500):
                    chunk = keys[start : start + 500]
                    marks = ",".join("?" * len(chunk))
                    rows = self._conn.execute(
                        f"SELECT key, dim, vec FROM vectors WHERE key IN ({marks})", chunk
                    ).fetchall()
                    for key, dim, blob in rows:
                        vec = array("f")
                        vec.frombytes(blob)
                        if len(vec) == dim:
                            found[by_key[key]] = list(vec)
        except sqlite3.Error:
            return found
        return found

    def put_many(self, vectors: dict[str, list[float]]) -> None:
        if self._conn is None or not vectors:
            return
        try:
            with self._lock:
                self._conn.executemany(
                    "INSERT OR REPLACE INTO vectors (key, dim, vec) VALUES (?, ?, ?)",
                    [
                        (self._key(text), len(vec), array("f", vec).tobytes())
                        for text, vec in vectors.items()
                    ],
                )
                self._conn.execute(
                    "DELETE FROM vectors WHERE rowid <= (SELECT MAX(rowid) FROM vectors) - ?",
                    (_MAX_CACHED_VECTORS,),
                )
                self._conn.commit()
        except sqlite3.Error:
            return


#: Below this cosine a hit is not "similar", it is merely the least dissimilar of what is stored.
#: ``search`` used to return the top-k whatever their score, which was harmless while a lexical floor
#: stood behind it and is not once the gate trusts the layer (P0-07): an orthogonal fact entered the
#: prompt. A conservative, UNMEASURED floor: it drops the clearly unrelated, and a store whose
#: embedder scores real paraphrases lower than this can lower it per index (``min_similarity``).
DEFAULT_MIN_SIMILARITY = 0.2


class SemanticIndex:
    """Ranks memory items against a query by embedding cosine similarity.

    Stateless w.r.t. the corpus — :meth:`search` takes the live item list each call, so it
    always reflects the current store. A content-keyed cache means only unseen texts are
    embedded, so passing the same corpus repeatedly is cheap.
    """

    def __init__(
        self,
        embed: EmbedFn,
        *,
        cache_path: Path | None = None,
        namespace: str = "",
        min_similarity: float = DEFAULT_MIN_SIMILARITY,
    ) -> None:
        self._embed = embed
        self._min_similarity = min_similarity
        self._cache: dict[str, list[float]] = {}
        self._disk = VectorCache(cache_path, namespace) if cache_path is not None else None

    def _vectors(self, texts: list[str]) -> list[list[float]]:
        missing = [t for t in dict.fromkeys(texts) if t not in self._cache]
        if missing and self._disk is not None:
            self._cache.update(self._disk.get_many(missing))
            missing = [t for t in missing if t not in self._cache]
        if missing:
            fresh = dict(zip(missing, self._embed(missing), strict=True))
            self._cache.update(fresh)
            if self._disk is not None:
                self._disk.put_many(fresh)
        return [self._cache[t] for t in texts]

    def near_fact(
        self, query: str, items: list[MemoryItem], *, threshold: float
    ) -> MemoryItem | None:
        """Find one near fact above threshold; used only by opt-in supersession."""
        if not items:
            return None
        item_vecs = self._vectors([item.content for item in items])
        query_vec = self._vectors([query])[0]
        scored = [
            (cosine(query_vec, vector), item)
            for item, vector in zip(items, item_vecs, strict=True)
        ]
        score, item = max(scored, key=lambda pair: pair[0])
        return item if score >= threshold else None

    def search(self, query: str, items: list[MemoryItem], k: int) -> list[MemoryItem]:
        """Top-``k`` items by cosine similarity to ``query``, keeping only those at or above the floor."""
        if not items:
            return []
        item_vecs = self._vectors([item.content for item in items])
        query_vec = self._vectors([query])[0]
        scored = sorted(
            ((cosine(query_vec, vec), item) for item, vec in zip(items, item_vecs, strict=True)),
            key=lambda pair: pair[0],
            reverse=True,
        )
        return [item for score, item in scored[:k] if score >= self._min_similarity]
