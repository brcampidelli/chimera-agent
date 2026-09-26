"""The browser situation's wall detector on live pages. See PREREGISTRATION-situation.md.

    python -m bench.browser_element_list.run_situation

US$ 0, no model. One `navigate` per page through `BrowserTool(situation=BrowserSituation())` on the
product's `PlaywrightDriver` (SSRF guard on), a fresh driver per page so one site's cookies or bot
check cannot follow the run to the next. The label comes from the page's title and visible text, which
the detector never reads, so agreement between the two is not by construction.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from bench.browser_element_list.run import PAGES  # noqa: E402
from chimera.tools.browser import BrowserTool  # noqa: E402
from chimera.tools.browser_playwright import PlaywrightDriver  # noqa: E402
from chimera.tools.browser_situation import BrowserSituation  # noqa: E402

#: Registered in PREREGISTRATION-situation.md: public pages that are walls by construction.
CONTROLS: dict[str, str] = {
    "https://github.com/login": "login",
    "https://gitlab.com/users/sign_in": "login",
    "https://accounts.google.com/": "login",
    "https://www.google.com/recaptcha/api2/demo": "captcha",
    "https://accounts.hcaptcha.com/demo": "captcha",
    "https://demo.turnstile.workers.dev/": "captcha",
    "https://checkout.stripe.dev/preview": "payment",
    "https://stripe-payments-demo.appspot.com/": "payment",
}

CHALLENGE_TITLE = re.compile(
    r"just a moment|attention required|verify you are human|access denied|are you a robot|captcha"
    r"|um momento",
    re.IGNORECASE,
)
CHALLENGE_TEXT = re.compile(
    r"verify you are human|checking your browser|enable javascript and cookies to continue"
    r"|press and hold|press & hold|are you a robot|confirm you are human",
    re.IGNORECASE,
)


def label(title: str, text: str) -> str:
    """The independent label: a bot check by its title or visible text, else an ordinary page."""
    return "captcha" if CHALLENGE_TITLE.search(title) or CHALLENGE_TEXT.search(text[:5000]) else "none"


def measure(url: str, expected: str | None) -> dict[str, Any]:
    row: dict[str, Any] = {"url": url, "control": expected is not None}
    driver = None
    try:
        driver = PlaywrightDriver(headless=True)
        situation = BrowserSituation()
        tool = BrowserTool(driver=driver, situation=situation)
        out = tool.run(action="navigate", url=url)
        if out.startswith("error:"):
            row["error"] = out[:200]
            return row
        wall = situation.take_handover()
        title = str(driver._page.title())
        text = driver.page_text()
        row.update({
            "final_url": driver.url,
            "title": title[:120],
            "elements": len(driver.read()),
            "expected": expected if expected is not None else label(title, text),
            "handover": wall.kind if wall is not None else "none",
            "evidence": wall.evidence if wall is not None else "",
        })
    except Exception as exc:  # noqa: BLE001 — a page that fails is reported, not replaced
        row["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    finally:
        if driver is not None:
            driver.close()
    return row


def main() -> None:
    out_dir = Path(__file__).resolve().parent / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = [measure(url, None) for url in PAGES] + [measure(url, kind) for url, kind in CONTROLS.items()]
    with (out_dir / "situation.jsonl").open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    for row in rows:
        if "error" in row:
            print(f"  ERROR    {row['url']}: {row['error']}")
            continue
        mark = "ok " if row["handover"] == row["expected"] else "XX "
        print(f"  {mark} expected={row['expected']:<8} handover={row['handover']:<10} "
              f"{row['elements']:>5} el  {row['url']}  [{row['evidence']}]  «{row['title'][:40]}»")
    loaded = [r for r in rows if "error" not in r]
    ordinary = [r for r in loaded if r["expected"] == "none"]
    walls = [r for r in loaded if r["expected"] != "none"]
    false_handovers = [r for r in ordinary if r["handover"] != "none"]
    found = [r for r in walls if r["handover"] == r["expected"]]
    need = [r for r in walls if r["expected"] in ("login", "captcha")]
    summary = {
        "pages": len(rows),
        "loaded": len(loaded),
        "ordinary": len(ordinary),
        "false_handovers": [(r["url"], r["handover"], r["evidence"]) for r in false_handovers],
        "walls": len(walls),
        "walls_found_with_kind": len(found),
        "missed": [(r["url"], r["expected"], r["handover"]) for r in walls if r not in found],
        "fit": len(false_handovers) <= 1 and all(r in found for r in need),
    }
    (out_dir / "situation_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
