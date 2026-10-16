"""Owner-managed, local read-only references for foreground agents."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, params
from pydantic import BaseModel

from chimera.config import Settings
from chimera.tools.references import load_references, save_references


class ReferencesIn(BaseModel):
    references: dict[str, dict[str, Any]]


class ReferencesOut(BaseModel):
    references: dict[str, dict[str, str]]


def register_references_api(app: FastAPI, guard: params.Depends, settings: Settings) -> None:
    """Mount the owner-only references editor; bridge callers have no route to these decisions."""
    home = Path(settings.home)

    @app.get("/api/references", dependencies=[guard], response_model=ReferencesOut)
    def references_get() -> dict[str, Any]:
        return {"references": load_references(home)}

    @app.put("/api/references", dependencies=[guard], response_model=ReferencesOut)
    def references_put(body: ReferencesIn) -> dict[str, Any]:
        try:
            return {"references": save_references(home, body.references)}
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
