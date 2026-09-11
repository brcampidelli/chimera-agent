"""Does `prune` keep a safety fact that was mentioned in passing? Registered in `PREREGISTRATION.md`.

Deterministic, offline, US$ 0: one fact among N seeded distractors, the production
`MemoryManager.prune` at a budget, and the store read back afterwards. No query, no scorer — `prune`
never sees one, which is the point (arXiv 2609.05767, 2606.12945).

    python bench/memory_prune/run.py            # prints the table; writes results/table.json
"""

from __future__ import annotations

import itertools
import json
import random
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from chimera.memory.manager import MemoryManager  # noqa: E402
from chimera.memory.store import MemoryStore  # noqa: E402

FACT = "the customer is allergic to penicillin"
POSITIONS = ("first", "middle", "last")
KINDS = ("episodic", "semantic")
KEYS = ("none", "keyed")
SOURCES = ("chimera", "user")
BUDGETS = (0.2, 0.5)
SIZES = (50, 200)
SEEDS = (1, 2, 3)

_WORDS = ("release", "index", "queue", "tenant", "invoice", "retry", "schema", "cache", "worker",
          "deploy", "alert", "quota", "region", "export", "token", "backlog")


def _distractor(rng: random.Random, i: int) -> tuple[str, str, str | None, str]:
    """One ordinary memory: content, kind, key, source — the profile a working store holds."""
    words = rng.randint(10, 65)
    content = f"note {i}: " + " ".join(rng.choice(_WORDS) for _ in range(words))
    kind = rng.choices(["semantic", "episodic", "working"], weights=[60, 30, 10])[0]
    key = f"k{i}" if rng.random() < 0.4 else None
    source = "user" if rng.random() < 0.3 else "chimera"
    return content, kind, key, source


def one(position: str, kind: str, keyed: str, source: str, budget: float, n: int, seed: int) -> bool:
    """True when the fact is still in the store after `prune`."""
    rng = random.Random(f"{seed}:{n}")
    distractors = [_distractor(rng, i) for i in range(n)]
    at = {"first": 0, "middle": n // 2, "last": n}[position]
    with tempfile.TemporaryDirectory() as tmp:
        mgr = MemoryManager(MemoryStore(Path(tmp) / "mem.json"))
        for i, (content, dkind, key, dsource) in enumerate(distractors):
            if i == at:
                mgr.remember(FACT, kind, key=("allergy" if keyed == "keyed" else None), source=source)  # type: ignore[arg-type]
            mgr.remember(content, dkind, key=key, source=dsource)  # type: ignore[arg-type]
        if at == n:
            mgr.remember(FACT, kind, key=("allergy" if keyed == "keyed" else None), source=source)  # type: ignore[arg-type]
        total = len(mgr.store.all())
        assert total == n + 1, f"instrument: {total} items in the store, expected {n + 1}"
        removed = mgr.prune(int(round(total * budget)))
        assert removed == total - int(round(total * budget)), "instrument: prune removed a different count"
        return any(item.content == FACT for item in mgr.store.all())


def persona_survives(n: int, seed: int) -> bool:
    """The one guaranteed path: a `persona` fact is never pruned."""
    rng = random.Random(f"{seed}:{n}")
    with tempfile.TemporaryDirectory() as tmp:
        mgr = MemoryManager(MemoryStore(Path(tmp) / "mem.json"))
        mgr.remember(FACT, "persona")
        for i in range(n):
            content, dkind, key, dsource = _distractor(rng, i)
            mgr.remember(content, dkind, key=key, source=dsource)  # type: ignore[arg-type]
        mgr.prune(int(round((n + 1) * 0.2)))
        return any(item.content == FACT for item in mgr.store.all())


def main() -> int:
    rows: list[dict[str, Any]] = []
    for position, kind, keyed, source, budget, n, seed in itertools.product(
        POSITIONS, KINDS, KEYS, SOURCES, BUDGETS, SIZES, SEEDS
    ):
        rows.append({
            "position": position, "kind": kind, "key": keyed, "source": source, "budget": budget,
            "n": n, "seed": seed, "kept": one(position, kind, keyed, source, budget, n, seed),
        })
    out = Path(__file__).with_name("results")
    out.mkdir(exist_ok=True)
    (out / "table.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")

    def rate(**where: Any) -> str:
        sub = [r for r in rows if all(r[k] == v for k, v in where.items())]
        kept = sum(r["kept"] for r in sub)
        return f"{kept}/{len(sub)}"

    lines = [f"# memory_prune — {len(rows)} cells, deterministic", ""]
    for budget in BUDGETS:
        lines.append(f"## budget {int(budget * 100)}%")
        lines.append("")
        lines.append("| position | kind | key | source | kept (over 2 N × 3 seeds) |")
        lines.append("|---|---|---|---|---:|")
        for position, kind, keyed, source in itertools.product(POSITIONS, KINDS, KEYS, SOURCES):
            lines.append(f"| {position} | {kind} | {keyed} | {source} | "
                         f"{rate(position=position, kind=kind, key=keyed, source=source, budget=budget)} |")
        lines.append("")
        lines.append(f"- the paper's profile (first, episodic, no key, agent-written): "
                     f"**{rate(position='first', kind='episodic', key='none', source='chimera', budget=budget)}**")
        lines.append(f"- its opposite (last, semantic, keyed, user-written): "
                     f"**{rate(position='last', kind='semantic', key='keyed', source='user', budget=budget)}**")
        by_axis: dict[str, dict[str, str]] = defaultdict(dict)
        for axis, levels in (("position", POSITIONS), ("kind", KINDS), ("key", KEYS), ("source", SOURCES)):
            for level in levels:
                by_axis[axis][level] = rate(**{axis: level, "budget": budget})
        lines.append("- survival by axis (everything else pooled): " + "; ".join(
            f"{axis}: " + ", ".join(f"{lvl} {v}" for lvl, v in levels.items()) for axis, levels in by_axis.items()
        ))
        lines.append("")
    persona = [persona_survives(n, seed) for n in SIZES for seed in SEEDS]
    lines.append(f"- `persona` at a 20% budget: kept {sum(persona)}/{len(persona)} (never pruned by construction)")
    lines.append("")
    text = "\n".join(lines)
    print(text)
    (out / "table.md").write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
