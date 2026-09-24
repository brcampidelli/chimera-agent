"""M3 — replay variance of the local backend on long states. See PREREGISTRATION.md.

    python -m bench.long_state_floor.run --jevbench PATH/TO/jevbench

US$ 0. The 30 longest hard-tier JevBench states (by the prompt tokens `bench/jevbench_local` recorded),
5 rounds of byte-identical requests in a shuffled order per round; the request, the mapping and the
state rendering are `bench/jevbench_local`'s own, imported, not copied.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bench.jevbench_local.run import body_of, load, pinned, question_of, state_text  # noqa: E402
from chimera.decisions import as_choice  # noqa: E402
from chimera.decisions.local import LocalLogprobBackend  # noqa: E402

HERE = Path(__file__).resolve().parent
CTX_ROWS = ROOT / "bench" / "jevbench_local" / "results" / "ctx.jsonl"
N_ITEMS, ROUNDS, SEED = 30, 5, 24


def chosen_ids() -> list[str]:
    rows = [json.loads(x) for x in CTX_ROWS.read_text(encoding="utf-8").splitlines() if x.strip()]
    hard = sorted((r for r in rows if r["file"] == "hard"), key=lambda r: (-r["prompt_eval_count"], r["id"]))
    return [r["id"] for r in hard[:N_ITEMS]]


def run(clone: Path) -> None:
    pinned(clone)
    by_id = {item["id"]: item for item in load(clone)}
    ids = chosen_ids()
    backend = LocalLogprobBackend("http://localhost:11434", timeout_s=300.0)
    build = backend.resolved_model()
    client = httpx.Client()
    rng = random.Random(SEED)
    out = HERE / "results" / "replays.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as sink:
        for rnd in range(1, ROUNDS + 1):
            order = ids[:]
            rng.shuffle(order)
            for item_id in order:
                item = by_id[item_id]
                choice = as_choice(question_of(item))
                body = body_of(backend, state_text(item["state"]), choice, "ctx")
                data = client.post(f"{backend.base_url}/api/chat", json=body, timeout=300.0).json()
                reading = backend.read(data, choice, resolved_model=build)
                sink.write(json.dumps({"round": rnd, "id": item_id, "choice": reading.choice, "shares": reading.shares,
                                       "tokens": int(data.get("prompt_eval_count") or 0), "build": build}) + "\n")
                sink.flush()
            print(f"round {rnd}/{ROUNDS} done", flush=True)


def read() -> None:
    rows = [json.loads(x) for x in (HERE / "results" / "replays.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    by_item: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_item.setdefault(r["id"], []).append(r)
    deltas, flips, partial = [], [], []
    for item_id, reps in by_item.items():
        reps.sort(key=lambda r: r["round"])
        read_ok = [r for r in reps if r["shares"]]
        if len(read_ok) != len(reps):
            partial.append(item_id)
        if not read_ok:
            continue
        label = max(read_ok[0]["shares"], key=read_ok[0]["shares"].get)
        ps = [r["shares"].get(label, 0.0) for r in read_ok]
        deltas.append(max(ps) - min(ps))
        argmaxes = {max(r["shares"], key=r["shares"].get) for r in read_ok}
        if len(argmaxes) > 1:
            flips.append(item_id)
    deltas.sort()
    summary = {
        "items": len(by_item), "rounds": len(rows) // max(len(by_item), 1), "build": rows[0]["build"] if rows else "",
        "max_abs_dp_median": deltas[len(deltas) // 2] if deltas else None, "max_abs_dp_max": max(deltas) if deltas else None,
        "flips": flips, "unread_in_some_rounds_only": partial,
    }
    ok = len(flips) <= 1 and (summary["max_abs_dp_max"] or 0) <= 0.02
    summary["decision"] = "floor extends to long states" if ok else "publish the floor beside spot_noul and manager_p"
    print(json.dumps(summary, indent=2))
    (HERE / "results" / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--jevbench", type=Path)
    parser.add_argument("--read", action="store_true")
    args = parser.parse_args()
    if args.read:
        read()
    else:
        run(args.jevbench.resolve())
