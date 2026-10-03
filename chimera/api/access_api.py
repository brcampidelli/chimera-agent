"""Every way into this machine, on one card: ``GET /api/security/access`` and its four controls.

The doors existed; a view of them did not. The bearer token is a row on the General tab, the
bridge's token was recreated whenever its switch went on and was visible nowhere, share links were
listed one conversation at a time in each conversation's Share dialog, and the LAN door showed only
inside that same dialog. A person asking "who can reach this machine right now?" had to open every
conversation to answer it — so nobody did, and a link made for a colleague last month went on
opening its conversation for as long as the file existed.

This module READS the stores that already hold each door and offers the owner the controls that
only NARROW: revoke one link, revoke all of them, and give the bridge a new token. Closing the LAN
door is the existing ``DELETE /api/code/share/network``. Nothing here opens a door or makes a token.

**No response here carries a token.** Each secret is reported as a fact (``set``) or, at most, as
the last four characters — the same rule ``config_api._hint`` applies to provider keys. A link is
revoked by its :attr:`~chimera.api.sharing.Share.id`, a digest of the token, so the token never has
to travel back to the screen for the screen to act on it. The bridge never lists these routes
(``bridge_routes.ROUTES``), so a client operating the app through the bridge cannot read the card
or rotate its own token.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException, params
from pydantic import BaseModel, Field

from chimera.api.desktop_bridge import DesktopBridge
from chimera.api.guest_api import GuestServer
from chimera.api.sharing import Share, ShareStore
from chimera.telemetry import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from chimera.config import Settings

_log = get_logger("api.access")


class AccessServerOut(BaseModel):
    """The app's own listener: where ``chimera desktop`` bound it.

    ``chimera desktop --host 0.0.0.0`` is a supported option, and then the guest app mounted at
    ``/guest`` on this listener answers the network with any share link — a network door the card
    used to call "Closed" because it looked only at the separate LAN listener."""

    bind: str | None = None
    """The host the app listens on; None when this process was not started by ``chimera desktop``
    (a test, or an app embedded elsewhere) and so does not know."""
    port: int | None = None
    network: bool = False
    """True when ``bind`` is not a loopback address: a machine on the network can reach this
    listener, the share links under ``/guest`` included."""


class AccessServerTokenOut(BaseModel):
    """The bearer every guarded route asks for. Whether it is set, and nothing of its value — the
    Settings row it is changed on reports it the same way (``ServerCfgOut.token_set``)."""

    set: bool = False


class AccessBridgeOut(BaseModel):
    """The desktop bridge (``chimera/api/desktop_bridge.py``)."""

    enabled: bool = False
    """The owner's switch (``CHIMERA_DESKTOP_BRIDGE``)."""
    active: bool = False
    """Whether a token is live right now — the switch on AND the app listening."""
    tier: str | None = None
    """``operate`` or ``full`` while active; None otherwise."""
    hint: str = ""
    """The last four characters of the live token, so a rotation is visible; empty while off."""


class AccessSharingOut(BaseModel):
    """The two settings that narrow sharing (``CHIMERA_SHARING``, ``CHIMERA_SHARE_EXPIRY_HOURS``)."""

    enabled: bool = True
    expiry_hours: float | None = None
    """How long a NEW link opens its conversation; None is never."""


class AccessLinkOut(BaseModel):
    """One share link, named by everything except itself."""

    id: str
    """A digest of the token — what ``DELETE /api/security/access/links/{id}`` takes."""
    session_id: str
    session_title: str = ""
    """The title the sidebar shows; empty when the conversation has none or is gone."""
    label: str = ""
    created_at: float
    expires_at: float | None = None
    expired: bool = False
    """It no longer opens anything; listed so the owner sees it expired rather than vanished."""
    hint: str = ""
    """The last four characters of the token, to tell two links of one label apart."""


class AccessGuestDoorOut(BaseModel):
    """The LAN listener that serves only the guest app (``guest_api.GuestServer``)."""

    open: bool = False
    port: int | None = None
    urls: list[str] = Field(default_factory=list)
    """Addresses without any token in them: the door, not a key to it."""


class AccessOut(BaseModel):
    server: AccessServerOut
    server_token: AccessServerTokenOut
    bridge: AccessBridgeOut
    sharing: AccessSharingOut
    links: list[AccessLinkOut]
    guest_door: AccessGuestDoorOut


class AccessRevokedOut(BaseModel):
    revoked: int


class AccessRevokeOneOut(BaseModel):
    ok: bool


def _bridge_out(bridge: DesktopBridge, settings: Settings) -> dict[str, Any]:
    tier = bridge.tier()
    return {
        "enabled": bool(settings.desktop_bridge),
        "active": tier is not None,
        "tier": tier,
        "hint": bridge.hint(),
    }


#: Where the app's listener is bound, as ``(host, port)``; None when nobody said.
BoundAddress = Callable[[], tuple[str, int] | None]


def _unbound() -> tuple[str, int] | None:
    return None


def is_loopback_host(host: str) -> bool:
    """Whether a bind host reaches only this machine. A name other than ``localhost`` is not
    assumed to: the card would rather call a door open than call an open one shut."""
    if host.strip().lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host.strip().strip("[]")).is_loopback
    except ValueError:
        return False


def _server_out(bound: BoundAddress) -> dict[str, Any]:
    address = bound()
    if address is None:
        return {"bind": None, "port": None, "network": False}
    host, port = address
    return {"bind": host, "port": port, "network": not is_loopback_host(host)}


def _link_out(share: Share, titles: dict[str, str]) -> dict[str, Any]:
    return {
        "id": share.id,
        "session_id": share.session_id,
        "session_title": titles.get(share.session_id, ""),
        "label": share.label,
        "created_at": share.created_at,
        "expires_at": share.expires_at,
        # The store's own clock, the one `resolve` refuses by: the card and the door must agree.
        "expired": share.expired(),
        "hint": share.hint,
    }


def register_access_api(
    app: FastAPI,
    guard: params.Depends,
    *,
    bridge: DesktopBridge,
    store: ShareStore,
    door: GuestServer,
    live_settings: Callable[[], Settings],
    session_titles: Callable[[], dict[str, str]] = dict,
    bound: BoundAddress = _unbound,
) -> None:
    """Mount ``/api/security/access`` and its narrowing controls behind the server's guard.

    ``bound`` reports where the app's own listener is bound — read per request, because the CLI
    binds the socket after the app is built."""

    def _titles() -> dict[str, str]:
        # A title is a nicety on this card, not a reason for it to fail: a conversation file that
        # cannot be read leaves its link listed by id instead of hiding the link.
        try:
            return session_titles()
        except Exception as exc:  # noqa: BLE001 -- the doors matter more than their labels
            _log.warning("could not read conversation titles for the access card: %s", exc)
            return {}

    @app.get("/api/security/access", dependencies=[guard], response_model=AccessOut)
    def access() -> dict[str, Any]:
        """Every way into this machine right now, with no secret in the answer."""
        settings = live_settings()
        titles = _titles()
        return {
            "server": _server_out(bound),
            "server_token": {"set": bool(settings.server_token)},
            "bridge": _bridge_out(bridge, settings),
            "sharing": {
                "enabled": bool(settings.sharing),
                "expiry_hours": settings.share_expiry_hours,
            },
            # Newest first: the link made a minute ago is the one the owner came to check.
            "links": [_link_out(s, titles) for s in reversed(store.all())],
            "guest_door": {"open": door.open, "port": door.port, "urls": door.urls()},
        }

    @app.post(
        "/api/security/access/bridge/rotate", dependencies=[guard], response_model=AccessBridgeOut
    )
    def rotate_bridge() -> dict[str, Any]:
        """A new bridge token; the old one stops working now. 409 while the bridge is off."""
        if not bridge.rotate():
            raise HTTPException(status_code=409, detail="the desktop bridge is off")
        _log.info("desktop bridge token rotated from the access card")
        return _bridge_out(bridge, live_settings())

    @app.delete(
        "/api/security/access/links/{link_id}",
        dependencies=[guard],
        response_model=AccessRevokeOneOut,
    )
    def revoke_link(link_id: str) -> dict[str, bool]:
        return {"ok": store.revoke_id(link_id) is not None}

    @app.delete("/api/security/access/links", dependencies=[guard], response_model=AccessRevokedOut)
    def revoke_links(session_id: str = "") -> dict[str, int]:
        """Every link, or every link of one conversation when ``session_id`` names it."""
        gone = store.revoke_session(session_id) if session_id else store.revoke_all()
        if gone:
            _log.info("revoked %d share link(s) from the access card", gone)
        return {"revoked": gone}
