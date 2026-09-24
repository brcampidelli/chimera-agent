"""M6 — did any solve reach into another solve's workspace? Read-only.

The registered pre-read check (PREREGISTRATION "Before the score is read"), which is B4b's
`bench/tool_router_hint/audit_contamination.py` with the M6 hid pattern in place of `sys1h`.

For every m6 home: the solve's own workspace id is read off the home's name; any OTHER workspace
id in its steps, or a path into another home, the results tree or the sandbox root, is a candidate
cross-run read. Traces truncate arguments, so this can miss; what it finds is real.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HOMES = Path.home() / "hb-homes"
HID = r"m6-(?:stop|esc)-(?:strong|weak)-r\d"
WS = re.compile(r"\d{3}-[a-z0-9-]+?-" + HID + r"-\d{8}-\d{6}-[0-9a-f]{8}")
OTHER = re.compile(r"hb-homes/|data_try6/results|b4b-quarantine")
SANDBOX_HID = re.compile(r"data_try6/sandbox/?(" + HID + r")?")


def strings(node: object) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for v in node.values() for s in strings(v)]
    if isinstance(node, list):
        return [s for v in node for s in strings(v)]
    return []


def main() -> None:
    homes = sorted(h for h in HOMES.iterdir() if "-m6-" in h.name and (h / "traces.jsonl").is_file())
    flagged, scanned = [], 0
    for home in homes:
        prefix = home.name + "-"
        own_hid = "m6-" + home.name.split("-m6-")[1]
        for line in (home / "traces.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            scanned += 1
            for s in strings(row.get("steps", [])):
                foreign = {w for w in WS.findall(s) if not w.startswith(prefix)}
                other = OTHER.search(s)
                sandbox_foreign = [m.group(0) for m in SANDBOX_HID.finditer(s) if m.group(1) != own_hid]
                if foreign or other or sandbox_foreign:
                    tag = other.group(0) if other else (sandbox_foreign[0] if sandbox_foreign else "")
                    flagged.append((home.name, sorted(foreign)[:2], tag, s[:160]))
    print(f"homes {len(homes)} · traces scanned {scanned}")
    print(f"flagged steps: {len(flagged)}")
    for name, foreign, other, snippet in flagged[:40]:
        print(f"- {name}: foreign={foreign} other={other!r} :: {snippet!r}")
    sys.exit(0)


if __name__ == "__main__":
    main()
