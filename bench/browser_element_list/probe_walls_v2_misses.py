"""Walls v2, diagnosis AFTER the registered run (not a second measurement): why did the set-B
walls miss? Run from the repo root with PYTHONPATH=. ; output in results/walls_v2_probe.jsonl. For each: provider requests, provider frames in the tree (with boxes), password inputs
(stamped or not, with a box or not), at the product's look (load, bounded) and 5 s later."""

import json
import time
from urllib.parse import urlsplit

from chimera.tools.browser_playwright import PlaywrightDriver
from chimera.tools.browser_situation import detect_wall, is_provider_frame

PAGES = [
    "https://nopecha.com/demo/hcaptcha",
    "https://2captcha.com/demo/hcaptcha",
    "https://2captcha.com/demo/recaptcha-v2",
    "https://2captcha.com/demo/cloudflare-turnstile",
    "https://login.salesforce.com/",
    "https://medium.com/",
]

INPUTS = r"""
() => Array.from(document.querySelectorAll('input')).filter(e => {
  const t = (e.type || '').toLowerCase();
  return t === 'password' || /pass|user|email|login/i.test(e.name + ' ' + e.id);
}).map(e => {
  const r = e.getBoundingClientRect();
  return {type: e.type, name: e.name, id: e.id, ref: e.getAttribute('data-chimera-ref'),
          w: Math.round(r.width), h: Math.round(r.height),
          vis: getComputedStyle(e).visibility, disp: getComputedStyle(e).display};
})
"""

PROVIDERS = ("hcaptcha.com", "challenges.cloudflare.com", "recaptcha", "gstatic.com")


def look(d, requests):
    page = d._page
    frames = []
    for f in page.frames[1:]:
        if not is_provider_frame(f.url):
            continue
        box = None
        try:
            box = f.frame_element().bounding_box()
        except Exception as exc:  # noqa: BLE001
            box = str(exc)[:40]
        p = urlsplit(f.url)
        frames.append({"frame": f"{p.hostname}{p.path[:40]}#{(p.fragment or '')[:30]}", "box": box})
    w = detect_wall(d.page_html(), d.url)
    return {
        "wall_html": w and w.kind,
        "provider_frames": frames,
        "provider_requests": sorted({urlsplit(u).hostname for u in requests
                                     if any(k in u for k in PROVIDERS)})[:6],
        "inputs": page.evaluate(INPUTS)[:6],
        "title": page.title()[:60],
    }


for url in PAGES:
    d = PlaywrightDriver(headless=True)
    requests: list[str] = []
    d._page.on("request", lambda r, seen=requests: seen.append(r.url))
    row: dict = {"url": url}
    try:
        t0 = time.monotonic()
        els = d.navigate(url)
        row["dcl_s"] = round(time.monotonic() - t0, 2)
        row["elements"] = len(els)
        row["at_dcl"] = look(d, requests)
        t1 = time.monotonic()
        d.settle(2.0)
        row["settle_s"] = round(time.monotonic() - t1, 2)
        row["after_settle"] = look(d, requests)
        time.sleep(5)
        row["after_5s_more"] = look(d, requests)
    except Exception as exc:  # noqa: BLE001
        row["error"] = str(exc)[:200]
    finally:
        d.close()
    print(json.dumps(row, default=str), flush=True)
