"""Study 29, P5.2(b): the browser may open a declared loopback port, and nothing else it could not before.

`CHIMERA_BROWSER_LOCAL_PORTS` exists so the agent can look at the dev server it is changing
(``http://localhost:3000``), which the SSRF floor refuses like any loopback address. Opening loopback
is the dangerous direction: the same machine serves Chimera's own API there, where ``/api/approvals``
answers a card, so a page that could steer the agent to it could approve its own actions. Every test
below is a refusal the exception must not lose — the sidecar's port however it was bound, an
undeclared port, private and link-local addresses, metadata, the other spellings of loopback, a DNS
name that resolves to it, and a redirect from an allowed site — plus the one thing it must allow.

The tests taking the ``driver`` fixtures drive a real Chromium against local servers and skip where
it cannot launch. In them a server on ``127.0.0.1`` plays the public web (the floor is injected to
allow exactly that port), so what is exercised is the interception, not DNS.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest

from chimera.scrape import ssrf
from chimera.scrape.ssrf import check_url, is_numeric_host, is_safe_url
from chimera.tools import browser_reach
from chimera.tools.browser import BrowserTool, Element
from chimera.tools.browser_playwright import PlaywrightDriver, RequestGuard
from chimera.tools.browser_reach import (
    DEFAULT_SERVER_PORT,
    OTHER,
    BrowserReach,
    chimera_ports,
    parse_ports,
)

DECLARED = 3000
SIDECAR = 51234  # stands for the desktop's port: dynamic, known only to this process


def _other(url: str) -> str:
    """Something that is not Chimera answers every declared port (no server runs in these
    predicate tests; who answers is `test_a_declared_port_that_relays_to_chimera_is_refused`'s)."""
    return OTHER


def _reach(*ports: int, owned: frozenset[int] = frozenset({SIDECAR})) -> BrowserReach:
    return BrowserReach(local_ports=ports or (DECLARED,), owned=lambda: owned, answers=_other)


# --- the predicate ------------------------------------------------------------------------------


def test_a_declared_port_on_loopback_is_opened_in_each_accepted_spelling() -> None:
    reach = _reach()
    for url in ("http://localhost:3000/", "http://127.0.0.1:3000/app", "https://[::1]:3000/", "http://LOCALHOST:3000"):
        assert reach.permits(url), url
        reach.check(url)  # no raise


def test_the_sidecars_own_port_is_refused_even_when_declared() -> None:
    """The owner wrote the sidecar's port in the list (by accident, or because a page told the agent
    to suggest it). The process knows it listens there, and that wins."""
    reach = _reach(DECLARED, SIDECAR)
    for url in (
        f"http://127.0.0.1:{SIDECAR}/api/approvals",
        f"http://localhost:{SIDECAR}/api/approvals/1",
        f"http://[::1]:{SIDECAR}/",
    ):
        assert not reach.permits(url), url
        with pytest.raises(ValueError, match="Chimera itself serves"):
            reach.check(url)


def test_the_desktops_dynamic_port_is_refused_wherever_the_process_learned_it(tmp_path: Path) -> None:
    """Three ways a Chimera port reaches the check, none of them written by the owner: a listener of
    this process (the sidecar binds port 0), the desktop server announced for this data folder
    (`desktop.url`, which a CLI run beside the app reads), and the default port."""
    from chimera.core.listeners import claim

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    bound = probe.getsockname()[1]
    probe.close()
    claim(bound)
    (tmp_path / "desktop.url").write_text("http://127.0.0.1:61999", encoding="utf-8")

    ports = chimera_ports(tmp_path)
    assert {bound, 61999, DEFAULT_SERVER_PORT} <= ports

    reach = BrowserReach(local_ports={bound, 61999, DECLARED}, home=tmp_path)
    assert not reach.permits(f"http://127.0.0.1:{bound}/api/approvals")
    assert not reach.permits("http://localhost:61999/api/config")
    assert not reach.permits(f"http://localhost:{DEFAULT_SERVER_PORT}/")


def test_every_listener_chimera_opens_reports_its_port() -> None:
    """The app's socket (the desktop sidecar's, bound to port 0) and the `serve` gateway both say what
    they got, at bind time, before anything is served."""
    from chimera.cli.main import _bind_app_socket
    from chimera.core.listeners import held
    from chimera.server.http import make_server

    sock, port = _bind_app_socket("127.0.0.1", 0)
    try:
        assert port in held()
    finally:
        sock.close()
    server = make_server(gateway=None, host="127.0.0.1", port=0)  # type: ignore[arg-type]  # never served
    try:
        assert server.server_address[1] in held()
    finally:
        server.server_close()


def test_the_guest_listener_reports_its_port() -> None:
    from fastapi import FastAPI

    from chimera.api.guest_api import GuestServer
    from chimera.core.listeners import held

    listener = GuestServer(FastAPI())
    port = listener.start(0, host="127.0.0.1")
    try:
        assert port in held()
    finally:
        listener.stop()


def test_an_undeclared_loopback_port_stays_refused() -> None:
    reach = _reach()
    for url in ("http://localhost:3001/", "http://127.0.0.1/", "http://127.0.0.1:80/", "https://localhost/"):
        assert not reach.permits(url), url
    with pytest.raises(ValueError, match="not one of the declared local ports"):
        reach.check("http://localhost:3001/")


def test_private_link_local_and_metadata_addresses_stay_refused_on_a_declared_port() -> None:
    reach = _reach()
    for url in (
        "http://10.0.0.5:3000/",
        "http://192.168.1.10:3000/",
        "http://172.16.0.1:3000/",
        "http://169.254.169.254:3000/latest/meta-data",
        "http://169.254.169.254/latest/meta-data/",
        "http://100.100.100.200:3000/latest/meta-data",  # Alibaba's metadata, in CGNAT
        "http://[fe80::1]:3000/",
        "http://[fd00::1]:3000/",
        "http://0.0.0.0:3000/",
    ):
        assert not reach.permits(url), url
        with pytest.raises(ValueError):
            reach.check(url)


def test_other_spellings_of_loopback_are_refused_not_decoded() -> None:
    """Only `localhost`, `127.0.0.1` and `[::1]` are the exception. An IPv4-mapped address, the long
    IPv6 form, another address in 127/8, and the integer, octal and hex spellings fall to the floor —
    which refuses them too, on the declared port and on the sidecar's."""
    reach = _reach()
    for port in (DECLARED, SIDECAR):
        for host in (
            "[::ffff:127.0.0.1]",
            "[0:0:0:0:0:0:0:1]",
            "[::ffff:7f00:1]",
            "127.0.0.2",
            "2130706433",
            "0177.0.0.1",
            "0x7f.0.0.1",
            "0x7f000001",
            "127.1",
            "017700000001",
            "localhost.",
        ):
            url = f"http://{host}:{port}/api/approvals"
            assert not reach.permits(url), url
            with pytest.raises(ValueError):
                reach.check(url)


def test_the_floor_refuses_a_numeric_spelling_on_every_platform() -> None:
    """`ipaddress` reads only the dotted quad, so these went to `getaddrinfo`: unresolvable on
    Windows (refused by luck), `127.0.0.1` on glibc. Chromium reads all of them as loopback; the floor
    now refuses them before asking any resolver."""
    for host in ("2130706433", "0177.0.0.1", "0x7f.1", "127.1", "0x7F000001", "3232235777", "127.0.0.1."):
        assert is_numeric_host(host), host
        with pytest.raises(ValueError, match="non-canonical"):
            check_url(f"http://{host}/")
    for host in ("127.0.0.1", "::1", "example.com", "1e100.net", "123.example", "::ffff:127.0.0.1"):
        assert not is_numeric_host(host), host


def test_a_dns_name_that_resolves_to_loopback_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """DNS rebinding and the `localtest.me` family: decided by the spelling, then by the address."""
    monkeypatch.setattr(ssrf, "_resolve_ips", lambda host: ["127.0.0.1", "::1"])
    reach = _reach()
    for url in ("http://localtest.me:3000/", f"http://rebind.attacker.example:{SIDECAR}/api/approvals"):
        assert not reach.permits(url), url
        with pytest.raises(ValueError, match="blocked internal address"):
            reach.check(url)


def test_localhost_that_does_not_resolve_to_loopback_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hosts file can point `localhost` anywhere; the exception is for loopback, not for the word."""
    monkeypatch.setattr(
        browser_reach.socket, "getaddrinfo", lambda host, port: [(2, 1, 6, "", ("10.0.0.7", 0))]
    )
    assert not _reach().permits("http://localhost:3000/")
    assert _reach().permits("http://127.0.0.1:3000/")  # the literal needs no resolver


def test_a_redirect_from_an_allowed_site_to_loopback_is_refused_hop_by_hop() -> None:
    """The guard sees every hop; with a reach it asks the reach, without a cache, on each one."""
    reach = BrowserReach(
        local_ports={DECLARED},
        owned=lambda: frozenset({SIDECAR}),
        floor=lambda url: urlparse(url).hostname == "example.com",
        answers=_other,
    )
    guard = RequestGuard(reach.permits, cache=False)
    sent: list[tuple[str, dict[str, Any]]] = []

    class _Session:
        def send(self, method: str, params: dict[str, Any]) -> None:
            sent.append((method, params))

    def hop(url: str, rid: str) -> None:
        guard.on_paused(_Session(), {"requestId": rid, "request": {"url": url}, "resourceType": "Document"})

    hop("https://example.com/", "public")
    hop(f"http://127.0.0.1:{SIDECAR}/api/approvals", "sidecar")
    hop("http://localhost:3001/", "undeclared")
    hop("http://169.254.169.254/latest/meta-data", "metadata")
    hop("http://localhost:3000/", "declared")
    verdicts = {params["requestId"]: method for method, params in sent}
    assert verdicts == {
        "public": "Fetch.continueRequest",
        "sidecar": "Fetch.failRequest",
        "undeclared": "Fetch.failRequest",
        "metadata": "Fetch.failRequest",
        "declared": "Fetch.continueRequest",
    }


def test_the_reach_decides_a_loopback_port_again_on_every_request() -> None:
    """The guest listener can open after the browser started. A decision cached at the first request
    would keep a port open that Chimera has since taken."""
    owned: set[int] = set()
    reach = BrowserReach(local_ports={DECLARED}, owned=lambda: frozenset(owned), answers=_other)
    guard = RequestGuard(reach.permits, cache=False)
    assert guard.permits("http://localhost:3000/a")
    owned.add(DECLARED)
    assert not guard.permits("http://localhost:3000/b")


# --- the settings ---------------------------------------------------------------------------------


def test_both_settings_empty_build_nothing_and_the_browser_is_what_it_was(tmp_path: Path) -> None:
    from chimera.config import Settings
    from chimera.tools.builtin import default_registry

    settings = Settings(CHIMERA_HOME=str(tmp_path))  # type: ignore[arg-type]
    assert BrowserReach.from_settings(settings) is None
    tool = default_registry(tmp_path).get("browser")
    assert isinstance(tool, BrowserTool) and tool.reach is None


def test_the_ports_setting_refuses_what_is_not_a_port_and_chimeras_own() -> None:
    assert parse_ports("3000, 5173 8080") == frozenset({3000, 5173, 8080})
    assert parse_ports("") == frozenset()
    for bad in ("abc", "70000", "0", "-1", "3000-3010", "localhost:3000", str(DEFAULT_SERVER_PORT)):
        with pytest.raises(ValueError):
            parse_ports(bad)


def test_the_screen_cannot_save_a_port_the_browser_would_not_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from chimera.api.config_api import patch_config
    from chimera.config import get_settings

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    monkeypatch.setenv("CHIMERA_BROWSER_LOCAL_PORTS", "")
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="Chimera's own server port"):
        patch_config({"CHIMERA_BROWSER_LOCAL_PORTS": "3000, 8765"}, env_path=tmp_path / ".env")
    assert not (tmp_path / ".env").exists()
    patch_config({"CHIMERA_BROWSER_LOCAL_PORTS": "3000, 5173"}, env_path=tmp_path / ".env")
    from chimera.api.config_api import read_config

    assert read_config(get_settings())["browser"]["local_ports"] == [3000, 5173]
    get_settings.cache_clear()


def test_a_hand_edited_value_that_does_not_parse_leaves_the_browser_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Neither wider nor narrower than written: no browser, and the log says why."""
    from chimera.config import get_settings
    from chimera.tools.builtin import default_registry

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    monkeypatch.setenv("CHIMERA_BROWSER_LOCAL_PORTS", "3000,all")
    get_settings.cache_clear()
    try:
        assert "browser" not in default_registry(tmp_path)
    finally:
        get_settings.cache_clear()


# --- the tool --------------------------------------------------------------------------------------


class _Driver:
    def __init__(self) -> None:
        self.visited: list[str] = []

    def navigate(self, url: str) -> list[Element]:
        self.visited.append(url)
        return [Element("e1", "link", "home")]

    def read(self) -> list[Element]:
        return []

    def page_html(self) -> str:
        return "<p>x</p>"

    def page_text(self) -> str:
        return "x"

    def frame(self) -> None:
        return None

    def close(self) -> None:
        return None


def test_the_tool_refuses_the_sidecar_before_the_driver_is_asked() -> None:
    driver = _Driver()
    tool = BrowserTool(driver=driver, reach=_reach())  # type: ignore[arg-type]
    out = tool.run(action="navigate", url=f"http://127.0.0.1:{SIDECAR}/api/approvals")
    assert out.startswith("error:") and "Chimera itself serves" in out
    out = tool.run(action="read_text", url="http://localhost:3001/")
    assert out.startswith("error:")
    assert driver.visited == []
    assert not tool.run(action="navigate", url="http://localhost:3000/").startswith("error:")
    assert driver.visited == ["http://localhost:3000/"]


def test_without_a_reach_the_tool_still_refuses_every_loopback_port() -> None:
    driver = _Driver()
    tool = BrowserTool(driver=driver)  # type: ignore[arg-type]
    assert tool.run(action="navigate", url="http://localhost:3000/").startswith("error:")
    assert driver.visited == []
    assert not is_safe_url("http://localhost:3000/")


# --- a real browser ------------------------------------------------------------------------------


class _Hits(BaseHTTPRequestHandler):
    """A server that records every path it was asked for; the sidecar stand-in must record none."""

    def log_message(self, *args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 — the stdlib's name
        self.server.hits.append(self.path)  # type: ignore[attr-defined]
        routes: dict[str, tuple[int, str, dict[str, str]]] = self.server.routes  # type: ignore[attr-defined]
        status, body, headers = routes.get(self.path.split("?")[0], (200, "<p>ok</p>", {}))
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for key, value in headers.items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)


def _serve(routes: dict[str, tuple[int, str, dict[str, str]]]) -> tuple[ThreadingHTTPServer, int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Hits)
    server.hits = []  # type: ignore[attr-defined]
    server.routes = routes  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, int(server.server_address[1])


@pytest.fixture()
def world() -> Iterator[dict[str, Any]]:
    """A public site, a declared dev server and a sidecar, each on its own loopback port."""
    sidecar, sidecar_port = _serve({"/api/approvals": (200, "<p>APPROVED-ALL</p>", {})})
    dev, dev_port = _serve({})
    public, public_port = _serve({})
    dev.routes.update({  # type: ignore[attr-defined]
        "/": (200, "<p>dev-home</p>", {}),
        "/link": (200, f'<a href="http://localhost:{sidecar_port}/api/approvals">approve</a>', {}),
        "/fetch": (
            200,
            f"<p>dev</p><script>fetch('http://localhost:{sidecar_port}/api/approvals?f=1')</script>",
            {},
        ),
    })
    public.routes.update({  # type: ignore[attr-defined]
        "/to-sidecar": (302, "", {"Location": f"http://localhost:{sidecar_port}/api/approvals"}),
        "/to-dev": (302, "", {"Location": f"http://localhost:{dev_port}/"}),
        "/img": (200, f'<p>public</p><img src="http://127.0.0.1:{sidecar_port}/api/approvals.png">', {}),
    })
    yield {
        "sidecar": sidecar, "sidecar_port": sidecar_port,
        "dev_port": dev_port, "public_port": public_port,
    }
    for server in (sidecar, dev, public):
        server.shutdown()


@pytest.fixture()
def reach_driver(world: dict[str, Any]) -> Iterator[PlaywrightDriver]:
    public_port = world["public_port"]
    reach = BrowserReach(
        # The owner declared the dev server AND, by mistake, the sidecar: the process must win.
        local_ports={world["dev_port"], world["sidecar_port"]},
        owned=lambda: frozenset({world["sidecar_port"]}),
        floor=lambda url: urlparse(url).hostname == "127.0.0.1" and urlparse(url).port == public_port,
    )
    try:
        d = PlaywrightDriver(headless=True, reach=reach)
    except Exception as exc:  # noqa: BLE001 — no Chromium here (CI): these tests need a real browser
        pytest.skip(f"chromium unavailable: {exc}")
    yield d
    d.close()


def test_a_real_browser_opens_the_declared_dev_server(world: dict[str, Any], reach_driver: PlaywrightDriver) -> None:
    reach_driver.navigate(f"http://localhost:{world['dev_port']}/")
    assert "dev-home" in reach_driver.page_text()


def test_a_real_redirect_from_a_public_site_to_the_sidecar_never_reaches_it(
    world: dict[str, Any], reach_driver: PlaywrightDriver
) -> None:
    with pytest.raises(ValueError, match="blocked navigation"):
        reach_driver.navigate(f"http://127.0.0.1:{world['public_port']}/to-sidecar")
    assert world["sidecar"].hits == []
    # ...while the same redirect to the declared port loads.
    reach_driver.navigate(f"http://127.0.0.1:{world['public_port']}/to-dev")
    assert "dev-home" in reach_driver.page_text()


def test_a_real_click_or_script_on_the_dev_server_cannot_reach_the_sidecar(
    world: dict[str, Any], reach_driver: PlaywrightDriver
) -> None:
    elements = reach_driver.navigate(f"http://localhost:{world['dev_port']}/link")
    with pytest.raises(ValueError, match="blocked navigation"):
        reach_driver.click(elements[0].ref)
    reach_driver.navigate(f"http://localhost:{world['dev_port']}/fetch")
    reach_driver._page.wait_for_timeout(500)
    reach_driver.navigate(f"http://127.0.0.1:{world['public_port']}/img")
    assert "public" in reach_driver.page_text()
    assert world["sidecar"].hits == []
    assert reach_driver.guard.blocked_requests >= 3


def test_a_refused_navigation_does_not_cost_the_next_one(
    world: dict[str, Any], reach_driver: PlaywrightDriver
) -> None:
    """Measured 5/5 before the fix, on the driver without a reach too: the blank page queued after a
    refusal landed inside the agent's next `navigate`, which failed as "interrupted by another
    navigation to about:blank". With the site list making refusals more common, that was a page lost
    per refusal."""
    for _ in range(2):
        with pytest.raises(ValueError, match="blocked navigation"):
            reach_driver.navigate(f"http://127.0.0.1:{world['public_port']}/to-sidecar")
        reach_driver.navigate(f"http://localhost:{world['dev_port']}/")
        assert "dev-home" in reach_driver.page_text()
