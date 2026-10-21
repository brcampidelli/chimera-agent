"""Read-only status for the owner's locally paired Chrome browser."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request, params
from pydantic import BaseModel

from chimera.api.bridge_routes import VIA_BRIDGE_SCOPE_KEY
from chimera.api.devices import DEVICE_SCOPE_KEY
from chimera.tools.browser_reach import TRUSTED_SITES_ENV, parse_sites


class BrowserLinkOut(BaseModel):
    paired: bool
    connected: bool


class BrowserStoppedOut(BaseModel):
    stopped: bool


class BrowserUnpairedOut(BaseModel):
    paired: bool


class TrustedSiteIn(BaseModel):
    host: str


class TrustedSiteRemoveIn(BaseModel):
    host: str


class TrustedSitesOut(BaseModel):
    trusted_sites: list[str]


def register_browser_api(app: FastAPI, guard: params.Depends, link: Any) -> None:
    @app.get("/api/browser/link", dependencies=[guard], response_model=BrowserLinkOut)
    def browser_link_status(request: Request) -> dict[str, bool]:
        if request.scope.get(VIA_BRIDGE_SCOPE_KEY):
            raise HTTPException(status_code=403, detail="Chrome pairing status is the owner's")
        if request.scope.get(DEVICE_SCOPE_KEY):
            raise HTTPException(status_code=403, detail="a paired device cannot read Chrome pairing status")
        return {"paired": link.paired, "connected": link.connected()}

    @app.post("/api/browser/link/stop", dependencies=[guard], response_model=BrowserStoppedOut)
    def stop_browser_link(request: Request) -> dict[str, bool]:
        _owner_only(request)
        if not link.connected():
            return {"stopped": False}
        try:
            reply = link.transport().send({"id": "e3-stop", "op": "close"})
        except (OSError, RuntimeError, TimeoutError, ValueError):
            return {"stopped": False}
        if reply.get("ok") is not True or "error" in reply:
            return {"stopped": False}
        link.stop()
        return {"stopped": True}

    @app.post("/api/browser/link/unpair", dependencies=[guard], response_model=BrowserUnpairedOut)
    def unpair_browser_link(request: Request) -> dict[str, bool]:
        _owner_only(request)
        link.unpair()
        return {"paired": False}

    @app.get("/api/browser/trusted-sites", dependencies=[guard], response_model=TrustedSitesOut)
    def list_browser_trusted_sites(request: Request) -> dict[str, list[str]]:
        if request.scope.get(VIA_BRIDGE_SCOPE_KEY):
            raise HTTPException(status_code=403, detail="Chrome settings belong to the owner")
        if request.scope.get(DEVICE_SCOPE_KEY):
            raise HTTPException(status_code=403, detail="a paired device cannot read Chrome settings")
        from chimera.config import get_settings

        return {"trusted_sites": list(parse_sites(get_settings().browser_trusted_sites, trusted=True))}

    @app.post("/api/browser/trusted-sites", dependencies=[guard], response_model=TrustedSitesOut)
    def add_browser_trusted_site(request: Request, body: TrustedSiteIn) -> dict[str, list[str]]:
        _owner_only(request)
        try:
            if "*" in body.host:
                raise ValueError("a specific hostname is required")
            host = parse_sites(body.host, trusted=True)
            if len(host) != 1:
                raise ValueError("one hostname is required")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        from chimera.api.config_api import patch_config
        from chimera.config import get_settings

        settings = get_settings()
        sites = list(parse_sites(settings.browser_trusted_sites, trusted=True))
        if host[0] not in sites:
            sites.append(host[0])
        patch_config({TRUSTED_SITES_ENV: ",".join(sites)})
        return {"trusted_sites": sites}

    @app.delete("/api/browser/trusted-sites", dependencies=[guard], response_model=TrustedSitesOut)
    def remove_browser_trusted_site(request: Request, body: TrustedSiteRemoveIn) -> dict[str, list[str]]:
        _owner_only(request)
        try:
            if "*" in body.host:
                raise ValueError("a specific hostname is required")
            host = parse_sites(body.host, trusted=True)
            if len(host) != 1:
                raise ValueError("one hostname is required")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        from chimera.api.config_api import patch_config
        from chimera.config import get_settings

        settings = get_settings()
        sites = [site for site in parse_sites(settings.browser_trusted_sites, trusted=True) if site != host[0]]
        patch_config({TRUSTED_SITES_ENV: ",".join(sites)})
        return {"trusted_sites": sites}


def _owner_only(request: Request) -> None:
    if request.scope.get(VIA_BRIDGE_SCOPE_KEY):
        raise HTTPException(status_code=403, detail="Chrome settings belong to the owner")
    if request.scope.get(DEVICE_SCOPE_KEY):
        raise HTTPException(status_code=403, detail="a paired device cannot change Chrome settings")
