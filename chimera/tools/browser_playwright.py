"""The real :class:`~chimera.tools.browser.BrowserDriver`, backed by Playwright (opt-in extra).

Kept in its own module so importing :mod:`chimera.tools.browser` never pulls Playwright — the
tool imports this lazily and falls back to an install hint when the extra (or the Chromium
binary) is missing. Reads a page as its accessibility tree: interactive elements are tagged
in-page with a stable ``data-chimera-ref`` so clicks/typing are by ref, never by coordinate.
"""

from __future__ import annotations

from typing import Any

from chimera.tools.browser import Element

# JS run in-page: tag each visible interactive element with a stable ref and return its role/name.
_TAG_SCRIPT = r"""
() => {
  const roleFor = (el) => {
    const r = el.getAttribute('role');
    if (r) return r;
    const tag = el.tagName.toLowerCase();
    if (tag === 'a') return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'input') return (el.type === 'submit' || el.type === 'button') ? 'button' : 'textbox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'select') return 'combobox';
    return tag;
  };
  const nameFor = (el) =>
    (el.getAttribute('aria-label') || el.innerText || el.value ||
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


class PlaywrightDriver:
    """A persistent Chromium page driven through the accessibility tree."""

    def __init__(self, *, headless: bool = True) -> None:
        from playwright.sync_api import sync_playwright  # lazy: only when actually browsing

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=headless)
        self._page = self._browser.new_page()

    def _snapshot(self) -> list[Element]:
        raw: list[dict[str, Any]] = self._page.evaluate(_TAG_SCRIPT)
        return [Element(ref=r["ref"], role=r["role"], name=r["name"]) for r in raw]

    def navigate(self, url: str) -> list[Element]:
        self._page.goto(url, wait_until="domcontentloaded")
        return self._snapshot()

    def read(self) -> list[Element]:
        return self._snapshot()

    def click(self, ref: str) -> list[Element]:
        self._page.click(f'[data-chimera-ref="{ref}"]', timeout=5000)
        self._page.wait_for_load_state("domcontentloaded")
        return self._snapshot()

    def type_text(self, ref: str, text: str) -> list[Element]:
        self._page.fill(f'[data-chimera-ref="{ref}"]', text, timeout=5000)
        return self._snapshot()

    def back(self) -> list[Element]:
        self._page.go_back(wait_until="domcontentloaded")
        return self._snapshot()

    def page_html(self) -> str:
        return str(self._page.content())  # the rendered DOM (post-JS), for HTML->Markdown

    def page_text(self) -> str:
        return str(self._page.inner_text("body"))  # visible text; fallback + basis for find

    def screenshot(self, path: str) -> None:
        self._page.screenshot(path=path, full_page=True)  # a real full-page PNG of the current page

    def close(self) -> None:
        from contextlib import suppress

        with suppress(Exception):
            self._browser.close()
        with suppress(Exception):
            self._pw.stop()
