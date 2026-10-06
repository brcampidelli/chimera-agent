"""The Memory Manager — curated memory with ADD/UPDATE/DELETE/NOOP operations.

Inspired by Memory-R1: instead of appending blindly, ``remember`` decides whether a
new fact is genuinely new (ADD), updates an existing one (UPDATE), or is a duplicate
(NOOP). ``merge`` applies this over a batch — the dedup engine behind the migration
memory-merge (which must never overwrite existing history blindly).
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any

from chimera.core.redact import redact
from chimera.memory.models import EVERY_PROJECT, MemoryItem, MemoryKind
from chimera.memory.semantic import EmbedFn, SemanticIndex
from chimera.memory.store import MemoryBackend
from chimera.telemetry import get_logger

_log = get_logger("memory.manager")


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


@dataclass(frozen=True)
class ConsolidationOutcome:
    """What a consolidation actually did, cluster by cluster."""

    removed: int
    """Net facts removed (each merged cluster of N leaves one)."""
    merged: int
    """Clusters merged into one fact."""
    blank: int
    """Clusters the summarizer was asked about and answered with nothing: left as they were, but the
    call was made (and, with a model, paid for)."""


class MemoryManager:
    """Curates a :class:`MemoryStore`."""

    def __init__(
        self,
        store: MemoryBackend,
        *,
        embed: EmbedFn | None = None,
        clock: Callable[[], float] = time.time,
        audit: Any = None,
    ) -> None:
        self.store = store
        #: Injected so a test can write a fact at a chosen time. `failover.py` takes its clock the
        #: same way, and for the same reason: a timestamp nothing can control is a timestamp nothing
        #: can assert.
        self._clock = clock
        # Opt-in semantic recall: when an embedder is supplied, ``search`` ranks by cosine
        # similarity (bridges paraphrases keyword search can't). Absent/failing embedder ->
        # the keyword/FTS path always remains as a fallback.
        self._semantic = SemanticIndex(embed) if embed is not None else None
        #: The :class:`AuditLog` memory writes are chained into (study 31, G31-06), or ``None``.
        #: Memory is the surface with the longest reach — a fact written today is read into the
        #: system prompt of every matching conversation from now on — and it was the one writer
        #: whose writes left no hashed record. ``None`` keeps the old behaviour, byte for byte:
        #: benches and tests build managers with no log, and a bench must not start writing one.
        self._audit = audit

    def _chain(self, event_type: str, payload: dict[str, Any]) -> None:
        """Record one memory write in the audit chain, when one was given.

        Swallows everything: the write already happened, and a broken log must not turn a saved
        fact into a failed turn. The content travels through the log's own redactor
        (:func:`AuditLog.record`), the same net every other audited writer passes through.
        """
        if self._audit is None:
            return
        try:
            self._audit.record(event_type, payload)
        except Exception as exc:  # noqa: BLE001 — the write stands; only the record is lost
            _log.warning("could not chain memory %s into the audit log: %s", event_type, exc)

    def add(
        self,
        content: str,
        kind: MemoryKind = "semantic",
        *,
        key: str | None = None,
        source: str = "chimera",
        provenance: str = "clean",
        project: str | None = None,
    ) -> MemoryItem:
        """Store a fact. ``project=None`` means it belongs everywhere.

        Everywhere is the DEFAULT here and not the norm at the call sites, deliberately: a caller
        that knows which folder it is in says so, and one that does not cannot invent an answer.
        Persona facts are the clearest case of the default being right — how somebody likes to be
        answered is about them, not about the repository they happen to have open.
        """
        item = MemoryItem(
            # Full uuid4 hex, not [:8]: an 8-char (32-bit) id has a ~1% birthday-collision chance by
            # 10k memories, and add() overwrites on a clash — silently destroying a distinct memory.
            id=uuid.uuid4().hex,
            kind=kind,
            content=content,
            key=key,
            source=source,
            provenance=provenance,
            project=project,
            created_at=self._clock(),
        )
        self.store.add(item)
        self._chain(
            "memory_add",
            {"id": item.id, "kind": item.kind, "source": item.source,
             "provenance": item.provenance, "project": item.project, "content": item.content},
        )
        return item

    def update(self, item_id: str, content: str) -> MemoryItem:
        """Rewrite one fact in place — the writer's own path, after it has masked the text.

        The previous text is kept on the record (``metadata["supersedes"]``) rather than dropped
        (study 31, G31-06): an update used to be indistinguishable from the fact never having been
        anything else, so a poisoned or wrong fact could be rewritten with no trace of what it
        said. The store keeps the metadata on both backends, so the superseded text survives the
        rewrite; it is not recalled (recall reads ``content``), it is the trail.

        The date is the new statement's, as before: the record keeps its identity, and the
        correction is what recency should rank.
        """
        item = self.store.get(item_id)
        previous = item.content
        item.content = content
        item.created_at = self._clock()
        item.metadata = {**item.metadata, "supersedes": previous}
        self.store.add(item)
        self._chain(
            "memory_update",
            {"id": item.id, "content": content, "supersedes": previous},
        )
        return item

    def edit(self, item_id: str, content: str) -> MemoryItem:
        """The owner rewriting one fact by hand. ``KeyError`` when it is gone, ``ValueError`` if blank.

        Distinct from :meth:`update`, which ``remember`` uses after it has already masked the text:
        an edit arrives straight from a text box, so it is masked here, on the same path every other
        write takes — a key pasted into an edit would otherwise be the one way to store one.

        The trust label is KEPT. Rewording a fact learned from untrusted content does not vet where
        it came from, and a label that cleared on any keystroke would launder poison with a typo
        fix. The owner who vouches for it can add it as their own fact and delete this one.
        """
        text = redact(content).strip()
        if not text:
            raise ValueError("a memory cannot be blank")
        # The write itself is chained by `update` (with the superseded text kept on the record).
        return self.update(item_id, text)

    def delete(self, item_id: str) -> None:
        # `remove` of an id that is not there was always a no-op on both backends; reading the
        # fact first must not turn it into a KeyError (the app's DELETE route and `memory forget`
        # pass ids a person typed or a stale screen held).
        try:
            gone: MemoryItem | None = self.store.get(item_id)
        except KeyError:
            gone = None
        self.store.remove(item_id)
        if gone is None:
            return
        # The fact is gone from the store; the chain is where what it SAID survives (study 31,
        # G31-06). Without this, a deletion was the one memory write with no record at all.
        self._chain(
            "memory_delete",
            {"id": item_id, "content": gone.content, "provenance": gone.provenance},
        )

    def _find_duplicate(self, content: str, key: str | None) -> MemoryItem | None:
        norm = _normalize(content)
        for item in self.store.all():
            if key is not None and item.key == key:
                return item
            if _normalize(item.content) == norm:
                return item
        return None

    def remember(
        self,
        content: str,
        kind: MemoryKind = "semantic",
        *,
        key: str | None = None,
        source: str = "chimera",
        provenance: str = "clean",
        project: str | None = None,
    ) -> tuple[str, MemoryItem]:
        """ADD a new fact, UPDATE an existing one (same key), or NOOP a duplicate.

        Returns the operation name and the resulting item. ``provenance="tainted"``
        marks a fact written during a run that consumed untrusted content; an UPDATE
        from a tainted run also taints the stored item (poison must not launder itself
        into a previously clean fact).
        """
        # Masked BEFORE the duplicate check, not after: the same fact written twice differs before
        # masking and matches after, so redacting later would store it twice. And masked here rather
        # than at the two call sites — `ChatSession` and the desktop turn both parse "remember
        # that …" and hand the text over, and the next writer would be one forgotten call from the
        # same hole.
        #
        # Memory is the surface with the longest reach: a fact written today is read back into the
        # system prompt of every matching conversation from now on, and nothing re-reads it to
        # notice. `redact` is narrow on purpose, which matters more here than anywhere else.
        content = redact(content)
        duplicate = self._find_duplicate(content, key)
        if duplicate is None:
            return "ADD", self.add(
                content, kind, key=key, source=source, provenance=provenance, project=project
            )
        if _normalize(duplicate.content) == _normalize(content):
            return "NOOP", duplicate
        updated = self.update(duplicate.id, content)
        if provenance == "tainted":
            updated.provenance = "tainted"
            self.store.add(updated)
        return "UPDATE", updated

    def merge(self, items: list[MemoryItem]) -> dict[str, int]:
        """Merge a batch, deduping against existing memory. Returns op counts."""
        counts = {"ADD": 0, "UPDATE": 0, "NOOP": 0}
        for item in items:
            # Carry provenance through the merge: a tainted imported fact must NOT launder itself
            # to clean (the same guarantee remember() makes for a direct tainted UPDATE).
            # And its project: an imported note about one repository filed with no project would be
            # recalled in every conversation in every folder.
            op, _ = self.remember(
                item.content, item.kind, key=item.key, source=item.source,
                provenance=item.provenance, project=item.project,
            )
            counts[op] += 1
        _log.debug("merged %d items: %s", len(items), counts)
        return counts

    def prune(self, max_items: int, *, dry_run: bool = False) -> int:
        """Keep the ``max_items`` highest-value memories; remove the rest. Returns the count removed.

        Value is the multi-factor model in :mod:`chimera.memory.value` (recency,
        specificity, kind, curation, reliability) — not a single cue.

        ``persona`` facts (the durable cross-session profile) are NEVER pruned — they're identity,
        not budget fodder — so the ``max_items`` budget applies only to the prunable items. With
        ``dry_run=True`` nothing is deleted; the return value is how many WOULD be removed.

        **What it evicts first, measured** (`bench/memory_prune`, 2026-09-11, deterministic): a
        short fact written early — "the customer is allergic to penicillin", 40 characters — is
        evicted at a 20% budget in 137 of 144 profiles, including keyed, user-written, semantic;
        the seven survivors were all written LAST. Recency is the position in the store, specificity
        is length, and neither knows what a fact is about (arXiv 2609.05767: no budget-time policy
        can). A fact that must outlive any budget is written as ``persona`` — that is the mechanism,
        and it is a decision the writer makes, not one this method can make for them.
        """
        from chimera.memory.value import rank

        prunable = [item for item in self.store.all() if item.kind != "persona"]
        if len(prunable) <= max_items:
            return 0
        to_remove = [item for _, item in rank(prunable)[max_items:]]
        if not dry_run:
            for item in to_remove:
                self.store.remove(item.id)
        return len(to_remove)

    def _consolidation_clusters(
        self, *, threshold: float, kinds: tuple[MemoryKind, ...]
    ) -> list[list[MemoryItem]]:
        """Clusters of two or more similar facts, never spanning two kinds or two projects.

        Per project, and not across them, because the merged fact can only carry ONE ``project``.
        Clustering across projects meant a fact about ``/repo/alpha`` and its near-twin about
        ``/repo/beta`` came out as one fact filed under neither — ``project=None``, which recall reads
        as "applies everywhere" — so a merge silently WIDENED what every conversation in every folder
        is told. ``None`` is a project of its own here: the everywhere facts merge among themselves.
        """
        from chimera.memory.consolidate import cluster

        groups: list[list[MemoryItem]] = []
        for kind in kinds:
            by_project: dict[str | None, list[MemoryItem]] = {}
            for item in self.store.by_kind(kind):
                by_project.setdefault(item.project, []).append(item)
            for items in by_project.values():
                groups += [g for g in cluster(items, threshold=threshold) if len(g) >= 2]
        return groups

    def consolidation_groups(
        self,
        *,
        threshold: float = 0.5,
        kinds: tuple[MemoryKind, ...] = ("semantic", "episodic"),
    ) -> list[list[MemoryItem]]:
        """The clusters :meth:`consolidate` WOULD merge — no model call, no write.

        The same clustering ``consolidate`` runs, so what a preview shows is what an apply merges.
        Free to compute (token Jaccard), which is the point: seeing what would be merged used to cost
        a model call per cluster, because the only way to see it was to do it.
        """
        return self._consolidation_clusters(threshold=threshold, kinds=kinds)

    def consolidate(
        self,
        summarizer: object,
        *,
        threshold: float = 0.5,
        kinds: tuple[MemoryKind, ...] = ("semantic", "episodic"),
        only: Collection[frozenset[str]] | None = None,
    ) -> int:
        """Merge clusters of similar memories into one summarised fact. Returns net reduction.

        :meth:`consolidate_outcome` does the work and says how many clusters were merged and how
        many came back blank; this keeps the one number most callers want.
        """
        return self.consolidate_outcome(summarizer, threshold=threshold, kinds=kinds, only=only).removed

    def consolidate_outcome(
        self,
        summarizer: object,
        *,
        threshold: float = 0.5,
        kinds: tuple[MemoryKind, ...] = ("semantic", "episodic"),
        only: Collection[frozenset[str]] | None = None,
    ) -> ConsolidationOutcome:
        """Merge clusters of similar memories into one summarised fact, and count what happened.

        ``summarizer`` is a ``list[str] -> str`` callable (see :mod:`chimera.memory.consolidate`).
        A merge is a write — call it deliberately (e.g. from ``memory consolidate``), not on
        every turn.

        ``only`` restricts the merge to clusters the owner reviewed, each named by its exact set of
        ids. A cluster that is no longer exactly that set — a member was deleted, or a new similar
        fact joined it since the preview — is NOT merged: the owner approved merging those facts,
        not whatever the clustering produces now. ``None`` merges every cluster, as before.

        What the merged fact keeps from its members: their kind and their project (a cluster never
        spans two of either), their key and their source when every member agrees on one, and the
        strongest trust label. Only the text is new.
        """
        removed = merged = blank = 0
        for group in self._consolidation_clusters(threshold=threshold, kinds=kinds):
            if only is not None and frozenset(i.id for i in group) not in only:
                continue
            # Masked on the way OUT, to the summarizer: a stored fact can predate write-time masking
            # (written before `redact` existed, edited in the store by hand, imported by an older
            # version) — the reason the export re-masks too — and the summarizer is usually a remote
            # model. A key in an old fact would otherwise be sent to a third party in the prompt.
            summary = summarizer([redact(item.content) for item in group])  # type: ignore[operator]
            # And on the way back IN: `add` does not mask, and a model can echo or reconstruct a
            # secret it was shown, so the summary takes the path every other write takes.
            text = redact(summary).strip()
            if not text:
                blank += 1
                continue
            # Propagate the strongest provenance of the cluster: merging a tainted member into a
            # summary must NOT launder it to clean (same guarantee remember/merge uphold).
            prov = "tainted" if any(i.provenance == "tainted" for i in group) else "clean"
            keys = {i.key for i in group}
            sources = {i.source for i in group}
            for item in group:
                self.store.remove(item.id)
            self.add(
                text,
                group[0].kind,
                key=keys.pop() if len(keys) == 1 else None,
                source=sources.pop() if len(sources) == 1 else "chimera",
                provenance=prov,
                project=group[0].project,
            )
            removed += len(group) - 1
            merged += 1
        _log.debug("consolidated: removed %d memories", removed)
        return ConsolidationOutcome(removed=removed, merged=merged, blank=blank)

    def nudges(self, user_texts: list[str], *, max_suggestions: int = 3) -> list[str]:
        """Suggest persona facts to save from preferences stated in recent user messages.

        Returns plain fact strings not already in memory (deduped, capped). Empty when
        there's nothing new worth saving. Storing is the user's call — this only surfaces.
        """
        from chimera.memory.nudges import detect_nudges

        known = [item.content for item in self.store.all()]
        return detect_nudges(user_texts, known, max_suggestions=max_suggestions)

    def autoconsolidate(
        self,
        summarizer: object,
        *,
        max_items: int,
        threshold: float = 0.5,
        kinds: tuple[MemoryKind, ...] = ("semantic", "episodic"),
    ) -> int:
        """Consolidate only when memory has outgrown ``max_items``. Returns net reduction.

        The budgeted, opt-in end-of-session hook: consolidation is skipped entirely while
        memory is small (no wasted model calls), and only kicks in once it grows past the
        budget. Returns 0 without calling the summariser when under budget.
        """
        if len(self.store.all()) <= max_items:
            return 0
        return self.consolidate(summarizer, threshold=threshold, kinds=kinds)

    def profile(self, *, max_items: int = 12) -> str:
        """A consolidated user-profile preamble from persona memories (highest-value first).

        Persona facts persist across sessions, so applying them as a preamble on every turn
        gives cross-session personalization without the user re-stating preferences. Empty
        string when no persona facts are stored.
        """
        facts = self.profile_facts(max_items=max_items)
        if not facts:
            return ""
        listed = '\n'.join(f"- {fact}" for fact in facts)
        return f"What you know about the user:\n{listed}"

    def profile_facts(self, *, max_items: int = 12) -> list[str]:
        """The persona facts themselves, highest-value first — no preamble.

        Split out from :meth:`profile` because the two have different readers. `profile` is a
        system-prompt block whose first line is an instruction TO A MODEL, and the Profile screen
        rendered that whole block verbatim — so an English sentence addressed to the agent showed
        up under an already-translated panel heading, in an app translated into ten languages.
        """
        from chimera.memory.value import rank

        personas = self.store.by_kind("persona")
        if not personas:
            return []
        # Surface trust provenance on recall: a fact learned from untrusted content must
        # not read as verified — the model is told, in-line, which facts to weigh less.
        return [
            item.content
            + (" [unverified: learned from untrusted content]" if item.provenance == "tainted" else "")
            for _, item in rank(personas)[:max_items]
        ]

    def _in_scope(self, project: str | None) -> list[MemoryItem]:
        """The facts a turn in ``project`` may recall: that project's, plus the global ones.

        A fact with no project belongs everywhere — which is what every fact written before the
        field existed is, so an upgrade loses nobody's memory.
        """
        items = self.store.all()
        if project == EVERY_PROJECT:
            return items
        return [i for i in items if i.project is None or i.project == project]

    def search(
        self,
        query: str,
        *,
        k: int = 5,
        on_layer: Callable[[str], None] | None = None,
        project: str | None = EVERY_PROJECT,
    ) -> list[MemoryItem]:
        """Retrieve relevant memories.

        Semantic ranking (opt-in, when an embedder is configured) first; on any embedder
        failure or when semantic is off, fall through to full-text (if the backend supports
        it) and finally to keyword overlap. The fallback is unconditional — recall must
        never hard-fail because an embeddings endpoint is down.

        ``on_layer`` (optional) is called with the name of the layer that actually produced the
        hits — ``"semantic"`` | ``"fts"`` | ``"keyword"`` — and never when a layer returns nothing,
        so a UI can honestly show which layer contributed rather than guessing.

        ``project`` scopes the CANDIDATES, not the results, and that is the load-bearing part:
        filtering afterwards would let ``k`` fill up with facts from other folders and return
        fewer than asked for — or nothing — while relevant ones sat just below the cut.

        * :data:`EVERY_PROJECT` (the default) — no filter. Every existing caller.
        * a path — that project's facts, plus the ones that belong everywhere.
        * ``None`` — only the ones that belong everywhere, which is a turn with no folder open.
        """
        from chimera.memory.tokens import idf_weights, informative, tokens

        # A tokenless query (blank/whitespace/punctuation) has nothing to match — return [] up front
        # so every path agrees. Otherwise the semantic path would embed "" and return k arbitrary
        # items while the keyword/FTS paths correctly return nothing. Through the SAME tokenizer the
        # keyword path uses: with two, a query written in Cyrillic was "tokenless" here and
        # matchable there.
        if not tokens(query):
            return []
        if self._semantic is not None:
            try:
                hits = self._semantic.search(query, self._in_scope(project), k)
                if hits:
                    if on_layer is not None:
                        on_layer("semantic")
                    return hits
            except Exception as exc:  # noqa: BLE001 — degrade to lexical, never fail recall
                _log.warning("semantic recall failed, falling back to keyword: %s", exc)
        backend_search = getattr(self.store, "search", None)
        if callable(backend_search):  # e.g. the SQLite/FTS5 store
            # The backend filters in SQL rather than here, for the same reason as above: a
            # LIMIT applied before scoping returns the wrong page.
            result: list[MemoryItem] = backend_search(query, k=k, project=project)
            if result and on_layer is not None:
                on_layer("fts")
            return result
        # Function words dropped BEFORE anything else. A real store of two facts recalled both of
        # them for a sentence about compiling a kernel, on `o`, `com`, `a`, `e` — and inverse
        # document frequency cannot catch that, because with two documents a word in one of them
        # looks maximally rare. Measured, not assumed: the IDF-only version left every one of those
        # false hits in place.
        terms = informative(tokens(query))
        if not terms:
            return []
        # Read once: `store.all()` can be a file read, and this used to call it inside the loop's
        # own iteration anyway.
        corpus = [(item, set(tokens(item.content))) for item in self._in_scope(project)]
        weights = idf_weights(terms, [list(hay) for _, hay in corpus])
        scored: list[tuple[float, str, MemoryItem]] = []
        for item, haystack in corpus:
            matched = terms & haystack
            if not matched:
                continue
            # Ranked by how much each term distinguishes THIS fact from the others; a term
            # common to every stored fact weighs nothing and simply does not lift a result up the
            # list. NOT used to reject: a floor of `log(2)` was tried and dropped, because a query
            # whose only real word appears in every fact ("how should answers be?" against two facts
            # that both mention answers) scored exactly zero and recalled nothing. Rejecting the
            # noise is the stopword list's job, and it does it without that side effect.
            score = (
                sum(weights.get(t, 0.0) for t in matched) if weights else 0.0
            ) or float(len(matched))
            scored.append((score, item.id, item))
        scored.sort(key=lambda entry: (-entry[0], entry[1]))
        top = [item for _, _, item in scored[:k]]
        if top and on_layer is not None:
            on_layer("keyword")
        return top
