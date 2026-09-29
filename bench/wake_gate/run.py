"""The wake gate on the cron daemon — see PREREGISTRATION.md (written first).

    python -m bench.wake_gate.run

Local only (Ollama qwen3:4b), US$ 0. Nothing sleeps: the gate's policy is scored against the
committed labels and the results carry the threshold sweep beside the shipped floor. One row per
scenario in ``results/wake_gate.jsonl``.
"""

from __future__ import annotations

import json
import random
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bench.jev_decisions.report import auroc  # noqa: E402
from chimera.decisions import Choice  # noqa: E402
from chimera.decisions.lint import errors  # noqa: E402
from chimera.decisions.local import LocalLogprobBackend  # noqa: E402

DECISION = "scheduler.wake"
OUT = Path(__file__).resolve().parent / "results" / "wake_gate.jsonl"
ITEMS = Path(__file__).resolve().parent / "items.jsonl"
NUM_CTX = 16384
SKIP_FLOOR = 0.2  # the shipped floor; the sweep re-scores the recorded answers over more floors
SWEEP_FLOORS = (0.1, 0.15, 0.2, 0.3, 0.5)
DRAWS, SEED = 2000, 7

WAKE = Choice(
    key="wake",
    instructions=(
        "A scheduled job's agent was asleep. Something fired. Decide whether this is worth waking the "
        "agent for a full turn, or whether it should keep sleeping. \"wake\" means the event plausibly "
        "changes what the agent is waiting on or produces something a person asked for. \"not_yet\" "
        "means the event is routine progress the next scheduled tick would cover anyway. \"unrelated\" "
        "means the event has nothing to do with what the job is for."
    ),
    options=("wake", "not_yet", "unrelated"),
    criteria={
        "wake": "Resume the agent now: the event merits a full turn this tick.",
        "not_yet": "Stay asleep this tick; the next scheduled tick covers it.",
        "unrelated": "The event does not concern this job at all.",
    },
)


def state_of(scenario: dict[str, Any]) -> str:
    """The registered state dict, rendered flat for the local backend's string state.

    Field caps are the registration's (600 chars on the job action and the last answer); the event
    carries a name only, never a body. Composed once here, so the corpus row and the model see the
    same string the product would build.
    """
    return json.dumps(
        {
            "job": {"name": scenario["job"]["name"], "schedule": scenario["job"]["schedule"],
                    "action": scenario["job"]["action"][:600]},
            "last_result": (scenario.get("last_result") or "")[:600],
            "event": scenario["event"],
            "skipped": scenario["skipped"],
        },
        ensure_ascii=False, sort_keys=True,
    )


def policy(choice: str | None, p: float | None) -> str:
    """The registered policy, in code: suppress only a confident put-down (wakegate's table).

    The registration's §Why sentence — "the choice is `wake` and its probability is below the skip
    floor" — is ambiguous as written; the reading implemented here is the table it cites, wakegate's
    "skip only on a confident p(wake) < 0.2", with the argmax required to agree: a put-down choice
    whose wake-mass sits at or under the floor. Everything else wakes: the unsure band (a put-down
    above the floor) wakes, a halt or unreadable answer wakes, a `wake` choice wakes at any
    confidence. The reading is recorded here and in the RESULTS rather than corrected in silence —
    the sentence and the code travel together (the claim_vs_diff rule).
    """
    if choice is None or p is None:
        return "wake(error)"
    if choice == "wake":
        return "wake"  # wake is wake at any confidence — the error/wake paths all point the same way
    # choice in {not_yet, unrelated}: suppress only a confident put-down.
    return f"skip(p_wake<={SKIP_FLOOR})" if p <= SKIP_FLOOR else "wake(unsure)"


def ci(rows: list[dict[str, Any]]) -> tuple[float | None, float, float]:
    scored = [(r["p_wake"], 1 if r["label"] == "wake" else 0) for r in rows if r["p_wake"] is not None]
    if not scored:
        return None, 0.0, 0.0
    point = auroc(scored) or 0.0
    rng = random.Random(SEED)
    draws: list[float] = []
    for _ in range(DRAWS):
        sample = [scored[rng.randrange(len(scored))] for _ in scored]
        a = auroc(sample)
        if a is not None:
            draws.append(a)
    draws.sort()
    return point, draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws)) - 1]


def main() -> None:
    if errors(WAKE):
        raise SystemExit(f"the question does not lint clean: {errors(WAKE)}")
    scenarios = [json.loads(line) for line in ITEMS.read_text(encoding="utf-8").splitlines() if line.strip()]
    wake_items = [s for s in scenarios if s["label"] == "wake"]
    sleep_items = [s for s in scenarios if s["label"] in ("not_yet", "unrelated")]
    boundary = [s for s in scenarios if s["label"].startswith("rule:")]
    if not (ITEMS.exists() and len(wake_items) >= 10 and len(sleep_items) >= 10 and scenarios):
        raise SystemExit(
            f"the corpus is not registered: {len(wake_items)} wake / {len(sleep_items)} sleep / "
            f"{len(boundary)} rule-boundary — the registration needs ≥10 wake, ≥10 sleep, labelled "
            "before the first call (edit bench/wake_gate/items.jsonl, not this runner)"
        )

    backend = LocalLogprobBackend("http://127.0.0.1:11434", "qwen3:4b", timeout_s=300.0)
    client = httpx.Client(timeout=300.0)
    rows: list[dict[str, Any]] = []
    for scenario in scenarios:
        if scenario["label"].startswith("rule:"):
            # A rule-boundary family is asserted, never scored: the gate must not ask the model
            # about a user-raised event at all. The corpus row records the assertion only.
            rows.append({"id": scenario["id"], "label": scenario["label"], "asked": False,
                         "assertion": "gate never asks; wakes by rule"})
            continue
        state = state_of(scenario)
        body = backend.body(state, WAKE)
        body["options"]["num_ctx"] = NUM_CTX
        t0 = time.perf_counter()
        response = client.post("http://127.0.0.1:11434/api/chat", json=body)
        response.raise_for_status()
        data = response.json()
        seconds = time.perf_counter() - t0
        prompt_tokens = int(data.get("prompt_eval_count") or 0)
        if not 0 < prompt_tokens < NUM_CTX:
            raise SystemExit(f"{scenario['id']}: prompt_eval_count {prompt_tokens} — truncated or unread")
        reading = backend.read(data, WAKE)
        # p_wake = the mass the model put on the wake option itself; the policy reads it, not the
        # argmax alone (the study-21 lesson: never threshold a raw Choice mass against a Noul-shaped
        # event — here the event IS one option, so the share is the number).
        p_wake = (reading.shares or {}).get("wake")
        rows.append({
            "id": scenario["id"], "label": scenario["label"], "why": scenario.get("why", ""),
            "asked": True, "choice": reading.choice, "p_wake": p_wake,
            "policy": policy(reading.choice, p_wake), "mass": reading.mass,
            "prompt_tokens": prompt_tokens, "seconds": round(seconds, 2), "raw": reading.raw,
        })
        print(f"{scenario['id']:32s} {scenario['label']:10s} {reading.choice} "
              f"p_wake={p_wake} {seconds:.1f}s", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in rows), encoding="utf-8")

    # -- the registered metrics -----------------------------------------------------------------
    scored = [r for r in rows if r.get("asked")]
    positives = [r for r in scored if r["label"] == "wake"]
    negatives = [r for r in scored if r["label"] in ("not_yet", "unrelated")]
    false_skips = [r for r in positives if str(r["policy"]).startswith("skip")]
    suppressed = [r for r in negatives if str(r["policy"]).startswith("skip")]
    unsure_wakes = [r for r in scored if r["policy"] == "wake(unsure)"]
    point, lo, hi = ci(scored)
    sweep: dict[str, dict[str, int]] = {}
    for floor in SWEEP_FLOORS:
        # The sweep re-reads the recorded answers under each floor: a put-down choice whose
        # wake-mass sits at or under the floor suppresses (the same reading the shipped policy
        # implements, at the shipped floor and at the others).
        fs = sum(1 for r in positives if (r["p_wake"] is not None and r["choice"] in ("not_yet", "unrelated")
                                         and r["p_wake"] <= floor))
        sup = sum(1 for r in negatives if (r["p_wake"] is not None and r["choice"] in ("not_yet", "unrelated")
                                           and r["p_wake"] <= floor))
        sweep[f"{floor}"] = {"false_skips": fs, "suppressed": sup, "reached": len(negatives) + len(positives)}
    # The repeat arm (§Items): one re-ask of every scored scenario, greedy — a flip is reported,
    # never averaged away.
    flips: list[str] = []
    for scenario in scenarios:
        if scenario["label"].startswith("rule:"):
            continue
        state = state_of(scenario)
        body = backend.body(state, WAKE)
        body["options"]["num_ctx"] = NUM_CTX
        data = client.post("http://127.0.0.1:11434/api/chat", json=body).json()
        reading = backend.read(data, WAKE)
        p2 = (reading.shares or {}).get("wake")
        first = next(r for r in scored if r["id"] == scenario["id"])
        if reading.choice != first["choice"] or (p2 is None) != (first["p_wake"] is None) or (
                p2 is not None and first["p_wake"] is not None and abs(p2 - first["p_wake"]) > 1e-9):
            flips.append(scenario["id"])
    # Latency p50/p95 (§Metrics) and the cost per 1,000 ticks, printed rather than estimated.
    times = sorted(r["seconds"] for r in scored)
    p50 = times[len(times) // 2] if times else 0.0
    p95 = times[max(int(0.95 * len(times)) - 1, 0)] if times else 0.0
    tokens = [r["prompt_tokens"] for r in scored]
    # qwen3:4b on this machine is a local model: the cost line is the hosted one the registration
    # asks to be printed — the same token counts at the cheapest hosted tier's posted price, stated
    # as a price assumption beside the number, not as a measurement of this machine.
    HOSTED_PER_1M = 0.10  # assumption, stated: a small-model tier at US$ 0.10 per 1M input tokens
    cost_per_1000 = round(sum(tokens) / max(len(tokens), 1) * 1000 / 1_000_000 * HOSTED_PER_1M, 4)
    # The T baseline (§Arms): the daemon as it ships — every timer wakes, no calls, no savings.
    t_baseline = {"wakeups": len(scored), "suppressed": 0, "calls": 0}
    summary = {
        "scenarios": len(scenarios), "scored": len(scored), "no_read": sum(1 for r in scored if r["p_wake"] is None),
        "false_skips_at_shipped_floor": len(false_skips),
        "suppressed_of_sleep": f"{len(suppressed)}/{len(negatives)}",
        "suppression_rate": round(len(suppressed) / len(negatives), 3) if negatives else None,
        "unsure_band_share": round(len(unsure_wakes) / len(scored), 3) if scored else None,
        "auroc_wake_vs_sleep": [point, lo, hi],
        "sweep": sweep,
        "repeat_flips": flips,
        "latency_p50_s": p50, "latency_p95_s": p95,
        "prompt_tokens_mean": round(sum(tokens) / max(len(tokens), 1), 1),
        "cost_per_1000_ticks_hosted_assumption_usd": cost_per_1000,
        "hosted_price_assumption": f"US$ {HOSTED_PER_1M}/1M input tokens — an assumption, not a measurement",
        "t_baseline_always_wake": t_baseline,
        "seconds_per_call": round(sum(r["seconds"] for r in scored) / max(len(scored), 1), 2),
        "prompt_tokens_max": max(tokens, default=0),
        "resolved_model": backend.resolved_model(),
        "policy_reading": ("skip = put-down choice (not_yet/unrelated) with wake-mass ≤ floor; the "
                           "registration's §Why sentence was ambiguous, the table it cites is what "
                           "the code implements — recorded, not corrected in silence"),
        "decision_rule": ("ships shadow iff false_skips==0 and suppression_rate>=0.5; harm (any false "
                          "skip) ships nothing and publishes the miss"),
    }
    (OUT.parent / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
