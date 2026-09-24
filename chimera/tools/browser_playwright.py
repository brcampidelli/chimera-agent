"""The real :class:`~chimera.tools.browser.BrowserDriver`, backed by Playwright (opt-in extra).

Kept in its own module so importing :mod:`chimera.tools.browser` never pulls Playwright — the
tool imports this lazily and falls back to an install hint when the extra (or the Chromium
binary) is missing. Reads a page as its accessibility tree: interactive elements are tagged
in-page with a ``data-chimera-ref`` so clicks/typing are by ref, never by coordinate.

Three things study 24 found missing (``bench/PLAN-study24-jev-practice.md``, items 1–2):

* **Every request is SSRF-checked, not only the first URL.** ``BrowserTool`` ran ``check_url`` on
  the address it was given and then let Chromium follow redirects, links and script navigation
  unchecked — and ``scrape(render="browser")`` never checked at all. An interception on every
  page (DevTools ``Fetch``, which pauses redirect hops too) now refuses any http(s) request whose
  host resolves to a private, loopback, link-local or metadata address, whatever issued it. A refused *navigation* raises, so the caller reads an error instead
  of an error page; a refused subresource is dropped quietly (it never reaches the model, but a
  blind GET to the sidecar could still act).
* **Refs are cleared before each snapshot and clicked strictly.** The old stamp was never removed,
  so a re-rendered page could hold two ``e3``s and ``page.click`` took the first.
* **A password field's value is never its name.** ``nameFor`` fell back to ``el.value``, so a
  typed password became the element's name in the next observation — into the model's context
  and the ledger.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from chimera.scrape.ssrf import is_safe_url
from chimera.tools.browser import BrowserFrame, Element

# JS run in-page: clear the previous snapshot's refs, then tag each visible interactive element
# with a fresh ref and return its role/name.
_TAG_SCRIPT = r"""
() => {
  for (const old of document.querySelectorAll('[data-chimera-ref]')) old.removeAttribute('data-chimera-ref');
  const isPassword = (el) => el.tagName.toLowerCase() === 'input' && (el.type || '').toLowerCase() === 'password';
  const roleFor = (el) => {
    const r = el.getAttribute('role');
    if (r) return r;
    const tag = el.tagName.toLowerCase();
    if (tag === 'a') return 'link';
    if (tag === 'button') return 'button';
    if (isPassword(el)) return 'textbox (password)';
    if (tag === 'input') return (el.type === 'submit' || el.type === 'button') ? 'button' : 'textbox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'select') return 'combobox';
    return tag;
  };
  const nameFor = (el) =>
    (el.getAttribute('aria-label') || el.innerText || (isPassword(el) ? '' : el.value) ||
     el.getAttribute('placeholder') || el.getAttribute('name') || '').trim().slice(0, 120);
  const sel = 'a,button,input,textarea,select,[role=button],[role=link],[role=textbox]';
  const els = Array.from(document.querySelectorAll(sel));
  const out = [];
  let i = 0;
  for (const el of els) {
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) continue;  // skip hidden
    const ref = 'e' + (++i);
    el.setAttribute('data-chimera-ref', ref);
    out.push({ ref, role: roleFor(el), name: nameFor(el) });
  }
  return out;
}
"""


class RequestGuard:
    """The SSRF check every request of the page passes through — redirect hops included.

    Installed through the DevTools protocol (``Fetch.requestPaused`` at the request stage), not
    Playwright's ``route``: measured before choosing, a context route saw ``/redirect`` and never
    the ``302``'s target, and the internal page loaded; the protocol pauses the hop too, and
    ``Fetch.failRequest`` stops it before it leaves (``net::ERR_BLOCKED_BY_CLIENT``, nothing loaded).

    ``allowed`` decides a URL; production uses :func:`~chimera.scrape.ssrf.is_safe_url`, cached per
    scheme+host so a page with a hundred subresources resolves each host once. Only http(s) is
    judged — ``data:``/``blob:`` never leave the page. ``blocked_navigations`` holds the document
    requests refused since the last :meth:`take`, which is how a driver action learns that the page
    it asked for was not loaded.
    """

    def __init__(self, allowed: Callable[[str], bool] | None = None) -> None:
        self._allowed = allowed or is_safe_url
        self._hosts: dict[str, bool] = {}
        self.blocked_navigations: list[str] = []
        self.blocked_requests = 0

    def permits(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return True
        key = f"{parsed.scheme}://{parsed.netloc}"
        if key not in self._hosts:
            self._hosts[key] = self._allowed(url)
        return self._hosts[key]

    def on_paused(self, session: Any, params: dict[str, Any]) -> None:
        """One paused request: continue it or fail it. Any error while deciding fails it — a request
        left paused hangs the page, and a guard that cannot decide must not wave it through."""
        request_id = params.get("requestId")
        try:
            url = str((params.get("request") or {}).get("url", ""))
            if self.permits(url):
                session.send("Fetch.continueRequest", {"requestId": request_id})
                return
            self.blocked_requests += 1
            if params.get("resourceType") == "Document":
                self.blocked_navigations.append(url)
        except Exception:  # noqa: BLE001 — fail closed, below
            pass
        session.send("Fetch.failRequest", {"requestId": request_id, "errorReason": "BlockedByClient"})

    def take(self) -> list[str]:
        out, self.blocked_navigations = self.blocked_navigations, []
        return out


class PlaywrightDriver:
    """A persistent Chromium page driven through the accessibility tree."""

    def __init__(self, *, headless: bool = True, allowed: Callable[[str], bool] | None = None) -> None:
        from playwright.sync_api import sync_playwright  # lazy: only when actually browsing

        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch(headless=headless)
        except Exception:
            # No Chromium yet (the tool installs it and builds a SECOND driver). Without this stop the
            # first driver's event loop stayed running for the life of the process — measured in the
            # WSL gate, where it failed 27 later async tests (the TUI's) that had nothing to do with it.
            self._pw.stop()
            raise
        self._context = self._browser.new_context()
        self.guard = RequestGuard(allowed)
        self._sessions: list[Any] = []
        # Every page of the context gets the guard: the driver's own page before its first request;
        # a popup the page opens as soon as the context reports it (its first request may race it,
        # and a popup is never read by this driver).
        self._context.on("page", self._attach_guard)
        self._page = self._context.new_page()
        self._attach_guard(self._page)  # a no-op when the event already attached it

    def _attach_guard(self, page: Any) -> None:
        if any(owner is page for owner, _ in self._sessions):
            return
        session = self._context.new_cdp_session(page)
        session.on("Fetch.requestPaused", lambda params: self.guard.on_paused(session, params))
        session.send("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]})
        self._sessions.append((page, session))

    def _snapshot(self) -> list[Element]:
        raw: list[dict[str, Any]] = self._page.evaluate(_TAG_SCRIPT)
        return [Element(ref=r["ref"], role=r["role"], name=r["name"]) for r in raw]

    def _guarded(self, step: Callable[[], object]) -> list[Element]:
        """Run a step that may navigate; a navigation the guard refused is an error, not a page."""
        self.guard.take()
        try:
            step()
        except Exception:
            blocked = self.guard.take()
            if blocked:
                self._blank()
                raise ValueError(f"blocked navigation to an internal address: {blocked[0]}") from None
            raise
        blocked = self.guard.take()
        if blocked:
            self._blank()
            raise ValueError(f"blocked navigation to an internal address: {blocked[0]}")
        return self._snapshot()

    def _blank(self) -> None:
        """After a refused navigation the page is mid-way to Chromium's error page, and the next read
        fails with "the page is navigating". A blank page is a settled state with nothing to read."""
        from contextlib import suppress

        with suppress(Exception):
            self._page.goto("about:blank", wait_until="domcontentloaded")

    def _by_ref(self, ref: str) -> Any:
        """The one element carrying ``ref``. Unknown is a KeyError (the tool reports it); more than
        one means the page re-stamped behind our back — refuse instead of acting on the first."""
        locator = self._page.locator(f'[data-chimera-ref="{ref}"]')
        count = locator.count()
        if count == 0:
            raise KeyError(f"unknown ref {ref!r} — read the page again")
        if count > 1:
            raise ValueError(f"ref {ref!r} matches {count} elements — the page changed; read it again")
        return locator

    def navigate(self, url: str) -> list[Element]:
        return self._guarded(lambda: self._page.goto(url, wait_until="domcontentloaded"))

    def read(self) -> list[Element]:
        return self._snapshot()

    def click(self, ref: str) -> list[Element]:
        target = self._by_ref(ref)

        def step() -> None:
            target.click(timeout=5000)
            self._page.wait_for_load_state("domcontentloaded")

        return self._guarded(step)

    def type_text(self, ref: str, text: str) -> list[Element]:
        self._by_ref(ref).fill(text, timeout=5000)
        return self._snapshot()

    def back(self) -> list[Element]:
        return self._guarded(lambda: self._page.go_back(wait_until="domcontentloaded"))

    def page_html(self) -> str:
        return str(self._page.content())  # the rendered DOM (post-JS), for HTML->Markdown

    def page_text(self) -> str:
        return str(self._page.inner_text("body"))  # visible text; fallback + basis for find

    def screenshot(self, path: str) -> None:
        self._page.screenshot(path=path, full_page=True)  # a real full-page PNG of the current page

    def frame(self) -> BrowserFrame | None:
        """The viewport as a JPEG at quality 55 — the size/time trade `BrowserFrame` measured."""
        size = self._page.viewport_size or {"width": 0, "height": 0}
        data = self._page.screenshot(type="jpeg", quality=55, full_page=False)
        return BrowserFrame(
            jpeg=data, url=self._page.url, title=self._page.title(),
            width=int(size.get("width", 0)), height=int(size.get("height", 0)),
        )

    def close(self) -> None:
        from contextlib import suppress

        with suppress(Exception):
            self._browser.close()
        with suppress(Exception):
            self._pw.stop()
