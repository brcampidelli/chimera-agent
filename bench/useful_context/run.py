"""Useful context length of the product default, on agent-shaped transcripts. See PREREGISTRATION.md.

    python bench/useful_context/run.py --check                      # grader self-test, renders; spends nothing
    python bench/useful_context/run.py --pilot  --out results/pilot.json
    python bench/useful_context/run.py --run --n 72 --cpt 3.9 --cap 1.30 --out results/main.json
    python bench/useful_context/run.py --report results/main.json

One model call per (item, length), with no agent loop around it: the loop's retries and tool calls
are variance that has nothing to do with length. The transcript is agent-SHAPED (tool calls and
their real results), which is the point; the call that answers is a single completion.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

import items as it  # noqa: E402

MODEL = "openrouter/deepseek/deepseek-v4-flash-0731"
PROVIDER = "DeepInfra"
TEMPERATURE = 0.0
MAX_TOKENS = 8_000
#: DeepInfra's quote for this model on the OpenRouter endpoints listing, read 2026-09-25 (per M tokens).
PRICE_IN, PRICE_CACHED, PRICE_OUT = 0.060, 0.015, 0.180
RETRIES = 2

_lock = threading.Lock()
_spent = 0.0


def _cost(prompt: int, cached: int, completion: int) -> float:
    return ((prompt - cached) * PRICE_IN + cached * PRICE_CACHED + completion * PRICE_OUT) / 1e6


def _call(request: dict[str, Any]) -> dict[str, Any]:
    import litellm

    from chimera.providers.thinking import strip_think

    raw = litellm.completion(
        model=MODEL,
        messages=request["messages"],
        tools=request["tools"],
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
        timeout=900,
        extra_body={"provider": {"order": [PROVIDER], "allow_fallbacks": False}, "usage": {"include": True}},
    )
    payload = raw.model_dump() if hasattr(raw, "model_dump") else dict(raw)
    choice = payload["choices"][0]
    message = choice.get("message") or {}
    usage = payload.get("usage") or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    cached = int(((usage.get("prompt_tokens_details") or {}).get("cached_tokens")) or 0)
    reasoning_tokens = int(((usage.get("completion_tokens_details") or {}).get("reasoning_tokens")) or 0)
    reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
    content = strip_think(message.get("content") or "")
    reported = usage.get("cost")
    computed = _cost(prompt, cached, completion)
    return {
        "provider": str(payload.get("provider") or ""),
        "content": content[:2_000],
        "reasoning_head": str(reasoning)[:600],
        "reasoning_chars": len(str(reasoning)),
        "tool_calls": len(message.get("tool_calls") or []),
        "finish_reason": str(choice.get("finish_reason") or ""),
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cached_tokens": cached,
        "reasoning_tokens": reasoning_tokens,
        "cost_reported": float(reported) if isinstance(reported, int | float) else None,
        "cost": max(computed, float(reported)) if isinstance(reported, int | float) else computed,
    }


def _one(item: it.Item, length: int, arm: str, cpt: float, cap: float) -> dict[str, Any]:
    global _spent
    request = it.render(item, length, cpt)
    row: dict[str, Any] = {
        "item": item.id, "arm": arm, "length": length, "rule": item.rule, "rule_pos": item.rule_pos,
        "depth": item.depth, "target": item.target, "country": item.country, "expected": item.expected,
        "sha": request["sha"], "filler_units": request["filler_units"], "est_chars": request["est_chars"],
        "error": "", "seconds": 0.0,
    }
    projected = request["est_chars"] / cpt * PRICE_IN / 1e6 * 1.1
    with _lock:
        if _spent + projected > cap:
            row["error"] = "budget: not sent"
            return row
        _spent += projected  # reserved now, settled below
    started = time.time()
    got: dict[str, Any] | None = None
    for attempt in range(RETRIES + 1):
        try:
            got = _call(request)
            break
        except Exception as exc:  # noqa: BLE001 -- a failed call is a halt, counted, never a zero
            row["error"] = f"{type(exc).__name__}: {exc}"[:300]
            if attempt < RETRIES:
                time.sleep(15 * (attempt + 1))
    row["seconds"] = round(time.time() - started, 1)
    with _lock:
        _spent -= projected
        if got is not None:
            _spent += got["cost"]
    if got is None:
        return row
    row["error"] = ""
    row.update(got)
    if got["provider"] and got["provider"] != PROVIDER:
        row["error"] = f"misrouted: {got['provider']}"
        return row
    row["grade"] = it.grade(item, got["content"], got["tool_calls"], got["finish_reason"])
    return row


def _plan(item: it.Item, lengths: list[int], replay: bool) -> list[tuple[int, str]]:
    """This item's calls, in an order drawn from its seed: no length is always first or last."""
    calls = [(n, str(n)) for n in lengths]
    if replay:
        calls.append((it.CONTROL, "replay"))
    random.Random(item.seed ^ 0x0DE7).shuffle(calls)
    return calls


def execute(plan: list[tuple[it.Item, list[tuple[int, str]]]], cpt: float, cap: float, workers: int, out: Path,
            meta: dict[str, Any]) -> None:
    rows: list[dict[str, Any]] = []

    def run_item(item: it.Item, calls: list[tuple[int, str]]) -> list[dict[str, Any]]:
        return [_one(item, n, arm, cpt, cap) for n, arm in calls]

    started = time.time()
    total_calls = sum(len(c) for _, c in plan)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_item, item, calls): item for item, calls in plan}
        for done, future in enumerate(as_completed(futures), 1):
            item = futures[future]
            got = future.result()
            rows.extend(got)
            marks = " ".join(
                f"{r['arm']}:{'E' if r['error'] else ('+' if r['grade']['ok'] else r['grade']['kind'][:4])}"
                f"({r.get('prompt_tokens', 0) // 1000}k)"
                for r in got
            )
            errors = sum(1 for r in rows if r["error"])
            print(f"  [{done:>3}/{len(plan)}] {item.id} {item.rule:7} d={item.depth} {marks}  "
                  f"US${_spent:.3f}  err {errors}/{len(rows)}", flush=True)
            _write(out, rows, meta, cpt, time.time() - started)
            if len(rows) >= 30 and errors / len(rows) > 0.10:
                print(f"STOP RULE: {errors}/{len(rows)} calls errored")
                for pending in futures:
                    pending.cancel()
                break
    _write(out, rows, meta, cpt, time.time() - started)
    print(f"\nwrote {out}: {len(rows)}/{total_calls} rows, US${_spent:.4f}")


def _write(out: Path, rows: list[dict[str, Any]], meta: dict[str, Any], cpt: float, seconds: float) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    order = {r: k for k, r in enumerate(sorted({x["item"] for x in rows}))}
    ordered = sorted(rows, key=lambda r: (order[r["item"]], r["arm"] != "replay", r["length"]))
    payload = {
        **meta, "model": MODEL, "provider": PROVIDER, "temperature": TEMPERATURE, "max_tokens": MAX_TOKENS,
        "chars_per_token": cpt, "prices_per_m": [PRICE_IN, PRICE_CACHED, PRICE_OUT],
        "usd": round(sum(r.get("cost", 0.0) for r in rows), 6), "wall_seconds": round(seconds, 1),
        "rows": ordered,
    }
    out.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


# --------------------------------------------------------------------------------------------------
# Statistics


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def mcnemar_exact(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, k) for k in range(0, min(b, c) + 1)) / 2**n)


def newcombe_paired(a: int, b: int, c: int, d: int, z: float = 1.96) -> tuple[float, float, float]:
    """Newcombe (1998) method 10: CI for p1 - p2 on paired binary data.

    p1 = (a + b) / n is the rate at the long length, p2 = (a + c) / n the rate at 4k; ``b`` counts
    pairs right only at the long length, ``c`` pairs right only at 4k. Returns (delta, lower, upper).
    """
    n = a + b + c + d
    if n == 0:
        return (0.0, -1.0, 1.0)
    p1, p2 = (a + b) / n, (a + c) / n
    l1, u1 = wilson(a + b, n, z)
    l2, u2 = wilson(a + c, n, z)
    denom = (a + b) * (c + d) * (a + c) * (b + d)
    phi = 0.0
    if denom > 0:
        num = a * d - b * c
        phi = (max(num - n / 2, 0.0) if num > 0 else num) / math.sqrt(denom)
    delta = p1 - p2
    dl = math.sqrt(max(0.0, (p1 - l1) ** 2 - 2 * phi * (p1 - l1) * (u2 - p2) + (u2 - p2) ** 2))
    du = math.sqrt(max(0.0, (u1 - p1) ** 2 - 2 * phi * (u1 - p1) * (p2 - l2) + (p2 - l2) ** 2))
    return (delta, max(-1.0, delta - dl), min(1.0, delta + du))


#: Registered: a length is useful while the 95% lower bound of (acc_L - acc_4k) is at or above this.
MARGIN = -0.10
#: Registered: the proposed trigger is this share of the useful length.
TRIGGER_SHARE = 0.8


def report(path: Path, show: int = 6) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = payload["rows"]
    valid = [r for r in rows if not r["error"]]
    halted = [r for r in rows if r["error"]]
    print(f"{payload['model']} via {payload['provider']}  T={payload['temperature']}  "
          f"US${payload['usd']:.4f}  rows {len(rows)}  halted {len(halted)}")
    for r in halted:
        print(f"  HALTED {r['item']} {r['arm']}: {r['error'][:120]}")
    by: dict[tuple[str, str], dict[str, Any]] = {(r["item"], r["arm"]): r for r in valid}
    arms = sorted({r["arm"] for r in valid}, key=lambda a: (a == "replay", int(a) if a.isdigit() else 0))
    summary: dict[str, Any] = {"cells": {}, "paired": {}, "floor": None}

    print("\nPER LENGTH (all valid rows)")
    print(f"{'arm':>7} {'n':>4} {'ok':>4} {'acc':>6} {'Wilson 95%':>16} {'fact':>6} {'rule|1':>7} "
          f"{'break':>6} {'forget':>6} {'med tok':>8} {'cache':>6} {'reas':>6} {'sec':>6} {'US$':>7}")
    for arm in arms:
        cell = [r for r in valid if r["arm"] == arm]
        n = len(cell)
        ok = sum(r["grade"]["ok"] for r in cell)
        lo, hi = wilson(ok, n)
        fact = sum(bool(r["grade"]["fact_ok"]) for r in cell)
        single = [r for r in cell if r["grade"]["rule_ok"] is not None]
        rule = sum(r["grade"]["rule_ok"] for r in single)
        brk = sum(r["grade"]["kind"] in it.BREAKAGE for r in cell)
        fgt = sum(r["grade"]["kind"] in it.FORGETTING for r in cell)
        med = int(statistics.median(r["prompt_tokens"] for r in cell)) if cell else 0
        cache = sum(r["cached_tokens"] for r in cell) / max(1, sum(r["prompt_tokens"] for r in cell))
        reas = statistics.median(r["reasoning_tokens"] for r in cell) if cell else 0
        secs = statistics.median(r["seconds"] for r in cell) if cell else 0
        usd = sum(r["cost"] for r in cell)
        print(f"{arm:>7} {n:>4} {ok:>4} {ok / max(1, n):>6.3f} [{lo:.3f}, {hi:.3f}] {fact / max(1, n):>6.3f} "
              f"{rule}/{len(single):<5} {brk:>6} {fgt:>6} {med:>8} {cache:>6.2f} "
              f"{reas:>6.0f} {secs:>6.1f} {usd:>7.4f}")
        kinds: dict[str, int] = {}
        for r in cell:
            kinds[r["grade"]["kind"]] = kinds.get(r["grade"]["kind"], 0) + 1
        # Descriptive, not registered: did the answers that skipped reasoning fail more often?
        skipped = [r for r in cell if r["reasoning_tokens"] == 0]
        skipped_fail = sum(not r["grade"]["ok"] for r in skipped)
        reasoned_fail = sum(not r["grade"]["ok"] for r in cell if r["reasoning_tokens"] > 0)
        summary["cells"][arm] = {"n": n, "ok": ok, "acc": ok / max(1, n), "wilson": [lo, hi],
                                 "fact_ok": fact, "rule_ok": [rule, len(single)], "kinds": kinds,
                                 "median_prompt_tokens": med, "cache_share": cache, "usd": usd,
                                 "median_reasoning_tokens": reas, "median_seconds": secs,
                                 "no_reasoning": [skipped_fail, len(skipped)],
                                 "with_reasoning_failures": reasoned_fail}
        print(f"        kinds: {kinds}   no reasoning: {len(skipped)} rows, {skipped_fail} failed; "
              f"with reasoning: {n - len(skipped)} rows, {reasoned_fail} failed")

    def pair(arm: str, base: str = str(it.CONTROL)) -> tuple[int, int, int, int]:
        a = b = c = d = 0
        for (item, x), r in by.items():
            if x != arm or (item, base) not in by:
                continue
            long_ok, short_ok = r["grade"]["ok"], by[(item, base)]["grade"]["ok"]
            a += long_ok and short_ok
            b += long_ok and not short_ok
            c += short_ok and not long_ok
            d += not long_ok and not short_ok
        return a, b, c, d

    if "replay" in arms:
        a, b, c, d = pair("replay")
        _, flo, fhi = newcombe_paired(a, b, c, d)
        same = sum(1 for (item, x), r in by.items() if x == "replay" and (item, str(it.CONTROL)) in by
                   and r["content"] == by[(item, str(it.CONTROL))]["content"])
        summary["floor"] = {"table": [a, b, c, d], "discordant": b + c, "n": a + b + c + d,
                            "ci": [flo, fhi], "gate_pass": flo >= MARGIN, "identical_content": same}
        print(f"\nFLOOR 4k replay vs 4k: both {a}, replay-only {b}, 4k-only {c}, neither {d} -> "
              f"{b + c}/{a + b + c + d} discordant; exact McNemar p = {mcnemar_exact(b, c):.3g}; "
              f"Newcombe [{flo:+.3f}, {fhi:+.3f}] -> FLOOR GATE {'PASS' if flo >= MARGIN else 'FAIL'}; "
              f"byte-identical answers {same}/{a + b + c + d}")

    control = summary["cells"].get(str(it.CONTROL))
    if control:
        gate = control["acc"] >= 0.90
        print(f"\nPOSITIVE CONTROL 4k: {control['ok']}/{control['n']} = {control['acc']:.3f} -> "
              f"{'PASS' if gate else 'FAIL: no length reading (void)'}")
        summary["control_pass"] = gate

    print("\nPAIRED vs 4k (Newcombe method 10, 95%; exact McNemar)")
    useful = it.CONTROL
    still = True
    for arm in arms:
        if arm in ("replay", str(it.CONTROL)):
            continue
        a, b, c, d = pair(arm)
        delta, lo, hi = newcombe_paired(a, b, c, d)
        passes = lo >= MARGIN
        p = mcnemar_exact(b, c)
        summary["paired"][arm] = {"table": [a, b, c, d], "delta": delta, "ci": [lo, hi], "mcnemar_p": p,
                                  "within_margin": passes}
        print(f"  {arm:>7}: n={a + b + c + d:<3} both {a:<3} long-only {b:<2} 4k-only {c:<2} neither {d:<2} "
              f"delta {delta:+.3f} [{lo:+.3f}, {hi:+.3f}]  p={p:.3g}  {'within' if passes else 'OUTSIDE'} {MARGIN:+.2f}")
        if still and passes:
            useful = int(arm)
        else:
            still = False
    top = max(int(a) for a in arms if a.isdigit())
    # Registered: the useful length is reported as that cell's median provider-counted prompt tokens.
    realised = summary["cells"][str(useful)]["median_prompt_tokens"]
    summary["useful_length"] = {"cell": useful, "median_prompt_tokens": realised, "lower_bound": useful == top}
    trigger = int(TRIGGER_SHARE * realised) // 1000 * 1000
    summary["trigger_tokens"] = trigger
    print(f"\nUSEFUL LENGTH (largest L with every tested L' <= L within margin): the {useful:,} cell, "
          f"median {realised:,} provider tokens" + ("  -- the top of the ladder: a lower bound" if useful == top else ""))
    print(f"PROPOSED TRIGGER = floor({TRIGGER_SHARE} x {realised:,}, to 1,000) = {trigger:,} provider tokens")

    print("\nBY TARGET DEPTH (ok/n)")
    for arm in arms:
        cells = []
        for depth in it.TARGET_DEPTHS:
            cell = [r for r in valid if r["arm"] == arm and r["depth"] == depth]
            cells.append(f"d={depth}: {sum(r['grade']['ok'] for r in cell)}/{len(cell)}")
        print(f"  {arm:>7}  " + "   ".join(cells))
    print("\nBY RULE (ok/n)")
    for arm in arms:
        cells = []
        for rule in it.RULE_IDS:
            cell = [r for r in valid if r["arm"] == arm and r["rule"] == rule]
            cells.append(f"{rule}: {sum(r['grade']['ok'] for r in cell)}/{len(cell)}")
        print(f"  {arm:>7}  " + "  ".join(cells))

    print(f"\nRAW OUTPUTS: every failure, and {show} random passes per length")
    rng = random.Random(7)
    for arm in arms:
        cell = [r for r in valid if r["arm"] == arm]
        fails = [r for r in cell if not r["grade"]["ok"]]
        passes = [r for r in cell if r["grade"]["ok"]]
        print(f"--- {arm}: {len(fails)} failures")
        for r in fails + rng.sample(passes, min(show, len(passes))):
            print(f"  {r['item']} {r['grade']['kind']:>14}  want {r['expected']!r:30} got {r['content'][:160]!r}"
                  f"  finish={r['finish_reason']} tools={r['tool_calls']}")
    return summary


def check() -> int:
    failures = it.selftest()
    print(f"grader self-test: {'PASS' if not failures else 'FAIL'}")
    for f in failures:
        print("  ", f)
    collisions = it.corpus_collisions()
    print(f"corpus collisions (service names / landmarks inside the filler): {len(collisions)}")
    for c in collisions[:20]:
        print("  ", c)
    item = it.items("M", 1)[0]
    print(f"\nitem {item.id}: rule={item.rule} pos={item.rule_pos} depth={item.depth} target={item.target} "
          f"country={item.country} expected={item.expected!r}")
    for n in it.LADDER:
        req = it.render(item, n)
        print(f"  {n:>7}: est_chars {req['est_chars']:>8}  ~{req['est_chars'] / it.DEFAULT_CHARS_PER_TOKEN:>9.0f} tok  "
              f"units {req['filler_units']:>3}  runbook positions {req['positions']}  sha {req['sha']}")
    req = it.render(item, it.CONTROL)
    print("\n--- 4k render, first user message ---")
    print(req["messages"][1]["content"])
    print("\n--- a runbook, and the tail ---")
    for m in req["messages"]:
        if m["role"] == "tool" and m["content"].startswith("# "):
            print(m["content"])
            break
    for m in req["messages"][-2:]:
        print(f"[{m['role']}] {m['content']}")
    return 1 if failures or collisions else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--pilot", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--report", default="")
    ap.add_argument("--n", type=int, default=0)
    ap.add_argument("--cpt", type=float, default=it.DEFAULT_CHARS_PER_TOKEN)
    ap.add_argument("--cap", type=float, default=0.0, help="US$ this invocation may spend")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    if args.check:
        return check()
    if args.report:
        source = Path(args.report)
        summary = report(source)
        target = source.with_name(source.stem + "-summary.json")
        target.write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8", newline="\n")
        return 0
    if args.pilot:
        pilot = it.items("P", 20)
        plan = [(item, _plan(item, [it.CONTROL] + ([it.LADDER[-1]] if k < 6 else []), replay=False))
                for k, item in enumerate(pilot)]
        execute(plan, args.cpt, args.cap or 0.15, args.workers, Path(args.out or HERE / "results" / "pilot.json"),
                {"phase": "pilot"})
        return 0
    if args.run:
        if not args.n:
            raise SystemExit("--n is fixed in PREREGISTRATION.md after the pilot; pass it")
        main_items = it.items("M", args.n)
        plan = [(item, _plan(item, list(it.LADDER), replay=True)) for item in main_items]
        execute(plan, args.cpt, args.cap, args.workers, Path(args.out or HERE / "results" / "main.json"),
                {"phase": "main", "n": args.n})
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
