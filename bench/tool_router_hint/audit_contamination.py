"""Study 24 M1 — did any B4b solve reach into another solve's workspace? Read-only.

For every sys1h home: the solve's own workspace id is the one its task text names; any OTHER
workspace id in its steps, or a path into another home, the results tree or the sandbox root, is a
candidate cross-run read. Traces truncate arguments, so this can miss; what it finds is real.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

HOMES = Path.home() / "hb-homes"
WS = re.compile(r"\d{3}-[a-z0-9-]+?-sys1h-(?:on|off)-(?:sol|strong|weak)-r\d-\d{8}-\d{6}-[0-9a-f]{8}")
OTHER = re.compile(r"hb-homes/|data_try6/results|b4b-quarantine")
SANDBOX_HID = re.compile(r"data_try6/sandbox/?(sys1h-(?:on|off)-(?:sol|strong|weak)-r\d)?")


def strings(node: object) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        return [s for v in node.values() for s in strings(v)]
    if isinstance(node, list):
        return [s for v in node for s in strings(v)]
    return []


def main() -> None:
    homes = sorted(h for h in HOMES.iterdir() if "-sys1h-" in h.name and (h / "traces.jsonl").is_file())
    flagged, scanned, no_own = [], 0, 0
    for home in homes:
        for line in (home / "traces.jsonl").read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            scanned += 1
            # The task text is elided in the trace, so the solve's own workspace is read off the home's
            # name: every workspace id of this solve starts with "<task>-<hid>-".
            prefix = home.name + "-"
            own_hid = home.name.split("-sys1h-")[1]
            own_hid = "sys1h-" + own_hid
            for s in strings(row.get("steps", [])):
                foreign = {w for w in WS.findall(s) if not w.startswith(prefix)}
                other = OTHER.search(s)
                sandbox_foreign = [m.group(0) for m in SANDBOX_HID.finditer(s) if m.group(1) != own_hid]
                if foreign or other or sandbox_foreign:
                    tag = other.group(0) if other else (sandbox_foreign[0] if sandbox_foreign else "")
                    flagged.append((home.name, sorted(foreign)[:2], tag, s[:160]))
    print(f"homes {len(homes)} · traces scanned {scanned} · traces with no own workspace id {no_own}")
    print(f"flagged steps: {len(flagged)}")
    for name, foreign, other, snippet in flagged[:40]:
        print(f"- {name}: foreign={foreign} other={other!r} :: {snippet!r}")
    sys.exit(0)


if __name__ == "__main__":
    main()
