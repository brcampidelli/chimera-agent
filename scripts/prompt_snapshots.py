"""Check or rewrite `tests/prompt_snapshots/`, the byte-exact copy of every registered prompt.

    python scripts/prompt_snapshots.py           # exit 1 and list what drifted
    python scripts/prompt_snapshots.py --write   # rewrite after an intended edit

The test `tests/test_every_prompt_is_registered.py` does the same check. The script exists so the
rewrite is one command, and so the diff it leaves is what a reviewer reads.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOTS = ROOT / "tests" / "prompt_snapshots"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--write", action="store_true", help="rewrite the snapshot files")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    from chimera.prompts.snapshots import render_all

    wanted = render_all()
    drift: list[str] = []
    for name, text in sorted(wanted.items()):
        path = SNAPSHOTS / f"{name}.txt"
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current != text:
            drift.append(name)
            if args.write:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8", newline="\n")
    orphans = sorted(p.stem for p in SNAPSHOTS.glob("*.txt") if p.stem not in wanted)
    for name in orphans:
        drift.append(f"{name} (orphan)")
        if args.write:
            (SNAPSHOTS / f"{name}.txt").unlink()
    if not drift:
        print(f"{len(wanted)} snapshots, all current")
        return 0
    verb = "rewrote" if args.write else "drifted"
    print(f"{verb} {len(drift)}:\n  " + "\n  ".join(drift))
    return 0 if args.write else 1


if __name__ == "__main__":
    raise SystemExit(main())
