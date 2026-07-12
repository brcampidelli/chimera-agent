"""Graph memory layer — entities and relations extracted from stored memories.

The fourth memory layer (alongside working / episodic / semantic / persona). It turns
free-text facts into ``(source, relation, target)`` triples with a deterministic
heuristic extractor, so related facts can be recalled by entity rather than only by
keyword. An optional model-backed extractor can enrich it, but the default needs no
network and is fully testable.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

# Multi-word relations first; "is" is the catch-all and must come last.
_RELATION_KEYWORDS = [
    "depends on",
    "is part of",
    "belongs to",
    "prefers",
    "requires",
    "uses",
    "owns",
    "needs",
    "likes",
    "has",
    "is",
]


def _clean(text: str) -> str:
    return text.strip().strip(".,;:—-").strip()


@dataclass(frozen=True)
class Relation:
    source: str
    relation: str
    target: str

    def as_text(self) -> str:
        return f"{self.source} {self.relation.replace('_', ' ')} {self.target}"


def extract_relations(text: str) -> list[Relation]:
    """Pull ``(source, relation, target)`` triples from text with simple heuristics."""
    relations: list[Relation] = []
    for sentence in re.split(r"[.\n;]+", text):
        clause = sentence.strip()
        if not clause:
            continue
        lowered = clause.lower()
        for keyword in _RELATION_KEYWORDS:
            idx = lowered.find(f" {keyword} ")
            if idx > 0:
                source = _clean(clause[:idx])
                target = _clean(clause[idx + len(keyword) + 2 :])
                if source and target:
                    relations.append(Relation(source, keyword.replace(" ", "_"), target))
                break  # one relation per clause
    return relations


class MemoryGraph:
    """An entity-relation graph built from memory text."""

    def __init__(self) -> None:
        self._relations: list[Relation] = []

    def add(self, relation: Relation) -> None:
        if relation not in self._relations:
            self._relations.append(relation)

    def add_text(self, text: str) -> int:
        before = len(self._relations)
        for relation in extract_relations(text):
            self.add(relation)
        return len(self._relations) - before

    def relations(self) -> list[Relation]:
        return list(self._relations)

    def entities(self) -> set[str]:
        names: set[str] = set()
        for relation in self._relations:
            names.add(relation.source)
            names.add(relation.target)
        return names

    def relations_of(self, entity: str) -> list[Relation]:
        low = entity.lower()
        return [r for r in self._relations if low in (r.source.lower(), r.target.lower())]

    def neighbors(self, entity: str) -> set[str]:
        low = entity.lower()
        out: set[str] = set()
        for relation in self.relations_of(entity):
            out.add(relation.target if relation.source.lower() == low else relation.source)
        return out

    def related_facts(self, query: str, k: int = 5) -> list[str]:
        """Facts connected to any graph entity mentioned in ``query`` (deduped)."""
        lowered = query.lower()
        seen: set[str] = set()
        facts: list[str] = []
        for entity in sorted(self.entities()):
            # Whole-word match, not substring: a raw `in` lets a 1-2 char entity ("C", "AI", "Go")
            # match inside unrelated words ("recommend", "brainstorm") and pollute the recall.
            if re.search(rf"\b{re.escape(entity.lower())}\b", lowered):
                for relation in self.relations_of(entity):
                    text = relation.as_text()
                    if text not in seen:
                        seen.add(text)
                        facts.append(text)
        return facts[:k]

    def to_dict(self) -> dict[str, list[dict[str, str]]]:
        return {
            "relations": [
                {"source": r.source, "relation": r.relation, "target": r.target}
                for r in self._relations
            ]
        }

    @classmethod
    def from_dict(cls, data: dict[str, list[dict[str, str]]]) -> MemoryGraph:
        graph = cls()
        for item in data.get("relations", []):
            # Skip a malformed relation (missing a field) instead of aborting the whole load.
            if isinstance(item, dict) and {"source", "relation", "target"} <= item.keys():
                graph.add(Relation(item["source"], item["relation"], item["target"]))
        return graph

    def save(self, path: Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: a crash mid-write must not truncate the graph file and lose it all on next load.
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(p)

    @classmethod
    def load(cls, path: Path) -> MemoryGraph:
        p = Path(path)
        if not p.exists():
            return cls()
        try:
            data = json.loads(p.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError:  # a truncated/corrupt file -> empty graph, not a crash
            return cls()
        return cls.from_dict(data)

    def __len__(self) -> int:
        return len(self._relations)


def build_graph(texts: list[str]) -> MemoryGraph:
    """Build a graph from a list of memory texts."""
    graph = MemoryGraph()
    for text in texts:
        graph.add_text(text)
    return graph
