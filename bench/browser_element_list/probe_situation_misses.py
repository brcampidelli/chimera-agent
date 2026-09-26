"""Diagnosis after the registered run (RESULTS-situation.md): why did the controls miss?

The wall scan at load, then again after 5 s and a fresh `read`, with the provider frames seen each time.

    python bench/browser_element_list/probe_situation_misses.py
"""

import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from chimera.tools.browser_playwright import PlaywrightDriver  # noqa: E402
from chimera.tools.browser_situation import detect_wall  # noqa: E402

PAGES = [
    "https://accounts.hcaptcha.com/demo",
    "https://demo.turnstile.workers.dev/",
    "https://checkout.stripe.dev/preview",
    "https://stripe-payments-demo.appspot.com/",
    "https://www.nytimes.com/",
]


def frames(html: str) -> list[str]:
    out = []
    for src in re.findall(r"<iframe[^>]*\ssrc=\"([^\"]*)\"", html):
        p = urlsplit(src.replace("&amp;", "&"))
        frag = "&".join(x for x in (p.fragment or "").split("&") if x.split("=")[0] in ("frame", "size"))
        out.append(f"{p.hostname}{p.path[:60]}#{frag}")
    return out


for url in PAGES:
    d = PlaywrightDriver(headless=True)
    try:
        d.navigate(url)
        at_load = d.page_html()
        w0 = detect_wall(at_load, d.url)
        time.sleep(5)
        d.read()  # a fresh snapshot, as the next `read` action would take
        later = d.page_html()
        w1 = detect_wall(later, d.url)
        print(json.dumps({
            "url": url,
            "at_load": {"wall": w0 and (w0.kind, w0.evidence), "frames": frames(at_load)[:6]},
            "after_5s_and_a_read": {"wall": w1 and (w1.kind, w1.evidence), "frames": frames(later)[:6]},
        }))
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"url": url, "error": str(exc)[:160]}))
    finally:
        d.close()
