"""The index: chunks, an FTS5 table, and vectors — in one SQLite file.

Three decisions worth naming.

**One file, not a service.** The whole product is local-first, and a retrieval layer that needs a
daemon running is a retrieval layer that is down half the time. SQLite is already how memory is
stored here; this is the same file format, the same backup story, and the same absence of a port.

**Vectors behind brute force, not in front of it.** `sqlite-vec` is an optional extension that may
be absent, may fail to load against a given SQLite build, and is not present on the machine this was
written on. So the exact path — cosine over every vector — is the one that always works, and the
extension is an accelerator asked for by name. Ten thousand chunks of 384 floats is a few million
multiplications: slower than an index and far faster than the model call it precedes.

**Nothing is embedded here.** The embedder is injected (`EmbedFn`, the same seam
:mod:`chimera.memory.semantic` already uses), so this module never decides whether that means
Ollama, a hosted API, or a fake in a test — and a machine with no embedder gets keyword retrieval
instead of an error.
"""

from __future__ import annotations

import json
import re
import sqlite3
import struct
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from chimera.rag.chunks import Chunk
from chimera.telemetry import get_logger

_log = get_logger("rag.store")

#: A batched embedder — the same shape as :data:`chimera.memory.semantic.EmbedFn`.
EmbedFn = Callable[[list[str]], list[list[float]]]

#: Rows embedded per call. Large enough that a repository is a handful of round trips, small enough
#: that a failure loses seconds rather than the whole index.
EMBED_BATCH = 64

#: Word tokens for an FTS query. Anything else a person types is punctuation, not syntax.
_TOKEN = re.compile(r"[A-Za-z0-9_]+")

#: Below this bm25 score, the whole result matched only on words common to every document.
#: Corpus-dependent by nature — bm25 scales with corpus size and document length — but the SEPARATION
#: is not: measured here, real matches score 0.8 to 1.6 and ubiquitous-term matches 1e-06. A floor
#: anywhere in that gap behaves identically, which is why a round number three orders below the
#: weakest real match is defensible rather than tuned.
_NOISE_FLOOR = 1e-3

#: Kept relative to the best hit, so a long tail of near-misses does not pad the candidate list.
_RELATIVE_FLOOR = 1e-3


@dataclass(frozen=True)
class Hit:
    """One retrieved chunk, with the score that retrieved it."""

    chunk: Chunk
    score: float
    #: "fts" | "vector" | "hybrid" — which retriever produced it. Kept because a hybrid result that
    #: cannot say where a hit came from cannot be debugged when it is wrong.
    source: str


def _pack(vector: Sequence[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def _unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = na = nb = 0.0
    for x, y in zip(a, b, strict=True):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return float(dot / ((na**0.5) * (nb**0.5)))


class ChunkStore:
    """Chunks on disk, searchable by keyword and — when embedded — by meaning."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self.fts_available = self._init_schema()

    # --- schema ---------------------------------------------------------------------------

    def _init_schema(self) -> bool:
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS chunks ("
            "ident TEXT PRIMARY KEY, path TEXT, symbol TEXT, kind TEXT, "
            "start_line INTEGER, end_line INTEGER, text TEXT, vector BLOB)"
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path)")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)"
        )
        try:
            self._conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5("
                "ident UNINDEXED, label, text)"
            )
            self._conn.commit()
            return True
        except sqlite3.OperationalError:
            # FTS5 is compiled into most Python builds and absent from a few. Keyword search then
            # falls back to LIKE, which is worse and works — the same trade the memory store makes.
            self._conn.commit()
            return False

    def close(self) -> None:
        self._conn.close()

    # --- writing --------------------------------------------------------------------------

    def replace_all(self, chunks: list[Chunk]) -> int:
        """Rebuild the index from scratch. Returns how many chunks were stored.

        Wholesale rather than incremental, and that is the honest shape for now: an incremental
        index needs to know which files changed AND which chunks those files used to produce, and a
        stale row that nobody deleted answers confidently about code that has moved. Rebuilding a
        few thousand chunks takes under a second; the embeddings are what cost, and those are
        preserved per chunk id below.
        """
        keep = {
            row["ident"]: row["vector"]
            for row in self._conn.execute("SELECT ident, vector FROM chunks WHERE vector IS NOT NULL")
        }
        self._conn.execute("DELETE FROM chunks")
        if self.fts_available:
            self._conn.execute("DELETE FROM chunks_fts")
        rows = [
            (c.ident, c.path, c.symbol, c.kind, c.start_line, c.end_line, c.text, keep.get(c.ident))
            for c in chunks
        ]
        self._conn.executemany(
            "INSERT OR REPLACE INTO chunks "
            "(ident, path, symbol, kind, start_line, end_line, text, vector) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        if self.fts_available:
            self._conn.executemany(
                "INSERT INTO chunks_fts (ident, label, text) VALUES (?, ?, ?)",
                [(c.ident, c.label, c.text) for c in chunks],
            )
        self._conn.commit()
        return len(rows)

    def embed_missing(self, embed: EmbedFn, *, limit: int = 100_000, embedder: str = "") -> int:
        """Embed the chunks that have no vector yet. Returns how many were embedded.

        Resumable by construction: a run that dies halfway leaves the vectors it wrote, and the next
        call picks up the rest. Nothing here retries a failed batch — a provider that is down should
        surface as "some chunks are not embedded" rather than as a loop nobody can stop.

        ``embedder`` names the model producing the vectors, and changing it invalidates every vector
        already stored. Nothing detected that: `embed_missing` only touches `WHERE vector IS NULL`,
        so old vectors of the old dimension stayed forever; `_cosine` returns 0.0 when the lengths
        differ, the `score > 0` filter then drops every one of them, and `stats()` kept counting
        them as embedded. The index reported healthy and returned nothing. The `meta` table this
        checks against existed with three accessors and no callers.
        """
        # BEFORE selecting what is pending. Checking after the first batch does nothing in the
        # case that matters — an index that is fully embedded has no pending rows, so the loop never
        # runs and the stale vectors stay stale forever.
        self._align_embedder(embed, embedder)
        pending = self._conn.execute(
            "SELECT ident, text FROM chunks WHERE vector IS NULL LIMIT ?", (limit,)
        ).fetchall()
        done = 0
        for start in range(0, len(pending), EMBED_BATCH):
            batch = pending[start : start + EMBED_BATCH]
            try:
                vectors = embed([row["text"] for row in batch])
                if start == 0 and vectors and embedder:
                    self._record_embedder(embedder, len(vectors[0]))
            except Exception as exc:  # noqa: BLE001 — a dead embedder is a degraded index, not a crash
                _log.warning("embedding failed after %d chunks: %s", done, exc)
                break
            if len(vectors) != len(batch):
                _log.warning("embedder returned %d vectors for %d texts", len(vectors), len(batch))
                break
            self._conn.executemany(
                "UPDATE chunks SET vector = ? WHERE ident = ?",
                [(_pack(v), row["ident"]) for row, v in zip(batch, vectors, strict=True)],
            )
            self._conn.commit()
            done += len(batch)
        return done

    def _record_embedder(self, embedder: str, dimension: int) -> None:
        """Store the signature of whatever produced the vectors now in the table."""
        self.set_meta("embedder", f"{embedder}:{dimension}")

    def _align_embedder(self, embed: EmbedFn, embedder: str) -> None:
        """Drop every stored vector when the embedder that would produce them has changed.

        Derived artefacts are not migrated, they are rebuilt: there is no conversion from one
        model's vector space to another's. Keeping the old ones costs a silent, permanent recall
        failure — `_cosine` returns 0.0 on a length mismatch, the `score > 0` filter drops them all,
        and `stats()` goes on counting them as embedded. Dropping them costs one re-embed, in the
        log, which is the honest price of changing embedder.

        The signature is name AND dimension, so a provider that resizes a model under the same slug
        is caught too — the case nobody would think to look for. Learning the dimension costs one
        embed of one short string, and only when a signature was already recorded: an index built
        before this field existed has no signature and is left exactly as it is, because treating an
        unknown as a mismatch would throw away every existing index on upgrade.
        """
        anterior = self.get_meta("embedder")
        if not embedder or not anterior:
            return
        try:
            sonda = embed(["x"])
        except Exception as exc:  # noqa: BLE001 — a dead embedder must not empty the index
            _log.warning("could not check the embedder signature: %s", exc)
            return
        if not sonda:
            return
        assinatura = f"{embedder}:{len(sonda[0])}"
        if anterior == assinatura:
            return
        apagados = self._conn.execute(
            "UPDATE chunks SET vector = NULL WHERE vector IS NOT NULL"
        ).rowcount
        self._conn.commit()
        _log.warning(
            "embedder changed (%s -> %s); dropped %d vectors, they will be rebuilt",
            anterior, assinatura, apagados,
        )
        self.set_meta("embedder", assinatura)

    def set_meta(self, key: str, value: str) -> None:
        self._conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
        self._conn.commit()

    def get_meta(self, key: str) -> str:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return str(row["value"]) if row else ""

    # --- reading --------------------------------------------------------------------------

    def stats(self) -> dict[str, int]:
        total = self._conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
        embedded = self._conn.execute(
            "SELECT COUNT(*) AS n FROM chunks WHERE vector IS NOT NULL"
        ).fetchone()["n"]
        files = self._conn.execute("SELECT COUNT(DISTINCT path) AS n FROM chunks").fetchone()["n"]
        return {"chunks": int(total), "embedded": int(embedded), "files": int(files)}

    def _row_to_chunk(self, row: sqlite3.Row) -> Chunk:
        return Chunk(
            path=row["path"],
            symbol=row["symbol"],
            kind=row["kind"],
            start_line=int(row["start_line"]),
            end_line=int(row["end_line"]),
            text=row["text"],
        )

    def search_keyword(self, query: str, k: int = 10) -> list[Hit]:
        """Keyword retrieval: FTS5 where available, LIKE where it is not."""
        if not query.strip():
            return []
        if self.fts_available:
            # Each WORD quoted, then OR'd — not the whole query quoted as one phrase.
            #
            # Quoting the whole string was the safe-looking version and it made every multi-word
            # query an exact-phrase search. `rag_bench` measured the result before anything was
            # built on top of it: recall@10 of 0.5%, which reads as "keyword search is useless" and
            # was really "this function is broken". Quoting is still per token, because an
            # unbalanced quote or a bare `AND` in a search box is otherwise a 500, and `NEAR(` is
            # not what someone typing "near the top" is asking for.
            words = _TOKEN.findall(query)
            if not words:
                return []
            phrase = " OR ".join(f'"{w}"' for w in words)
            try:
                rows = self._conn.execute(
                    "SELECT c.*, bm25(chunks_fts) AS rank FROM chunks_fts "
                    "JOIN chunks c ON c.ident = chunks_fts.ident "
                    "WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?",
                    (phrase, k),
                ).fetchall()
            except sqlite3.OperationalError as exc:
                _log.debug("fts query failed, falling back to LIKE: %s", exc)
                rows = []
            if rows:
                # bm25 returns a NEGATIVE score where lower is better. Flipped so every score in
                # this module means the same direction; a mixed convention is how a fusion silently
                # ranks the worst result first.
                #
                # Noise is dropped, symmetrically with the vector side. The query is an OR of its
                # words, so "discard the edits" matches every chunk containing "the" — and handing
                # the fusion four confident non-answers means a retriever that found nothing still
                # produces a rank-1 result.
                #
                # Measured on a four-chunk corpus: a real single-term match scores 0.81, a
                # whole-phrase match 1.63, and a match on a term present in every document
                # 1.4e-06 — six orders of magnitude apart, which is what makes a threshold
                # possible at all. (Not zero: an earlier version of this filter said "score > 0"
                # on the assumption that bm25 zeroes a zero-IDF term, and it does not.)
                scored = [Hit(self._row_to_chunk(r), -float(r["rank"]), "fts") for r in rows]
                best = max((h.score for h in scored), default=0.0)
                if best < _NOISE_FLOOR:
                    return []  # everything matched only on ubiquitous words
                return [h for h in scored if h.score >= best * _RELATIVE_FLOOR]

        like = f"%{query}%"
        rows = self._conn.execute(
            "SELECT * FROM chunks WHERE text LIKE ? OR symbol LIKE ? LIMIT ?", (like, like, k)
        ).fetchall()
        return [Hit(self._row_to_chunk(r), 1.0, "fts") for r in rows]

    def search_vector(self, vector: Sequence[float], k: int = 10) -> list[Hit]:
        """Nearest chunks by cosine. Exact, over every embedded row.

        No index. `sqlite-vec` would make this sub-linear and it is absent from the machine this was
        written on — so the path that always works is the one that ships, and an accelerator can sit
        in front of it later without changing what a caller sees. At ten thousand chunks this is a
        few million multiplications: slower than an index, and far faster than the model call it
        precedes.
        """
        if not vector:
            return []
        rows = self._conn.execute("SELECT * FROM chunks WHERE vector IS NOT NULL").fetchall()
        scored = [(_cosine(vector, _unpack(r["vector"])), r) for r in rows]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [Hit(self._row_to_chunk(r), score, "vector") for score, r in scored[:k] if score > 0]


def default_index_path(home: Path, workspace: Path) -> Path:
    """Where a workspace's index lives — under the app's home, keyed by the workspace path.

    Not inside the workspace: an index file in someone's repository is a file they have to gitignore
    and a diff they have to explain, for a cache they did not ask to store there.
    """
    digest = str(abs(hash(str(Path(workspace).resolve()))))[:16]
    return Path(home) / "rag" / f"{Path(workspace).name}-{digest}.sqlite3"


def load_json_meta(raw: str) -> dict[str, object]:
    try:
        parsed = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
