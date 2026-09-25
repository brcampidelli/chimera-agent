"""Report how the registered prompts stand against study 25's composition rules. Never fails.

    python scripts/prompt_lint.py          # grouped by rule
    python scripts/prompt_lint.py --json   # one object per finding, for a diff between versions

See :mod:`chimera.prompts.lint` for the rules and why this reports rather than blocks.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="print the findings as JSON lines")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    from chimera.prompts.lint import lint_registry

    report = lint_registry()
    if args.json:
        for f in report.findings:
            print(json.dumps({"rule": f.rule, "section": f.section, "detail": f.detail}, ensure_ascii=False))
        return 0
    rules: dict[str, list[str]] = {}
    for f in report.findings:
        rules.setdefault(f.rule, []).append(f"{f.section}: {f.detail}")
    for rule in sorted(rules):
        print(f"\n== {rule} ({len(rules[rule])})")
        for line in rules[rule]:
            print(f"  {line}")
    print("\n== fence syntaxes in registered text")
    for marker, sections in sorted(report.fences.items()):
        print(f"  <<{marker}: {', '.join(sections)}")
    print("\n== fence syntaxes anywhere in the package")
    for marker, files in sorted(report.source_fences.items()):
        print(f"  <<{marker}: {', '.join(files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
