"""Local retrieval over a repository.

Cross-file search (:mod:`chimera.core.search`) answers "where does this string appear". This answers
the question that has no string: "where do we decide whether to keep a change", asked of a codebase
that calls it `verify-or-revert` and never uses the word "decide".

Three parts, and the seam between them is the point:

* :mod:`chimera.rag.chunks` cuts the repository at symbol boundaries, because the unit of retrieval
  decides what retrieval can answer;
* :mod:`chimera.rag.store` keeps them in one SQLite file with an FTS5 index and optional vectors,
  the embedder injected so a machine without one degrades to keyword search instead of failing;
* :mod:`chimera.rag.hybrid` fuses the two rankings by rank rather than by score, so there is no
  weight to tune per repository.

What this is NOT: a claim that it helps. :mod:`chimera.eval.rag_bench` measures retrieval against
probes with known answers, keyword-only versus hybrid, and the honest thing to do with that number
is publish it either way.
"""

from chimera.rag.chunks import Chunk, chunk_source, walk
from chimera.rag.hybrid import reciprocal_rank_fusion
from chimera.rag.store import ChunkStore, EmbedFn, Hit, default_index_path

__all__ = [
    "Chunk",
    "ChunkStore",
    "EmbedFn",
    "Hit",
    "chunk_source",
    "default_index_path",
    "reciprocal_rank_fusion",
    "walk",
]
