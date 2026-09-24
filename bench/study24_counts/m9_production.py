"""Study 24, M9, step 1 in production (US$ 0, read-only): does the 20k-character cut of the web tools
ever fire on the Chimera the owner runs?

Run INSIDE the production container, where CHIMERA_HOME holds the scheduler's step log:

    ssh <vps> 'docker exec -i chimera python3 -' < bench/study24_counts/m9_production.py

It reads only `scheduler/cron_traces.jsonl` and prints counts, sizes and task prefixes; no observation
text leaves the container.

Why this can answer the question (§2q, checked before any count was trusted). The step log clips
every observation to head + tail (`steplog.clip`, 800 characters) and writes `…[N chars elided]…` in
the middle, so the ORIGINAL length is recoverable. The tools write their own cut marker,
`[truncated, N chars total]`, at the END, which is the part the clip keeps. The positive control is
in the same file: `run_shell` has the same 20k cap, and every time it fired, both signals are
visible.
"""

from __future__ import annotations

import collections
import json
import os
import re
from pathlib import Path

HOME = Path(os.environ.get("CHIMERA_HOME", "/data"))
TRACES = HOME / "scheduler" / "cron_traces.jsonl"
WEB = ("http_get", "scrape", "browser", "crawl", "extract", "web_search")
CUT = 20_000
ELIDED = re.compile(r"…\[(\d+) chars elided\]…")
MARK = re.compile(r"\[truncated, \d+ chars total\]\s*$")


def original_length(observation: str) -> int:
    m = ELIDED.search(observation)
    if not m:
        return len(observation)
    return len(observation) - len(m.group(0)) - 2 + int(m.group(1))  # the clip wraps the marker in \n


def main() -> None:
    sizes: dict[str, list[int]] = collections.defaultdict(list)
    marked: collections.Counter[str] = collections.Counter()
    shell_cut_tasks: collections.Counter[str] = collections.Counter()
    runs, first, last = 0, "", ""
    for line in TRACES.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        runs += 1
        first = first or str(row.get("ts", ""))
        last = str(row.get("ts", "")) or last
        for step in row.get("steps") or []:
            for tool in step.get("tools") or []:
                name = str(tool.get("name"))
                obs = str(tool.get("observation") or "")
                sizes[name].append(original_length(obs))
                if MARK.search(obs):
                    marked[name] += 1
                    if name == "run_shell":
                        shell_cut_tasks[str(row.get("task", ""))[:48]] += 1
    calls = sum(len(v) for v in sizes.values())
    print(f"window {first[:10]} -> {last[:10]} · cron runs {runs} · tool calls {calls}")
    for name in (*WEB, "run_shell"):
        v = sorted(sizes.get(name, []))
        if not v:
            print(f"  {name:10s} n 0")
            continue
        print(f"  {name:10s} n {len(v):4d} · original length p50 {v[len(v) // 2]:6d} max {v[-1]:6d} · "
              f"over {CUT}: {sum(x > CUT for x in v)} · cut marker at the tail: {marked[name]}")
    print("positive control, run_shell cuts by task:", shell_cut_tasks.most_common(6))


if __name__ == "__main__":
    main()
