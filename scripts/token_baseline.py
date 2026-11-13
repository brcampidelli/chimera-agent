#!/usr/bin/env python3
"""Read-only baseline of tokens and cost from the local logs (P0-18). Thin shell over
``chimera.eval.token_baseline``; the logic and its tests live there.

Usage: python scripts/token_baseline.py [--home DIR] [--since-days 7] [--json]

Opens files for reading only. Prints aggregates only: no key, prompt text, path or session id.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from chimera.eval.token_baseline import build_report, discover_home, now_utc, render_text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=None, help="Chimera home (default: the desktop's)")
    parser.add_argument("--since-days", type=int, default=7)
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    parser.add_argument("--now", default=None, help="window end, ISO-8601 with zone (default: now, UTC)")
    args = parser.parse_args(argv)

    home = args.home or discover_home(os.environ, Path.home())
    if home is None or not home.exists():
        print("home not found: pass --home", file=sys.stderr)
        return 2
    now = datetime.fromisoformat(args.now) if args.now else now_utc()
    if now.tzinfo is None:
        print("--now needs an explicit zone (e.g. +00:00)", file=sys.stderr)
        return 2
    report = build_report(home, now=now, since_days=args.since_days)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
