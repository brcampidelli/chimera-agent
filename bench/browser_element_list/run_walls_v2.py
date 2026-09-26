"""Walls v2: the wall detector after its two fixes, on live pages. See PREREGISTRATION-walls-v2.md.

    python -m bench.browser_element_list.run_walls_v2 [--smoke]

US$ 0, no model. One `navigate` per page through `BrowserTool(situation=BrowserSituation())` on the
product's `PlaywrightDriver` (SSRF guard on), a fresh driver per page. The bench subclasses the driver
only to RECORD: the requests the page made (control validity), the page as v1 looked at it (at
domcontentloaded, taken as the settle wait begins), and how long the wait and the navigation took.
Nothing it records feeds the handover, which is the product path's own.

Two sets, reported apart: A is in-sample (the fix was designed on it), B is fresh.
"""

from __future__ import annotations

import json
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]

from bench.browser_element_list.run import PAGES  # noqa: E402
from bench.browser_element_list.run_situation import (  # noqa: E402
    CHALLENGE_TEXT,
    CHALLENGE_TITLE,
    CONTROLS,
)
from chimera.tools.browser import BrowserTool, Element  # noqa: E402
from chimera.tools.browser_playwright import PlaywrightDriver  # noqa: E402
from chimera.tools.browser_situation import (  # noqa: E402
    SETTLE_SECONDS,
    BrowserSituation,
    _drawn_frames,
    detect_wall,
)

#: Set B, registered in PREREGISTRATION-walls-v2.md before any of these pages was loaded.
FRESH_PAGES = [
    "https://www.rust-lang.org/",
    "https://go.dev/doc/effective_go",
    "https://nodejs.org/en/about",
    "https://www.npmjs.com/package/express",
    "https://crates.io/crates/serde",
    "https://docs.djangoproject.com/en/5.1/topics/http/views/",
    "https://kubernetes.io/docs/concepts/overview/",
    "https://www.w3.org/TR/WCAG21/",
    "https://www.theguardian.com/international",
    "https://edition.cnn.com/",
    "https://www.uol.com.br/",
    "https://www.estadao.com.br/",
    "https://www.imdb.com/chart/top/",
    "https://www.youtube.com/",
    "https://stripe.com/pricing",
    "https://www.shopify.com/pricing",
    "https://www.cloudflare.com/plans/",
    "https://www.hubspot.com/products/crm",
    "https://www.digitalocean.com/pricing",
    "https://www.atlassian.com/software/jira",
    "https://www.magazineluiza.com.br/",
    "https://www.kabum.com.br/",
    "https://medium.com/",
    "https://www.booking.com/",
]
FRESH_CONTROLS: dict[str, str] = {
    "https://2captcha.com/demo/hcaptcha": "captcha",
    "https://nopecha.com/demo/hcaptcha": "captcha",
    "https://democaptcha.com/demo-form-eng/hcaptcha.html": "captcha",
    "https://2captcha.com/demo/cloudflare-turnstile": "captcha",
    "https://nopecha.com/demo/turnstile": "captcha",
    "https://2captcha.com/demo/recaptcha-v2": "captcha",
    "https://nopecha.com/demo/recaptcha": "captcha",
    "https://patrickhlauke.github.io/recaptcha/": "captcha",
    "https://news.ycombinator.com/login": "login",
    "https://pypi.org/account/login/": "login",
    "https://www.npmjs.com/login": "login",
    "https://huggingface.co/login": "login",
    "https://login.salesforce.com/": "login",
    "https://id.heroku.com/login": "login",
    "https://discord.com/login": "login",
    "https://account.proton.me/login": "login",
    "https://stripe.github.io/elements-examples/": "payment",
}

#: A control is valid when an independent signal shows the page offered what it is a control for.
#: The detector reads frames, fields and challenge markers; these read requests and visible text.
PROVIDER_HOSTS: dict[str, tuple[str, ...]] = {
    "payment": ("js.stripe.com", "braintreegateway.com", "adyen.com"),
}
CAPTCHA_PATH = re.compile(r"hcaptcha|turnstile|recaptcha", re.IGNORECASE)
LOGIN_TEXT = re.compile(r"password|senha|passwort|mot de passe|contrase", re.IGNORECASE)


class _BenchDriver(PlaywrightDriver):
    """The product's driver plus a record of what happened; every product method is the parent's."""

    def __init__(self) -> None:
        super().__init__(headless=True)
        self.requests: list[str] = []
        self._page.on("request", lambda request: self.requests.append(request.url))
        self.v1_html: str | None = None
        self.settle_s: float | None = None
        self.navigate_s = 0.0
        self.nav_elements = -1

    def navigate(self, url: str) -> list[Element]:
        started = time.monotonic()
        elements = super().navigate(url)
        self.navigate_s = time.monotonic() - started
        self.nav_elements = len(elements)
        return elements

    def settle(self, seconds: float) -> None:
        # v1 looked right after the action returned; this is that moment, before the wait.
        try:
            self.v1_html = self.page_html()
        except Exception:  # noqa: BLE001 — a diagnostic; the product's own look is what counts
            self.v1_html = None
        started = time.monotonic()
        super().settle(seconds)
        self.settle_s = time.monotonic() - started


def label(title: str, text: str, elements_after: int, *, v2: bool) -> str:
    """The independent label. v1: a bot check by its title or visible text, else ordinary. v2 adds
    one outcome, registered before this run: a page with no interactive element even after the
    wait is ``excluded`` — the label cannot tell DataDome's block page from a page that did not
    render, and neither is a page a run could browse."""
    if CHALLENGE_TITLE.search(title) or CHALLENGE_TEXT.search(text[:5000]):
        return "captcha"
    return "excluded" if v2 and elements_after == 0 else "none"


def _is(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def valid_control(kind: str, requests: list[str], text: str, served_bot_check: bool) -> bool:
    """Whether the page offered what it is a control for, by signals the detector does not read."""
    if served_bot_check:
        return True
    if kind == "login":
        return bool(LOGIN_TEXT.search(text[:20000]))
    for request in requests:
        parts = urlsplit(request)
        host = (parts.hostname or "").lower()
        if kind == "payment" and any(_is(host, d) for d in PROVIDER_HOSTS["payment"]):
            return True
        if kind == "captcha":
            # google.com and gstatic.com serve more than reCAPTCHA: a request to them counts only
            # with a captcha name in its path.
            if any(_is(host, d) for d in ("hcaptcha.com", "challenges.cloudflare.com", "recaptcha.net")):
                return True
            if any(_is(host, d) for d in ("google.com", "gstatic.com")) and CAPTCHA_PATH.search(parts.path):
                return True
    return False


def _short(address: str) -> str:
    parts = urlsplit(address)
    return f"{parts.hostname}{parts.path[:50]}"


def measure(url: str, page_set: str, table_kind: str | None) -> dict[str, Any]:
    row: dict[str, Any] = {"set": page_set, "url": url, "control": table_kind is not None}
    driver = None
    try:
        driver = _BenchDriver()
        situation = BrowserSituation()
        tool = BrowserTool(driver=driver, situation=situation)
        started = time.monotonic()
        out = tool.run(action="navigate", url=url)
        total_s = time.monotonic() - started
        if out.startswith("error:"):
            row["error"] = out[:200]
            return row
        wall = situation.take_handover()
        final_url = driver.url
        final_html = driver.page_html()
        html_only = detect_wall(final_html, final_url)
        tree = _drawn_frames(driver)
        v1 = detect_wall(driver.v1_html, final_url) if driver.v1_html is not None else None
        title = str(driver._page.title())
        text = driver.page_text()
        elements_after = len(driver.read())  # after the wait and the verdict: the label's input
        v1_label = label(title, text, elements_after, v2=False)
        v2_label = label(title, text, elements_after, v2=True)
        served_bot_check = v2_label == "captcha"
        if table_kind is None:
            expected, expected_v1 = v2_label, v1_label
            valid = True
        else:
            expected = "captcha" if served_bot_check else table_kind
            expected_v1 = table_kind
            valid = valid_control(table_kind, driver.requests, text, served_bot_check)
        row.update({
            "final_url": final_url,
            "title": title[:120],
            "elements": driver.nav_elements,
            "elements_after_wait": elements_after,
            "table_kind": table_kind,
            "label_v1": v1_label,
            "label_v2": v2_label,
            "expected": expected,
            "expected_v1_reading": expected_v1,
            "valid_control": valid,
            "handover": wall.kind if wall is not None else "none",
            "evidence": wall.evidence if wall is not None else "",
            "v1_detector": v1.kind if v1 is not None else "none",
            "html_after_settle": html_only.kind if html_only is not None else "none",
            "tree_frames": [_short(a) for a in tree],
            "all_frames": [_short(f.url) for f in driver._page.frames[1:]][:12],
            "settle_s": round(driver.settle_s, 3) if driver.settle_s is not None else None,
            "navigate_s": round(driver.navigate_s, 3),
            "overhead_s": round(total_s - driver.navigate_s, 3),
        })
    except Exception as exc:  # noqa: BLE001 — a page that fails is reported, not replaced
        row["error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    finally:
        if driver is not None:
            driver.close()
    return row


def _verdict(rows: list[dict[str, Any]], *, expected_key: str) -> dict[str, Any]:
    loaded = [r for r in rows if "error" not in r]
    excluded = [r for r in loaded if r[expected_key] == "excluded"]
    ordinary = [r for r in loaded if r[expected_key] == "none"]
    walls = [r for r in loaded if r[expected_key] not in ("none", "excluded") and r["valid_control"]]
    invalid = [r for r in loaded if r["control"] and not r["valid_control"]]
    false_handovers = [r for r in ordinary if r["handover"] != "none"]
    found = [r for r in walls if r["handover"] == r[expected_key]]
    need = [r for r in walls if r[expected_key] in ("login", "captcha")]
    return {
        "pages": len(rows),
        "loaded": len(loaded),
        "failed": [(r["url"], r["error"][:80]) for r in rows if "error" in r],
        "excluded": [(r["url"], r["handover"], r["evidence"]) for r in excluded],
        "ordinary": len(ordinary),
        "false_handovers": [(r["url"], r["handover"], r["evidence"]) for r in false_handovers],
        "walls": len(walls),
        "walls_found_with_kind": len(found),
        "missed": [(r["url"], r[expected_key], r["handover"]) for r in walls if r not in found],
        "invalid_controls": [r["url"] for r in invalid],
        "fit": len(false_handovers) <= 1 and all(r in found for r in need),
    }


def _v1_detector(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """What the v1 detector said on this same load (diagnostic): its false stops and its finds."""
    loaded = [r for r in rows if "error" not in r]
    ordinary = [r for r in loaded if r["expected"] == "none"]
    walls = [r for r in loaded if r["expected"] not in ("none", "excluded") and r["valid_control"]]
    return {
        "false_handovers": sum(1 for r in ordinary if r["v1_detector"] != "none"),
        "walls_found_with_kind": sum(1 for r in walls if r["v1_detector"] == r["expected"]),
        "walls": len(walls),
    }


def _timing(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ordinary = [r for r in rows if "error" not in r and r["expected"] == "none"]
    settle = sorted(r["settle_s"] for r in ordinary if r["settle_s"] is not None)
    overhead = sorted(r["overhead_s"] for r in ordinary)
    if not settle:
        return {}

    def p90(xs: list[float]) -> float:
        return xs[min(len(xs) - 1, int(round(0.9 * (len(xs) - 1))))]

    return {
        "n": len(settle),
        "settle_median_s": round(statistics.median(settle), 3),
        "settle_p90_s": round(p90(settle), 3),
        "settle_max_s": round(settle[-1], 3),
        "at_the_bound": sum(1 for s in settle if s >= SETTLE_SECONDS - 0.05),
        "overhead_median_s": round(statistics.median(overhead), 3),
        "overhead_max_s": round(overhead[-1], 3),
    }


def smoke() -> None:
    """The apparatus on pages in neither set (example.com, as an ordinary page and as a login
    control that shows no password): every field filled, nothing written. Run before the real run."""
    for kind in (None, "login"):
        print(json.dumps(measure("https://example.com/", "smoke", kind), ensure_ascii=False))


def main() -> None:
    if "--smoke" in sys.argv:
        smoke()
        return
    out_dir = Path(__file__).resolve().parent / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    plan = (
        [(u, "A", None) for u in PAGES]
        + [(u, "A", k) for u, k in CONTROLS.items()]
        + [(u, "B", None) for u in FRESH_PAGES]
        + [(u, "B", k) for u, k in FRESH_CONTROLS.items()]
    )
    rows = []
    for url, page_set, kind in plan:
        row = measure(url, page_set, kind)
        rows.append(row)
        if "error" in row:
            print(f"  {page_set} ERROR {url}: {row['error']}", flush=True)
            continue
        mark = "ok " if row["handover"] == row["expected"] else "XX "
        mark = mark if row["valid_control"] else "?? "
        print(f"  {page_set} {mark} expected={row['expected']:<8} handover={row['handover']:<8} "
              f"v1={row['v1_detector']:<8} settle={row['settle_s']}s {row['elements']:>5} el  {url}  "
              f"[{row['evidence']}]", flush=True)
    with (out_dir / "walls_v2.jsonl").open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    summary: dict[str, Any] = {"settle_seconds": SETTLE_SECONDS}
    for page_set in ("A", "B"):
        these = [r for r in rows if r["set"] == page_set]
        summary[page_set] = {
            "verdict": _verdict(these, expected_key="expected"),
            "timing": _timing(these),
            "v1_detector_on_this_load": _v1_detector(these),
        }
    summary["A"]["verdict_under_the_v1_label"] = _verdict(
        [r for r in rows if r["set"] == "A"], expected_key="expected_v1_reading"
    )
    summary["candidate_for_default_on_pending_m2_check"] = (
        summary["A"]["verdict"]["fit"] and summary["B"]["verdict"]["fit"]
    )
    (out_dir / "walls_v2_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
