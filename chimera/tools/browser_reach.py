"""Where the agent's browser may go: the SSRF floor, a narrower site list, and declared local ports.

Study 29, P5.2. Two settings, both empty by default, and with both empty nothing here is built — the
browser tool keeps ``check_url`` and the driver keeps ``is_safe_url``, exactly as before.

* ``CHIMERA_BROWSER_SITES`` (hosts, and ``*.domain`` for its subdomains) only NARROWS. A top-level
  navigation to a host off the list is a question for a person (the same approver that lets a file
  tool outside the project folder, ``ask_outside``); where nobody can be asked it is a refusal. A
  "yes" holds for that host for the rest of the browser's life. Subresources of a listed page are
  not judged by the list — a site's CDN is not a site the agent visits — only by the floor.
* ``CHIMERA_BROWSER_LOCAL_PORTS`` WIDENS, by exactly one thing: ``http(s)://localhost:<port>``,
  ``127.0.0.1:<port>`` or ``[::1]:<port>`` for a port the owner declared, so the agent can look at the
  dev server it is changing. Nothing else that the floor refuses becomes reachable — not a private
  network, not link-local, not cloud metadata, not another loopback port.

**Why the local exception is this narrow.** The same machine serves Chimera's own API on loopback,
where ``/api/approvals`` answers a card: a page that could steer the agent there could approve its own
actions. So the exception is decided per request, against

- **the ports this process listens on** (`chimera.core.listeners`: the app's API, whatever port the
  desktop sidecar was given; the guest listener; the ``serve`` gateway), the port the desktop server
  holding this data folder announced (``desktop.url``), and Chimera's default port, 8765 — refused
  whatever the owner declared;
- **the spelling of the host**, not only the address it resolves to: three literal names and nothing
  else. A DNS name that resolves to loopback (DNS rebinding, ``localtest.me``), ``127.0.0.2``, an
  IPv4-mapped ``[::ffff:127.0.0.1]`` and the integer and octal spellings are all refused here and
  fall to the floor, which refuses them too. ``localhost`` itself must resolve only to loopback.

- **who answers on the port**, not only its number. The desktop's own dev server (Vite on 5173,
  ``npm --prefix apps/desktop run dev``) proxies ``/api`` to the app's API, so a declared 5173 would
  reach ``/api/approvals`` through a port Chimera never held. Every Chimera listener marks its
  responses (``chimera.core.listeners.INSTANCE_HEADER``) and a proxy relays the mark, so before a
  request goes to a declared port the same URL is asked with an ``OPTIONS`` — which runs no route's
  handler — and a port that answers with the mark, or does not answer, is refused for that request.

- **who sends the request**: the driver's guard lets a request reach a declared port only from a
  page that is itself on one, or as the agent's own top-level navigation and its redirect hops
  (``RequestGuard`` in `browser_playwright`). Any other site the agent visits cannot send the dev
  server an image, a form or a script navigation.

**What this cannot close.** The floor resolves a public name once and Chromium resolves it again; a
name that answers public to the first and loopback to the second (rebinding) is the gap the floor
always had, on every port, sidecar included. Declaring a port does not widen it toward Chimera's own
ports, and the request the browser makes to a declared port is still that port.
"""

from __future__ import annotations

import http.client
import ipaddress
import re
import socket
import ssl
from collections.abc import Callable, Iterable
from pathlib import Path
from urllib.parse import urlparse

from chimera.scrape.ssrf import check_url, is_safe_url

SITES_ENV = "CHIMERA_BROWSER_SITES"
PORTS_ENV = "CHIMERA_BROWSER_LOCAL_PORTS"

#: `chimera serve` and `chimera app` both default to it; another process on this machine may hold it
#: and this process cannot see that one's listeners.
DEFAULT_SERVER_PORT = 8765

#: The only spellings of loopback the exception accepts (``urlparse`` lowercases the host and strips
#: the brackets of an IPv6 literal).
_LOCAL_NAMES = frozenset({"localhost", "127.0.0.1", "::1"})

_SITE = re.compile(r"^(?:\*\.)?[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*$")


def ascii_host(host: str) -> str:
    """``host`` as Chromium sends it: lowercased, trailing dot dropped, and an internationalised
    name in punycode (UTS #46, non-transitional — Chromium's reading). A person approves
    ``bücher.de`` while Chromium asks for ``xn--bcher-kva.de``; compared in two spellings, the yes
    never matched the request and the page never loaded, however many times it was given. A host the
    encoder refuses (an IP literal with colons, an underscore) is kept as written: it was never IDN.
    """
    host = (host or "").strip().lower().rstrip(".")
    if host.isascii() and "xn--" not in host:
        return host
    try:
        import idna  # httpx's own dependency, so always installed beside Chimera

        return str(idna.encode(host, uts46=True, transitional=False).decode("ascii"))
    except Exception:  # noqa: BLE001 — not a name the encoder takes: compare it as written
        return host


def _entries(raw: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,\s]+", raw or "") if part.strip()]


def parse_sites(raw: str) -> tuple[str, ...]:
    """The site list as written — ``example.com``, ``*.example.com`` — lowercased; ValueError on an
    entry that is not a host (a URL, a path, a port, a bare ``*``), so a typo is refused at save time
    rather than read as "no site matches"."""
    out: list[str] = []
    for entry in _entries(raw):
        # `*.bücher.de` and `bücher.de` are stored as the browser will ask for them, so the list can
        # be written the way a person writes a name.
        wild = entry.startswith("*.")
        site = ("*." if wild else "") + ascii_host(entry[2:] if wild else entry)
        if not _SITE.match(site):
            raise ValueError(
                f"{SITES_ENV}: {entry!r} is not a host — write example.com or *.example.com, "
                "without a scheme, path or port"
            )
        if site not in out:
            out.append(site)
    return tuple(out)


def parse_ports(raw: str) -> frozenset[int]:
    """The declared local ports; ValueError on anything that is not a port, and on Chimera's own
    default port, which no declaration can open."""
    ports: set[int] = set()
    for entry in _entries(raw):
        if not entry.isdigit() or not 1 <= int(entry) <= 65535:
            raise ValueError(f"{PORTS_ENV}: {entry!r} is not a port number (1-65535)")
        if int(entry) == DEFAULT_SERVER_PORT:
            raise ValueError(
                f"{PORTS_ENV}: {DEFAULT_SERVER_PORT} is Chimera's own server port and cannot be opened"
            )
        ports.add(int(entry))
    return frozenset(ports)


def chimera_ports(home: Path | None) -> frozenset[int]:
    """Every port a Chimera server is known to listen on, read at the moment of asking."""
    from chimera.core.instance import running_url
    from chimera.core.listeners import held

    ports = set(held()) | {DEFAULT_SERVER_PORT}
    if home is not None:
        announced = running_url(home)
        if announced:
            try:
                port = urlparse(announced).port
            except ValueError:
                port = None
            if port:
                ports.add(port)
    return frozenset(ports)


def _host(url: str) -> str:
    return ascii_host(urlparse(url).hostname or "")


def _port(url: str) -> int | None:
    parsed = urlparse(url)
    try:
        explicit = parsed.port
    except ValueError:
        return None
    return explicit or (443 if parsed.scheme == "https" else 80)


#: What :func:`who_answers` found at a declared port.
CHIMERA, SILENT, OTHER = "chimera", "silent", "other"

#: Seconds the probe waits for a loopback server; a dev server that cannot answer an OPTIONS in this
#: long is not one the page will get much from either.
_PROBE_TIMEOUT = 2.0


def who_answers(url: str) -> str:
    """Ask ``url`` with an ``OPTIONS`` and read whether the answer is Chimera's own.

    ``OPTIONS`` because it is the one method no route of Chimera's (or of a dev server worth
    declaring) runs a handler for, so asking cannot do what the request it guards would do; and the
    SAME path, because a proxy decides per path — Vite relays ``/api`` and serves ``/`` itself.
    Nothing is cached: the answer belongs to this request. ``SILENT`` when nothing answered (a
    refused connection, a timeout, a broken reply): refused like Chimera, because a port that cannot
    be asked cannot be cleared. No proxy from the environment and no redirect is followed — the
    question is about this port. A dev certificate is not verified: the answer is read for one
    header, on loopback, and Chromium makes its own decision about the certificate."""
    from chimera.core.listeners import INSTANCE_HEADER

    parsed = urlparse(url)
    host = parsed.hostname or ""
    port = _port(url)
    path = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
    conn: http.client.HTTPConnection
    if parsed.scheme == "https":
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        conn = http.client.HTTPSConnection(host, port, timeout=_PROBE_TIMEOUT, context=context)
    else:
        conn = http.client.HTTPConnection(host, port, timeout=_PROBE_TIMEOUT)
    try:
        conn.request("OPTIONS", path)
        response = conn.getresponse()
        marked = response.getheader(INSTANCE_HEADER) is not None
        response.read(64 * 1024)
    except (OSError, http.client.HTTPException):
        return SILENT
    finally:
        conn.close()
    return CHIMERA if marked else OTHER


def _loopback_only(host: str) -> bool:
    """Every address ``host`` names is loopback. ``localhost`` is resolved, because a hosts file can
    point it anywhere; the two literals are what they say."""
    if host != "localhost":
        return ipaddress.ip_address(host).is_loopback
    try:
        found = {str(info[4][0]) for info in socket.getaddrinfo(host, None)}
    except OSError:
        return False
    return bool(found) and all(ipaddress.ip_address(ip.split("%")[0]).is_loopback for ip in found)


class BrowserReach:
    """The predicate every request of the agent's browser passes, and the list its top-level
    navigations are held to. One object per browser, shared by the tool and its driver, so a host a
    person approved through the tool is the host the driver then lets through."""

    def __init__(
        self,
        sites: Iterable[str] = (),
        local_ports: Iterable[int] = (),
        *,
        home: Path | None = None,
        floor: Callable[[str], bool] = is_safe_url,
        owned: Callable[[], frozenset[int]] | None = None,
        answers: Callable[[str], str] = who_answers,
    ) -> None:
        self.sites = tuple(sites)
        self.local_ports = frozenset(local_ports)
        self.home = home
        # The SSRF floor, injectable so a test can let a local server play the public web.
        self._floor = floor
        self._owned = owned or (lambda: chimera_ports(self.home))
        # Who answers on a declared port (`who_answers`), injectable so a predicate test needs no
        # server; the real-browser tests use the real probe against real servers.
        self._answers = answers
        self._public: dict[str, bool] = {}
        self.approved: set[str] = set()

    @classmethod
    def from_settings(cls, settings: object) -> BrowserReach | None:
        """The reach the owner configured, or None when both settings are empty — the case where
        nothing of this module may be in the path."""
        sites = parse_sites(str(getattr(settings, "browser_sites", "") or ""))
        ports = parse_ports(str(getattr(settings, "browser_local_ports", "") or ""))
        if not sites and not ports:
            return None
        return cls(sites, ports, home=getattr(settings, "home", None))

    # --- the request predicate ---------------------------------------------------------------

    def declared_local(self, url: str) -> bool:
        """A declared loopback port, spelled one of the three accepted ways, that no Chimera server
        holds — decided on every call, because the sidecar and the guest listener bind late. Says
        nothing about who answers there; :meth:`local_target` asks that too."""
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not self.local_ports:
            return False
        host = parsed.hostname or ""
        if host not in _LOCAL_NAMES:
            return False
        port = _port(url)
        if port is None or port not in self.local_ports or port in self._owned():
            return False
        return _loopback_only(host)

    def local_target(self, url: str) -> bool:
        """A declared local port (:meth:`declared_local`) where something that is not Chimera
        answers this very URL — the only loopback request the browser may send."""
        return self.declared_local(url) and self._answers(url) == OTHER

    def permits(self, url: str) -> bool:
        """Every request the browser makes — navigations, redirect hops, subresources, popups."""
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return True  # data:, blob:, about: never leave the page (the guard's own rule)
        if self.local_target(url):
            return True
        key = f"{parsed.scheme}://{parsed.netloc}"
        if key not in self._public:
            self._public[key] = self._floor(url)
        return self._public[key]

    def check(self, url: str) -> None:
        """Raise ValueError when ``url`` may not be opened at all — the floor's own message, plus why
        a loopback address that looks declared is not."""
        if self.declared_local(url):
            answer = self._answers(url)
            if answer == OTHER:
                return
            port = _port(url)
            if answer == CHIMERA:
                raise ValueError(
                    f"blocked: {url} is answered by Chimera itself (port {port} relays to it, as "
                    "the desktop's own dev server does for /api); it is never opened"
                )
            raise ValueError(
                f"blocked: nothing answered at the declared local port {port} — start the dev "
                "server first"
            )
        host, port = _host(url), _port(url)
        if self.local_ports and host in _LOCAL_NAMES and port is not None:
            if port in self._owned():
                raise ValueError(f"blocked: port {port} is one Chimera itself serves on; it is never opened")
            if port not in self.local_ports:
                raise ValueError(
                    f"blocked: port {port} is not one of the declared local ports ({PORTS_ENV})"
                )
        if self._floor is is_safe_url:
            check_url(url)
        elif not self._floor(url):
            raise ValueError(f"blocked internal address for host {host!r}")

    # --- the site list ---------------------------------------------------------------------------

    def listed(self, url: str) -> bool:
        """Whether a top-level navigation to ``url`` needs no question: no list, a non-web scheme, a
        declared local port, a host a person approved, or a host the list names."""
        parsed = urlparse(url)
        if not self.sites or parsed.scheme not in ("http", "https"):
            return True
        if self.declared_local(url):
            return True  # the owner declared it, which is a statement about where the agent may go
        host = _host(url)
        if host in self.approved:
            return True
        for site in self.sites:
            if site.startswith("*."):
                suffix = site[1:]
                if host.endswith(suffix) and len(host) > len(suffix):
                    return True
            elif host == site:
                return True
        return False

    def approve(self, url: str) -> None:
        """A person said yes to this host: it stays open for this browser."""
        host = _host(url)
        if host:
            self.approved.add(host)
