"""``GET /api/catalogs/{kind}`` — the three public catalogues, read from their pinned commits (plan M3).

Read-only and neutral: one item shape for plugins, skills and connectors
(:class:`~chimera.catalogs.model.CatalogItemOut`), each item carrying the repository, commit and file
it was read from, whether today's Chimera could install it and why not. Nothing here installs,
enables or adds anything — those stay on the plugin, skill and MCP routes, with their pending states
and the owner's switch — so the read is on the desktop bridge (``catalogs.read``).

Never an error for a catalogue that cannot be read: offline with nothing cached, or every file
refused by its pin, is ``available: false`` with the reason in a sentence. The first read of a
catalogue fetches its pinned files (bounded, through the plugin installer's fetch); every later read
comes from the cache under ``CHIMERA_HOME/catalogs``.

Plan M4 adds, in the same response rather than a second route: GitHub stars with the date they were
read (``chimera.catalogs.stars``, cached 24 h, refreshed in the background, off with
``CHIMERA_CATALOG_STARS=0``) and ``sections`` computed on this machine with the reason for each item
(``chimera.catalogs.sections``). One response because the sections are a view of these same items —
a second route would mean a second read of the same pins, and a screen that could show a section
naming an item its list does not have.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, params
from fastapi.concurrency import run_in_threadpool

from chimera.catalogs.model import CatalogOut
from chimera.catalogs.pins import CatalogKind
from chimera.config import Settings
from chimera.telemetry import get_logger

log = get_logger(__name__)


def catalog_view(kind: CatalogKind, home: Path, *, stars: bool) -> CatalogOut:
    """The pinned catalogue, with this machine's star readings and the sections computed here.

    Stars never wait on the network: cached readings are applied and, when ``stars`` is on and
    something is stale, one bounded refresh starts in the background for the next read. A failure in
    either extra leaves the catalogue as the pin reads it.
    """
    from chimera.catalogs.loader import load_catalog
    from chimera.catalogs.sections import compute_sections, local_signals
    from chimera.catalogs.stars import with_stars

    catalog = load_catalog(kind, home)
    if not catalog.items:
        return catalog
    try:
        with_stars(catalog, home, enabled=stars)
    except Exception as exc:  # noqa: BLE001 -- stars are an extra; the catalogue still reads
        log.info("catalogue stars not applied: %s", exc)
    try:
        catalog.sections = compute_sections(catalog, local_signals(home))
    except Exception as exc:  # noqa: BLE001 -- sections are an extra; the catalogue still reads
        log.warning("catalogue sections not computed: %s", exc)
    return catalog


def register_catalogs_api(app: FastAPI, guard: params.Depends, settings: Settings) -> None:
    """Mount the catalogue reads. ``home`` is the launch photograph's, like every store's."""
    home = Path(settings.home)
    stars_on = bool(getattr(settings, "catalog_stars", True))

    @app.get("/api/catalogs/{kind}", dependencies=[guard], response_model=CatalogOut)
    async def read_catalog(kind: CatalogKind) -> CatalogOut:
        """One catalogue: its items, the pinned commit, and why it is empty or partial if it is."""
        try:
            return await run_in_threadpool(catalog_view, kind, home, stars=stars_on)
        except Exception as exc:  # noqa: BLE001 -- the screen gets a sentence, never a 500
            log.warning("reading the %s catalogue failed: %s", kind, exc)
            return CatalogOut(kind=kind, repo="", sha="", available=False,
                              reason=f"the {kind} catalogue could not be read on this machine")


__all__ = ["catalog_view", "register_catalogs_api"]
