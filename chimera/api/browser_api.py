"""Read-only status for the owner's locally paired Chrome browser."""

from __future__ import annotations

import ipaddress
import threading
from typing import Any

from fastapi import FastAPI, HTTPException, Request, params
from pydantic import BaseModel

from chimera.api import screen_credential
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
        return {"trusted_sites": _saved_sites()}

    @app.post("/api/browser/trusted-sites", dependencies=[guard], response_model=TrustedSitesOut)
    def add_browser_trusted_site(request: Request, body: TrustedSiteIn) -> dict[str, list[str]]:
        _owner_only(request)
        host = _one_host(body.host, allow_wildcard=False)
        from chimera.api.config_api import patch_config

        with _LIST_LOCK:
            sites = _saved_sites()
            if host not in sites:
                sites.append(host)
            patch_config({TRUSTED_SITES_ENV: ",".join(sites)})
        return {"trusted_sites": sites}

    @app.delete("/api/browser/trusted-sites", dependencies=[guard], response_model=TrustedSitesOut)
    def remove_browser_trusted_site(request: Request, body: TrustedSiteRemoveIn) -> dict[str, list[str]]:
        _owner_only(request)
        # A wildcard can be removed (it only ever narrows the list), never added from here.
        host = _one_host(body.host, allow_wildcard=True)
        from chimera.api.config_api import patch_config

        with _LIST_LOCK:
            sites = [site for site in _saved_sites() if site != host]
            patch_config({TRUSTED_SITES_ENV: ",".join(sites)})
        return {"trusted_sites": sites}


# Read-modify-write of the list: two clicks at once must not drop an entry.
_LIST_LOCK = threading.Lock()


def _saved_sites() -> list[str]:
    """The trusted list as saved; a hand-edited value that does not parse is a 409 with the parser's
    reason, never a 500 — and never read as an empty list the next write would overwrite."""
    from chimera.config import get_settings

    try:
        return list(parse_sites(get_settings().browser_trusted_sites, trusted=True))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _one_host(raw: str, *, allow_wildcard: bool) -> str:
    """One hostname from the card or the screen, or a 400. A loopback name or an IP literal is
    refused: trusting it would take the card off every local port the owner declared."""
    try:
        if "*" in raw and not allow_wildcard:
            raise ValueError("a specific hostname is required")
        hosts = parse_sites(raw, trusted=True)
        if len(hosts) != 1:
            raise ValueError("one hostname is required")
        host = hosts[0]
        bare = host[2:] if host.startswith("*.") else host
        if bare == "localhost" or bare.endswith(".localhost"):
            raise ValueError("a local name cannot be a trusted site")
        try:
            ipaddress.ip_address(bare)
        except ValueError:
            pass
        else:
            raise ValueError("an IP address cannot be a trusted site")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return host


def _owner_only(request: Request) -> None:
    if request.scope.get(VIA_BRIDGE_SCOPE_KEY):
        raise HTTPException(status_code=403, detail="Chrome settings belong to the owner")
    if request.scope.get(DEVICE_SCOPE_KEY):
        raise HTTPException(status_code=403, detail="a paired device cannot change Chrome settings")
    # The server token is a file any local process reads, the agent's own shell included, so it
    # alone must not be able to trust a site for itself (no card on that site from then on) or
    # unpair the Chrome. Same rule as answering an approval: the click came from the app's screen.
    if not request.scope.get(screen_credential.SCOPE_KEY):
        raise HTTPException(status_code=403, detail="Chrome settings are changed from the app's screen")
