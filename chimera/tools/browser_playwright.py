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

import contextlib
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
  const vw = window.innerWidth, vh = window.innerHeight;
  const whereFor = (r) =>
    r.bottom <= 0 ? 'above' : r.top >= vh ? 'below' : (r.right <= 0 || r.left >= vw) ? 'beside' : 'in';
  const out = [];
  let i = 0;
  for (const el of els) {
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 && rect.height === 0) continue;  // skip hidden
    const ref = 'e' + (++i);
    el.setAttribute('data-chimera-ref', ref);
    out.push({ ref, role: roleFor(el), name: nameFor(el), where: whereFor(rect) });
  }
  return out;
}
"""

# One viewport's worth, less a strip of overlap so a line cut at the edge is seen whole on one side.
_SCROLL_SCRIPT = r"""
(sign) => window.scrollBy({ top: sign * Math.round(window.innerHeight * 0.85), behavior: 'instant' })
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
        """One paused request: continue it or fail it. Any error while DECIDING fails it — a request
        left paused hangs the page, and a guard that cannot decide must not wave it through.

        The decision and the command are separate steps. The first version sent ``continueRequest``
        inside the try, so when the page had already cancelled the request (it navigated away), the
        protocol's "Invalid InterceptionId" landed in the except and the guard sent ``failRequest``
        for the same dead id — which raised again, out of the event handler (study 24, M7, seen on a
        live page with nothing refused). A request that is already gone cannot be sent either way,
        so a failed command is dropped, never retried as the opposite verdict."""
        request_id = params.get("requestId")
        url = ""
        try:
            url = str((params.get("request") or {}).get("url", ""))
            allow = self.permits(url)
        except Exception:  # noqa: BLE001 — cannot decide: fail closed
            allow = False
        if not allow:
            self.blocked_requests += 1
            if params.get("resourceType") == "Document":
                self.blocked_navigations.append(url)
        command = (
            ("Fetch.continueRequest", {"requestId": request_id})
            if allow
            else ("Fetch.failRequest", {"requestId": request_id, "errorReason": "BlockedByClient"})
        )
        with contextlib.suppress(Exception):  # the request is gone: nothing left to allow or refuse
            session.send(*command)

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
        return [Element(ref=r["ref"], role=r["role"], name=r["name"], where=r.get("where")) for r in raw]

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

    def scroll(self, direction: str = "down") -> list[Element]:
        """Move the viewport one screen (``down`` or ``up``) and snapshot again. Used only by the
        opt-in viewport-first listing. It navigates nowhere, but a page may load content on scroll,
        and those requests pass the same guard as any other."""
        self._page.evaluate(_SCROLL_SCRIPT, -1 if direction == "up" else 1)
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

    @property
    def url(self) -> str:
        """The loaded page's address, after redirects — what a browser handover names (study 25,
        S11). Not in the `BrowserDriver` protocol: the handover reads it when a driver has it."""
        return str(self._page.url)

    def settle(self, seconds: float) -> None:
        """Wait at most ``seconds`` for the page's ``load`` event (study 25, S11, walls v2).

        Every page action returns at ``domcontentloaded``, which comes before the scripts and
        frames a page loads asynchronously. Measured on the hCaptcha demo: no checkbox frame at
        ``domcontentloaded``, the frame there 0.18 s later, at ``load``. A page still loading when
        the time runs out is read as it is — the wait is a bound, never a condition. Not in the
        `BrowserDriver` protocol: the browser situation calls it when a driver has it.
        """
        with contextlib.suppress(Exception):  # a timeout, or a page that navigated meanwhile
            self._page.wait_for_load_state("load", timeout=seconds * 1000)

    def frame_boxes(self, keep: Callable[[str], bool]) -> list[tuple[str, float, float]]:
        """Each frame whose address ``keep`` accepts, with the size it is drawn at (0 × 0 if not).

        Read off the browser's frame tree, not the page's HTML, because the HTML cannot show a
        frame inside a shadow root, and a closed shadow root cannot be walked from the page at
        all. Measured on the Turnstile demo: its frame sits in a **closed** root, invisible to
        ``page.content()`` and to any script walking open roots, and present in this tree with
        its 300 × 65 box. Frames nested in other frames are in the tree too. ``keep`` is asked
        first so only the frames it wants cost the two round trips a box takes. Not in the
        `BrowserDriver` protocol, like :meth:`settle`.
        """
        found: list[tuple[str, float, float]] = []
        for frame in self._page.frames:
            if frame is self._page.main_frame or not keep(frame.url):
                continue
            box = None
            with contextlib.suppress(Exception):  # a frame detached while it was being read
                box = frame.frame_element().bounding_box()
            found.append((frame.url, box["width"], box["height"]) if box else (frame.url, 0.0, 0.0))
        return found

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
