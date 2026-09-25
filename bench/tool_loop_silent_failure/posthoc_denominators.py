"""POST-HOC, descriptive: per-tool denominators for the "how much it acts" line of the registered replay.

The registration predicted the share of run_shell / execute_code / code_interpreter calls read as an
answered failure; the registered script counted the numerator only. Same traces, same helpers.
Run in WSL from the repo root: ``python3 bench/tool_loop_silent_failure/posthoc_denominators.py``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

# Both benches name their script `replay`; this one is loaded under another name so the near-args
# `replay` it imports is not mistaken for it.
_spec = importlib.util.spec_from_file_location("silent_replay", Path(__file__).resolve().parent / "replay.py")
assert _spec is not None and _spec.loader is not None
r = importlib.util.module_from_spec(_spec)
sys.modules["silent_replay"] = r
_spec.loader.exec_module(r)

TOOLS = ("run_shell", "execute_code", "code_interpreter")
answered = r.DETECTORS["new"]._answered_failure
total: Counter[str] = Counter()
read: Counter[str] = Counter()
silent: Counter[str] = Counter()
for home in sorted(p for p in r.base.HOMES.iterdir() if p.is_dir()):
    trace = home / "traces.jsonl"
    if not trace.is_file():
        continue
    fam, _, _ = r.base.family_of(home.name)
    for line in trace.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if fam == "other" and str(row.get("ts") or "")[:10] >= r.base.FIX_DAY:
            continue
        calls, _ = r.base.calls_of(row, fam.startswith(r.base.ESCALATING))
        for _, name, _, obs, ok in calls:
            if name not in TOOLS or not ok:
                continue
            total[name] += 1
            if answered(obs):
                read[name] += 1
            elif obs.strip().startswith("[exit ") and not obs.strip().startswith("[exit 0]"):
                silent[name] += 1
for name in TOOLS:
    n = total[name]
    print(f"{name:18s} ok calls {n:6d}  read as failure {read[name]:5d} ({read[name] / n:.1%})  "
          f"non-zero exit with no text, not counted {silent[name]:4d}")
n_all, r_all = sum(total.values()), sum(read.values())
print(f"{'all three':18s} ok calls {n_all:6d}  read as failure {r_all:5d} ({r_all / n_all:.1%})")
