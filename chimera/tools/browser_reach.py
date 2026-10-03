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

**What this cannot close.** The floor resolves a public name once and Chromium resolves it again; a
name that answers public to the first and loopback to the second (rebinding) is the gap the floor
always had, on every port, sidecar included. Declaring a port does not widen it toward Chimera's own
ports, and the request the browser makes to a declared port is still that port.
"""

from __future__ import annotations

import ipaddress
import re
import socket
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


def _entries(raw: str) -> list[str]:
    return [part.strip() for part in re.split(r"[,\s]+", raw or "") if part.strip()]


def parse_sites(raw: str) -> tuple[str, ...]:
    """The site list as written — ``example.com``, ``*.example.com`` — lowercased; ValueError on an
    entry that is not a host (a URL, a path, a port, a bare ``*``), so a typo is refused at save time
    rather than read as "no site matches"."""
    out: list[str] = []
    for entry in _entries(raw):
        site = entry.lower().rstrip(".")
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
    return (urlparse(url).hostname or "").rstrip(".")


def _port(url: str) -> int | None:
    parsed = urlparse(url)
    try:
        explicit = parsed.port
    except ValueError:
        return None
    return explicit or (443 if parsed.scheme == "https" else 80)


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
    ) -> None:
        self.sites = tuple(sites)
        self.local_ports = frozenset(local_ports)
        self.home = home
        # The SSRF floor, injectable so a test can let a local server play the public web.
        self._floor = floor
        self._owned = owned or (lambda: chimera_ports(self.home))
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

    def local_target(self, url: str) -> bool:
        """A declared loopback port, spelled one of the three accepted ways, that no Chimera server
        holds — decided on every call, because the sidecar and the guest listener bind late."""
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
        if self.local_target(url):
            return
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
        if self.local_target(url):
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
