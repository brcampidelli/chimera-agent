"""Study 24, M6, step 1 (US$ 0) — run in WSL with the harness venv, where the homes live: how often does the tool-loop breaker fire in the stored solves?

Registered in bench/PLAN-study24-jev-practice.md (M6): count stop_reason "tool_loop"; below 5% of
solves, the question closes. Read-only over every home's traces.jsonl (one row per worker round),
with the solve's oracle outcome joined from the results tree where one exists.
"""

from __future__ import annotations

import contextlib
import glob
import json
from collections import Counter, defaultdict
from pathlib import Path

HOMES = Path.home() / "hb-homes"
RESULTS = Path.home() / "harness-bench" / "data_try6" / "results"


def outcome(hid: str, task: str) -> float | None:
    hits = glob.glob(str(RESULTS / hid / "*" / f"{task}.json"))
    if not hits:
        return None
    v = (json.loads(Path(hits[0]).read_text(encoding="utf-8")).get("oracle_result") or {}).get("outcome_score")
    return float(v) if isinstance(v, int | float) else None


def main() -> None:
    reasons: Counter[str] = Counter()
    solves_with: dict[str, set[str]] = defaultdict(set)
    per_solve_outcome: dict[str, float | None] = {}
    solves = 0
    for home in sorted(HOMES.iterdir()):
        trace = home / "traces.jsonl"
        if not trace.is_file():
            continue
        solves += 1
        name = home.name
        # "<task>-<hid>": the hid is the last dash-separated arm id; tasks start with three digits.
        task, parts = "", name.split("-")
        for cut in range(len(parts) - 1, 0, -1):
            candidate_task, candidate_hid = "-".join(parts[:cut]), "-".join(parts[cut:])
            if (RESULTS / candidate_hid).is_dir():
                task, hid = candidate_task, candidate_hid
                break
        else:
            hid = ""
        per_solve_outcome[name] = outcome(hid, task) if hid else None
        for line in trace.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                reason = str(json.loads(line).get("stopped_reason") or "none")
            except ValueError:
                reason = "unparseable_line"  # a row cut mid-write (a killed solve); counted, not hidden
            reasons[reason] += 1
            solves_with[reason].add(name)
    print(f"solves (homes with a trace): {solves}")
    print(f"worker rounds by stop reason: {dict(reasons.most_common())}")
    for reason, names in sorted(solves_with.items(), key=lambda kv: -len(kv[1])):
        scored = [per_solve_outcome[n] for n in names if per_solve_outcome.get(n) is not None]
        mean = sum(scored) / len(scored) if scored else float("nan")
        print(f"  {reason:<16} solves {len(names):>5} ({len(names) / max(solves, 1):.1%})  mean outcome {mean:.3f} over {len(scored)} scored")
    loop = len(solves_with.get("tool_loop", set()))
    rate = loop / max(solves, 1)
    print(f"tool_loop solves: {loop}/{solves} = {rate:.2%} -> {'CLOSE (below 5%)' if rate < 0.05 else 'go to a paired arm'}")


if __name__ == "__main__":
    main()


def strata() -> None:
    """Where the breaker fires: by experiment family and arm, and whether the same task also ends
    normally elsewhere (so the outcome gap is not simply the harder tasks)."""
    import re

    rows = []
    for home in sorted(HOMES.iterdir()):
        trace = home / "traces.jsonl"
        if not trace.is_file():
            continue
        loop = False
        for line in trace.read_text(encoding="utf-8").splitlines():
            with contextlib.suppress(ValueError):  # a row cut mid-write; counted in main()
                loop = loop or json.loads(line).get("stopped_reason") == "tool_loop"
        m = re.match(r"(\d{3}-[a-z0-9-]+?)-(arm-\d{3}-r\d|sys1h?-[a-z0-9-]+)$", home.name)
        family = "factorial" if m and m.group(2).startswith("arm-") else ("b4b" if "sys1h-" in home.name else "b4" if "sys1-" in home.name else "other")
        arm = m.group(2) if m else home.name
        rows.append((family, arm, m.group(1) if m else "?", loop))
    by: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for family, arm, _task, loop in rows:
        key = (family, re.sub(r"-r\d$", "", arm) if family != "factorial" else "all")
        by[key][0] += int(loop)
        by[key][1] += 1
    for (family, arm), (k, n) in sorted(by.items()):
        print(f"  {family:<9} {arm:<22} tool_loop {k:>3}/{n:<4} = {k / n:.1%}")
    tasks = Counter(task for _f, _a, task, loop in rows if loop)
    print("  tasks where it fires most:", tasks.most_common(6))


strata()
