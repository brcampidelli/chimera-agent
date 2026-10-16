"""The owner's devices' door: a LAN listener that serves the owner's app to a paired cookie only.

The guest door (``guest_api.GuestServer``) serves the guest app and nothing else. This one serves
two things, on its own port, only while the owner has it open (closed at every start, like the
guest door, and never 0.0.0.0 unless the owner opened it):

* ``/pair`` — a page with one form and no script: the code (pre-filled when the QR carried it) and
  a name for the device. Posting it redeems the code (``devices.PairingCodes``) and answers with
  the device cookie: ``HttpOnly``, ``SameSite=Strict``, ``Secure`` when the page came over https,
  thirty days, this host only.
* everything else — the owner's own app, in this process, for a request whose cookie verifies. The
  request is forwarded with :data:`~chimera.api.devices.DEVICE_SCOPE_KEY` in its ASGI scope, which
  is what the owner's token middleware accepts INSTEAD of the bearer, and with the bearer, the
  screen credential's header and the cookie itself stripped: the owner's app sees a device, never a
  token it could mistake for the owner's screen. What it may reach is
  :func:`~chimera.api.devices.device_may_reach`; a write from another origin is refused here.

A revoke reaches a request that is already running: every response frame checks the device again,
and a watcher ends the request within a second, so an open live stream does not outlive its device.
"""

from __future__ import annotations

import html
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, parse_qsl, quote, unquote, urlsplit

import anyio

from chimera.api.devices import (
    COOKIE_NAME,
    COOKIE_SECONDS,
    DEVICE_SCOPE_KEY,
    DeviceRegistry,
    clean_name,
    device_may_reach,
    device_place_refusal,
    name_from_agent,
    shown_code,
)
from chimera.api.guest_api import GuestServer
from chimera.api.screen_credential import HEADER_NAME as SCREEN_HEADER
from chimera.api.sharing import lan_addresses

ASGIApp = Callable[..., Any]

#: The page's policy: no script at all, a form that posts to itself, no framing.
PAIR_PAGE_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
    "frame-ancestors 'none'; base-uri 'none'"
)
_MAX_FORM_BYTES = 4096
#: Stripped from every forwarded request: the owner's app must see a device and nothing that could
#: pass for the owner's token or the owner's screen.
_STRIPPED = {b"authorization", b"cookie", SCREEN_HEADER.encode("latin-1")}

#: The pairing page's words, by the app's languages; the browser's first match wins, English last.
_WORDS: dict[str, dict[str, str]] = {
    "en": {
        "title": "Pair this device",
        "lead": "Type the code shown in Chimera (Settings › Access, or `chimera pair`).",
        "code": "Code", "name": "Name for this device", "submit": "Pair",
        "bad": "That code did not work. It may have expired or been used; ask for a new one.",
        "slow": "Too many wrong codes from this address. Wait a few minutes.",
    },
    "pt": {
        "title": "Parear este dispositivo",
        "lead": "Digite o código mostrado no Chimera (Configurações › Acesso, ou `chimera pair`).",
        "code": "Código", "name": "Nome deste dispositivo", "submit": "Parear",
        "bad": "Esse código não funcionou. Pode ter expirado ou já ter sido usado; peça um novo.",
        "slow": "Códigos errados demais a partir deste endereço. Espere alguns minutos.",
    },
    "es": {
        "title": "Vincular este dispositivo",
        "lead": "Escribe el código que muestra Chimera (Ajustes › Acceso, o `chimera pair`).",
        "code": "Código", "name": "Nombre de este dispositivo", "submit": "Vincular",
        "bad": "Ese código no funcionó. Puede haber caducado o ya se usó; pide uno nuevo.",
        "slow": "Demasiados códigos erróneos desde esta dirección. Espera unos minutos.",
    },
    "fr": {
        "title": "Associer cet appareil",
        "lead": "Saisissez le code affiché dans Chimera (Réglages › Accès, ou `chimera pair`).",
        "code": "Code", "name": "Nom de cet appareil", "submit": "Associer",
        "bad": "Ce code n'a pas fonctionné. Il a peut-être expiré ou déjà servi ; demandez-en un autre.",
        "slow": "Trop de codes erronés depuis cette adresse. Patientez quelques minutes.",
    },
    "de": {
        "title": "Dieses Gerät koppeln",
        "lead": "Gib den Code ein, den Chimera anzeigt (Einstellungen › Zugriff oder `chimera pair`).",
        "code": "Code", "name": "Name dieses Geräts", "submit": "Koppeln",
        "bad": "Dieser Code hat nicht funktioniert. Er ist abgelaufen oder verbraucht; fordere einen neuen an.",
        "slow": "Zu viele falsche Codes von dieser Adresse. Warte ein paar Minuten.",
    },
    "it": {
        "title": "Associa questo dispositivo",
        "lead": "Inserisci il codice mostrato in Chimera (Impostazioni › Accesso, o `chimera pair`).",
        "code": "Codice", "name": "Nome di questo dispositivo", "submit": "Associa",
        "bad": "Il codice non ha funzionato. Potrebbe essere scaduto o già usato; chiedine uno nuovo.",
        "slow": "Troppi codici errati da questo indirizzo. Attendi qualche minuto.",
    },
    "pl": {
        "title": "Sparuj to urządzenie",
        "lead": "Wpisz kod pokazany w Chimerze (Ustawienia › Dostęp lub `chimera pair`).",
        "code": "Kod", "name": "Nazwa tego urządzenia", "submit": "Sparuj",
        "bad": "Ten kod nie zadziałał. Mógł wygasnąć lub zostać użyty; poproś o nowy.",
        "slow": "Zbyt wiele błędnych kodów z tego adresu. Odczekaj kilka minut.",
    },
    "zh": {
        "title": "配对此设备",
        "lead": "输入 Chimera 显示的代码（设置 › 访问，或 `chimera pair`）。",
        "code": "代码", "name": "此设备的名称", "submit": "配对",
        "bad": "该代码无效。它可能已过期或已被使用；请重新获取。",
        "slow": "此地址输入错误代码次数过多。请等待几分钟。",
    },
    "ja": {
        "title": "このデバイスをペアリング",
        "lead": "Chimera に表示されたコードを入力してください（設定 › アクセス、または `chimera pair`）。",
        "code": "コード", "name": "このデバイスの名前", "submit": "ペアリング",
        "bad": "このコードは使えませんでした。期限切れか使用済みです。新しいコードを発行してください。",
        "slow": "このアドレスからの誤ったコードが多すぎます。数分待ってください。",
    },
    "ru": {
        "title": "Подключить это устройство",
        "lead": "Введите код, показанный в Chimera (Настройки › Доступ или `chimera pair`).",
        "code": "Код", "name": "Название устройства", "submit": "Подключить",
        "bad": "Код не подошёл. Возможно, он истёк или уже использован; запросите новый.",
        "slow": "Слишком много неверных кодов с этого адреса. Подождите несколько минут.",
    },
}


def _language(accept: str) -> str:
    for part in (accept or "").split(","):
        tag = part.split(";", 1)[0].strip().lower()
        if tag[:2] in _WORDS:
            return tag[:2]
    return "en"


def pair_page(*, code: str = "", name: str = "", error: str = "", accept_language: str = "") -> str:
    lang = _language(accept_language)
    words = _WORDS[lang]
    e = html.escape
    message = f'<p role="alert" class="bad">{e(words[error])}</p>' if error in ("bad", "slow") else ""
    return f"""<!doctype html>
<html lang="{lang}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(words["title"])} · Chimera</title>
<style>
:root{{color-scheme:light dark;font-family:system-ui,sans-serif}}
body{{margin:0;padding:24px 16px;max-width:420px;margin-inline:auto;line-height:1.45}}
h1{{font-size:1.35rem}} label{{display:block;margin-top:16px;font-size:.9rem}}
input{{box-sizing:border-box;width:100%;padding:10px;font-size:1.1rem;margin-top:6px}}
.code{{letter-spacing:.15em;text-transform:uppercase;font-family:ui-monospace,monospace}}
button{{margin-top:20px;width:100%;padding:12px;font-size:1rem}} .bad{{font-weight:600}}
</style></head><body>
<h1>{e(words["title"])}</h1><p>{e(words["lead"])}</p>{message}
<form method="post" action="/pair" autocomplete="off">
<label>{e(words["code"])}<input class="code" name="code" required maxlength="12" value="{e(code)}"
 autocapitalize="characters" spellcheck="false"></label>
<label>{e(words["name"])}<input name="name" maxlength="40" value="{e(name)}"></label>
<button type="submit">{e(words["submit"])}</button>
</form></body></html>"""


def _headers(scope: Any) -> dict[bytes, bytes]:
    out: dict[bytes, bytes] = {}
    for key, value in scope.get("headers") or []:
        out.setdefault(key.lower(), value)
    return out


def _cookie(headers: dict[bytes, bytes]) -> str:
    raw = headers.get(b"cookie", b"").decode("latin-1")
    for part in raw.split(";"):
        name, _, value = part.strip().partition("=")
        if name == COOKIE_NAME:
            return value
    return ""


def _client(scope: Any) -> str:
    client = scope.get("client")
    return str(client[0]) if client else ""


def _https(scope: Any, headers: dict[bytes, bytes]) -> bool:
    # A tunnel (Tailscale Serve, Cloudflare Tunnel) ends TLS before this listener. Trusting the
    # header can only ADD `Secure`, which a liar would only be locking themselves out with.
    forwarded = headers.get(b"x-forwarded-proto", b"").decode("latin-1").lower()
    return scope.get("scheme") == "https" or forwarded == "https"


def _same_origin(headers: dict[bytes, bytes]) -> bool:
    """A write whose ``Origin`` names another site is refused; one without it (no browser sends a
    cross-site POST without it today) rests on ``SameSite=Strict``."""
    origin = headers.get(b"origin", b"").decode("latin-1")
    if not origin:
        return True
    host = headers.get(b"host", b"").decode("latin-1").lower()
    try:
        return urlsplit(origin).netloc.lower() == host
    except ValueError:
        return False


async def _send_simple(
    send: Any,
    status: int,
    body: bytes,
    content_type: bytes,
    extra: list[tuple[bytes, bytes]] | None = None,
) -> None:
    headers = [
        (b"content-type", content_type),
        (b"content-length", str(len(body)).encode("latin-1")),
        (b"cache-control", b"no-store"),
        *(extra or []),
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


#: A device's JSON body — a message, an answer — is text a person typed on a phone.
_MAX_JSON_BYTES = 2 * 1024 * 1024


def _body_fields(raw: bytes) -> list[tuple[str, str]]:
    """Every string field of a JSON body, at any depth, as ``(key, value)``."""
    try:
        data = json.loads(raw) if raw else None
    except ValueError:
        return []
    out: list[tuple[str, str]] = []

    def walk(node: Any, key: str) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, str(k))
        elif isinstance(node, list):
            for item in node:
                walk(item, key)
        elif isinstance(node, str) and key.lower() in _BODY_PLACE_KEYS:
            out.append((key, node))

    walk(data, "")
    return out


#: Only the fields that name a place: a message's own text may mention `.env` freely.
_BODY_PLACE_KEYS = frozenset({"workspace", "path", "paths", "file", "dir", "root", "cwd", "folder"})


def _replay(body: bytes, receive: Any) -> Any:
    sent = False

    async def again() -> Any:
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return again


async def _read_body(receive: Any, limit: int = _MAX_FORM_BYTES) -> bytes:
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message.get("type") != "http.request":
            break
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > limit:
            raise ValueError("body too large")
        chunks.append(chunk)
        if not message.get("more_body"):
            break
    return b"".join(chunks)


class _Revoked(Exception):
    """The device was revoked while its request was running."""


class _Scrubbing:
    """Every answer to a device passes the bridge's own nets (`bridge_routes.scrub`).

    A JSON answer is held until it is complete, then sent with credential-named fields dropped and
    credential-shaped strings masked — the owner's token and the screen credential by value, encoded
    copies included. A text stream (a turn's events) is masked chunk by chunk. Anything else (an
    image, a download) passes untouched: it is not text to mask.
    """

    def __init__(self, send: Any, secrets: list[str]) -> None:
        self._send = send
        self._secrets = [s for s in secrets if s]
        self._start: dict[str, Any] | None = None
        self._kind = ""
        self._held: list[bytes] = []

    async def __call__(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == "http.response.start":
            ctype = ""
            for key, value in message.get("headers") or []:
                if key.lower() == b"content-type":
                    ctype = value.decode("latin-1").lower()
            self._kind = "json" if "json" in ctype else "text" if ctype.startswith("text/") else ""
            if self._kind == "json":
                self._start = message
                return
            await self._send(message)
            return
        if kind != "http.response.body" or not self._kind:
            await self._send(message)
            return
        if self._kind == "text":
            body = message.get("body", b"")
            if body:
                masked = _mask_text(body.decode("utf-8", "replace"), self._secrets).encode("utf-8")
                message = {**message, "body": masked}
            await self._send(message)
            return
        self._held.append(message.get("body", b""))
        if message.get("more_body"):
            return
        raw = b"".join(self._held)
        try:
            from chimera.api.bridge_routes import scrub

            out = json.dumps(scrub(json.loads(raw), self._secrets)).encode("utf-8")
        except ValueError:
            out = _mask_text(raw.decode("utf-8", "replace"), self._secrets).encode("utf-8")
        start = self._start or {"type": "http.response.start", "status": 200, "headers": []}
        headers = [(k, v) for k, v in start.get("headers") or [] if k.lower() != b"content-length"]
        headers.append((b"content-length", str(len(out)).encode("latin-1")))
        await self._send({**start, "headers": headers})
        await self._send({"type": "http.response.body", "body": out})


def _mask_text(text: str, secrets: list[str]) -> str:
    from chimera.core import redact as redaction

    if secrets:
        text = redaction.mask_known(text, secrets, encoded=redaction.MASK_ENCODED)
    return redaction.redact(text)


class DeviceGateway:
    """The ASGI app the device door serves (see the module docstring)."""

    def __init__(
        self,
        owner_app: ASGIApp,
        registry: DeviceRegistry,
        *,
        watch_seconds: float = 1.0,
        roots: Callable[[], list[Path]] | None = None,
        secrets: Callable[[], list[str]] | None = None,
    ) -> None:
        self._owner = owner_app
        self._registry = registry
        self._watch = watch_seconds
        # The folders a device's query may not name (data, service, bridge) and the secret values
        # no answer to it may carry (the owner's token, the screen credential) — read per request.
        self._roots = roots or (lambda: [registry.home])
        self._secrets = secrets or list

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") == "lifespan":
            # The owner's app has its own lifespan, run by its own server; this door has none.
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope.get("type") != "http":
            return  # no websocket route is served to a device
        headers = _headers(scope)
        path = scope.get("path") or "/"
        if path in ("/pair", "/pair/"):
            await self._pair(scope, receive, send, headers)
            return
        device = self._registry.verify(_cookie(headers))
        if device is None:
            if path.startswith("/api/"):
                await _send_simple(send, 401, b'{"detail":"not paired"}', b"application/json")
            else:
                await _send_simple(send, 303, b"", b"text/plain", [(b"location", b"/pair")])
            return
        method = str(scope.get("method") or "GET").upper()
        reason = device_may_reach(method, path) if path.startswith(("/api/", "/v1/")) else ""
        if not reason and method not in ("GET", "HEAD") and not _same_origin(headers):
            reason = "a write from another origin"
        if not reason:
            query = parse_qsl((scope.get("query_string") or b"").decode("latin-1"), keep_blank_values=True)
            reason = device_place_refusal([("url", unquote(path)), *query], self._roots())
        held: bytes | None = None
        if not reason and method not in ("GET", "HEAD") and b"json" in headers.get(b"content-type", b""):
            # A JSON body names places too (a turn's `workspace`): read it once, check every
            # path-shaped field at any depth, and hand the same bytes on to the app.
            try:
                held = await _read_body(receive, limit=_MAX_JSON_BYTES)
                reason = device_place_refusal(_body_fields(held), self._roots())
            except ValueError:
                reason = "a request body too large for a paired device"
        if reason:
            body = json.dumps({"detail": reason}).encode("utf-8")
            await _send_simple(send, 403, body, b"application/json")
            return
        if held is not None:
            receive = _replay(held, receive)
        self._registry.touch(device.id, _client(scope))
        forwarded = {
            **scope,
            "headers": [(k, v) for k, v in scope.get("headers") or [] if k.lower() not in _STRIPPED],
            DEVICE_SCOPE_KEY: device.id,
        }
        await self._forward(device.id, forwarded, receive, send)

    async def _forward(self, device_id: str, scope: Any, receive: Any, send: Any) -> None:
        registry = self._registry
        scrubbed = _Scrubbing(send, self._secrets())

        async def checked_send(message: Any) -> None:
            if message.get("type") == "http.response.body" and not registry.is_active(device_id):
                raise _Revoked
            await scrubbed(message)

        try:
            async with anyio.create_task_group() as group:

                async def watch() -> None:
                    while True:
                        await anyio.sleep(self._watch)
                        if not registry.is_active(device_id):
                            group.cancel_scope.cancel()
                            return

                group.start_soon(watch)
                try:
                    await self._owner(scope, receive, checked_send)
                finally:
                    group.cancel_scope.cancel()
        except* _Revoked:
            # The response is cut where it stood; the server closes the connection.
            pass

    async def _pair(self, scope: Any, receive: Any, send: Any, headers: dict[bytes, bytes]) -> None:
        accept = headers.get(b"accept-language", b"").decode("latin-1")
        method = str(scope.get("method") or "GET").upper()
        page = b"text/html; charset=utf-8"
        csp = [(b"content-security-policy", PAIR_PAGE_CSP.encode("latin-1"))]
        if method == "GET":
            query = parse_qs((scope.get("query_string") or b"").decode("latin-1"))
            code = (query.get("code") or [""])[0][:12]
            text = pair_page(code=code, accept_language=accept).encode("utf-8")
            await _send_simple(send, 200, text, page, csp)
            return
        if method != "POST":
            await _send_simple(send, 405, b"", b"text/plain")
            return
        address = _client(scope)
        attempts = self._registry.attempts
        if attempts.blocked(address):
            text = pair_page(error="slow", accept_language=accept).encode("utf-8")
            await _send_simple(send, 429, text, page, csp)
            return
        if not _same_origin(headers):
            await _send_simple(send, 403, b"", b"text/plain")
            return
        try:
            form = parse_qs((await _read_body(receive)).decode("utf-8", "replace"))
        except ValueError:
            await _send_simple(send, 413, b"", b"text/plain")
            return
        code = (form.get("code") or [""])[0]
        user_agent = headers.get(b"user-agent", b"").decode("latin-1")
        name = clean_name((form.get("name") or [""])[0], name_from_agent(user_agent))
        if not self._registry.codes.redeem(code):
            attempts.fail(address)
            text = pair_page(code="", name=name, error="bad", accept_language=accept).encode("utf-8")
            await _send_simple(send, 401, text, page, csp)
            return
        _device, value = self._registry.issue(name=name, address=address)
        flags = f"; Path=/; Max-Age={COOKIE_SECONDS}; HttpOnly; SameSite=Strict"
        if _https(scope, headers):
            flags += "; Secure"
        cookie = f"{COOKIE_NAME}={value}{flags}".encode("latin-1")
        await _send_simple(send, 303, b"", b"text/plain", [(b"set-cookie", cookie), (b"location", b"/")])


class DeviceDoor(GuestServer):
    """The LAN listener for paired devices. :class:`GuestServer`'s lifecycle, a different app."""

    def __init__(self, gateway: DeviceGateway, registry: DeviceRegistry) -> None:
        super().__init__(gateway)  # type: ignore[arg-type]  # any ASGI app; the guest one is a FastAPI
        self._registry = registry

    async def _through_door(self, scope: Any, receive: Any, send: Any) -> None:
        # Not the guest door's scope mark: a device is the owner, not a guest who came in by the LAN.
        await self._app(scope, receive, send)

    def open_door(self, port: int = 0) -> int:
        """Open on the remembered port when free, so a phone's bookmark keeps working."""
        wanted = port or self._registry.door_port()
        bound = self.start(wanted)
        self._registry.remember_door_port(bound)
        return bound

    def stop(self) -> None:
        super().stop()
        # A code minted for an open door is for that door: closing it voids them.
        self._registry.codes.clear()

    def urls(self, token: str | None = None) -> list[str]:  # noqa: ARG002 -- the base signature
        if not self.listening or self._port is None:
            return []
        return [f"http://{address}:{self._port}/" for address in lan_addresses()]

    def pair_urls(self, code: str) -> list[str]:
        shown = quote(shown_code(code), safe="-")
        return [f"{url}pair?code={shown}" for url in self.urls()]
