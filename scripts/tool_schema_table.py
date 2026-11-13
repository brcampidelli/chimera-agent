"""Write, or check, `docs/tool-schema-by-surface.md`: the tool schema each surface sends the model.

    python scripts/tool_schema_table.py           # rewrite the file
    python scripts/tool_schema_table.py --check   # exit 1 if the file is stale

The numbers come from assembling each surface with the functions it calls in production
(`chimera/prompts/tool_schemas.py`), so they move when a tool description or parameter does. The test
`tests/test_the_tool_schema_by_surface.py` runs the same check.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail instead of rewriting")
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT))
    from chimera.prompts.tool_schemas import TABLE_PATH, render_table

    wanted = render_table()
    current = TABLE_PATH.read_text(encoding="utf-8") if TABLE_PATH.exists() else None
    if current == wanted:
        print(f"{TABLE_PATH.name} is current")
        return 0
    if args.check:
        print(f"{TABLE_PATH.name} is stale: run python scripts/tool_schema_table.py")
        return 1
    TABLE_PATH.parent.mkdir(parents=True, exist_ok=True)
    TABLE_PATH.write_text(wanted, encoding="utf-8", newline="\n")
    print(f"wrote {TABLE_PATH.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
