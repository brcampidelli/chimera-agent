"""The pages Chimera serves carry a Content-Security-Policy, and its image rule names no other host.

The desktop window is this server's own `index.html` (Tauri loads it as an external URL), and it
was served with no policy, so `"csp": null` in `tauri.conf.json` was never the setting in force. An
answer rendered as Markdown could carry `![x](https://host/?d=<secret>)` and the WebView fetched
it by itself, outside the taint ledger and `CHIMERA_EGRESS_ALLOW`. These tests pin the header on
every route that hands out a page, and pin the directives that close that channel — plus the ones
the HTML preview needs, because the preview frame inherits this policy and a "tightening" that
drops them blanks every `render_chart` page in the viewer.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sse_starlette")

from fastapi.testclient import TestClient  # noqa: E402

from chimera.api.page_csp import guest_page_csp, inline_script_hashes  # noqa: E402
from chimera.config import Settings  # noqa: E402
from chimera.interface import ChatSession  # noqa: E402

THEME_SCRIPT = "\n      (function () { document.documentElement.dataset.theme = 'dark'; })();\n    "


class _FakeAgent:
    def answer(self, message: str) -> str:  # pragma: no cover - trivial
        return message


def _client(tmp_path: Path) -> TestClient:
    from chimera.api import build_api_app

    static = tmp_path / "dist"
    (static / "assets").mkdir(parents=True)
    page = f"<html><head><script>{THEME_SCRIPT}</script></head><body>app</body></html>"
    (static / "index.html").write_text(page, encoding="utf-8")
    (static / "guest.html").write_text(page.replace(">app<", ">guest<"), encoding="utf-8")
    (static / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    return TestClient(build_api_app(lambda: ChatSession(_FakeAgent()), settings=settings, static_dir=static))


def _directives(header: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for part in header.split(";"):
        words = part.split()
        if words:
            out[words[0]] = words[1:]
    return out


def _sha256(text: str) -> str:
    return "'sha256-" + base64.b64encode(hashlib.sha256(text.encode()).digest()).decode() + "'"


@pytest.mark.parametrize("path", ["/", "/a/client/route", "/index.html"])
def test_every_route_that_hands_out_the_app_page_sends_the_policy(tmp_path: Path, path: str) -> None:
    # `/index.html` is the file reached by name through the SPA fallback — the one way around the
    # page handler, so it must not be the one way around the policy.
    r = _client(tmp_path).get(path)
    assert r.status_code == 200 and "app" in r.text
    assert "content-security-policy" in r.headers


def test_an_image_in_an_answer_can_only_come_from_this_origin_or_be_inline(tmp_path: Path) -> None:
    csp = _directives(_client(tmp_path).get("/").headers["content-security-policy"])
    assert csp["img-src"] == ["'self'", "data:", "blob:"]
    assert csp["default-src"] == ["'self'"]
    # The other ways a page can make a request without asking: plugins, forms, a <base> that
    # rewrites every relative URL, and a frame that navigates itself to somebody else's host.
    assert csp["object-src"] == ["'none'"]
    assert csp["form-action"] == ["'self'"]
    assert csp["base-uri"] == ["'self'"]
    assert csp["frame-src"] == ["'none'"]


def test_connections_reach_this_origin_and_only_the_remotes_the_servers_screen_accepts(tmp_path: Path) -> None:
    # The Servers screen accepts https:// anywhere and http:// only on loopback, and the app talks
    # to the active one from this page — so those are the sources, and plain http to a real host
    # (where the token would cross the network in clear) is not among them.
    connect = _directives(_client(tmp_path).get("/").headers["content-security-policy"])["connect-src"]
    assert connect == ["'self'", "https:", "http://127.0.0.1:*", "http://localhost:*"]
    assert "*" not in connect and "http:" not in connect


def test_the_preview_frame_still_gets_what_a_chart_needs(tmp_path: Path) -> None:
    # A srcdoc frame inherits this policy, and both must allow a script for it to run. render_chart
    # writes an inline script that loads Vega from jsDelivr, and Vega compiles expressions with
    # `new Function` — measured in Edge: without 'unsafe-eval' HERE the chart draws nothing.
    script = _directives(_client(tmp_path).get("/").headers["content-security-policy"])["script-src"]
    assert {"'self'", "'unsafe-inline'", "'unsafe-eval'", "https://cdn.jsdelivr.net"} <= set(script)
    # And nothing broader: a scheme or a wildcard here would let any host's script into the page.
    assert not {"*", "https:", "http:", "data:"} & set(script)


def test_the_guest_page_sends_its_own_policy_pinned_to_its_inline_script(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/guest/")
    assert r.status_code == 200 and "guest" in r.text
    csp = _directives(r.headers["content-security-policy"])
    assert csp["img-src"] == ["'self'", "data:", "blob:"]
    assert csp["connect-src"] == ["'self'"]
    # No preview, no remotes: the guest page's scripts are its bundle and the exact theme script.
    assert csp["script-src"] == ["'self'", _sha256(THEME_SCRIPT)]
    assert "'unsafe-inline'" not in csp["script-src"] and "'unsafe-eval'" not in csp["script-src"]


def test_a_script_hash_survives_windows_line_endings() -> None:
    # The browser normalises CRLF to LF before hashing a script. A checkout with Windows line
    # endings hashing the raw bytes would block the theme script on one OS and nowhere else.
    lf = "<script>\nvar a = 1;\nvar b = 2;\n</script>"
    assert inline_script_hashes(lf.replace("\n", "\r\n")) == inline_script_hashes(lf) == [_sha256("\nvar a = 1;\nvar b = 2;\n")]


def test_only_inline_scripts_are_hashed() -> None:
    html = '<script type="module" src="/assets/guest.js"></script><script>x()</script>'
    assert inline_script_hashes(html) == [_sha256("x()")]
    assert "'sha256-" not in guest_page_csp('<script src="/a.js"></script>')
