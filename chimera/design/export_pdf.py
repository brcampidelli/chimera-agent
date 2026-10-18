"""A design as a PDF: the static print page (:func:`~chimera.design.export_html.render_print_html`)
printed by Playwright's Chromium, one PDF page per artboard at the artboard's size.

The page Chromium loads is the same inert HTML the HTML export writes, and the browser is held to
less than that page already allows:

* **JavaScript is off** for the context — there is no script in the page, and if a bug ever let one
  in, it would not run;
* **no network**: the context is offline and every request except a ``data:`` URL is aborted, so a
  URL that slipped into the markup is never fetched (the page's own CSP forbids it too);
* the content goes in with ``set_content`` on ``about:blank`` — no file is written for Chromium to
  open, and no origin the page could talk to.

**Lazy, and never a crash.** Playwright is a core dependency, but the Chromium binary is not shipped
by pip; on a machine that never browsed it is absent. Then this raises
:class:`~chimera.design.exports.ExportUnavailable` with the command that installs it and its size
(about 150 MB, once). It does not download it itself: the browser tool asks for that on first use,
and an export button fetching 150 MB unannounced is a surprise nobody asked for.

Chromium writes a page in CSS pixels at 96 per inch, so an 800×600 artboard prints as a 600×450 pt
page; the ``@page`` rule of each artboard gives its size (``prefer_css_page_size``).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from typing import Any, Final, TypeVar

from chimera.design.export_html import artboards, render_print_html
from chimera.design.exports import ExportFailed, ExportUnavailable
from chimera.design.model import DesignDocument
from chimera.telemetry import get_logger

_log = get_logger("design.export_pdf")
_T = TypeVar("_T")

#: How long one export may take in the browser. A design of the largest size renders in seconds.
RENDER_TIMEOUT_MS: Final = 60_000

CHROMIUM_MISSING: Final = (
    "this export draws the design in Chromium, which is not installed on this machine. It downloads "
    "once, about 150 MB: run `playwright install chromium` (on Linux, `playwright install "
    "--with-deps chromium` also installs the system libraries), or use the browser tool once, which "
    "installs it. Then export again."
)
PLAYWRIGHT_MISSING: Final = (
    "this export needs Playwright, a core dependency that appears to be missing — reinstall with "
    "`pip install --upgrade chimera-agent`, then `playwright install chromium` (about 150 MB, once)."
)


def _chromium_unavailable(exc: Exception) -> ExportUnavailable:
    from chimera.tools.browser import _missing_browser_binary

    if _missing_browser_binary(exc):
        return ExportUnavailable(CHROMIUM_MISSING)
    _log.warning("design export: Chromium did not start: %s", exc)
    return ExportUnavailable(
        f"Chromium could not start on this machine ({type(exc).__name__}). Install it, with its "
        "system libraries, by `playwright install --with-deps chromium` (about 150 MB, once)."
    )


def _only_data_urls(route: Any) -> None:
    """Every request the page makes is refused unless it is the page's own ``data:`` image."""
    if str(route.request.url).startswith("data:"):
        route.continue_()
    else:
        route.abort()


@contextmanager
def static_page(html: str, *, width: int, height: int) -> Iterator[Any]:
    """A Chromium page holding ``html``, with scripts off and no network, closed on the way out."""
    from chimera.tools.browser import _configure_frozen_browser_path

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise ExportUnavailable(PLAYWRIGHT_MISSING) from None
    _configure_frozen_browser_path()
    try:
        manager = sync_playwright().start()
    except Exception as exc:  # noqa: BLE001 — a driver that will not start is a refusal, not a crash
        _log.warning("design export: Playwright did not start: %s", exc)
        raise ExportUnavailable(
            f"the PDF/PNG renderer could not start here ({type(exc).__name__})."
        ) from None
    try:
        try:
            browser = manager.chromium.launch(headless=True)
        except Exception as exc:  # noqa: BLE001
            raise _chromium_unavailable(exc) from None
        try:
            context = browser.new_context(
                java_script_enabled=False,
                offline=True,
                viewport={"width": max(1, width), "height": max(1, height)},
                device_scale_factor=1,
            )
            context.route("**/*", _only_data_urls)
            page = context.new_page()
            page.set_default_timeout(RENDER_TIMEOUT_MS)
            page.set_content(html, wait_until="load")
            yield page
        finally:
            browser.close()
    finally:
        manager.stop()


def _rendered(work: Callable[[], _T], what: str) -> _T:
    """``work()``, with any failure past the launch turned into one sentence."""
    try:
        return work()
    except (ExportUnavailable, ExportFailed):
        raise
    except Exception as exc:  # noqa: BLE001 — the reason goes to the log, a sentence to the caller
        _log.warning("design export (%s) failed: %s", what, exc)
        raise ExportFailed(f"the {what} could not be rendered; try again") from None


def render_pdf(doc: DesignDocument, *, assets: Mapping[str, bytes] | None = None) -> bytes:
    """``doc`` as PDF bytes: one page per artboard, page by page, each at its artboard's size."""
    boards = artboards(doc)
    if not boards:
        raise ExportFailed("this design has no artboard to print; add one first")
    html = render_print_html(doc, assets=assets)
    width = int(max(b.width for b in boards))
    height = int(max(b.height for b in boards))

    def work() -> bytes:
        with static_page(html, width=min(width, 16_384), height=min(height, 16_384)) as page:
            return bytes(page.pdf(print_background=True, prefer_css_page_size=True))

    return _rendered(work, "PDF")
