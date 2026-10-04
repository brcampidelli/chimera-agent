"""DNS rebinding: a page that re-points its own name to 127.0.0.1 is not this app's page.

Without a server token the API trusts whoever reaches loopback. A browser reaches it on behalf of
any page whose DNS name now answers 127.0.0.1 — and to the browser that page and this API are one
origin. The one header it cannot choose is ``Host``, filled from the URL it fetched; so on a
loopback bind a Host that is a DNS name is refused for every route.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from chimera.api.host_guard import host_allowed, named_hostnames, unrebindable


def _app(tmp_path: Path, bound: tuple[str, int] | None, **env: str) -> Any:
    pytest.importorskip("fastapi")
    from chimera.api import build_api_app
    from chimera.config import Settings
    from chimera.interface import ChatSession

    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"), **env)  # type: ignore[call-arg]  # alias
    app = build_api_app(lambda: ChatSession(None), settings=settings)  # type: ignore[arg-type]  # never run
    app.state.bound_address = bound
    return app


def _get(app: Any, base_url: str, path: str = "/api/skills/bundles") -> int:
    from fastapi.testclient import TestClient

    return int(TestClient(app, base_url=base_url).get(path).status_code)


@pytest.mark.parametrize(
    "base_url",
    [
        "http://attacker.example:8765",
        "http://attacker.example",
        # A literal that is not loopback cannot be how a browser reached 127.0.0.1.
        "http://10.0.0.5:8765",
    ],
)
def test_a_name_that_is_not_this_machine_is_refused_on_a_loopback_bind(base_url: str, tmp_path: Path) -> None:
    app = _app(tmp_path, ("127.0.0.1", 8765))

    assert _get(app, base_url) == 403
    # Every route, not only the ones that write: a rebound page can READ answers, too.
    assert _get(app, base_url, "/api/health") == 403


@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.0.0.1:8765",
        # (`[::1]` is covered below, at the header: Starlette's test client cannot parse an IPv6
        # base URL.)
        "http://localhost:8765",
        # The Vite dev server proxies /api without rewriting Host — another port, still this machine.
        "http://localhost:5173",
    ],
)
def test_the_names_this_machine_answers_to_are_let_through(base_url: str, tmp_path: Path) -> None:
    assert _get(_app(tmp_path, ("127.0.0.1", 8765)), base_url) == 200


def test_a_name_the_operator_named_is_let_through(tmp_path: Path) -> None:
    app = _app(tmp_path, ("127.0.0.1", 8765), CHIMERA_ALLOWED_ORIGINS="http://mybox.example:8765")

    assert _get(app, "http://mybox.example:8765") == 200
    assert _get(app, "http://attacker.example:8765") == 403


@pytest.mark.parametrize("bound", [None, ("0.0.0.0", 8765), ("192.168.1.5", 8765)])
def test_a_listener_that_is_not_loopback_only_or_not_yet_bound_is_left_alone(
    bound: tuple[str, int] | None, tmp_path: Path
) -> None:
    # A LAN bind answers to whatever the network calls this machine; a test client or the schema
    # dump has no listener at all. The rule is about a loopback listener, and there is none.
    assert _get(_app(tmp_path, bound), "http://mybox.local:8765") == 200


def test_the_rule_reads_the_header_and_not_the_port() -> None:
    named = named_hostnames(["http://mybox.example:8765", "not a url"])
    assert named == frozenset({"mybox.example"})

    assert host_allowed("127.0.0.1:1", frozenset())
    assert host_allowed("LOCALHOST", frozenset())
    assert host_allowed("[::1]", frozenset()) and host_allowed("[::1]:8765", frozenset())
    assert host_allowed("mybox.example:9", named)
    # Every browser sends a Host; a client that does not is not a page in the owner's browser.
    assert host_allowed(None, frozenset())

    assert not host_allowed("attacker.example:8765", frozenset())
    assert not host_allowed("127.0.0.1.attacker.example", frozenset())
    assert not host_allowed("[::1", frozenset())  # malformed: refused, not guessed at


def test_what_counts_as_unrebindable() -> None:
    assert unrebindable("127.0.0.1") and unrebindable("::1") and unrebindable("192.168.1.5")
    assert unrebindable("localhost")
    assert not unrebindable("testserver") and not unrebindable(None)
