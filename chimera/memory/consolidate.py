"""LLM-assisted memory consolidation — compress clusters of similar facts into one.

Pruning by value drops low-value memories; consolidation instead *merges* near-duplicate
facts into a single, richer one, cutting bloat while preserving specifics. Clustering is a
pure, deterministic token-Jaccard grouping; the summariser is injected (a
``list[str] -> str`` callable), so the logic is testable without a model. :func:`model_summarizer`
supplies the real, model-backed one. A merge is a write, so it's opt-in (``memory consolidate``).
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable, Iterable
from pathlib import Path

from chimera.memory.models import MemoryItem

Summarizer = Callable[[list[str]], str]

#: Below this many characters a token is a fragment or a function word, and Jaccard overlap on
#: those measures grammar rather than meaning. It does NOT apply to scripts where one character is
#: a word: `tokens` splits Han per character, and a two-character rule would discard every one of
#: them — which is how Chinese and Russian memories came to never cluster at all.
_MIN_LENGTH = 3


def _tokens(text: str) -> set[str]:
    """Significant tokens, through the shared tokenizer.

    This module had its own ``[a-z0-9]+``, so two nearly identical Russian facts produced two empty
    sets and `_similar` refused them both. Measured: Portuguese and English clustered, Russian and
    Chinese never did — consolidation was inert in those languages rather than merely worse.
    """
    from chimera.memory.tokens import tokens as _shared

    return {t for t in _shared(text) if len(t) >= _MIN_LENGTH or not t.isascii()}


def _similar(a: str, b: str, threshold: float) -> bool:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / len(ta | tb) >= threshold  # Jaccard overlap


def group_similar(texts: list[str], *, threshold: float = 0.5) -> list[list[int]]:
    """Greedily group *indices* of texts whose content is similar (Jaccard >= threshold).

    A generic clustering primitive reused by memory consolidation and skill nudges.
    """
    groups: list[list[int]] = []
    reps: list[str] = []  # one representative text per group
    for index, text in enumerate(texts):
        for group_index, rep in enumerate(reps):
            if _similar(text, rep, threshold):
                groups[group_index].append(index)
                break
        else:
            groups.append([index])
            reps.append(text)
    return groups


def cluster(items: list[MemoryItem], *, threshold: float = 0.5) -> list[list[MemoryItem]]:
    """Greedily group memories whose content is similar (Jaccard >= threshold)."""
    groups = group_similar([item.content for item in items], threshold=threshold)
    return [[items[i] for i in group] for group in groups]


def model_summarizer(backend: object, model: str | None = None) -> Summarizer:
    """A summariser that asks a model to merge related facts into one concise fact."""

    def summarize(facts: list[str]) -> str:
        from chimera.providers.gateway import Message

        joined = "\n".join(f"- {fact}" for fact in facts)
        prompt = (
            f"Merge these related facts into ONE concise fact, preserving every specific "
            f"detail (names, numbers, preferences):\n{joined}\n\nReply with only the merged fact."
        )
        reply = backend.complete(  # type: ignore[attr-defined]
            [Message(role="user", content=prompt)], model=model, temperature=0.2
        )
        merged: str = reply.content.strip()
        return merged

    return summarize


def cluster_signature(items: Iterable[MemoryItem]) -> str:
    """A fingerprint of one cluster: its member ids and the exact text of each.

    Content is in it, not only ids: an edited member is a different question to put to the
    summarizer, and a blank answer remembered against the old text must not silence the new one.
    """
    parts = sorted(f"{item.id}\x00{' '.join(item.content.lower().split())}" for item in items)
    return hashlib.sha256("\x01".join(parts).encode("utf-8")).hexdigest()


class ConsolidationLedger:
    """Which clusters the summarizer was already asked about, so nothing is paid for twice.

    Consolidation used to re-cluster the whole store on every pass and ask the model about every
    cluster again, including the ones it had answered with nothing last time: the same blank call
    billed on each session end, forever, because nothing recorded that it had been made. A cluster
    whose members and text are unchanged since it was settled is skipped; any change makes a new
    signature, so only what changed is processed.

    With a ``path`` the memory survives the process (it lives beside the store); without one it
    lasts as long as the manager, which is still enough for a pass and its immediate repeat.
    A file that cannot be read starts an empty ledger: the cost of forgetting is one repeated call,
    never a lost fact.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path is not None else None
        self._lock = threading.Lock()
        self._settled: set[str] = set()
        if self._path is not None:
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self._settled = {str(entry) for entry in data}
            except (OSError, ValueError):
                self._settled = set()

    def settled(self, signature: str) -> bool:
        with self._lock:
            return signature in self._settled

    def settle(self, signature: str) -> None:
        with self._lock:
            self._settled.add(signature)
            if self._path is None:
                return
            try:
                from chimera.core.filelock import atomic_write_text

                self._path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write_text(self._path, json.dumps(sorted(self._settled)))
            except OSError:
                pass  # in-memory still holds; a lost write costs one repeated call, nothing else
