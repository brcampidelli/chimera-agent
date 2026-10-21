"""Read-only status for the owner's locally paired Chrome browser."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request, params
from pydantic import BaseModel

from chimera.api.bridge_routes import VIA_BRIDGE_SCOPE_KEY
from chimera.api.devices import DEVICE_SCOPE_KEY


class BrowserLinkOut(BaseModel):
    paired: bool
    connected: bool


def register_browser_api(app: FastAPI, guard: params.Depends, link: Any) -> None:
    @app.get("/api/browser/link", dependencies=[guard], response_model=BrowserLinkOut)
    def browser_link_status(request: Request) -> dict[str, bool]:
        if request.scope.get(VIA_BRIDGE_SCOPE_KEY):
            raise HTTPException(status_code=403, detail="Chrome pairing status is the owner's")
        if request.scope.get(DEVICE_SCOPE_KEY):
            raise HTTPException(status_code=403, detail="a paired device cannot read Chrome pairing status")
        return {"paired": link.paired, "connected": link.connected()}
