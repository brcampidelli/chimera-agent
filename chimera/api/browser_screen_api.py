"""The routes of browser screen pairing (`chimera/api/browser_screen.py`).

Four of the five are the desktop app's: minting a code, listing the paired browsers, revoking one and
revoking all. Each is refused to anything that is not the desktop's own screen — the bridge, a paired
device, a browser session, and a screen the page itself offers. The fifth, the exchange, is what a
browser tab calls with the code the owner read off the desktop: it needs the owner's bearer token like
every route (a loopback page carries it), and it refuses the bridge and a device outright.

Every route sits under ``/api/security/``, which a paired device may never reach (``devices.py``'s
``_NEVER``), and none is in the desktop bridge's table (``bridge_routes.ROUTES``), so Claude cannot
call any of them. A response never carries a code, a secret or a digest, except the one code that the
mint answers, once, to the desktop.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response, params
from pydantic import BaseModel, Field

from chimera.api.bridge_routes import VIA_BRIDGE_SCOPE_KEY
from chimera.api.browser_screen import (
    BAD_CODE_TEXT,
    COOKIE_NAME,
    BrowserScreens,
    desktop_screen_refusal,
)
from chimera.api.devices import CODE_SECONDS, DEVICE_SCOPE_KEY


class BrowserScreenCodeOut(BaseModel):
    code: str
    """``ABCD-EFGH``. Shown once, to the desktop, valid for five minutes, and used up by one exchange."""
    expires_at: float
    seconds: int = CODE_SECONDS


class BrowserScreenSessionOut(BaseModel):
    id: str
    name: str
    created_at: float
    last_used_at: float
    last_ip: str = ""
    """A network, never the host (``192.168.1.x``)."""
    expires_at: float


class BrowserScreenSessionsOut(BaseModel):
    sessions: list[BrowserScreenSessionOut]
    seconds: int
    """How long a browser stays the owner's screen once paired."""


class BrowserScreenExchangeIn(BaseModel):
    code: str = Field(min_length=1, max_length=32)


class BrowserScreenExchangeOut(BaseModel):
    ok: bool
    expires_at: float
    name: str


class BrowserScreenRevokedOut(BaseModel):
    ok: bool = True
    revoked: int = 0


def _desktop_only(request: Request) -> None:
    """Refuse every caller that is not the desktop app's own screen, with the reason."""
    reason = desktop_screen_refusal(request.scope)
    if reason:
        raise HTTPException(status_code=403, detail=reason)


def _exchange_refusal(request: Request) -> str:
    """The bridge and a device never exchange: a code is typed by the owner into a browser, not by them."""
    if request.scope.get(VIA_BRIDGE_SCOPE_KEY):
        return "the bridge cannot pair a browser"
    if request.scope.get(DEVICE_SCOPE_KEY):
        return "a paired device cannot pair a browser"
    return ""


def register_browser_screen_api(
    app: FastAPI, guard: params.Depends, *, screens: BrowserScreens
) -> None:
    @app.post(
        "/api/security/browser-screen/code",
        dependencies=[guard],
        response_model=BrowserScreenCodeOut,
    )
    def mint_code(request: Request) -> dict[str, Any]:
        """A one-time code for a browser tab, made only from the desktop app's screen."""
        _desktop_only(request)
        code, expires = screens.mint()
        return {"code": code, "expires_at": expires, "seconds": CODE_SECONDS}

    @app.post(
        "/api/security/browser-screen/exchange",
        dependencies=[guard],
        response_model=BrowserScreenExchangeOut,
    )
    def exchange_code(
        body: BrowserScreenExchangeIn, request: Request, response: Response
    ) -> dict[str, Any]:
        """Trade the code the owner read off the desktop for a cookie that makes this tab a screen.

        Wrong, expired, used up and too-many-tries all answer the same, so the response tells a guesser
        nothing it did not already know. The cookie is HttpOnly and SameSite=Strict: a page script cannot
        read it, and a cross-site request does not carry it.
        """
        refusal = _exchange_refusal(request)
        if refusal:
            raise HTTPException(status_code=403, detail=refusal)
        address = request.client.host if request.client else ""
        result = screens.exchange(
            body.code,
            user_agent=request.headers.get("user-agent", ""),
            address=address,
        )
        if result is None:
            raise HTTPException(status_code=403, detail=BAD_CODE_TEXT)
        session, cookie_value = result
        response.set_cookie(
            COOKIE_NAME,
            cookie_value,
            max_age=screens.seconds,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            path="/",
        )
        return {"ok": True, "expires_at": session.expires_at, "name": session.name}

    @app.get(
        "/api/security/browser-screen/sessions",
        dependencies=[guard],
        response_model=BrowserScreenSessionsOut,
    )
    def list_sessions(request: Request) -> dict[str, Any]:
        """The browsers paired as the owner's screen, newest use first. The desktop's list only."""
        _desktop_only(request)
        return {
            "sessions": [s.out() for s in screens.sessions()],
            "seconds": screens.seconds,
        }

    @app.delete(
        "/api/security/browser-screen/sessions/{session_id}",
        dependencies=[guard],
        response_model=BrowserScreenRevokedOut,
    )
    def revoke_session(session_id: str, request: Request) -> dict[str, Any]:
        """Revoke one browser. Its screen goes at its very next request."""
        _desktop_only(request)
        gone = screens.revoke(session_id)
        return {"ok": True, "revoked": 1 if gone else 0}

    @app.delete(
        "/api/security/browser-screen/sessions",
        dependencies=[guard],
        response_model=BrowserScreenRevokedOut,
    )
    def revoke_all_sessions(request: Request) -> dict[str, Any]:
        """Revoke every paired browser at once."""
        _desktop_only(request)
        return {"ok": True, "revoked": screens.revoke_all()}
