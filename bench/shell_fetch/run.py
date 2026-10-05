"""S30-28, per PREREGISTRATION.md. Deterministic, no model, US$ 0.

    uv run python bench/shell_fetch/run.py [--homes ~/hb-homes] [--out bench/shell_fetch/results]

Clone rows: a fresh `TaintLedger` told the row's instruction, and `assess_action("run_shell", ...)`
with `shell_fetch_guard` off (the control) and on. Shell rows: the command recorded through
`record_exec` with a fixed output, and whether the run is tainted afterwards. The trace replay runs
both over every exec-tool call of the stored runs.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

from chimera.governance.ledger import EXEC_TOOLS, TaintLedger, assess_action  # noqa: E402
from chimera.governance.policy import Decision  # noqa: E402

_COMMAND_KEYS = ("command", "cmd", "code", "script")


def clone_verdict(instruction: str, command: str, *, on: bool) -> Decision:
    ledger = TaintLedger(exfil_host_path=False, shell_fetch_guard=on)
    ledger.set_instruction(instruction)
    return assess_action("run_shell", {"command": command}, ledger).decision


def tainted_after(command: str, *, on: bool) -> bool:
    ledger = TaintLedger(exfil_host_path=False, shell_fetch_guard=on)
    ledger.set_instruction("Work on the repository.")
    ledger.record_exec(command, output="README: welcome.")
    return ledger.run_tainted()


def replay(homes: Path) -> dict[str, Any]:
    calls = asked = tainting = runs = 0
    examples: list[str] = []
    for path in sorted(glob.glob(str(homes / "*" / "traces.jsonl"))):
        for line in open(path, encoding="utf-8", errors="replace"):
            try:
                row = json.loads(line)
            except ValueError:
                continue
            runs += 1
            task = str(row.get("task") or "")
            for step in row.get("steps") or []:
                for tool in step.get("tools") or []:
                    name = str(tool.get("name") or "")
                    if name not in EXEC_TOOLS:
                        continue
                    raw = tool.get("arguments")
                    try:
                        args = json.loads(raw) if isinstance(raw, str) else raw
                    except ValueError:
                        args = {"command": str(raw)}
                    args = args if isinstance(args, dict) else {"command": str(args)}
                    command = next((str(args[k]) for k in _COMMAND_KEYS if args.get(k)), "")
                    calls += 1
                    if clone_verdict(task, command, on=True) is Decision.REVIEW:
                        asked += 1
                        examples.append(command[:160])
                    if tainted_after(command, on=True):
                        tainting += 1
                        examples.append(command[:160])
    return {"runs": runs, "exec_calls": calls, "clone_asked": asked, "shell_fetch_tainted": tainting,
            "examples": examples[:20]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--homes", default=os.path.expanduser("~/hb-homes"))
    parser.add_argument("--out", default=str(HERE / "results"))
    a = parser.parse_args()
    corpus = json.loads((HERE / "corpus.json").read_text(encoding="utf-8"))
    out: dict[str, Any] = {"clone": [], "shell": []}
    lines = ["# bench/shell_fetch — readings", "", "## Clone: verdict off -> on", ""]
    clone_tally: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for row in corpus["clone"]:
        off = clone_verdict(row["instruction"], row["command"], on=False)
        on = clone_verdict(row["instruction"], row["command"], on=True)
        out["clone"].append({"id": row["id"], "class": row["class"], "off": off.value, "on": on.value})
        clone_tally[row["class"]][0] += 1
        clone_tally[row["class"]][1] += on is Decision.REVIEW
        lines.append(f"{row['id']:24} {row['class']:8} {off.value:6}-> {on.value}")
    lines.append("")
    for cls, (n, rev) in clone_tally.items():
        lines.append(f"clone {cls:8} asked {rev}/{n}")
    lines += ["", "## Shell: tainted after the command, off -> on", ""]
    shell_tally: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for row in corpus["shell"]:
        off = tainted_after(row["command"], on=False)
        on = tainted_after(row["command"], on=True)
        out["shell"].append({"id": row["id"], "class": row["class"], "off": off, "on": on})
        shell_tally[row["class"]][0] += 1
        shell_tally[row["class"]][1] += on
        lines.append(f"{row['id']:16} {row['class']:9} {off!s:5}-> {on}")
    lines.append("")
    for cls, (n, t) in shell_tally.items():
        lines.append(f"shell {cls:9} tainted {t}/{n}")
    homes = Path(a.homes)
    out["replay"] = replay(homes) if homes.is_dir() else {"skipped": f"{homes} not found"}
    lines += ["", f"replay: {json.dumps({k: v for k, v in out['replay'].items() if k != 'examples'})}"]
    for example in out["replay"].get("examples", []):
        lines.append(f"  fired on: {example}")
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8", newline="\n")
    (out_dir / "readings.txt").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
