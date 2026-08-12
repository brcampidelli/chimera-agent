"""The SPA fallback, against paths a filesystem refuses to be asked about.

The handler answers any unknown path with the app's entrypoint, so client-side routing works. To
know whether a path is a real asset it has to ask the filesystem — and that question is not always
safe to ask. `%00` decodes to a NUL and makes `stat` raise `ValueError`, which "is this a file"
does not catch — so a request for a page that does not exist became a 500: the server reading as
broken instead of the URL as wrong. Removing the guard fails the two `%00` cases below and no
others, which is the honest extent of what was reproduced.

The `:` and `|` cases are here as regression cover for path shapes some platforms are documented to
raise on. They pass either way on the interpreter measured here, so they are not evidence for the
fix — they are there so a future change that starts raising on them is caught.

The traversal guard is a separate line and was already correct — `..` and an absolute path both
resolve outside the static dir and are refused. These tests hold both facts at once, because
"we fixed the path handling" is exactly the change that could quietly widen the other one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sse_starlette")

from fastapi.testclient import TestClient  # noqa: E402

from chimera.config import Settings  # noqa: E402
from chimera.interface import ChatSession  # noqa: E402


class _FakeAgent:
    def answer(self, message: str) -> str:  # pragma: no cover - trivial
        return message


def _client(tmp_path: Path) -> TestClient:
    from chimera.api import build_api_app

    static = tmp_path / "dist"
    (static / "assets").mkdir(parents=True)
    (static / "index.html").write_text("<html><head></head><body>app</body></html>", encoding="utf-8")
    (static / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    app = build_api_app(
        lambda: ChatSession(_FakeAgent()), settings=settings, static_dir=static
    )
    return TestClient(app, raise_server_exceptions=False)


def test_a_real_asset_is_served(tmp_path: Path) -> None:
    r = _client(tmp_path).get("/assets/app.js")
    assert r.status_code == 200
    assert "console.log" in r.text


def test_an_unknown_page_gets_the_app(tmp_path: Path) -> None:
    """Client-side routing: /settings is a route in the app, not a file on disk."""
    r = _client(tmp_path).get("/settings")
    assert r.status_code == 200
    assert "app" in r.text


@pytest.mark.parametrize(
    "path",
    [
        "/x%00y",  # NUL — `stat` raises ValueError, not OSError
        "/a%00",
        "/c:/windows/win.ini",  # a colon: OSError on Windows, a plain miss elsewhere
        "/x|y",
        "/https://exemplo.com",
    ],
)
def test_a_path_the_filesystem_refuses_gets_the_app_not_a_500(tmp_path: Path, path: str) -> None:
    """The whole fix. A malformed path is an unknown path, and unknown paths get the app."""
    r = _client(tmp_path).get(path)
    assert r.status_code == 200, f"{path} answered {r.status_code}"
    assert "app" in r.text


@pytest.mark.parametrize("path", ["/../pyproject.toml", "/../../etc/passwd", "/..%2f..%2fetc/passwd"])
def test_traversal_is_still_refused(tmp_path: Path, path: str) -> None:
    """The guard that was already right, asserted so that widening it later is loud.

    Never a 200 carrying the file: either the router never matches, or the handler resolves the
    path outside `static_dir` and falls through to the app.
    """
    r = _client(tmp_path).get(path)
    assert "[project]" not in r.text, "the repo's pyproject leaked through the SPA handler"
    assert "root:" not in r.text


def test_an_unknown_api_path_is_a_404_not_the_app(tmp_path: Path) -> None:
    """Answering the SPA on /api/* would mask a wrong URL or a stale generated client as a 200."""
    r = _client(tmp_path).get("/api/nao-existe")
    assert r.status_code == 404


def test_a_malformed_api_path_is_still_a_404(tmp_path: Path) -> None:
    """The new guard must not turn an unknown API route into the app because the path was odd."""
    r = _client(tmp_path).get("/api/x%00y")
    assert r.status_code == 404


def _unused(_: Any) -> None:  # pragma: no cover - keeps the Any import honest
    return None
