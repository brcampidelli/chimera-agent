"""Does retrieval over this repository actually find the right code?

The pre-registered A/B for :mod:`chimera.rag`, and it is deliberately built to be able to say NO.

**The probes are derived, not written.** For every documented Python symbol, the query is the first
line of its docstring with the symbol's own name-tokens stripped out. `resolve_verify` documented as
"The verify command actually used" becomes the query "the command actually used" — a question about
the function that does not contain its name. That is mechanical, reproducible, and free of the bias
of an author writing queries for a system they built.

**And the docstring is removed from what gets indexed.** Without that, the corpus contains the
question: the target chunk holds the very sentence the query is made of, so keyword retrieval finds
it by matching a string to itself. Measured before the fix — recall@10 of **1.00** across 400 probes
on 2,690 chunks, a perfect score for a question nobody asked. What is indexed is the CODE, which is
the honest shape of the real task: somebody describes a behaviour, and the implementation does not
contain their description.

It is still not a real user question. A docstring shares an author and a vocabulary with the code
below it, so a keyword retriever does better here than it will in the field — and that direction is
the safe one, because it makes this a hard test for the vectors and an easy one for the baseline.

**Measure the ceiling before building the machine.** ``headroom`` is the share of probes keyword
retrieval already misses. It needs no embedder and no provider, and it bounds everything the
vectors could possibly add. If it is small, the honest answer is that this whole layer is not worth
its cost — which is a thing this file is allowed to conclude.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

from chimera.rag.chunks import Chunk, walk
from chimera.rag.hybrid import reciprocal_rank_fusion
from chimera.rag.store import EMBED_BATCH, ChunkStore, EmbedFn

#: Words too common to make a query about anything.
_STOP = frozenset((
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "has",
    "have",
    "in",
    "is",
    "it",
    "its",
    "of",
    "on",
    "or",
    "that",
    "the",
    "this",
    "to",
    "was",
    "were",
    "with",
    "when",
    "where",
    "which",
    "while",
    "what",
    "how",
    "why",
    "not",
    "no",
    "if",
    "then",
    "than",
    "into",
    "out",
    "up",
    "down",
    "over",
    "under",
    "only",
    "rather",
    "never",
    "always",
    "must",
    "can",
    "cannot",
    "does",
    "do",
    "done",
    "use",
    "used",
    "using",
    "make",
    "makes",
    "made",
    "one",
    "two",
    "both",
    "each",
    "every",
    "any",
    "all",
    "some",
    "more",
    "most",
    "less",
    "least",
    "same",
    "other",
    "another",
    "such",
    "so",
    "because",
    "since",
    "about",
))

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass
class Probe:
    """One question with a known answer."""

    query: str
    #: The chunk id that answers it.
    target: str
    path: str


@dataclass
class RagReport:
    """What retrieval found, per retriever."""

    probes: int = 0
    chunks: int = 0
    keyword_recall: float = 0.0
    #: None when no embedder was supplied — never 0.0, which would read as "the vectors failed".
    vector_recall: float | None = None
    hybrid_recall: float | None = None
    #: Share of probes keyword retrieval misses: the ceiling on what any semantic layer can add.
    headroom: float = 0.0
    k: int = 10
    notes: list[str] = field(default_factory=list)
    #: Which model produced the vectors, and how wide they are. Inside the record rather than in
    #: a footnote: there is no conversion from one model's vector space to another's, so a recall
    #: figure without the embedder that produced it is a number about nothing in particular.
    embedder: str = ""
    dimensions: int = 0
    #: Hit/miss per probe, aligned across the three lists. Totals cannot be tested against each
    #: other — the arms answer the SAME probes, so the comparison that carries signal is paired,
    #: and a paired test needs the pairs. `chimera/eval/paired.py` consumes these directly.
    keyword_hit: list[bool] = field(default_factory=list)
    vector_hit: list[bool] = field(default_factory=list)
    hybrid_hit: list[bool] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "probes": self.probes,
            "chunks": self.chunks,
            "k": self.k,
            "keyword_recall": round(self.keyword_recall, 4),
            "vector_recall": None if self.vector_recall is None else round(self.vector_recall, 4),
            "hybrid_recall": None if self.hybrid_recall is None else round(self.hybrid_recall, 4),
            "headroom": round(self.headroom, 4),
            "embedder": self.embedder,
            "dimensions": self.dimensions,
            "notes": list(self.notes),
        }


def _name_tokens(symbol: str) -> set[str]:
    """Every token of a symbol name: `resolve_verify` and `Gateway.send` both split fully.

    Stripped from the query so the probe cannot be answered by echoing the name back — which would
    measure whether the retriever can match a string to itself.
    """
    parts: set[str] = set()
    for piece in re.split(r"[._]", symbol):
        if not piece:
            continue
        parts.add(piece.lower())
        # camelCase and PascalCase, so `resolveVerify` splits too.
        parts.update(m.group(0).lower() for m in re.finditer(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", piece))
    return parts


def build_probes(chunks: list[Chunk], *, min_words: int = 4) -> list[Probe]:
    """One probe per documented symbol whose docstring survives having its own name removed."""
    probes: list[Probe] = []
    for chunk in chunks:
        if chunk.kind not in ("function", "class") or not chunk.symbol:
            continue
        summary = _docstring_summary(chunk.text)
        if not summary:
            continue
        banned = _name_tokens(chunk.symbol) | _name_tokens(Path(chunk.path).stem)
        words = [
            w for w in _WORD.findall(summary)
            if w.lower() not in banned and w.lower() not in _STOP and len(w) > 2
        ]
        if len(words) < min_words:
            # Too little left to be a question. Keeping it would measure retrieval against noise.
            continue
        probes.append(Probe(query=" ".join(words), target=chunk.ident, path=chunk.path))
    return probes


def _without_docstring(chunk: Chunk) -> Chunk:
    """The same chunk with its docstring lines removed, id and span unchanged.

    Textual rather than AST-based: the chunk has to keep the identity it was stored under, and
    re-parsing then unparsing would rewrite formatting the retriever indexes. Removing the
    triple-quoted block at the top of the body is the whole job.
    """
    lines = chunk.text.splitlines(keepends=True)
    out: list[str] = []
    inside = False
    fence = ""
    for line in lines:
        stripped = line.strip()
        if not inside:
            for quote in ('"""', "'''"):
                if stripped.startswith(quote) or stripped.startswith(f"r{quote}"):
                    body = stripped.split(quote, 1)[1]
                    # A one-line docstring opens and closes on the same line.
                    if not body.endswith(quote) or len(stripped) <= len(quote):
                        inside, fence = True, quote
                    break
            else:
                out.append(line)
                continue
            continue
        if fence in line:
            inside = False
    text = "".join(out)
    return replace(chunk, text=text if text.strip() else chunk.text)


def _docstring_summary(text: str) -> str:
    """The first line of the chunk's docstring, or "" when it has none."""
    try:
        tree = ast.parse(text.strip())
    except (SyntaxError, ValueError):
        return ""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            doc = ast.get_docstring(node)
            if doc:
                return doc.strip().splitlines()[0].strip()
    return ""


def run_rag_bench(
    root: Path,
    *,
    index_path: Path,
    embed: EmbedFn | None = None,
    embedder: str = "",
    k: int = 10,
    max_probes: int = 400,
) -> RagReport:
    """Index ``root``, probe it, and report recall for each retriever.

    ``embed`` is optional. Without it the vector and hybrid figures are None rather than zero: an
    embedder that was never called did not fail, and a bench that reports 0.0 for it invites exactly
    the wrong conclusion.
    """
    chunks = walk(root)
    probes = build_probes(chunks)[:max_probes]
    # Indexed WITHOUT the docstrings the probes are made from. Leaving them in puts the question
    # inside the corpus, and the measurement becomes "can FTS match a string to itself" — which it
    # can, at recall@10 = 1.00.
    store = ChunkStore(index_path)
    store.replace_all([_without_docstring(c) for c in chunks])
    report = RagReport(probes=len(probes), chunks=len(chunks), k=k)
    if not probes:
        report.notes.append("no documented symbols to probe")
        return report

    embedded = 0
    if embed is not None:
        # `embedder=` arms `_align_embedder`, which zeroes every vector when the model identity or
        # width changes. Without it the guard is inert in exactly the run that introduces vectors,
        # and a mixed-dimension index reports healthy while returning nothing: `_cosine` gives 0.0
        # on a mismatch and the `score > 0` filter then drops the lot.
        embedded = store.embed_missing(embed, embedder=embedder)
        report.embedder = embedder
        if embedded == 0:
            report.notes.append("the embedder produced no vectors; semantic figures are unavailable")

    # One batched pass rather than one call per probe inside the loop. Same money, and it is the
    # difference between a run measured in seconds and one measured in minutes.
    query_vectors: list[list[float]] = []
    if embedded and embed is not None:
        for start in range(0, len(probes), EMBED_BATCH):
            batch = [p.query for p in probes[start : start + EMBED_BATCH]]
            query_vectors.extend(embed(batch))
        if len(query_vectors) != len(probes):
            report.notes.append(
                f"the embedder returned {len(query_vectors)} vectors for {len(probes)} queries; "
                "semantic figures are unavailable"
            )
            embedded = 0
        elif query_vectors:
            report.dimensions = len(query_vectors[0])

    for index, probe in enumerate(probes):
        keyword = store.search_keyword(probe.query, k=k)
        report.keyword_hit.append(any(h.chunk.ident == probe.target for h in keyword))
        if embedded:
            vector = store.search_vector(query_vectors[index], k=k)
            report.vector_hit.append(any(h.chunk.ident == probe.target for h in vector))
            fused = reciprocal_rank_fusion([keyword, vector], limit=k)
            report.hybrid_hit.append(any(h.chunk.ident == probe.target for h in fused))

    total = len(probes)
    report.keyword_recall = sum(report.keyword_hit) / total
    report.headroom = 1.0 - report.keyword_recall
    if embedded:
        report.vector_recall = sum(report.vector_hit) / total
        report.hybrid_recall = sum(report.hybrid_hit) / total
    else:
        report.notes.append("no embedder supplied — the ceiling below is what one could add at most")
    store.close()
    return report
