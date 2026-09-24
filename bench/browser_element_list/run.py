"""M7 — the size and content of the element list the browser hands the model. See PREREGISTRATION.md.

    python -m bench.browser_element_list.run

US$ 0, no model. The product's `PlaywrightDriver` (SSRF guard on), one read per page after
`domcontentloaded`; the listed elements are then classified in-page by the same `data-chimera-ref` stamp.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from chimera.tools.browser import render_elements  # noqa: E402
from chimera.tools.browser_playwright import PlaywrightDriver  # noqa: E402

PAGES = [
    "https://docs.python.org/3/library/json.html",
    "https://en.wikipedia.org/wiki/Large_language_model",
    "https://github.com/brcampidelli/chimera-agent",
    "https://developer.mozilla.org/en-US/docs/Web/HTML/Element/input",
    "https://news.ycombinator.com/",
    "https://www.bbc.com/news",
    "https://pypi.org/project/httpx/",
    "https://stackoverflow.com/questions/231767/what-does-the-yield-keyword-do-in-python",
    "https://www.python.org/",
    "https://react.dev/learn",
    "https://fastapi.tiangolo.com/",
    "https://www.gov.br/pt-br",
    "https://g1.globo.com/",
    "https://www.mercadolivre.com.br/",
    "https://docs.github.com/en/actions",
    "https://arxiv.org/abs/1706.03762",
    "https://www.reddit.com/r/Python/",
    "https://huggingface.co/models",
    "https://www.nytimes.com/",
    "https://tauri.app/",
]

CLASSIFY = r"""
() => {
  const vw = window.innerWidth, vh = window.innerHeight;
  let hidden = 0, disabled = 0, offscreen = 0, total = 0;
  for (const el of document.querySelectorAll('[data-chimera-ref]')) {
    total++;
    const style = getComputedStyle(el);
    if (style.visibility === 'hidden' || el.closest('[aria-hidden="true"]')) hidden++;
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') disabled++;
    const r = el.getBoundingClientRect();
    if (r.bottom < 0 || r.right < 0 || r.top > vh || r.left > vw) offscreen++;
  }
  return {total, hidden, disabled, offscreen};
}
"""


def measure(driver: PlaywrightDriver, url: str) -> dict[str, Any]:
    try:
        elements = driver.navigate(url)
        classes = driver._page.evaluate(CLASSIFY)
        text = driver.page_text()
    except Exception as exc:  # noqa: BLE001 — a page that fails is reported, not replaced
        return {"url": url, "error": f"{type(exc).__name__}: {str(exc)[:120]}"}
    return {
        "url": url,
        "elements": len(elements),
        "rendered_chars": len(render_elements(elements)),
        "text_chars": len(text),
        **{k: classes[k] for k in ("hidden", "disabled", "offscreen")},
        "stamped": classes["total"],
    }


def pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def main() -> None:
    driver = PlaywrightDriver(headless=True)
    try:
        rows = [measure(driver, url) for url in PAGES]
    finally:
        driver.close()
    ok = [r for r in rows if "error" not in r]
    for r in rows:
        if "error" in r:
            print(f"FAILED {r['url']}: {r['error']}")
        else:
            print(f"{r['elements']:>5} elements {r['rendered_chars']:>7} chars  hidden {r['hidden']:>3}  disabled {r['disabled']:>3}"
                  f"  offscreen {r['offscreen']:>4}  text {r['text_chars']:>7}  {r['url']}")
    counts = [r["elements"] for r in ok]
    hidden_share = [(r["hidden"] + r["disabled"]) / r["elements"] for r in ok if r["elements"]]
    summary = {
        "pages": len(PAGES), "measured": len(ok), "failed": len(rows) - len(ok),
        "elements_median": statistics.median(counts), "elements_p90": pct(counts, 0.9),
        "rendered_chars_median": statistics.median(r["rendered_chars"] for r in ok),
        "hidden_or_disabled_share_median": statistics.median(hidden_share),
        "offscreen_share_median": statistics.median(r["offscreen"] / r["elements"] for r in ok if r["elements"]),
    }
    trigger = summary["elements_median"] > 150 or summary["elements_p90"] > 400 or summary["hidden_or_disabled_share_median"] > 0.10
    summary["decision"] = "propose a cap/filter in its own PR" if trigger else "close: the list is not the problem"
    print(json.dumps(summary, indent=2))
    out = Path(__file__).resolve().parent / "results"
    out.mkdir(parents=True, exist_ok=True)
    (out / "pages.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
