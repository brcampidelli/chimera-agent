"""Does the harm check (M2) still describe the module after walls v2? A US$ 0 replay of its site.

    python -m bench.browser_situation.check_walls_v2

Walls v2 changes three things in the ON arm and nothing in the OFF arm: a bounded wait for ``load``
after an action that can load a document, a read of the driver's frame tree, and a new ending for
`chimera solve`. The system prompt is unchanged (its snapshot test pins it), and so is every
observation on a page with no wall. So what the model saw in M2 would change only where v2 finds a
wall v1 did not. This replays every page the M2 ON arm requested, plus every authored page of the
site, through the v2 ON tool and the OFF tool on the same Chromium: M2 stays valid if v2 hands over
on none of them and every observation matches byte for byte. See PREREGISTRATION-walls-v2.md.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

HERE = Path(__file__).resolve().parent
SOLVES = HERE / "results" / "solves"
MANIFEST = ROOT / "bench" / "browser_viewport_tasks" / "pages" / "MANIFEST.json"


def paths_to_replay() -> list[str]:
    """Every path the 48 ON solves requested, and every authored page, once each, in order."""
    seen: dict[str, None] = {}
    for record in sorted(SOLVES.glob("*__on__*.json")):
        for path in json.loads(record.read_text(encoding="utf-8")).get("requested_paths", []):
            seen.setdefault(path, None)
    for page in json.loads(MANIFEST.read_text(encoding="utf-8")):
        seen.setdefault("/" + page, None)
    return [p for p in seen if p.endswith(".html") or p.startswith("/go/")]


def main() -> int:
    from bench.browser_viewport_tasks.harness import only_this_origin
    from bench.browser_viewport_tasks.server import SiteServer
    from chimera.tools.browser import BrowserTool
    from chimera.tools.browser_playwright import PlaywrightDriver
    from chimera.tools.browser_situation import BrowserSituation

    paths = paths_to_replay()
    rows: list[dict[str, Any]] = []
    with SiteServer() as origin:
        driver = PlaywrightDriver(
            headless=True, allowed=lambda u: u == origin or u.startswith(origin + "/")
        )
        try:
            with only_this_origin(origin):
                off = BrowserTool(driver=driver)
                for path in paths:
                    url = origin + path
                    seen_off = off.run(action="navigate", url=url)
                    situation = BrowserSituation()
                    on = BrowserTool(driver=driver, situation=situation)
                    started = time.monotonic()
                    seen_on = on.run(action="navigate", url=url)
                    wall = situation.take_handover()
                    rows.append({
                        "path": path,
                        "same": seen_on == seen_off,
                        "handover": wall.describe() if wall is not None else "",
                        "on_seconds": round(time.monotonic() - started, 3),
                        "error": seen_off.startswith("error:") or seen_on.startswith("error:"),
                    })
        finally:
            driver.close()
    summary = {
        "pages": len(rows),
        "handovers": [r for r in rows if r["handover"]],
        "different_observations": [r["path"] for r in rows if not r["same"]],
        "errors": [r["path"] for r in rows if r["error"]],
        "on_seconds_max": max((r["on_seconds"] for r in rows), default=0.0),
    }
    summary["m2_still_valid"] = not (
        summary["handovers"] or summary["different_observations"] or summary["errors"]
    )
    out = HERE / "results" / "walls_v2_check.json"
    out.write_text(json.dumps({"summary": summary, "rows": rows}, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0 if summary["m2_still_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
