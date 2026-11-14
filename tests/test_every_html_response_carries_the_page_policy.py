"""Every response that hands a browser an HTML document carries a Content-Security-Policy.

`test_the_app_page_cannot_fetch_what_an_answer_names.py` pins WHAT the app page's policy says. This
file pins WHERE a policy is sent, and it is built to notice the next place that forgets one. Three
were found while writing it (audit P1-19): FastAPI's `/redoc` and `/docs/oauth2-redirect`, left at
their defaults when the docs moved under `/api` and served outside the token guard with no policy,
and a stray `x.htm` at the bundle root, which the SPA fallback only policed when it ended in `.html`.

Three layers, because each catches what the others cannot:

* **The pages, requested.** Every HTML document the app, the guest door and the device door can
  answer with, fetched for real, and its policy compared with the one that route is meant to send.
* **The routes, enumerated.** Every route and mount that is not under `/api/` or `/v1/` — the only
  place a token-less browser navigation lands — must be one the first layer requests. A route added
  outside the API fails here until someone says what page it serves.
* **The source, counted.** Every way the backend has of producing an HTML body (`HTMLResponse`, a
  `text/html` type, `FileResponse`, a static mount), per file. A new one fails here until it is
  covered above. It is a census, not a proof: the message tells whoever trips it what to do.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sse_starlette")

from fastapi.testclient import TestClient
from starlette.routing import Mount

from chimera.api.device_door import PAIR_PAGE_CSP, DeviceGateway
from chimera.api.page_csp import (
    APP_PAGE_CSP,
    ASSET_HTML_CSP,
    DOCS_PAGE_CSP,
    guest_page_csp,
)
from chimera.config import Settings, get_settings
from chimera.interface import ChatSession

REPO = Path(__file__).resolve().parents[1]
PAGE = "<html><head><script>document.documentElement.dataset.theme = 'dark';</script></head><body>{}</body></html>"
STRAY = "<html><body><script>steal()</script></body></html>"
PHONE = ("192.168.1.20", 50123)


class _Agent:
    def answer(self, message: str) -> str:  # pragma: no cover - no turn runs here
        return message


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    from chimera.api import build_api_app

    # No token: the pages are what a browser gets before it has one, and `/api/docs` is reachable.
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", "")
    monkeypatch.delenv("CHIMERA_TAURI_SIDECAR", raising=False)
    get_settings.cache_clear()
    static = tmp_path / "dist"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text(PAGE.format("app"), encoding="utf-8")
    (static / "guest.html").write_text(PAGE.format("guest"), encoding="utf-8")
    (static / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    # Nothing the build ships, and nothing the agent can write: what a stray document would get.
    for name in ("stray.htm", "stray.xhtml", "assets/stray.html", "assets/stray.htm", "assets/stray.xhtml"):
        (static / name).write_text(STRAY, encoding="utf-8")
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"), CHIMERA_SERVER_TOKEN="")
    built = build_api_app(lambda: ChatSession(_Agent()), settings=settings, static_dir=static)
    yield built
    get_settings.cache_clear()


@dataclass(frozen=True)
class Page:
    """One HTML document the server can hand out, how to ask for it, and the policy it must carry."""

    route: str
    """The route or mount that answers it, as `_non_api_routes` names it (`""` for the device door)."""
    fetch: Callable[[Any], Any]
    policy: Callable[[Any], str]
    status: int = 200


def _owner(path: str) -> Callable[[Any], Any]:
    return lambda app: TestClient(app).get(path)


def _phone(app: Any) -> TestClient:
    gateway = DeviceGateway(app, app.state.device_registry, watch_seconds=0.05)
    return TestClient(gateway, base_url="http://192.168.1.5:8123", client=PHONE, follow_redirects=False)


def _wrong_code(app: Any) -> Any:
    app.state.device_registry.codes.mint()
    return _phone(app).post("/pair", data={"code": "AAAA-AAA2"})


def _paired_root(app: Any) -> Any:
    from chimera.api.devices import shown_code

    phone = _phone(app)
    code, _ = app.state.device_registry.codes.mint()
    assert phone.post("/pair", data={"code": shown_code(code)}).status_code == 303
    return phone.get("/")


def _design_export(app: Any) -> Any:
    client = TestClient(app)
    created = client.post("/api/design", json={"title": "t"})
    assert created.status_code == 200, created.text
    return client.post(f"/api/design/{created.json()['id']}/export", json={"format": "html", "confirm": True})


def _app_policy(_: Any) -> str:
    return APP_PAGE_CSP


def _asset_policy(_: Any) -> str:
    return ASSET_HTML_CSP


def _guest_policy(_: Any) -> str:
    return guest_page_csp(PAGE.format("guest"))


def _export_policy(_: Any) -> str:
    from chimera.design.export_html import CSP

    return CSP


PAGES: dict[str, Page] = {
    "app page": Page("/", _owner("/"), _app_policy),
    "app page by file name": Page("/{full_path:path}", _owner("/index.html"), _app_policy),
    "client route (SPA fallback)": Page("/{full_path:path}", _owner("/a/client/route"), _app_policy),
    # `%00` makes the filesystem question raise instead of answer; it still falls back to the page.
    "malformed path (SPA fallback)": Page("/{full_path:path}", _owner("/a%00b"), _app_policy),
    "guest.html by file name": Page("/{full_path:path}", _owner("/guest.html"), _app_policy),
    "stray .htm at the root": Page("/{full_path:path}", _owner("/stray.htm"), _app_policy),
    "stray .xhtml at the root": Page("/{full_path:path}", _owner("/stray.xhtml"), _app_policy),
    "stray .html under assets": Page("/assets", _owner("/assets/stray.html"), _asset_policy),
    "stray .htm under assets": Page("/assets", _owner("/assets/stray.htm"), _asset_policy),
    "stray .xhtml under assets": Page("/assets", _owner("/assets/stray.xhtml"), _asset_policy),
    "guest page": Page("/guest", _owner("/guest/"), _guest_policy),
    "stray .html under guest assets": Page("/guest", _owner("/guest/assets/stray.html"), _asset_policy),
    "API docs": Page("/api/docs", _owner("/api/docs"), lambda _: DOCS_PAGE_CSP),
    "design exported as HTML": Page("/api/design/{design_id}/export", _design_export, _export_policy),
    "pairing page": Page("", lambda app: _phone(app).get("/pair?code=ABCD-EFGH"), lambda _: PAIR_PAGE_CSP),
    "pairing page, wrong code": Page("", _wrong_code, lambda _: PAIR_PAGE_CSP, status=401),
    "app page through the device door": Page("", _paired_root, _app_policy),
}


@pytest.mark.parametrize("name", sorted(PAGES))
def test_the_page_carries_the_policy_its_route_is_meant_to_send(app: Any, name: str) -> None:
    page = PAGES[name]
    response = page.fetch(app)
    assert response.status_code == page.status, response.text[:200]
    assert response.headers["content-type"].startswith(("text/html", "application/xhtml+xml")), (
        f"{name}: not an HTML document ({response.headers['content-type']}), so this case tests nothing"
    )
    assert "content-security-policy" in response.headers, f"{name} is served with no policy"
    assert response.headers["content-security-policy"] == page.policy(app)


def test_the_documentation_pages_that_had_no_policy_are_gone(app: Any) -> None:
    # Off, not policed: nothing links to them, and `/api/docs` is the one that was meant to exist.
    client = TestClient(app)
    for path in ("/redoc", "/docs/oauth2-redirect", "/docs"):
        response = client.get(path)
        # The SPA fallback answers an unknown path with the app page — which has its policy.
        assert "swagger" not in response.text.lower() and "redoc" not in response.text.lower(), path
        assert response.headers.get("content-security-policy") == APP_PAGE_CSP, path


def _non_api_routes(routes: list[Any], prefix: str = "") -> Iterator[str]:
    """Every route and mount a browser could navigate to without a token, by its full path."""
    for route in routes:
        path = prefix + getattr(route, "path", "")
        if path.startswith(("/api/", "/v1/")) and path != "/api/docs":
            continue
        if isinstance(route, Mount):
            yield path
            continue
        if getattr(route, "path", None) is None:  # an included router's placeholder, no path of its own
            continue
        yield path


def test_every_route_outside_the_api_is_a_page_this_file_requests(app: Any) -> None:
    covered = {page.route for page in PAGES.values()}
    # Mounts are reached by prefix; their own HTML is requested above. The guest door's other routes
    # (its API, its icon, its schema) sit under the `/guest` mount, which is covered as a whole.
    found = set(_non_api_routes(app.routes))
    unknown = sorted(found - covered)
    assert not unknown, (
        f"routes outside /api/ that no case in PAGES requests: {unknown}. If one can answer with HTML, "
        "add a Page for it with the policy it must send; if it cannot, add a Page that proves it."
    )


# ---- the census ----------------------------------------------------------------------------------

#: How many times each file builds an HTML body (or serves files that could be one). The pages above
#: request every one of them; when a count changes, find the new site and cover it in PAGES first.
HTML_SITES: dict[str, int] = {
    "chimera/api/app.py": 5,  # app page, /api/docs, FileResponse x2 in the SPA fallback, /assets mount
    "chimera/api/guest_api.py": 3,  # guest page, its icon (a PNG), /guest/assets mount
    "chimera/api/device_door.py": 1,  # the pairing page (GET, wrong code, address shut out)
    "chimera/api/page_csp.py": 3,  # the policed StaticFiles subclass, its return, its docstring
    "chimera/design/exports.py": 2,  # the HTML export (attachment, via design_api) and its media type
}
_SITE = re.compile(r"HTMLResponse\(|FileResponse\(|StaticFiles\(|static_files_with_policy\(|text/html")


def _sites() -> dict[str, int]:
    out: dict[str, int] = {}
    for file in sorted((REPO / "chimera").rglob("*.py")):
        count = 0
        for line in file.read_text(encoding="utf-8").splitlines():
            code = line.split("#", 1)[0]
            if re.match(r"\s*(from|import)\s", code) or "def static_files_with_policy" in code:
                continue
            count += len(_SITE.findall(code))
        if count:
            out[file.relative_to(REPO).as_posix()] = count
    return out


def test_every_place_that_builds_an_html_body_is_counted() -> None:
    found = _sites()
    # The design exporter writes the document itself; its own <meta> policy is tested with it.
    found.pop("chimera/design/export_html.py", None)
    # A docstring in fs_api names text/html as what `guess_type` answers; that module refuses HTML.
    found.pop("chimera/api/fs_api.py", None)
    assert found == HTML_SITES, (
        "the places that can hand a browser an HTML document changed. Cover the new one in PAGES "
        "(with the policy it must send), then update HTML_SITES."
    )
