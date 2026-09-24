"""Study 24, M9, step 1 (US$ 0) — run in WSL with the harness venv, where the homes live: how often do scrape / browser read_text hit the 20k-character cut?

Registered in bench/PLAN-study24-jev-practice.md (M9): count how often the cut is hit at all before
building goal-chosen chunking. Read-only over every stored trace: tool calls named scrape / browser /
crawl / extract, and results carrying the truncation marker both tools write.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

HOMES = Path.home() / "hb-homes"
TOOLS = ("scrape", "browser", "crawl", "extract", "http_get", "web_search")
MARK = "[truncated,"


def walk(node: object, out: list[dict]) -> None:
    if isinstance(node, dict):
        out.append(node)
        for v in node.values():
            walk(v, out)
    elif isinstance(node, list):
        for v in node:
            walk(v, out)


def main() -> None:
    calls: Counter[str] = Counter()
    cut: Counter[str] = Counter()
    solves_using = set()
    traces = 0
    for home in sorted(HOMES.iterdir()):
        trace = home / "traces.jsonl"
        if not trace.is_file():
            continue
        for line in trace.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            traces += 1
            for step in row.get("steps") or []:
                nodes: list[dict] = []
                walk(step, nodes)
                for n in nodes:
                    name = str(n.get("name") or n.get("tool") or "")
                    if name in TOOLS:
                        calls[name] += 1
                        solves_using.add(home.name)
                        if MARK in json.dumps(n, ensure_ascii=False):
                            cut[name] += 1
    print(f"traces {traces} · solves using a web tool {len(solves_using)}")
    print(f"web-tool calls: {dict(calls)}")
    print(f"calls whose result carries the truncation marker: {dict(cut)}")


if __name__ == "__main__":
    main()
