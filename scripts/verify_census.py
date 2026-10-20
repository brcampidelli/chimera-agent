#!/usr/bin/env python3
"""Read-only census of verification outcomes in persisted Code-session receipts.

Usage: python scripts/verify_census.py [CHIMERA_HOME/code_sessions]

The default mirrors Settings.home/code_sessions: .chimera/code_sessions when the
legacy project directory exists (or the home directory is unavailable), otherwise
~/.chimera/code_sessions. No message text is loaded into the report.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

STATES = ("passed", "failed", "abstained", "none", "missing", "unknown")
REASONS = ("inferred", "no sandbox", "host-exec declined", "no command", "other")


def default_sessions_dir() -> Path:
    """Resolve the same default home choice as chimera.config._default_home."""
    configured = os.environ.get("CHIMERA_HOME")
    if configured:
        return Path(configured).expanduser() / "code_sessions"
    legacy = Path(".chimera")
    try:
        user_home = Path.home()
    except RuntimeError:
        return legacy / "code_sessions"
    home = legacy if legacy.is_dir() else user_home / ".chimera"
    return home / "code_sessions"


def _reason(verified: dict[str, Any] | None) -> str | None:
    if verified is None:
        return None
    if verified.get("state") == "none" or verified.get("command") is None:
        return "no command"
    output = verified.get("output")
    source = verified.get("source")
    text = output.casefold() if isinstance(output, str) else ""
    # Prefer the recorded abstention cause over its provenance: a command inferred from
    # a project file can still specifically abstain because the host-exec gate refused it.
    if "host-exec" in text or "host exec" in text or "declined to run" in text:
        return "host-exec declined"
    if "network" in text and ("sandbox" in text or "cannot grant" in text):
        return "no sandbox"
    if isinstance(source, str) and source.startswith("inferred"):
        return "inferred"
    return "other"


def census(root: Path) -> dict[str, Any]:
    """Count receipts and classify absent/invalid verification data without exposing content."""
    states: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    total = 0
    unreadable = 0
    if not root.is_dir():
        files: list[Path] = []
    else:
        files = sorted(root.glob("*.json"))
    for path in files:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            unreadable += 1
            continue
        if not isinstance(document, dict) or not isinstance(document.get("receipts", []), list):
            unreadable += 1
            continue
        receipts = document.get("receipts", [])
        for receipt in receipts:
            if not isinstance(receipt, dict):
                continue
            total += 1
            value = receipt.get("verified")
            if not isinstance(value, dict):
                state = "missing"
                verified = None
            else:
                raw_state = value.get("state")
                state = raw_state if isinstance(raw_state, str) and raw_state in STATES else "unknown"
                verified = value
            states[state] += 1
            reason = _reason(verified) if state in {"abstained", "none"} else None
            if reason is not None:
                reasons[reason] += 1
    return {
        "sessions_dir": str(root),
        "turns": total,
        "states": {state: states[state] for state in STATES},
        "abstention_reasons": {reason: reasons[reason] for reason in REASONS},
        "unreadable_session_files": unreadable,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_dir", nargs="?", type=Path,
                        help="Code-session directory, or CHIMERA_HOME (which contains code_sessions)")
    args = parser.parse_args(argv)
    root = args.data_dir if args.data_dir is not None else default_sessions_dir()
    if root.name != "code_sessions":
        root = root / "code_sessions"
    report = census(root)
    print("Code verification census")
    print(f"Directory: {report['sessions_dir']}")
    print(f"Code turns (receipts): {report['turns']}")
    print("State             Count")
    for state, count in report["states"].items():
        print(f"{state:<17} {count}")
    print("Abstention reason  Count")
    for reason, count in report["abstention_reasons"].items():
        print(f"{reason:<17} {count}")
    print(f"Unreadable session files: {report['unreadable_session_files']}")
    print("JSON:")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
