"""POST-HOC, not registered, descriptive only.

Lists the legacy stops that no rule reproduces whose tail is reads or mixed, plus the two F
disagreements. Uses the registered replay's own functions. Run in WSL from the repo root:
``python3 bench/tool_loop_near_args/posthoc_neither.py``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay as r  # noqa: E402

WANT = {"016-code-repair-pytest-sys1-off-weak-r0", "016-code-repair-pytest-sys1h-off-weak-r2"}
for home in sorted(p for p in r.HOMES.iterdir() if p.is_dir()):
    trace = home / "traces.jsonl"
    if not trace.is_file():
        continue
    fam, task, hid = r.family_of(home.name)
    esc = fam.startswith(r.ESCALATING)
    for line in trace.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        ts = str(row.get("ts") or "")
        if fam.startswith("brk-fixed") or (fam == "other" and ts[:10] >= r.FIX_DAY):
            continue
        calls, switch = r.calls_of(row, esc)
        rec = r.recorded_trip(row, calls, switch, esc)
        fires = {n: r.first_fire(m, calls) for n, m in r.DETECTORS.items()}
        step = {n: (calls[k][0] if k is not None else None) for n, k in fires.items()}
        k = fires["legacy"]
        if home.name in WANT:
            print(f"## F-disagreement {home.name} recorded={rec} legacy@{step['legacy']} fixed@{step['fixed']} "
                  f"new@{step['new']} stopped={row.get('stopped_reason')}")
            for c in calls[max(0, (k or 0) - 5): (k or 0) + 2]:
                print(f"    s{c[0]} {c[1]} {json.dumps(c[2], sort_keys=True)[:70]} ok={c[4]} -> {c[3][:60]!r}")
        if k is None or step["legacy"] != rec:
            continue
        if step["fixed"] == step["legacy"] or step["new"] == step["legacy"]:
            continue
        kind = r.tail_kind(calls, k)
        if kind.startswith("writes"):
            continue
        print(f"## {fam if fam != 'other' else 'other'} {home.name} [{kind}]")
        for c in calls[max(0, k - 3): k + 1]:
            print(f"    s{c[0]} {c[1]} {json.dumps(c[2], sort_keys=True)[:80]} ok={c[4]} -> {c[3][:70]!r}")
