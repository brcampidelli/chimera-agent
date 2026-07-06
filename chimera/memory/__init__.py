"""Memory layers: working / episodic / semantic / persona + a graph + a Memory Manager.

Hierarchical memory with ADD/UPDATE/DELETE/NOOP operations to keep context lean and to
merge imported memory without overwriting existing history. The graph layer
(:mod:`chimera.memory.graph`) extracts entity-relation triples for recall by entity.
"""

from chimera.memory.gate import MemoryGate
from chimera.memory.graph import MemoryGraph, Relation, build_graph, extract_relations
from chimera.memory.manager import MemoryManager
from chimera.memory.models import MemoryItem, MemoryKind
from chimera.memory.semantic import EmbedFn, SemanticIndex, cosine
from chimera.memory.sqlite_store import SqliteMemoryStore
from chimera.memory.store import MemoryBackend, MemoryStore
from chimera.memory.value import ValueWeights, rank, value

__all__ = [
    "MemoryItem",
    "MemoryKind",
    "MemoryStore",
    "MemoryBackend",
    "SqliteMemoryStore",
    "MemoryManager",
    "SemanticIndex",
    "EmbedFn",
    "cosine",
    "MemoryGraph",
    "Relation",
    "build_graph",
    "extract_relations",
    "MemoryGate",
    "value",
    "rank",
    "ValueWeights",
]
