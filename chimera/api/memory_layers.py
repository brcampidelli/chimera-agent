"""Memory layers + provenance view for the desktop app: a pure read-model over the one memory store.

The four memory "layers" are really four co-located KINDS in a single store
(:data:`chimera.memory.models.MemoryKind` = ``working | episodic | semantic | persona``), so this
helper just groups ``store.all()`` by kind, by provenance, and by source. It computes nothing the
store doesn't already carry — every count is derived from the real items.

Honesty rules baked in here:

- The full four-kind taxonomy is ALWAYS reported, including kinds with count 0. For a desktop-only
  user ``working``/``episodic`` are typically 0 (no writer on the chat path) and that 0 is shown as-is,
  never implied to be non-empty.
- ``provenance`` is either ``"clean"`` or ``"tainted"``; ``tainted`` is counted separately and shown as
  "unverified". A chat-only store is 100% clean (taint only comes from CLI ``solve``/``taint``), and
  that is the honest expected state — never fabricated.
- ``semantic_embeddings_enabled`` is passed straight through from ``settings.semantic_memory`` (opt-in,
  off by default). It is a BOOLEAN FLAG for an honest UI note only — this helper NEVER emits an
  "embeddings index: N" count, because when the flag is off no such index exists. The ``"semantic"``
  KIND count is a different, always-real thing and is reported per-kind like the others.
"""

from __future__ import annotations

from typing import Any

# The canonical layer order, mirroring chimera.memory.models.MemoryKind. Kept explicit (not derived)
# so the UI always renders the full taxonomy in a stable order, even for kinds with zero items.
_LAYER_KINDS: tuple[str, ...] = ("working", "episodic", "semantic", "persona")


def summarize_memory_layers(
    items: list[Any], *, semantic_embeddings_enabled: bool
) -> dict[str, Any]:
    """Group memory items by kind / provenance / source into the layers view.

    ``items`` are MemoryItem-like objects, read duck-typed: ``.kind``, ``.provenance`` (defaulting to
    ``"clean"`` when absent), ``.source``. ``tainted`` counts provenance == ``"tainted"``; ``clean`` is
    the rest. All four canonical kinds appear in ``layers`` even at count 0; any UNKNOWN kind found in
    the items is folded into a trailing entry so nothing is silently dropped.
    """
    # Per-kind accumulators, seeded with the four canonical kinds so 0-count kinds still appear.
    per_kind: dict[str, dict[str, int]] = {
        kind: {"count": 0, "clean": 0, "tainted": 0} for kind in _LAYER_KINDS
    }
    by_source: dict[str, int] = {}
    total = clean = tainted = 0

    for it in items:
        kind = getattr(it, "kind", "") or ""
        provenance = getattr(it, "provenance", "clean")
        source = getattr(it, "source", "") or ""

        entry = per_kind.get(kind)
        if entry is None:  # unknown kind — keep it (trailing), never drop
            entry = {"count": 0, "clean": 0, "tainted": 0}
            per_kind[kind] = entry

        is_tainted = provenance == "tainted"
        entry["count"] += 1
        entry["tainted" if is_tainted else "clean"] += 1

        total += 1
        if is_tainted:
            tainted += 1
        else:
            clean += 1

        by_source[source] = by_source.get(source, 0) + 1

    layers = [
        {"kind": kind, "count": e["count"], "clean": e["clean"], "tainted": e["tainted"]}
        for kind, e in per_kind.items()
    ]

    by_source_list = [
        {"source": source, "count": count}
        for source, count in sorted(by_source.items(), key=lambda kv: kv[1], reverse=True)[:20]
    ]

    return {
        "total": total,
        "clean": clean,
        "tainted": tainted,
        "layers": layers,
        "by_source": by_source_list,
        "semantic_embeddings_enabled": semantic_embeddings_enabled,
    }
