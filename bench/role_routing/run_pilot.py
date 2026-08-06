"""Costing pilot for bench/role_routing (Amendment 1) — measures US$ per instance per arm.

Outcomes are NOT evidence here and are not reported. This measures money and wall time, so that the
registered run's budget is a number rather than a guess. See PREREGISTRATION.md §7, Amendment 1.

Three things this does that the first attempt did not, each because the first attempt got it wrong:

**Ground truth for cost comes from the provider, not from receipts.** A run killed by the per-solve
timeout never writes a receipt, so the internal accounting undercounted by 8x — US$0.48 against
US$5.25 actually spent. Reading OpenRouter's own balance before and after each arm survives that,
because censored work is still billed work.

**It runs round by round, all arms per round.** Arm-at-a-time spends the whole budget on the first
arm if that arm is the expensive one, which is exactly the arm you cannot afford to be wrong about.
Rounds keep n equal across arms, so whatever the budget buys is comparable.

**It stops itself.** Before each round it checks whether the remaining balance covers the worst case
(every arm running to the timeout at the last measured burn rate) and stops if it does not. A key
cap is a backstop, not a plan: hitting it mid-solve leaves a half-run nobody can attribute.

Usage (WSL, from the repo root, with OPENROUTER_API_KEY exported):
    python bench/role_routing/run_pilot.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ARMS = ["roles_single", "roles_balanced", "roles_max"]
_SLICE = Path(os.environ.get("PILOT_SLICE", str(_HERE / "results" / "slice_pilot.jsonl")))
_OUT = Path(os.environ.get("PILOT_OUT", str(_HERE / "results")))
#: Leave this much unspent. The cap exists to bound a mistake, not to be run into: a solve killed
#: by an exhausted key is billed for the tokens it burned and produces nothing attributable.
_RESERVE = float(os.environ.get("PILOT_RESERVE", "0.40"))
#: Registered per-solve timeout (swe_bench Amendment 2, and now role_routing §3).
_TIMEOUT = int(os.environ.get("BENCH_TIMEOUT", "1800"))


def _balance() -> tuple[float, float | None]:
    """(spent, limit) for this key, from OpenRouter's own accounting. The key is never printed."""
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY is not set")
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)["data"]
    return float(data.get("usage") or 0.0), (float(data["limit"]) if data.get("limit") else None)


def _run_arm(arm: str, instance_id: str) -> tuple[float, float]:
    """Run ONE instance of one arm. Returns (usd_spent, seconds), cost read from the provider."""
    before, _ = _balance()
    started = time.monotonic()
    env = {
        **os.environ,
        "BENCH_ARM": arm,
        "BENCH_SLICE": str(_OUT / f"_one_{instance_id}.jsonl"),
        "BENCH_OUT": str(_OUT),
        "BENCH_TIMEOUT": str(_TIMEOUT),
        "BENCH_MAX_STEPS": os.environ.get("BENCH_MAX_STEPS", "30"),
    }
    subprocess.run(
        [sys.executable, str(_HERE.parent / "swe_bench" / "run_swe.py")],
        env=env, check=False,
    )
    elapsed = time.monotonic() - started
    # A moment for the provider's accounting to settle before reading it back. Without this the
    # last call of a solve can land in the NEXT arm's window and mis-attribute the cost.
    time.sleep(5)
    after, _ = _balance()
    return round(after - before, 6), elapsed


def main() -> None:
    instances = [json.loads(line) for line in _SLICE.read_text(encoding="utf-8").splitlines() if line.strip()]
    _OUT.mkdir(parents=True, exist_ok=True)

    spent, limit = _balance()
    print(f"pilot | timeout={_TIMEOUT}s | key spent US$ {spent:.4f}" + (f" of {limit:.2f}" if limit else ""))
    if limit is None:
        raise SystemExit("this key has no limit; refusing to run an uncapped costing pilot")

    ledger: list[dict[str, object]] = []
    del limit  # re-read per round; see the loop. Kept out of scope so it cannot be used stale.
    # Seeded from the buggy 900s pilot ONLY as a starting worst case; replaced by measurement after
    # round 1. Those numbers came from a frontier panel that the fusion fix has since removed, so
    # they are an over-estimate on purpose — the guard should start pessimistic.
    worst_per_arm = {"roles_single": 0.72, "roles_balanced": 1.40, "roles_max": 1.24}

    for index, instance in enumerate(instances, start=1):
        need = sum(worst_per_arm.values())
        # BOTH numbers re-read per round, and the cap is one of them. A cap raised mid-pilot is the
        # normal case — the operator watches the rounds land and decides to buy more n. Reading it
        # once at startup silently ignores that decision and stops early against a number nobody
        # believes any more; reading it once is also how a cap *lowered* mid-run gets overspent.
        spent, limit = _balance()
        if limit is None:
            print("\nstopping: the key's cap was removed mid-pilot; refusing to run uncapped")
            break
        room = limit - spent - _RESERVE
        if room < need:
            print(f"\nstopping before round {index}: worst case US$ {need:.2f} > room US$ {room:.2f}")
            break

        one = _OUT / f"_one_{instance['instance_id']}.jsonl"
        one.write_text(json.dumps(instance) + "\n", encoding="utf-8")
        print(f"\n=== round {index}: {instance['instance_id']} (room US$ {room:.2f}) ===")
        for arm in _ARMS:
            usd, seconds = _run_arm(arm, instance["instance_id"])
            # The measured cost replaces the seed, so the guard tightens as it learns. max() keeps
            # it pessimistic: one cheap solve must not license a round the expensive case cannot pay.
            worst_per_arm[arm] = max(usd, worst_per_arm.get(arm, 0.0) * 0.5)
            ledger.append({"instance": instance["instance_id"], "arm": arm, "usd": usd, "seconds": round(seconds, 1)})
            print(f"  {arm:16s} US$ {usd:7.4f}  {seconds:6.0f}s")
        one.unlink(missing_ok=True)

    out = _OUT / "pilot_cost.json"
    out.write_text(json.dumps(ledger, indent=2), encoding="utf-8")
    final, cap = _balance()
    print(f"\nwrote {out}  |  key spent US$ {final:.4f}" + (f" of {cap:.2f}" if cap else ""))
    if ledger:
        print(f"\n{'arm':16s} {'n':>2s} {'US$/inst':>9s} {'s/inst':>7s}")
        for arm in _ARMS:
            rows = [r for r in ledger if r["arm"] == arm]
            if rows:
                usd = sum(float(r["usd"]) for r in rows) / len(rows)
                sec = sum(float(r["seconds"]) for r in rows) / len(rows)
                print(f"{arm:16s} {len(rows):2d} {usd:9.4f} {sec:7.0f}")


if __name__ == "__main__":
    main()
