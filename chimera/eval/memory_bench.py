"""Memory-bench — does recall hold as memory grows, and where does lexical search fail?

The AutoMem prerequisite ([[chimera-paper-study-3]]): before evolving a memory *policy* you
need to measure the memory. This inserts N facts and probes them two ways — a **lexical**
probe that shares the fact's distinctive term, and a **paraphrase** probe that uses a synonym
with NO shared token — measuring recall@k for each as N grows.

The honest result it surfaces: keyword/FTS recall holds on lexical probes even at scale, but
collapses on paraphrase probes (no shared token to match). That gap is exactly what the opt-in
semantic retrieval (M11b) is for — and this bench is how we prove whether it actually helps.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from chimera.memory.manager import MemoryManager

# Synonym pairs: a fact uses the first word, its paraphrase probe uses the second (no shared
# token), so lexical search cannot bridge them but semantic search should.
_SYNONYMS: list[tuple[str, str]] = [
    ("car", "automobile"), ("doctor", "physician"), ("house", "residence"),
    ("teacher", "educator"), ("river", "stream"), ("money", "currency"),
    ("boss", "manager"), ("quick", "rapid"), ("buy", "purchase"), ("start", "begin"),
    ("big", "large"), ("smart", "intelligent"), ("happy", "cheerful"), ("job", "occupation"),
    ("city", "metropolis"), ("food", "cuisine"),
]


@dataclass
class MemoryProbe:
    query: str
    target_key: str
    kind: str  # "lexical" | "paraphrase"


@dataclass
class RecallReport:
    n_facts: int
    probes: list[tuple[MemoryProbe, bool, bool]] = field(default_factory=list)  # (probe, hit@1, hit@k)

    def _rate(self, kind: str | None, at1: bool) -> float:
        rows = [p for p in self.probes if kind is None or p[0].kind == kind]
        if not rows:
            return 0.0
        return round(sum(r[1] if at1 else r[2] for r in rows) / len(rows), 3)

    def summary(self) -> dict[str, float]:
        return {
            "n_facts": float(self.n_facts),
            "probes": float(len(self.probes)),
            "recall@1": self._rate(None, True),
            "recall@k": self._rate(None, False),
            "recall@k_lexical": self._rate("lexical", False),
            "recall@k_paraphrase": self._rate("paraphrase", False),
        }


def synthetic_facts_and_probes(n: int) -> tuple[list[tuple[str, str]], list[MemoryProbe]]:
    """Build a corpus of ``n`` facts and probes for the target subset.

    Up to ``len(_SYNONYMS)`` **target** facts carry a unique needle word; the rest are
    **distractor** facts (a growing sea of noise) that no probe matches. Each target gets a
    *lexical* probe (the needle word — shared token, FTS finds it) and a *paraphrase* probe
    (the needle's synonym — NO shared token, so lexical search cannot bridge it). Keeping the
    probe to the needle alone makes the lexical/semantic gap clean: lexical recall ~1.0,
    paraphrase recall ~0.0 for pure keyword search, regardless of scale.
    """
    num_targets = min(n, len(_SYNONYMS))
    facts: list[tuple[str, str]] = []
    probes: list[MemoryProbe] = []
    for j in range(num_targets):
        word, syn = _SYNONYMS[j]
        key = f"target:{j}"
        facts.append((key, f"The {word} for the archive is catalogued and verified."))
        probes.append(MemoryProbe(word, key, "lexical"))
        probes.append(MemoryProbe(syn, key, "paraphrase"))
    for i in range(n - num_targets):
        # Distractors use only common words — never a needle or its synonym.
        facts.append((f"distractor:{i}", f"Log entry {i:05d}: routine status nominal, no action taken."))
    return facts, probes


def run_memory_bench(
    manager: MemoryManager, facts: list[tuple[str, str]], probes: list[MemoryProbe], *, k: int = 5
) -> RecallReport:
    """Insert facts, then measure recall@1 / recall@k for each probe against the manager's search."""
    for key, content in facts:
        manager.add(content, key=key)
    report = RecallReport(n_facts=len(facts))
    for probe in probes:
        results = manager.search(probe.query, k=k)
        keys = [item.key for item in results]
        hit_at_1 = bool(keys) and keys[0] == probe.target_key
        hit_at_k = probe.target_key in keys
        report.probes.append((probe, hit_at_1, hit_at_k))
    return report


def sweep(
    manager_factory: Callable[[], MemoryManager], sizes: list[int], *, k: int = 5
) -> list[RecallReport]:
    """Run the bench at each size with a FRESH memory, exposing the recall-vs-scale curve."""
    reports: list[RecallReport] = []
    for n in sizes:
        facts, probes = synthetic_facts_and_probes(n)
        reports.append(run_memory_bench(manager_factory(), facts, probes, k=k))
    return reports
