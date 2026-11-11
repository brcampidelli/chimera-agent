"""Pick an element of the dev server's page and hand it to the next message.

The Code screen frames the page in an opaque-origin iframe (no ``allow-same-origin``), so nothing can be
injected into it and the screen cannot ask it what is under a click. This module asks the browser Chimera
already has instead: it opens the page, returns a picture of it, and when the owner clicks a point of the
picture it resolves the element at that point IN THE SAME PAGE (``elementFromPoint``) and returns what
the next message will carry.

* **Only a server this app may open.** A configured process server that is running, whose own process tree
  listens on its port (`DevServer.tree_ports`), and whose port is none of Chimera's (`chimera_ports`). An
  entry attached by ``url`` is never one: nothing confirms who answers there.
* **The page is the page's.** What the page's script returned is not trusted (it can replace
  ``elementFromPoint`` as it likes): every field is re-validated here, the attributes are an allowlist,
  an address loses its query and fragment, a form field's value is never read, and the text that reaches
  the message goes through the attachment path, which sanitizes and data-fences it. The message that
  carries it is marked tainted like any other page content (`PAGE_ELEMENT_MARK`).
* **One browser thread.** Playwright's sync API belongs to the thread that made it, and the routes run on
  a pool, so one single-thread executor owns the browser and every call goes through it. One session at a
  time: a capture of another server closes the previous one.
"""

from __future__ import annotations

import base64
import re
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from chimera.core.dev_servers import DevServer, DevServerManager, psutil_available, server_url
from chimera.core.launch_config import ServerConfig
from chimera.telemetry import get_logger

_log = get_logger("core.element_pick")

#: First line of the text a picked element becomes. The turn that receives an attachment carrying it
#: records the text as fetched page content (`chimera/api/code_api.py`), so the run is tainted.
PAGE_ELEMENT_MARK = "[page element picked from the dev-server preview: the page's own words, untrusted data]"

MAX_TEXT = 160
MAX_ATTR = 120
MAX_SELECTOR = 200
MAX_CROP_BYTES = 400_000
#: An idle session is closed by a timer this many seconds after its last call.
IDLE_SECONDS = 300.0
#: The attributes a picked element reports. Nothing else of the page's markup is read.
ATTRIBUTES = ("id", "class", "role", "aria-label", "name", "type", "href", "src", "alt", "title", "placeholder", "data-testid")
_TAGS = re.compile(r"^[a-z][a-z0-9-]{0,30}$")
_CALL_TIMEOUT = 90.0
#: How long a close waits for a browser that is busy before leaving it to close itself.
_CLOSE_WAIT = 5.0


def record_page_origin(ledger: Any, home: Path, ids: list[str]) -> int:
    """Tell the ledger about every attachment marked as page content, of any kind (the crop is a PNG and
    carries no text mark). Recorded under the attachment's own id, as the fetch of that page's pixels."""
    from chimera.api.attachments import is_page_origin

    found = 0
    for ident in ids:
        if is_page_origin(home, ident):
            ledger.record_fetch(f"preview:element:{ident}", "")
            found += 1
    return found


def record_page_elements(ledger: Any, texts: list[str]) -> int:
    """Tell the turn's ledger about every attached text that is a picked page element; how many were.

    Recorded as a fetch with its text, before any tool runs, exactly as a page read through the browser
    would be: the run is tainted, so the dangerous tools ask and a call that copies a value out of the
    page is a tainted flow. Whoever attached the text, the mark in it is what says where it came from.
    """
    found = 0
    for text in texts:
        if PAGE_ELEMENT_MARK in text:
            ledger.record_fetch("preview:element", text)
            found += 1
    return found


class PickRefused(Exception):
    """The pick is refused. ``status`` is the HTTP status the route answers with."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class PickedElement:
    """One element, as the next message will carry it. Every field has been validated here."""

    tag: str
    selector: str
    text: str
    attrs: dict[str, str]
    box: tuple[int, int, int, int]
    page: str
    server: str
    unique: bool
    crop: bytes | None

    @property
    def label(self) -> str:
        """``<button.primary> 'Salvar'``: what the chip says."""
        mark = ""
        if self.attrs.get("id"):
            mark = "#" + self.attrs["id"].split()[0]
        elif self.attrs.get("class"):
            mark = "." + self.attrs["class"].split()[0]
        text = f" '{self.text[:40]}'" if self.text else ""
        return f"<{self.tag}{mark[:50]}>{text}"

    def document(self) -> str:
        """The text the message carries, before the attachment path fences it."""
        x, y, w, h = self.box
        lines = [
            PAGE_ELEMENT_MARK,
            f"server: {self.server}",
            f"page: {self.page}",
            f"selector: {self.selector}" + ("" if self.unique else "  (not unique on the page)"),
            f"tag: {self.tag}",
            f"text: {self.text}",
            *[f"{name}: {value}" for name, value in self.attrs.items()],
            f"box: x={x} y={y} width={w} height={h}",
        ]
        return "\n".join(lines) + "\n"


def _short(value: Any, limit: int) -> str:
    text = " ".join(str(value if isinstance(value, (str, int, float)) else "").split())
    return text if len(text) <= limit else text[:limit] + "..."


#: A path segment that looks like a credential: long hex, or long url-safe base64 that mixes upper case,
#: lower case and digits. A readable slug is lower case and is left alone; this is a heuristic, named as one
#: in docs/preview-element-pick.md.
_TOKEN_SEGMENT = re.compile(r"^(?:[0-9a-fA-F]{20,}|(?=.*[A-Z])(?=.*[a-z])(?=.*\d)[A-Za-z0-9_-]{20,})$")


def _without_tokens(path: str) -> str:
    return "/".join("[redacted]" if _TOKEN_SEGMENT.match(part) else part for part in path.split("/"))


def _clean_address(value: str) -> str:
    """An address without credentials, query or fragment; ``""`` for anything that is not http(s)."""
    try:
        parts = urlsplit(value)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return parts.scheme + ":" if parts.scheme else ""
        host = parts.hostname + (f":{parts.port}" if parts.port else "")
        return f"{parts.scheme}://{host}{_without_tokens(parts.path)}"
    except ValueError:
        return ""


def validate(raw: dict[str, Any], *, page: str, server: str) -> PickedElement | None:
    """What the page said is at the point, reduced to what is allowed. None when it is not an element."""
    tag = str(raw.get("tag", "")).lower()
    if not _TAGS.match(tag):
        return None
    attrs: dict[str, str] = {}
    given = raw.get("attrs")
    for name in ATTRIBUTES:
        if isinstance(given, dict) and name in given:
            value = _short(given[name], MAX_ATTR)
            if name in ("href", "src"):
                value = _clean_address(value) if "://" in value else _without_tokens(value.split("?")[0].split("#")[0])
            if value:
                attrs[name] = value
    box = raw.get("box")
    numbers: list[int] = []
    for key in ("x", "y", "w", "h"):
        try:
            number = float(box[key]) if isinstance(box, dict) else 0.0
        except (TypeError, ValueError):
            number = 0.0
        numbers.append(int(max(-100_000.0, min(100_000.0, number))) if number == number else 0)
    crop = raw.get("crop")
    return PickedElement(
        tag=tag,
        selector=_short(raw.get("selector", ""), MAX_SELECTOR) or tag,
        text="" if tag in ("input", "textarea", "select") else _short(raw.get("text", ""), MAX_TEXT),
        attrs=attrs,
        box=(numbers[0], numbers[1], numbers[2], numbers[3]),
        page=page,
        server=server,
        unique=raw.get("unique") is True,
        crop=crop if isinstance(crop, bytes) and 0 < len(crop) <= MAX_CROP_BYTES else None,
    )


class _Session:
    def __init__(self, key: tuple[str, str], browser: Any, port: int, address: str, generation: int) -> None:
        self.key = key
        self.browser = browser
        self.port = port
        self.address = address
        self.generation = generation
        self.width = 0
        self.height = 0
        #: Identifies the document the picture was taken of (address and load time): a pick on another one
        #: is refused rather than answered about a page nobody saw.
        self.revision = ""
        self.touched = time.monotonic()


BrowserFactory = Callable[[Path, Any, Callable[[], frozenset[int]]], Any]


def _default_browser(root: Path, settings: Any, ports: Callable[[], frozenset[int]]) -> Any:
    """The browser tool, built from the APP's settings (site list, headless switch), with ONE exception: the
    cookie profile is always ephemeral, whatever ``CHIMERA_BROWSER_COOKIE_RETENTION`` says. The project's page
    is code the project wrote and must not run in the profile that holds the agent's sign-ins."""
    from chimera.tools.builtin import make_browser_tool

    tool = make_browser_tool(root, settings.model_copy(update={"browser_cookie_retention": "ephemeral"}), turn_ports=ports)
    if tool is None:
        raise PickRefused(503, "the browser is not available here (Playwright is not installed)")
    return tool


class ElementPicker:
    """The one browser session behind the Preview panel's "Select element" mode."""

    def __init__(
        self,
        mgr: DevServerManager,
        *,
        home: Callable[[], Path],
        settings: Callable[[], Any] | None = None,
        browser_factory: BrowserFactory | None = None,
        clock: Callable[[], float] = time.monotonic,
        idle_seconds: float = IDLE_SECONDS,
        call_timeout: float = _CALL_TIMEOUT,
    ) -> None:
        self._mgr = mgr
        self._home = home
        self._settings = settings or _app_settings
        self._factory = browser_factory  # None: `_default_browser`, looked up at call time so a test can replace it
        self._clock = clock
        self._idle = idle_seconds
        self._timeout = call_timeout
        self._lock = threading.Lock()
        self._executor: ThreadPoolExecutor | None = None
        self._session: _Session | None = None
        self._timer: threading.Timer | None = None
        #: Bumped by every close. A capture that started before it is cancelled when it finishes.
        self._generation = 0

    # --- which server -------------------------------------------------------------------------

    def target(self, root: Path, entry: ServerConfig | None, name: str) -> tuple[DevServer, str]:
        """The server and its address, or a refusal. The rules of the agent's own `preview` look."""
        if entry is None:
            raise PickRefused(404, f"no server named {name!r} in launch.json")
        if entry.attach_only:
            raise PickRefused(
                409, f"{name} is a server that was already running (url): nothing confirms who answers there, so it is not picked from"
            )
        server = self._mgr.get(root, name)
        if server is None or server.state() != "running":
            raise PickRefused(409, f"{name} is not running; start it first")
        from chimera.tools.browser_reach import chimera_ports

        if server.port in chimera_ports(self._home()):
            raise PickRefused(403, f"port {server.port} is one Chimera itself serves on")
        if not psutil_available():
            raise PickRefused(
                409, "this install cannot inspect process trees (psutil is missing), so it cannot confirm which process owns the port"
            )
        if server.port not in server.tree_ports():
            raise PickRefused(409, f"no process started by {name} is listening on port {server.port}, so its page is not opened")
        return server, server_url(server) + "/"

    # --- the browser thread -------------------------------------------------------------------

    def _run(self, work: Callable[[], Any]) -> Any:
        """Run ``work`` on the browser thread. A browser that does not answer in time is abandoned, not waited
        for: its thread is left to finish, the next call gets a new one, and the caller gets a refusal."""
        with self._lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="element-pick")
            executor = self._executor
        future: Future[Any] = executor.submit(work)
        try:
            return future.result(timeout=self._timeout)
        except FutureTimeout as exc:
            future.cancel()
            self._abandon(executor)
            raise PickRefused(504, "the browser did not answer in time; try again") from exc
        except PickRefused:
            raise
        except Exception as exc:  # noqa: BLE001 — a driver failure is the owner's sentence, not a 500
            raise PickRefused(502, str(exc)[:300] or "the browser failed") from exc

    def _abandon(self, executor: ThreadPoolExecutor) -> None:
        with self._lock:
            if self._executor is executor:
                self._executor = None
            self._generation += 1
            self._session = None  # the stuck thread owns the browser; it is closed when that thread returns
        executor.shutdown(wait=False, cancel_futures=True)

    def _drop(self, up_to: int | None = None) -> None:
        """Close the session; with ``up_to``, only if it was made no later than that generation (a close
        that raced a newer capture must not take the newer session with it)."""
        session = self._session
        if session is None or (up_to is not None and session.generation > up_to):
            return
        self._session = None
        try:
            session.browser.close()
        except Exception as exc:  # noqa: BLE001 — a browser that will not close is logged, not raised
            _log.debug("element picker browser did not close: %s", exc)

    # --- idle expiry ---------------------------------------------------------------------------

    def _arm(self, delay: float | None = None) -> None:
        """(Re)start the idle timer: a real one, so an owner who walks away still has the browser closed."""
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            timer = threading.Timer(self._idle if delay is None else delay, self._expire)
            timer.daemon = True
            self._timer = timer
        timer.start()

    def _expire(self) -> None:
        session = self._session
        if session is None:
            return
        left = self._idle - (self._clock() - session.touched)
        if left > 0.001:
            # Early: on Windows a timer wait can return ~15 ms before its timeout. Returning here without
            # re-arming left the browser open for good (seen on CI). A use since the last arm lands
            # here too, and waits the time that is left.
            self._arm(left)
            return
        try:
            self.close()
        except Exception as exc:  # noqa: BLE001 — a timer thread must never raise
            _log.debug("element picker idle close: %s", exc)

    # --- the two calls ------------------------------------------------------------------------

    def capture(self, root: Path, entry: ServerConfig | None, name: str) -> dict[str, Any]:
        """Open the page and return the picture to click on: base64 JPEG, viewport size, address."""
        server, address = self.target(root, entry, name)
        key = (str(root.resolve()), name)
        generation = self._generation

        def work() -> dict[str, Any]:
            session = self._session
            if self._generation != generation:
                raise PickRefused(409, "the capture was cancelled")
            if session is None or session.key != key or session.port != server.port:
                self._drop()
                browser = (self._factory or _default_browser)(
                    root, self._settings(), lambda: self._ports_of(server)
                )
                session = _Session(key, browser, server.port, address, generation)
                self._session = session
            seen = session.browser.capture_view(address)
            if self._generation != generation:
                # A close came in while the page was loading: nobody is waiting for this picture.
                self._drop(generation)
                raise PickRefused(409, "the capture was cancelled")
            if seen.get("error"):
                self._drop()
                raise PickRefused(502, str(seen["error"]).removeprefix("error: "))
            session.width, session.height = int(seen["width"]), int(seen["height"])
            session.revision = str(seen.get("revision", ""))
            session.touched = self._clock()
            return {
                "image": base64.b64encode(seen["jpeg"]).decode("ascii"),
                "width": session.width, "height": session.height, "url": address,
            }

        out: dict[str, Any] = self._run(work)
        self._arm()
        return out

    def pick(self, root: Path, entry: ServerConfig | None, name: str, x: float, y: float) -> PickedElement:
        """The element at ``(x, y)`` of the page the last capture opened, in that same page."""
        server, address = self.target(root, entry, name)
        key = (str(root.resolve()), name)
        generation = self._generation

        def work() -> PickedElement:
            session = self._session
            if self._generation != generation or session is None or session.key != key or session.port != server.port:
                raise PickRefused(409, "capture the page first")
            if not (0 <= x <= session.width and 0 <= y <= session.height):
                raise PickRefused(400, "that point is outside the captured page")
            found = session.browser.element_at(x, y, only_port=server.port, revision=session.revision)
            if found.get("error"):
                raise PickRefused(409, str(found["error"]).removeprefix("error: "))
            picked = validate(found, page=address, server=name)
            if picked is None:
                raise PickRefused(409, "nothing selectable is at that point")
            session.touched = self._clock()
            return picked

        out: PickedElement = self._run(work)
        self._arm()
        return out

    def _ports_of(self, server: DevServer) -> frozenset[int]:
        """The turn's rule, for the owner: the port counts only while the server's own tree listens on it."""
        if server.state() == "running" and server.port in server.tree_ports():
            return frozenset({server.port})
        return frozenset()

    def close(self) -> None:
        """Close the browser and the thread, and cancel a capture still loading. Called when the panel
        leaves select mode, when the idle timer fires and at shutdown. Never waits for a stuck browser."""
        with self._lock:
            self._generation += 1
            closing = self._generation - 1
            executor = self._executor
            timer, self._timer = self._timer, None
        if timer is not None:
            timer.cancel()
        if executor is None:
            self._drop()
            return
        future = executor.submit(self._drop, closing)
        try:
            future.result(timeout=_CLOSE_WAIT)
        except FutureTimeout:
            _log.debug("element picker: the browser is busy; it closes when its call returns")
        except Exception as exc:  # noqa: BLE001
            _log.debug("element picker close: %s", exc)


    def shutdown(self) -> None:
        """At the app's exit: close, and let the browser thread go."""
        self.close()
        with self._lock:
            executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=False, cancel_futures=True)


def _app_settings() -> Any:
    from chimera.config import get_settings

    return get_settings()

