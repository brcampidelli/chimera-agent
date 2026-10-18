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


def register_catalogs_api(app: FastAPI, guard: params.Depends, settings: Settings) -> None:
    """Mount the catalogue reads. ``home`` is the launch photograph's, like every store's."""
    home = Path(settings.home)

    @app.get("/api/catalogs/{kind}", dependencies=[guard], response_model=CatalogOut)
    async def read_catalog(kind: CatalogKind) -> CatalogOut:
        """One catalogue: its items, the pinned commit, and why it is empty or partial if it is."""
        from chimera.catalogs.loader import load_catalog

        try:
            return await run_in_threadpool(load_catalog, kind, home)
        except Exception as exc:  # noqa: BLE001 -- the screen gets a sentence, never a 500
            log.warning("reading the %s catalogue failed: %s", kind, exc)
            return CatalogOut(kind=kind, repo="", sha="", available=False,
                              reason=f"the {kind} catalogue could not be read on this machine")


__all__ = ["register_catalogs_api"]
