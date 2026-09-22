"""B7 — a Noul per chunk as a reranker over the hybrid top-30, on the RAG bench's own probes.

    python bench/rag_rerank/run.py --run       # embeds (≈ US$ 0.02), asks the local model, writes results/
    python bench/rag_rerank/run.py --report    # reads results/, prints the paired tables

Everything retrieval-side is `chimera.eval.rag_bench` and `chimera.rag` as `chimera find` uses them;
the reranker is the bench's own local instrument (`bench/jev_decisions/run.py::local`) with a yes/no
label set. PREREGISTRATION.md is the rule; this file only produces the numbers it names.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from chimera.eval.paired import compare_paired, format_report  # noqa: E402
from chimera.eval.rag_bench import _without_docstring, build_probes  # noqa: E402
from chimera.rag.chunks import walk  # noqa: E402
from chimera.rag.hybrid import reciprocal_rank_fusion  # noqa: E402
from chimera.rag.store import EMBED_BATCH, ChunkStore  # noqa: E402

RESULTS = HERE / "results"
PUBLISHED_HYBRID = 0.5050
K = 10
WIDE = 30
SEED = 20260922
CHUNK_CHARS = 1500
QUESTION = (
    "You are ranking code search results. Below are a one-line description of a symbol and one "
    "chunk of source code. Is this chunk the DEFINITION that the description describes? Answer YES "
    "only if this chunk defines it — not if it merely calls, imports or mentions it. Reply with "
    "exactly one word: YES or NO."
)
LABELS = ("YES", "NO")


def _state(query: str, chunk: Any) -> str:
    text = chunk.text if len(chunk.text) <= CHUNK_CHARS else chunk.text[:CHUNK_CHARS] + "\n… [cut]"
    return f"Description: {query}\n\nChunk: {chunk.path} :: {chunk.symbol or '(window)'} [{chunk.kind}]\n```\n{text}\n```"


def _hit(hits: list[Any], target: str, k: int) -> bool:
    return any(h.chunk.ident == target for h in hits[:k])


# --- run -------------------------------------------------------------------------------------------


def run(out: Path, *, max_probes: int) -> None:
    from bench.jev_decisions.run import LOCAL_MODEL, local
    from chimera.config import get_settings
    from chimera.evolution.wiring import semantic_embed

    settings = get_settings()
    embed = semantic_embed(settings, force=True)
    assert embed is not None
    chunks = walk(ROOT / "chimera")
    probes = build_probes(chunks)[:max_probes]
    print(f"corpus chimera/ — {len(chunks)} chunks, {len(probes)} probes, embedder {settings.embed_model}")

    client = httpx.Client(timeout=600.0)
    build = client.post("http://localhost:11434/api/show", json={"model": LOCAL_MODEL}).json()
    meta = {
        "arm": "meta", "model": LOCAL_MODEL, "quantization": (build.get("details") or {}).get("quantization_level"),
        "chunks": len(chunks), "probes": len(probes), "embedder": settings.embed_model, "seed": SEED, "k": K, "wide": WIDE,
    }
    rng = random.Random(SEED)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        store = ChunkStore(Path(tmp) / "index.db")
        store.replace_all([_without_docstring(c) for c in chunks])
        embedded = store.embed_missing(embed, embedder=settings.embed_model)
        print(f"embedded {embedded} chunks")
        vectors: list[list[float]] = []
        for start in range(0, len(probes), EMBED_BATCH):
            vectors.extend(embed([p.query for p in probes[start : start + EMBED_BATCH]]))
        assert len(vectors) == len(probes)
        shown = 0
        with out.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(meta) + "\n")
            for index, probe in enumerate(probes):
                kw10 = store.search_keyword(probe.query, k=K)
                vec10 = store.search_vector(vectors[index], k=K)
                hybrid = reciprocal_rank_fusion([kw10, vec10], limit=K)
                kw30 = store.search_keyword(probe.query, k=WIDE)
                vec30 = store.search_vector(vectors[index], k=WIDE)
                wide = reciprocal_rank_fusion([kw30, vec30], limit=WIDE)
                in_wide = _hit(wide, probe.target, WIDE)
                row: dict[str, Any] = {
                    "arm": "probe", "i": index, "query": probe.query, "target": probe.target,
                    "hybrid": _hit(hybrid, probe.target, K), "hybrid30": _hit(wide, probe.target, K),
                    "in_wide": in_wide, "wide_rank": next((r for r, h in enumerate(wide, 1) if h.chunk.ident == probe.target), None),
                    "n_wide": len(wide),
                }
                if in_wide:
                    # Only a probe whose target is among the 30 can change under a re-ordering of
                    # the 30 (PREREGISTRATION §3). The others are recorded with `noul` = hybrid30.
                    scored: list[tuple[float, int, str]] = []
                    t0 = time.perf_counter()
                    for rank, hit in enumerate(wide):
                        ans = local(client, _state(probe.query, hit.chunk), think=False, labels=LABELS, system=QUESTION)
                        p = ans["p"] if ans["p"] is not None else 0.0  # P(YES): `local` reads labels[0] when no ALLOW
                        scored.append((p, rank, hit.chunk.ident))
                        if shown < 3:
                            print(f"  raw reading — target={hit.chunk.ident == probe.target} p={p:.3f} verdict={ans['verdict']} content={ans['raw']!r}")
                            shown += 1
                    ordered = sorted(scored, key=lambda s: (-s[0], s[1]))
                    row["noul"] = any(ident == probe.target for _, _, ident in ordered[:K])
                    row["noul_rank"] = next((r for r, (_, _, ident) in enumerate(ordered, 1) if ident == probe.target), None)
                    row["target_p"] = next((p for p, _, ident in scored if ident == probe.target), None)
                    row["p_max_other"] = max((p for p, _, ident in scored if ident != probe.target), default=None)
                    shuffled = list(range(len(wide)))
                    rng.shuffle(shuffled)
                    row["random"] = any(wide[j].chunk.ident == probe.target for j in shuffled[:K])
                    row["oracle"] = True
                    row["seconds"] = round(time.perf_counter() - t0, 2)
                else:
                    row["noul"] = row["hybrid30"]
                    row["random"] = False
                    row["oracle"] = False
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
                fh.flush()
                if (index + 1) % 20 == 0:
                    print(f"  {index + 1}/{len(probes)}")
        store.close()
    print(f"wrote {out}")


# --- report ----------------------------------------------------------------------------------------


def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar on the discordant pairs: binomial(b + c, 0.5)."""
    from math import comb

    m = b + c
    if m == 0:
        return 1.0
    k = min(b, c)
    tail = sum(comb(m, i) for i in range(k + 1)) / 2**m
    return min(1.0, 2 * tail)


def report(path: Path) -> None:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    meta = rows[0]
    probes = [r for r in rows if r.get("arm") == "probe"]
    n = len(probes)
    print(f"corpus {meta['chunks']} chunks · {n} probes · {meta['model']} {meta.get('quantization')} · embedder {meta['embedder']}")
    rec = {a: sum(1 for r in probes if r[a]) / n for a in ("hybrid", "hybrid30", "noul", "random", "oracle")}
    ceiling = sum(1 for r in probes if r["in_wide"])
    headroom = sum(1 for r in probes if r["in_wide"] and not r["hybrid30"])
    print("\n=== recall@10 ===")
    for a in ("hybrid", "hybrid30", "noul", "random", "oracle"):
        print(f"  {a:9s} {rec[a]:.4f}")
    print(f"  target in the 30: {ceiling}/{n} (the ceiling) · at ranks 11–30: {headroom} (the reranker's headroom)")
    print(f"\n=== paired control: published hybrid {PUBLISHED_HYBRID:.4f}, today {rec['hybrid']:.4f} ===")
    if abs(rec["hybrid"] - PUBLISHED_HYBRID) > 0.03:
        print("  WARNING: outside ±0.03 — the corpus moved more than the tolerance; read the chunk count")
    if rec["oracle"] != ceiling / n:
        raise SystemExit("HALT: the oracle arm does not equal the ceiling")

    hyb = [r["hybrid"] for r in probes]
    res = compare_paired(hyb, [r["noul"] for r in probes], baseline_name="hybrid", treatment_name="noul")
    print("\n=== primary — noul against hybrid, paired ===")
    print(format_report(res))
    res30 = compare_paired([r["hybrid30"] for r in probes], [r["noul"] for r in probes], baseline_name="hybrid30", treatment_name="noul")
    print("\n=== noul against hybrid30 (the candidate set it was given) ===")
    print(format_report(res30))
    resr = compare_paired([r["random"] for r in probes], [r["noul"] for r in probes], baseline_name="random", treatment_name="noul")
    print("\n=== noul against random re-order (control) ===")
    print(format_report(resr))
    same = sum(1 for r in probes if r["hybrid"] == r["hybrid30"]) / n
    sent = [r for r in probes if r["in_wide"]]
    tp = [r["target_p"] for r in sent if r.get("target_p") is not None]
    above = sum(1 for r in sent if r.get("target_p") is not None and r.get("p_max_other") is not None and r["target_p"] > r["p_max_other"])
    secs = sum(r.get("seconds", 0) for r in sent)
    print(f"\n  hybrid30 top-10 == hybrid on {same:.1%} of probes (P3 ≥ 95%)")
    print(f"  probes sent to the model {len(sent)} · mean P(yes) on the target {sum(tp) / max(1, len(tp)):.3f} · target strictly top by P(yes) on {above}/{len(sent)} · {secs / 60:.1f} min of model time")
    p_value = mcnemar_exact(res.baseline_only, res.treatment_only)
    adopt = res.delta >= 0.05 and (p_value is not None and p_value < 0.01) and rec["noul"] >= rec["hybrid30"]
    print("\n=== decision rule (§4) ===")
    print(f"  delta {res.delta * 100:+.2f} pp · p {p_value} · noul ≥ hybrid30 {rec['noul'] >= rec['hybrid30']}  => {'ADOPT' if adopt else 'NULL — no reranking step'}")
    predictions = {
        "P1_below_5pp": res.delta < 0.05,
        "P2_beats_random_by_5pp": resr.delta >= 0.05,
        "P3_hybrid30_equals_hybrid_95pct": same >= 0.95,
        "control_random_le_hybrid30_plus_0.02": rec["random"] <= rec["hybrid30"] + 0.02,
    }
    print("  predictions:", predictions)
    (HERE / "results.json").write_text(
        json.dumps(
            {
                "meta": meta, "recall": rec, "ceiling": ceiling, "headroom": headroom, "n": n,
                "paired_noul_vs_hybrid": {"a": res.both_pass, "b": res.baseline_only, "c": res.treatment_only, "d": res.both_fail, "delta": res.delta, "p": p_value},
                "paired_noul_vs_hybrid30": {"b": res30.baseline_only, "c": res30.treatment_only, "delta": res30.delta},
                "paired_noul_vs_random": {"b": resr.baseline_only, "c": resr.treatment_only, "delta": resr.delta},
                "sent": len(sent), "target_top_by_p": above, "model_minutes": round(secs / 60, 1),
                "verdict": "ADOPT" if adopt else "NULL", "predictions": predictions,
            },
            indent=2, sort_keys=True,
        ) + "\n",
        encoding="utf-8", newline="\n",
    )
    print("\nwrote results.json")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--max-probes", type=int, default=400)
    ap.add_argument("--out", type=Path, default=RESULTS / "2026-09-22-rerank-local.jsonl")
    args = ap.parse_args()
    if args.run:
        run(args.out, max_probes=args.max_probes)
    if args.report or not args.run:
        report(args.out)


if __name__ == "__main__":
    main()
