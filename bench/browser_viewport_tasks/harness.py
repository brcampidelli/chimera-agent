"""The apparatus both the dry-run and the paid solve use — one construction, so the scripted check
and the model are measured through the same browser.

* **The browser reaches the local site and nothing else.** The product refuses 127.0.0.1 twice (the
  tool's ``check_url`` and the driver's per-request guard), which is right for the product and in
  the way here. Both are narrowed, not opened: the driver's guard allows exactly this origin, and
  ``check_url`` is replaced, for this process only, by one that accepts exactly this origin and
  answers anything else the way an offline machine does ("could not resolve host"). A run can
  therefore never touch the live web, whatever the model tries.
* **The arm is the constructor flag and nothing else.** ``BrowserTool(viewport_first=...)``, the
  product's own switch; the environment variable is cleared so it cannot leak in.
* **Every browser call is counted** — action, characters handed back — by a pass-through wrapper
  that shows the model the tool's own name, description and schema.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from chimera.tools.base import Tool
from chimera.tools.browser import BrowserTool

ARMS = ("today", "viewport")


@contextlib.contextmanager
def only_this_origin(origin: str) -> Iterator[None]:
    import chimera.scrape.ssrf as ssrf

    original = ssrf.check_url

    def check(url: str) -> None:
        if url == origin or url.startswith(origin + "/"):
            return
        host = urlparse(url).hostname or url
        raise ValueError(f"could not resolve host {host!r}")

    ssrf.check_url = check
    try:
        yield
    finally:
        ssrf.check_url = original


@dataclass
class CallLog:
    calls: list[dict[str, Any]] = field(default_factory=list)

    @property
    def chars(self) -> int:
        return sum(int(c["chars"]) for c in self.calls)

    def actions(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for c in self.calls:
            out[str(c["action"])] = out.get(str(c["action"]), 0) + 1
        return out


class CountedBrowser(Tool):
    """The browser, unchanged, with each call's action and output size written down."""

    def __init__(self, inner: BrowserTool, log: CallLog) -> None:
        self.inner = inner
        self.name = inner.name
        self.description = inner.description
        self.parameters = inner.parameters
        self.untrusted_output = bool(getattr(inner, "untrusted_output", False))
        self.log = log

    def run(self, **kwargs: Any) -> str:
        out = self.inner.run(**kwargs)
        self.log.calls.append({
            "action": str(kwargs.get("action", "")).strip(),
            "chars": len(out),
            "error": out.startswith("error:"),
        })
        return out


@contextlib.contextmanager
def browser_for(origin: str, arm: str) -> Iterator[tuple[CountedBrowser, Any]]:
    """The counted browser tool for one arm, on a fresh Chromium that reaches only ``origin``."""
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}")
    os.environ.pop("CHIMERA_BROWSER_VIEWPORT_FIRST", None)
    from chimera.tools.browser_playwright import PlaywrightDriver

    driver = PlaywrightDriver(headless=True, allowed=lambda u: u == origin or u.startswith(origin + "/"))
    tool = BrowserTool(driver=driver, viewport_first=(arm == "viewport"))
    try:
        with only_this_origin(origin):
            yield CountedBrowser(tool, CallLog()), driver
    finally:
        driver.close()
