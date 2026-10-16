"""A code-less yes comes from the owner's SCREEN, never from a token that is on disk.

``POST /api/approvals/{id}`` with ``approved: true`` approves a question this process asked from
the code it keeps in memory — the app's card, one click. Until 2026-10-16 the route asked only for
an owner credential, and since S1 one of those is ``<home>/service.token``: a file any local process
can read, the agent's own shell included. A ``cat`` of it and a ``curl`` approved the agent's own
question with no code, which is the bypass the per-question code (study 30, S30-30) closes
everywhere else. The click now needs the screen credential (`chimera/api/screen_credential.py`).

What each test would catch if the rule broke:

* the service token (or any bearer token) alone approves a question this process asked;
* the screen credential stops approving — the app's one-click card breaks;
* the owner's ``.env`` token, offered as the screen credential, is taken as one;
* a refusal starts needing a code;
* the desktop bridge's "Full control" yes, or the guest door, changes behaviour;
* the credential reaches disk, ``/api/info``, the page of a backend that is not serving a browser,
  or a child process.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from chimera.api import build_api_app
from chimera.api import screen_credential as sc
from chimera.api.desktop_bridge import asgi_call
from chimera.config import Settings, get_settings
from chimera.core.service_record import load_or_create_token
from chimera.governance import pending
from chimera.interface import ChatSession
from chimera.sandbox.local import _child_env

WAIT = 20.0


class _Asked:
    """A question this process asked — what a turn in the app's own backend does."""

    def __init__(self, home: Path) -> None:
        self.result: bool | None = None

        def run() -> None:
            self.result = pending.ask_durably(
                home, "run_shell\ngit push --force", "policy: force push",
                on_asked=lambda *_: None, wait_seconds=WAIT, poll_seconds=0.05,
            )

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + WAIT
        while time.monotonic() < deadline and not pending.pending(home):
            time.sleep(0.01)
        assert pending.pending(home), "precondition: the question was written"
        self.id = pending.pending(home)[0].id
        assert pending.approvable_here(self.id), "precondition: this process holds its code"
        self.home = home

    def waiting(self) -> bool:
        return pending.is_waiting(self.home, self.id)

    def outcome(self) -> bool | None:
        self.thread.join(WAIT)
        assert not self.thread.is_alive(), "the asker never resolved"
        return self.result


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    folder = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(folder))
    monkeypatch.delenv(sc.ENV_VAR, raising=False)
    monkeypatch.delenv("CHIMERA_TAURI_SIDECAR", raising=False)
    # Recorded so `_app`'s token is undone at teardown: the guard reads the PROCESS settings
    # (`get_settings()`), not the injected ones, so the token has to be there to be enforced.
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", "")
    get_settings.cache_clear()
    yield folder
    get_settings.cache_clear()


def _app(home: Path, *, token: str | None, screen: sc.ScreenCredential | None) -> Any:
    os.environ["CHIMERA_SERVER_TOKEN"] = token or ""
    get_settings.cache_clear()
    settings = Settings(
        CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json", CHIMERA_SERVER_TOKEN=token or ""
    )
    return build_api_app(
        lambda: ChatSession(None),  # type: ignore[arg-type]  # no turn runs here
        workspace=home.parent, settings=settings, screen=screen,
    )


def _post(client: TestClient, request_id: str, approved: bool, **headers: str) -> dict[str, Any]:
    got = client.post(f"/api/approvals/{request_id}", json={"approved": approved}, headers=headers)
    assert got.status_code == 200, got.text
    return dict(got.json())


def _bearer(token: str) -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


def test_the_service_token_alone_does_not_approve_a_question_this_process_asked(home: Path) -> None:
    """The service's own process asked; the caller holds the token from `service.token` on disk."""
    token = load_or_create_token(home)
    assert (home / "service.token").read_text(encoding="utf-8").strip() == token
    asked = _Asked(home)

    with TestClient(_app(home, token=token, screen=sc.for_mode("service"))) as client:
        answered = _post(client, asked.id, True, **_bearer(token))
        assert answered["ok"] is False and answered["outcome"] == "needs_code", answered
        assert answered["reason"] == "not_the_screen"
        assert f"chimera approve {asked.id} --yes --code" in answered["detail"]
        assert asked.waiting(), "the token's yes resolved the question"
        # A remembered rule is a yes too, and must not slip past on its own branch.
        remembered = client.post(
            f"/api/approvals/{asked.id}", json={"approved": True, "remember": "project"},
            headers=_bearer(token),
        ).json()
        assert remembered["outcome"] == "needs_code" and asked.waiting()
        # Offering the token itself as the screen credential does not make it one.
        again = _post(client, asked.id, True, **_bearer(token), **{sc.HEADER_NAME: token})
        assert again["outcome"] == "needs_code" and asked.waiting()
        # A refusal needs no code, from any owner credential.
        assert _post(client, asked.id, False, **_bearer(token)) == {"ok": True}

    assert asked.outcome() is False


def test_the_screen_credential_approves_with_one_click(home: Path) -> None:
    token = "owner-bearer-token-0123456789"
    screen = sc.for_mode("app", server_token=token)
    assert screen.value and screen.value != token
    asked = _Asked(home)

    with TestClient(_app(home, token=token, screen=screen)) as client:
        # Without it, the same request is refused; with a wrong one, too.
        assert _post(client, asked.id, True, **_bearer(token))["outcome"] == "needs_code"
        assert _post(client, asked.id, True, **_bearer(token), **{sc.HEADER_NAME: "nope"})["outcome"] == "needs_code"
        assert asked.waiting()
        assert _post(client, asked.id, True, **_bearer(token), **{sc.HEADER_NAME: screen.value}) == {"ok": True}

    assert asked.outcome() is True


def test_the_screen_credential_does_not_replace_the_owners_token(home: Path) -> None:
    """Beside the bearer token, never instead of it: the screen header alone is a 401."""
    token = "owner-bearer-token-0123456789"
    screen = sc.for_mode("app", server_token=token)
    asked = _Asked(home)
    assert screen.value
    with TestClient(_app(home, token=token, screen=screen)) as client:
        got = client.post(f"/api/approvals/{asked.id}", json={"approved": True}, headers={sc.HEADER_NAME: screen.value})
        assert got.status_code == 401
        assert _post(client, asked.id, False, **_bearer(token)) == {"ok": True}
    assert asked.outcome() is False


def test_the_owners_env_token_is_never_a_screen_credential(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The desktop shell reuses an owner's `.env` token as the bearer token. Handed in as the screen
    credential too — the defect this would be — the backend refuses it, and a click needs the code."""
    dotenv = "the-owners-dotenv-token-0123456789"
    monkeypatch.setenv("CHIMERA_TAURI_SIDECAR", "1")
    monkeypatch.setenv(sc.ENV_VAR, dotenv)
    screen = sc.for_mode("app", server_token=dotenv)
    assert screen.value is None
    assert sc.ENV_VAR not in os.environ, "the variable outlived the read"
    asked = _Asked(home)

    with TestClient(_app(home, token=dotenv, screen=screen)) as client:
        answered = _post(client, asked.id, True, **_bearer(dotenv), **{sc.HEADER_NAME: dotenv})
        assert answered["outcome"] == "needs_code" and answered["reason"] == "not_the_screen"
        assert asked.waiting()
        assert _post(client, asked.id, False, **_bearer(dotenv)) == {"ok": True}
    assert asked.outcome() is False


def test_the_desktop_shells_value_is_the_screen_credential(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHIMERA_TAURI_SIDECAR", "1")
    monkeypatch.setenv(sc.ENV_VAR, "fresh-per-launch-screen-0123456789")
    screen = sc.for_mode("app", server_token="the-owners-dotenv-token")
    assert screen.value == "fresh-per-launch-screen-0123456789" and not screen.in_page
    # A service adopts nothing from the environment, whatever is there.
    monkeypatch.setenv(sc.ENV_VAR, "left-in-the-environment")
    assert sc.for_mode("service").value is None
    assert sc.ENV_VAR not in os.environ


def test_the_bridge_and_another_processes_questions_are_unchanged(home: Path) -> None:
    """The bridge's "Full control" yes still approves an in-process question, with no header: it
    marks its own request in the ASGI scope. A question asked by another process still needs the
    code from every door, the screen included."""
    token = "owner-bearer-token-0123456789"
    screen = sc.for_mode("app", server_token=token)
    assert screen.value
    app = _app(home, token=token, screen=screen)
    asked = _Asked(home)
    with TestClient(app) as client:
        status, raw = client.portal.call(  # type: ignore[union-attr]
            lambda: asgi_call(
                app, "POST", f"/api/approvals/{asked.id}", body={"approved": True}, headers=_bearer(token)
            )
        )
        assert status == 200 and json.loads(raw) == {"ok": True}, raw
    assert asked.outcome() is True

    other = _Asked(home)
    pending._forget_code(other.id)  # asked by a terminal run: this app never held its code
    with TestClient(app) as client:
        answered = _post(client, other.id, True, **_bearer(token), **{sc.HEADER_NAME: screen.value})
        assert answered["outcome"] == "needs_code" and answered["reason"] == "other_process"
        assert _post(client, other.id, False, **_bearer(token)) == {"ok": True}
    assert other.outcome() is False


def test_the_guest_door_still_cannot_answer(home: Path) -> None:
    token = "owner-bearer-token-0123456789"
    screen = sc.for_mode("app", server_token=token)
    assert screen.value
    asked = _Asked(home)
    with TestClient(_app(home, token=token, screen=screen)) as client:
        for path in (f"/guest/api/approvals/{asked.id}", f"/guest/approvals/{asked.id}"):
            got = client.post(path, json={"approved": True}, headers={sc.HEADER_NAME: screen.value})
            assert got.status_code in (401, 403, 404, 405), (path, got.status_code)
        assert asked.waiting()
        assert _post(client, asked.id, False, **_bearer(token)) == {"ok": True}
    assert asked.outcome() is False


def test_the_page_carries_the_credential_only_where_it_serves_a_browser(
    home: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><head></head><body></body></html>", encoding="utf-8")
    settings = Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json", CHIMERA_SERVER_TOKEN="tok-0123456789")

    def page(screen: sc.ScreenCredential) -> str:
        app = build_api_app(
            lambda: ChatSession(None),  # type: ignore[arg-type]  # no turn runs here
            workspace=tmp_path, settings=settings, static_dir=dist, screen=screen,
        )
        with TestClient(app, client=("127.0.0.1", 50000)) as client:
            return client.get("/").text

    browser = sc.for_mode("app")
    assert browser.value and f'name="chimera-screen" content="{browser.value}"' in page(browser)
    assert "chimera-screen" not in page(sc.for_mode("service"))
    monkeypatch.setenv("CHIMERA_TAURI_SIDECAR", "1")
    monkeypatch.setenv(sc.ENV_VAR, "shell-screen-0123456789")
    native = sc.for_mode("app", server_token="tok-0123456789")
    shown = page(native)
    assert "chimera-screen" not in shown and "shell-screen-0123456789" not in shown


def test_no_child_process_inherits_the_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHIMERA_TAURI_SIDECAR", "1")
    monkeypatch.setenv(sc.ENV_VAR, "inherit-me-not-0123456789")
    assert sc.for_mode("app").value == "inherit-me-not-0123456789"
    assert sc.ENV_VAR not in os.environ and sc.ENV_VAR not in _child_env()
    # A child started with the default environment — not only the agent's shell, which strips
    # TOKEN-named variables anyway — sees nothing either.
    seen = subprocess.run(
        [sys.executable, "-c", f"import os; print(os.environ.get({sc.ENV_VAR!r}, ''))"],
        capture_output=True, text=True, check=True, timeout=60,
    ).stdout.strip()
    assert seen == ""


def test_a_running_backend_never_writes_the_credential_down(tmp_path: Path) -> None:
    """The real `chimera app` as the desktop shell starts it: the screen credential arrives in the
    environment, approves nothing it should not, and is nowhere on disk afterwards — not in the
    data folder, the discovery records, the logs — nor in `/api/info`."""
    from tests.service_harness import START_TIMEOUT, ServiceSandbox

    box = ServiceSandbox(tmp_path)
    screen = "screen-credential-on-disk-probe-6f1c2a"
    owner = "owner-token-for-the-disk-probe-0123456789"
    port_file = tmp_path / "port.txt"
    env = {**box.env(), "CHIMERA_TAURI_SIDECAR": "1", sc.ENV_VAR: screen, "CHIMERA_SERVER_TOKEN": owner}
    proc = subprocess.Popen(
        [sys.executable, "-m", "chimera.cli.main", "app", "--no-open", "--port", "0",
         "--emit-port-file", str(port_file)],
        cwd=tmp_path, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    box.pids.add(proc.pid)
    try:
        deadline = time.monotonic() + START_TIMEOUT
        while time.monotonic() < deadline and not (port_file.exists() and port_file.read_text().strip()):
            assert proc.poll() is None, proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
            time.sleep(0.2)
        url = port_file.read_text(encoding="utf-8").strip()
        with httpx.Client(base_url=url, trust_env=False, timeout=30) as client:
            info = client.get("/api/info", headers=_bearer(owner))
            assert info.status_code == 200 and screen not in info.text
            stale = client.post(
                "/api/approvals/0123456789ab", json={"approved": True},
                headers={**_bearer(owner), sc.HEADER_NAME: screen},
            )
            assert stale.status_code == 200 and stale.json() == {"ok": False}
            assert screen not in client.get("/", headers=_bearer(owner)).text
    finally:
        proc.kill()
        proc.wait(30)
        box.close()
    leaked = [
        str(path) for path in tmp_path.rglob("*")
        if path.is_file() and screen.encode() in path.read_bytes()
    ]
    assert leaked == [], f"the screen credential was written to {leaked}"


def test_a_service_gives_up_its_credential_before_it_publishes_itself(
    home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`chimera service` builds its app as `chimera app` does, then revokes the credential before
    the record exists. Without that, its loopback page would hand a click's power to any reader."""
    from chimera.cli.service_cmd import publish_service

    monkeypatch.setenv("CHIMERA_SERVICE_DIR", str(tmp_path / "svc"))
    token = load_or_create_token(home)
    app = _app(home, token=token, screen=None)
    credential = app.state.screen_credential
    assert credential.value and credential.in_page, "precondition: built the way `chimera app` is"
    app.state.request_shutdown = lambda: None
    app.state.service_info = {"mode": "service", "id": None, "started_at": None}

    publication = publish_service(app, url="http://127.0.0.1:9", token=token, home=home, bridge="http://127.0.0.1:9")
    try:
        assert credential.value is None and not credential.in_page
        asked = _Asked(home)
        with TestClient(app) as client:
            answered = _post(client, asked.id, True, **_bearer(token))
            assert answered["outcome"] == "needs_code" and asked.waiting()
            assert _post(client, asked.id, False, **_bearer(token)) == {"ok": True}
        assert asked.outcome() is False
    finally:
        publication.close()
