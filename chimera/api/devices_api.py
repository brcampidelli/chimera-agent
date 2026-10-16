"""The owner's controls for paired devices: open the door, mint a code, list, revoke (S3, C10).

Behind the owner's token, like every route on the Access card, and refused to the two callers that
must never hand out access: the desktop bridge (Claude operating the app — these routes are not in
``bridge_routes.ROUTES``, and a request the bridge marked is refused here as well) and a paired device
itself (``devices.device_may_reach`` keeps the door from forwarding them; refused here too). A
response never carries a device's cookie; the one secret any of them returns is the pairing code,
to the owner who asked for it, valid five minutes, once.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request, params
from pydantic import BaseModel, Field

from chimera.api.bridge_routes import VIA_BRIDGE_SCOPE_KEY
from chimera.api.device_door import DeviceDoor
from chimera.api.devices import CODE_SECONDS, DEVICE_SCOPE_KEY, DeviceRegistry, shown_code
from chimera.telemetry import get_logger

_log = get_logger("api.devices")


class DeviceOut(BaseModel):
    id: str
    name: str
    created_at: float
    last_used_at: float
    last_ip: str = ""
    """A network, never the host (``192.168.1.x``)."""
    expires_at: float


class DeviceDoorOut(BaseModel):
    open: bool = False
    port: int | None = None
    urls: list[str] = Field(default_factory=list)
    """Where a device on the LAN opens the app — no code and no credential in them."""


class DevicesOut(BaseModel):
    door: DeviceDoorOut
    devices: list[DeviceOut]


class DeviceDoorIn(BaseModel):
    port: int = Field(default=0, ge=0, le=65535)


class PairCodeOut(BaseModel):
    code: str
    """``ABCD-EFGH``. Once, for five minutes, and only through the open door."""
    expires_at: float
    seconds: int = CODE_SECONDS
    urls: list[str]
    """The pairing page with the code filled in, one per LAN address, best first."""
    qr: list[str]
    """The first URL as a QR code: one string of ``1``/``0`` per module row."""


class DeviceRevokedOut(BaseModel):
    ok: bool


class DevicesRevokedOut(BaseModel):
    revoked: int


def _owner_only(request: Request) -> None:
    if request.scope.get(VIA_BRIDGE_SCOPE_KEY):
        raise HTTPException(status_code=403, detail="pairing is the owner's, never the bridge's")
    if request.scope.get(DEVICE_SCOPE_KEY):
        raise HTTPException(status_code=403, detail="a paired device does not pair or revoke devices")


def door_out(door: DeviceDoor) -> dict[str, Any]:
    return {"open": door.listening, "port": door.port, "urls": door.urls()}


def register_devices_api(
    app: FastAPI, guard: params.Depends, *, registry: DeviceRegistry, door: DeviceDoor
) -> None:
    @app.get("/api/security/devices", dependencies=[guard], response_model=DevicesOut)
    def list_devices(request: Request) -> dict[str, Any]:
        _owner_only(request)
        return {"door": door_out(door), "devices": [d.out() for d in registry.devices()]}

    @app.post("/api/security/devices/door", dependencies=[guard], response_model=DeviceDoorOut)
    def open_door(request: Request, body: DeviceDoorIn | None = None) -> dict[str, Any]:
        """Listen on the LAN for paired devices. Never at launch: the owner opens it, each time."""
        _owner_only(request)
        try:
            door.open_door(body.port if body else 0)
        except OSError as exc:
            raise HTTPException(status_code=409, detail=f"could not listen: {exc}") from exc
        _log.info("device door open on port %s", door.port)
        return door_out(door)

    @app.delete("/api/security/devices/door", dependencies=[guard], response_model=DeviceDoorOut)
    def close_door(request: Request) -> dict[str, Any]:
        _owner_only(request)
        door.stop()
        return door_out(door)

    @app.post("/api/security/devices/pair", dependencies=[guard], response_model=PairCodeOut)
    def pair(request: Request) -> dict[str, Any]:
        """A one-time code for the next device. 409 while the door is shut: a code nobody can reach
        the page to type is a code that only waits to be guessed."""
        _owner_only(request)
        if not door.listening:
            raise HTTPException(
                status_code=409,
                detail="open the listener for your devices first (Settings > Access)",
            )
        from chimera.core.qr import encode, to_rows

        code, expires = registry.codes.mint()
        urls = door.pair_urls(code)
        return {
            "code": shown_code(code),
            "expires_at": expires,
            "urls": urls,
            "qr": to_rows(encode(urls[0])) if urls else [],
        }

    @app.delete("/api/security/devices/{device_id}", dependencies=[guard], response_model=DeviceRevokedOut)
    def revoke_device(device_id: str, request: Request) -> dict[str, bool]:
        _owner_only(request)
        return {"ok": registry.revoke(device_id)}

    @app.delete("/api/security/devices", dependencies=[guard], response_model=DevicesRevokedOut)
    def revoke_devices(request: Request) -> dict[str, int]:
        """Every device, and the secret that signed them: no cookie issued before now opens anything."""
        _owner_only(request)
        return {"revoked": registry.revoke_all()}
