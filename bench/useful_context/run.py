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
import random
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from chimera.eval import proportions

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

import items as it  # noqa: E402

MODEL = "openrouter/deepseek/deepseek-v4-flash-0731"
PROVIDER = "DeepInfra"
TEMPERATURE: float | None = 0.0
MAX_TOKENS = 8_000
#: DeepInfra's quote for this model on the OpenRouter endpoints listing, read 2026-09-25 (per M tokens).
PRICE_IN, PRICE_CACHED, PRICE_OUT = 0.060, 0.015, 0.180
RETRIES = 2
#: Seconds to wait before retrying a call the route refused for load (HTTP 429), times the attempt
#: number. ``None``: a refusal waits like any other error (15 s, 30 s).
RATE_LIMIT_WAIT: int | None = None
#: How long one call may take, and how long the top rung's may. ``TOP_TIMEOUT`` None: the same for all.
TIMEOUT = 900
TOP_TIMEOUT: int | None = None
#: The lengths a run climbs. The default profile's is the registered `items.LADDER`.
LADDER: tuple[int, ...] = it.LADDER
#: A row billed at a price this far from the quoted one came from another tier of the same provider
#: (OpenAI serves flex, standard and fast under one provider name). ``None``: not checked.
TIER_BAND: tuple[float, float] | None = None

#: Everything a run's model identity changes. ``v4flash`` is the registered 2026-09-25 run and must
#: stay byte-for-byte what produced `results/main.json`; ``luna`` is PREREGISTRATION_luna.md.
PROFILES: dict[str, dict[str, Any]] = {
    "v4flash": {},
    "luna": {
        "MODEL": "openrouter/openai/gpt-6-luna",
        "PROVIDER": "OpenAI",
        # The model takes no temperature (catalogue note); sending one is refused, so none is sent.
        "TEMPERATURE": None,
        # OpenAI's standard route on the endpoints listing, read 2026-09-27, below its 272k price step.
        "PRICE_IN": 0.10, "PRICE_CACHED": 0.01, "PRICE_OUT": 0.50,
        "LADDER": (4_000, 16_000, 32_000, 64_000, 128_000, 256_000),
        # Amendment 2: the standard route bills a prompt as a cache write (1.25x); flex would read <= 0.63x, fast >= 2x.
        "TIER_BAND": (0.8, 1.35),
    },
    # PREREGISTRATION_large.md. Both route to the cheapest fp8 endpoint serving 1,048,576 tokens on
    # the listing read 2026-09-27; both take a temperature, so 0.0 is sent as in the v4-flash run.
    "glm53flash": {
        "MODEL": "openrouter/z-ai/glm-5.3-flash",
        "PROVIDER": "Sail Research",
        "PRICE_IN": 0.045, "PRICE_CACHED": 0.0285, "PRICE_OUT": 0.60,
        "LADDER": (4_000, 16_000, 32_000, 64_000, 128_000, 256_000, 512_000, 900_000),
        "TIER_BAND": (0.8, 1.35),
        "CORPUS_ROOTS": ("chimera", "tests"),
        # Amendment, option A (owner, 2026-09-29): an uncached 900k prefill on this route outlasts 900 s,
        # so the top rung alone waits 2,400 s, once. It is never retried, so a slow prefill is not sent
        # (and billed) up to three times. Every other rung keeps 900 s and two retries.
        "TOP_TIMEOUT": 2_400,
    },
    # Amendment, option B (owner, 2026-09-30): the 900k rung cannot be served on this route, whose server
    # cuts a call at ~300 s before its first byte, streamed or not. The ladder tops out at 512k, where every
    # call of both earlier runs answered in 174-207 s. Everything else is the glm53flash profile, without
    # option A's top-rung timeout: at 512k the top rung is waited for like every other one.
    "glm53flash512": {
        "MODEL": "openrouter/z-ai/glm-5.3-flash",
        "PROVIDER": "Sail Research",
        "PRICE_IN": 0.045, "PRICE_CACHED": 0.0285, "PRICE_OUT": 0.60,
        "LADDER": (4_000, 16_000, 32_000, 64_000, 128_000, 256_000, 512_000),
        "TIER_BAND": (0.8, 1.35),
        "CORPUS_ROOTS": ("chimera", "tests"),
        # Second amendment to option B (owner, 2026-09-30): the first run was ended by upstream 429s, so a
        # call the route refuses for load waits 60 s, then 120 s, before its two retries. Run with
        # --workers 1. Neither changes what is measured, only how hard the route is pressed.
        "RATE_LIMIT_WAIT": 60,
    },
    # Amendment, option 2 (owner, 2026-09-30): the Sail Research route refused even one call at a time
    # (HTTP 429 upstream), so the same model is measured on another route, chosen by the rule this study
    # registered: the cheapest fp8 endpoint serving 1,048,576 tokens on the listing (read 2026-09-30,
    # 13:49 UTC), Sail Research excluded. Everything else is `glm53flash512`.
    "glm53flash512_novita": {
        "MODEL": "openrouter/z-ai/glm-5.3-flash",
        "PROVIDER": "Novita",
        "PRICE_IN": 0.084, "PRICE_CACHED": 0.0168, "PRICE_OUT": 0.28,
        "LADDER": (4_000, 16_000, 32_000, 64_000, 128_000, 256_000, 512_000),
        "TIER_BAND": (0.8, 1.35),
        "CORPUS_ROOTS": ("chimera", "tests"),
        "RATE_LIMIT_WAIT": 60,
    },
    "glm53": {
        "MODEL": "openrouter/z-ai/glm-5.3",
        "PROVIDER": "Baidu",
        "PRICE_IN": 0.3556, "PRICE_CACHED": 0.06604, "PRICE_OUT": 1.1176,
        "LADDER": (4_000, 16_000, 32_000, 64_000, 128_000, 256_000, 512_000),
        "TIER_BAND": (0.8, 1.35),
        "CORPUS_ROOTS": ("chimera", "tests"),
    },
}


def use_profile(name: str) -> None:
    for key, value in PROFILES[name].items():
        if key == "CORPUS_ROOTS":
            it.CORPUS_ROOTS = value  # the filler's source, which lives in items.py
        else:
            globals()[key] = value


_lock = threading.Lock()
_spent = 0.0


def _cost(prompt: int, cached: int, completion: int) -> float:
    return ((prompt - cached) * PRICE_IN + cached * PRICE_CACHED + completion * PRICE_OUT) / 1e6


def _call(request: dict[str, Any], timeout: int = 900) -> dict[str, Any]:
    import litellm

    from chimera.providers.thinking import strip_think

    sampling: dict[str, Any] = {} if TEMPERATURE is None else {"temperature": TEMPERATURE}
    raw = litellm.completion(
        model=MODEL,
        messages=request["messages"],
        tools=request["tools"],
        max_tokens=MAX_TOKENS,
        timeout=timeout,
        extra_body={"provider": {"order": [PROVIDER], "allow_fallbacks": False}, "usage": {"include": True}},
        **sampling,
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


class EmptyResponse(Exception):
    """A reply the route sent without reading the prompt.

    Seen on 2026-09-30, once in the Novita pilot: a 512k call came back after 23.6 s with
    finish_reason "stop", no content, 0 prompt tokens, 0 completion tokens and no cost. The grader
    scored it `empty`, a wrong answer, so a route failure would have counted as the model forgetting.
    No row of any earlier file has this shape. It is retried like any failed call, and if it persists
    it is an error, never a grade.
    """


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
    top = TOP_TIMEOUT is not None and length == LADDER[-1]
    retries = 0 if top else RETRIES
    for attempt in range(retries + 1):
        try:
            got = _call(request, timeout=TOP_TIMEOUT if top and TOP_TIMEOUT else TIMEOUT)
            if not got["prompt_tokens"]:
                raise EmptyResponse("the route answered without reading the prompt (0 prompt tokens)")
            break
        except Exception as exc:  # noqa: BLE001 -- a failed call is a halt, counted, never a zero
            got = None
            row["error"] = f"{type(exc).__name__}: {exc}"[:300]
            if attempt < retries:
                refused = "RateLimit" in type(exc).__name__ or "429" in str(exc)[:200]
                wait = RATE_LIMIT_WAIT if refused and RATE_LIMIT_WAIT else 15
                time.sleep(wait * (attempt + 1))
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
    if TIER_BAND is not None and got["cost_reported"]:
        ratio = got["cost_reported"] / max(1e-12, _cost(got["prompt_tokens"], got["cached_tokens"], got["completion_tokens"]))
        row["billed_ratio"] = round(ratio, 3)
        if not TIER_BAND[0] <= ratio <= TIER_BAND[1]:
            row["error"] = f"misrouted: billed at {ratio:.2f}x the quoted tier"
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
    consumed: set[Any] = set()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_item, item, calls): item for item, calls in plan}
        for done, future in enumerate(as_completed(futures), 1):
            consumed.add(future)
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
    # Leaving the `with` waited for the items that were already running when the stop rule fired: they
    # were sent and paid for. Their rows used to be dropped while their cost stayed in `_spent`, so the
    # 2026-09-30 option-A run printed US$ 0.3166 over a file that summed to US$ 0.1811. They are kept
    # now, beside the analysed rows and never among them, and the file carries what the runner counted.
    after_stop = [
        row for f in futures
        if f not in consumed and f.done() and not f.cancelled() and f.exception() is None
        for row in f.result()
    ]
    _write(out, rows, meta, cpt, time.time() - started, after_stop=after_stop, final=True)
    print(f"\nwrote {out}: {len(rows)}/{total_calls} rows, US${_spent:.4f}")


def _write(out: Path, rows: list[dict[str, Any]], meta: dict[str, Any], cpt: float, seconds: float,
           after_stop: list[dict[str, Any]] | None = None, final: bool = False) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    order = {r: k for k, r in enumerate(sorted({x["item"] for x in rows}))}
    ordered = sorted(rows, key=lambda r: (order[r["item"]], r["arm"] != "replay", r["length"]))
    payload = {
        **meta, "model": MODEL, "provider": PROVIDER, "temperature": TEMPERATURE, "max_tokens": MAX_TOKENS,
        "timeout": TIMEOUT, "top_timeout": TOP_TIMEOUT, "retries": RETRIES, "rate_limit_wait": RATE_LIMIT_WAIT,
        "chars_per_token": cpt, "prices_per_m": [PRICE_IN, PRICE_CACHED, PRICE_OUT],
        "usd": round(sum(r.get("cost", 0.0) for r in rows), 6), "wall_seconds": round(seconds, 1),
        "rows": ordered,
    }
    if final:
        # Every call's settled cost, rows or not. Only at the end: mid-run it would include reservations
        # for calls still in flight.
        payload["runner_usd"] = round(_spent, 6)
        if after_stop:
            payload["after_stop_rows"] = after_stop
    out.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


# --------------------------------------------------------------------------------------------------
# Statistics


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    return proportions.wilson(k, n, z)


def mcnemar_exact(b: int, c: int) -> float:
    return proportions.mcnemar_exact(b, c)


def newcombe_paired(a: int, b: int, c: int, d: int, z: float = 1.96) -> tuple[float, float, float]:
    """Newcombe (1998) method 10: CI for p1 - p2 on paired binary data.

    p1 = (a + b) / n is the rate at the long length, p2 = (a + c) / n the rate at 4k; ``b`` counts
    pairs right only at the long length, ``c`` pairs right only at 4k. Returns (delta, lower, upper).
    """
    n = a + b + c + d
    if n == 0:
        return (0.0, -1.0, 1.0)
    low, high = proportions.newcombe_paired(a, c, b, d, z)  # the long length is the treatment
    return ((b - c) / n, low, high)


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
    for n in LADDER:
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
    ap.add_argument("--profile", choices=sorted(PROFILES), default="v4flash")
    args = ap.parse_args()
    use_profile(args.profile)
    if args.pilot or args.run:
        # litellm's own .env lookup starts from this script's folder, which in a git worktree has no
        # .env: the key is read from the working directory's instead, never printed.
        from dotenv import load_dotenv

        load_dotenv(Path.cwd() / ".env", override=False)
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
        plan = [(item, _plan(item, [it.CONTROL] + ([LADDER[-1]] if k < 6 else []), replay=False))
                for k, item in enumerate(pilot)]
        execute(plan, args.cpt, args.cap or 0.15, args.workers, Path(args.out or HERE / "results" / "pilot.json"),
                {"phase": "pilot"})
        return 0
    if args.run:
        if not args.n:
            raise SystemExit("--n is fixed in PREREGISTRATION.md after the pilot; pass it")
        main_items = it.items("M", args.n)
        plan = [(item, _plan(item, list(LADDER), replay=True)) for item in main_items]
        execute(plan, args.cpt, args.cap, args.workers, Path(args.out or HERE / "results" / "main.json"),
                {"phase": "main", "n": args.n})
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
