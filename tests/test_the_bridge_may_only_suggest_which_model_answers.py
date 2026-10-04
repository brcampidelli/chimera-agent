"""The bridge may suggest which model answers; only the owner, in the app, makes it so.

The owner's decision of 2026-10-04: the model choices (every ``*_MODEL``, the fallback chain, the
fusion roles, the cost mode, the cascade, verified answers) and whether the app runs scheduled jobs
are the owner's to WRITE, but the desktop bridge may SUGGEST a change. A bridge ``settings.edit``
naming only those keys writes nothing; it leaves a card in the approval queue with the key, the value
now and the value proposed (`chimera/governance/setting_suggestions.py`).

What is held here:

* per key: the bridge's edit writes nothing — not ``.env``, not the process — and leaves a card with
  exactly the key, the current value and the proposed one; below Full control it is refused;
* the owner's yes in the app writes exactly the proposed value; the owner's no writes nothing;
* the card cannot be approved by the bridge itself, by any path: its answering route, every other
  route of its ``approve`` area, a request it forwards to the app's own answering route, the
  operate tier, the chat's one-time codes, ``chimera approve --yes``, an answer file dropped into the
  queue, or the guest door;
* the yes is checked again when it lands: a key that moved since the card was written makes it
  ``stale``, a value that fails a check now makes it ``invalid``, a card past its day ``expired`` —
  and none of the three writes anything.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from chimera.api import build_api_app
from chimera.api.bridge_routes import ROUTES, SUGGESTABLE_SETTINGS
from chimera.api.desktop_bridge import asgi_call
from chimera.config import get_settings
from chimera.governance import setting_suggestions
from chimera.governance.pending import (
    SETTINGS_SUGGESTION,
    answer,
    answer_with_code,
    history,
    pending,
)
from chimera.interface import ChatSession
from chimera.interface.session import SupportsRun

URL = "http://127.0.0.1:65007"

#: Each suggestable key with the value it starts at and the value a client proposes.
VALUES: dict[str, tuple[str, str]] = {
    "CHIMERA_DEFAULT_MODEL": ("openrouter/vendor/before", "openrouter/vendor/after"),
    "CHIMERA_WEAK_MODEL": ("openrouter/vendor/weak-a", "openrouter/vendor/weak-b"),
    "CHIMERA_MID_MODEL": ("openrouter/vendor/mid-a", "openrouter/vendor/mid-b"),
    "CHIMERA_ORCHESTRATOR_MODEL": ("openrouter/vendor/orch-a", "openrouter/vendor/orch-b"),
    "CHIMERA_FALLBACK_MODELS": ("openrouter/a/one", "openrouter/b/two,openrouter/c/three"),
    "CHIMERA_EMBED_MODEL": ("openrouter/openai/embed-a", "ollama/nomic-embed-text"),
    "CHIMERA_COMPLETE_MODEL": ("ollama/base-a", "ollama/base-b"),
    "CHIMERA_VOICE_MODEL": ("openrouter/vendor/voice-a", "openrouter/vendor/voice-b"),
    "CHIMERA_VOICE_WORK_MODEL": ("openrouter/vendor/work-a", "openrouter/vendor/work-b"),
    "CHIMERA_FUSION_PANEL": (
        "openrouter/a/one,openrouter/b/two",
        "openrouter/c/three,openrouter/d/four",
    ),
    "CHIMERA_FUSION_JUDGE": ("openrouter/judge/a", "openrouter/judge/b"),
    "CHIMERA_FUSION_SYNTHESIZER": ("openrouter/synth/a", "openrouter/synth/b"),
    "CHIMERA_COST_MODE": ("auto", "premium"),
    "CHIMERA_CASCADE": ("false", "true"),
    "CHIMERA_VERIFIED_ANSWERS": ("true", "false"),
    "CHIMERA_APP_CRON": ("false", "true"),
}
KEY = "CHIMERA_DEFAULT_MODEL"
BEFORE, AFTER = VALUES[KEY]


def _app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, full: bool = True, **env: str) -> Any:
    """A real app with the bridge on. Every key a test may write is owned through ``monkeypatch``
    first: ``patch_config`` sets ``os.environ`` for real."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", "")
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE", "true")
    monkeypatch.setenv("CHIMERA_DESKTOP_BRIDGE_FULL", "true" if full else "false")
    monkeypatch.setenv(KEY, BEFORE)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    app = build_api_app(lambda: ChatSession(cast(SupportsRun, None)))
    app.state.desktop_bridge.attach(URL)
    return app


def _call(client: TestClient, app: Any, route: str, **kw: Any) -> Any:
    headers = {"Authorization": f"Bearer {app.state.desktop_bridge.token}"}
    return client.post("/api/bridge/call", json={"route": route, **kw}, headers=headers)


def _env_file(tmp_path: Path) -> str:
    env = tmp_path / ".env"
    return env.read_text(encoding="utf-8") if env.exists() else ""


def _home(tmp_path: Path) -> Path:
    return tmp_path / "home"


def _suggest(client: TestClient, app: Any, body: dict[str, str] | None = None) -> str:
    made = _call(client, app, "settings.edit", body=body or {KEY: AFTER})
    assert made.status_code == 200, made.text
    assert made.json()["status"] == 202, made.text
    return str(made.json()["data"]["suggestion"])


def _untouched(tmp_path: Path, key: str = KEY, value: str = BEFORE) -> None:
    """Nothing reached ``.env`` or the process, and the card is still waiting."""
    assert f"{key}=" not in _env_file(tmp_path)
    assert os.environ[key] == value


# ---- the card ------------------------------------------------------------------------------------


@pytest.mark.parametrize("key", sorted(VALUES))
def test_the_bridge_suggests_it_writes_nothing_and_the_owners_yes_writes_exactly_it(
    key: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert set(VALUES) == SUGGESTABLE_SETTINGS
    initial, proposed = VALUES[key]
    app = _app(tmp_path, monkeypatch, **{key: initial})

    with TestClient(app) as client:
        made = _call(client, app, "settings.edit", body={key: proposed})
        assert made.status_code == 200 and made.json()["status"] == 202, made.text
        data = made.json()["data"]
        assert data["written"] == []
        assert data["changes"] == [{"key": key, "current": initial, "proposed": proposed}]
        _untouched(tmp_path, key, initial)

        # The owner's queue shows it as the change itself, with who suggested it.
        listed = client.get("/api/approvals").json()
        assert [q["id"] for q in listed] == [data["suggestion"]]
        card = listed[0]
        assert card["kind"] == SETTINGS_SUGGESTION
        assert card["suggestion"]["changes"] == [
            {"key": key, "current": initial, "proposed": proposed}
        ]
        assert card["suggestion"]["suggested_by"] == "desktop_bridge"
        hint = app.state.desktop_bridge.hint()
        assert hint and card["suggestion"]["client_hint"] == hint
        assert app.state.desktop_bridge.token not in json.dumps(card)

        answered = client.post(f"/api/approvals/{card['id']}", json={"approved": True})
    assert answered.status_code == 200
    assert answered.json() == {"ok": True, "outcome": "applied", "detail": key}
    assert f"{key}={proposed}" in _env_file(tmp_path).splitlines()
    assert os.environ[key] == proposed
    assert pending(_home(tmp_path)) == []
    get_settings.cache_clear()


def test_below_full_control_a_suggestion_is_refused_and_no_card_is_left(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, full=False)
    with TestClient(app) as client:
        refused = _call(client, app, "settings.edit", body={KEY: AFTER})
    assert refused.status_code == 403
    _untouched(tmp_path)
    assert pending(_home(tmp_path)) == []


def test_the_owners_no_writes_nothing_and_retires_the_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        request_id = _suggest(client, app)
        answered = client.post(f"/api/approvals/{request_id}", json={"approved": False})
    assert answered.json() == {"ok": True, "outcome": "refused", "detail": ""}
    _untouched(tmp_path)
    assert pending(_home(tmp_path)) == []
    line = history(_home(tmp_path))[-1]
    assert (line["id"], line["outcome"], line["answered_via"]) == (request_id, "refused", "app")


def test_a_second_click_on_an_applied_card_is_a_stale_click(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        request_id = _suggest(client, app)
        first = client.post(f"/api/approvals/{request_id}", json={"approved": True})
        second = client.post(f"/api/approvals/{request_id}", json={"approved": True})
    assert first.json()["outcome"] == "applied"
    # No longer a suggestion, so the ordinary answer: nothing is waiting under that id.
    assert second.json() == {"ok": False}
    get_settings.cache_clear()


def test_a_value_a_save_would_refuse_is_refused_before_any_card_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, CHIMERA_CASCADE="false")
    with TestClient(app) as client:
        bad_bool = _call(client, app, "settings.edit", body={"CHIMERA_CASCADE": "maybe"})
        newline = _call(client, app, "settings.edit", body={KEY: "a/b\nCHIMERA_REACH=x"})
        a_key = _call(
            client, app, "settings.edit", body={KEY: "sk-or-v1-" + "a1b2c3d4e5f6" * 4}
        )
        not_text = _call(client, app, "settings.edit", body={KEY: 3})
    for refused in (bad_bool, newline, a_key, not_text):
        assert refused.status_code == 400, refused.text
    assert "looks like a credential" in a_key.json()["detail"]
    assert pending(_home(tmp_path)) == []
    _untouched(tmp_path)


def test_a_suggestion_of_the_value_already_set_leaves_no_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        same = _call(client, app, "settings.edit", body={KEY: BEFORE})
    assert same.json()["status"] == 200
    assert same.json()["data"] == {"suggestion": None, "unchanged": [KEY], "written": []}
    assert pending(_home(tmp_path)) == []


# ---- the yes is checked again when it lands ----------------------------------------------------


def test_a_card_whose_setting_moved_since_is_stale_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The yes was given to a change FROM the value shown. If the owner (or anything) changed the
    setting in between, applying would overwrite a decision made after the card was read."""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        request_id = _suggest(client, app)
        moved = client.patch("/api/config", json={KEY: "openrouter/vendor/owners-own"})
        assert moved.status_code == 200
        answered = client.post(f"/api/approvals/{request_id}", json={"approved": True})
    assert answered.json() == {"ok": True, "outcome": "stale", "detail": KEY}
    assert os.environ[KEY] == "openrouter/vendor/owners-own"
    assert f"{KEY}={AFTER}" not in _env_file(tmp_path)
    assert pending(_home(tmp_path)) == []
    get_settings.cache_clear()


def test_a_value_that_fails_a_check_now_is_invalid_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Re-validated at apply time by the same checks a save makes, not by a copy of them."""
    from chimera.api import config_api

    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        request_id = _suggest(client, app)

        def refuses(value: str) -> None:
            raise ValueError(f"{KEY} is not offered any more")

        monkeypatch.setitem(config_api._VALUE_CHECKS, KEY, refuses)
        answered = client.post(f"/api/approvals/{request_id}", json={"approved": True})
    assert answered.json() == {
        "ok": True,
        "outcome": "invalid",
        "detail": f"{KEY} is not offered any more",
    }
    _untouched(tmp_path)
    assert pending(_home(tmp_path)) == []


def test_a_card_for_a_setting_that_is_not_suggestable_is_never_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A card is data in a folder: one written by an older build, by hand, or before a key moved to
    the owner's flat refusal names a key no suggestion may change. The owner's yes on it writes
    nothing — the classification is read when the card is applied, not when it was made."""
    app = _app(tmp_path, monkeypatch, CHIMERA_REACH="read_only")
    request_id = setting_suggestions.suggest(
        _home(tmp_path),
        [setting_suggestions.Change("CHIMERA_REACH", "read_only", "workspace_shell")],
        suggested_by="desktop_bridge",
    )
    with TestClient(app) as client:
        answered = client.post(f"/api/approvals/{request_id}", json={"approved": True})
    assert answered.json()["outcome"] == "invalid"
    assert os.environ["CHIMERA_REACH"] == "read_only"
    assert "CHIMERA_REACH=" not in _env_file(tmp_path)


def test_a_card_past_its_day_is_expired_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        request_id = _suggest(client, app)
        path = _home(tmp_path) / "approvals" / f"{request_id}.ask.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["suggestion"]["expires_at"] = time.time() - 1
        path.write_text(json.dumps(data), encoding="utf-8")
        answered = client.post(f"/api/approvals/{request_id}", json={"approved": True})
    assert answered.json() == {"ok": True, "outcome": "expired", "detail": ""}
    _untouched(tmp_path)
    assert history(_home(tmp_path))[-1]["outcome"] == "timeout"


def test_an_expired_card_leaves_the_list_as_a_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        request_id = _suggest(client, app)
        later = time.time() + setting_suggestions.TTL_SECONDS + 1
        monkeypatch.setattr(setting_suggestions.time, "time", lambda: later)
        listed = client.get("/api/approvals").json()
    assert listed == []
    assert history(_home(tmp_path))[-1]["id"] == request_id
    assert history(_home(tmp_path))[-1]["outcome"] == "timeout"


# ---- the bridge cannot approve its own suggestion ------------------------------------------------


def test_the_bridges_answering_route_refuses_a_suggestion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        request_id = _suggest(client, app)
        refused = _call(
            client,
            app,
            "approve.approval",
            params={"request_id": request_id},
            body={"approved": True},
        )
        listed = client.get("/api/approvals").json()
    assert refused.status_code == 403
    assert "owner in the app" in refused.json()["detail"]
    assert [q["id"] for q in listed] == [request_id]
    _untouched(tmp_path)


def test_no_route_of_the_approve_area_resolves_a_suggestion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every full-control answering route, aimed at the card's id in every placeholder it has."""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        request_id = _suggest(client, app)
        for route_id, route in ROUTES.items():
            if not route_id.startswith(("approve.", "approvals.")):
                continue
            params = {name: request_id for name in re.findall(r"{(\w+)}", route.path)}
            for body in (
                {"approved": True},
                {"action": "accept"},
                {"status": "active"},
                {"event": False},
            ):
                _call(client, app, route_id, params=params, body=body)
        listed = client.get("/api/approvals").json()
    assert [q["id"] for q in listed] == [request_id], "a bridge route resolved the card"
    _untouched(tmp_path)


def test_a_request_the_bridge_forwards_to_the_owners_answering_route_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second lock, on its own: the app's answering route reads the bridge's mark in the ASGI
    scope. This is what still holds if a bridge route ever reaches that handler some other way."""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        request_id = _suggest(client, app)
        status, raw = client.portal.call(  # type: ignore[union-attr]
            lambda: asgi_call(
                app, "POST", f"/api/approvals/{request_id}", body={"approved": True}
            )
        )
        owner = client.post(f"/api/approvals/{request_id}", json={"approved": True})
    assert status == 403, raw
    assert b"never through the desktop bridge" in raw
    # The owner's own request, a moment later, still applies it: the refusal was about who asked.
    assert owner.json()["outcome"] == "applied"
    get_settings.cache_clear()


def test_the_operate_tier_cannot_reach_the_answering_route_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, full=False)
    request_id = setting_suggestions.suggest(
        _home(tmp_path),
        [setting_suggestions.Change(KEY, BEFORE, AFTER)],
        suggested_by="desktop_bridge",
    )
    with TestClient(app) as client:
        refused = _call(
            client,
            app,
            "approve.approval",
            params={"request_id": request_id},
            body={"approved": True},
        )
    assert refused.status_code == 403
    assert setting_suggestions.is_suggestion(_home(tmp_path), request_id)
    _untouched(tmp_path)


def test_a_file_written_through_the_bridge_into_the_queue_is_refused_and_would_apply_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        request_id = _suggest(client, app)
        answer_file = _home(tmp_path) / "approvals" / f"{request_id}.answer.json"
        written = _call(
            client,
            app,
            "files.write",
            body={"path": str(answer_file), "content": json.dumps({"approved": True})},
        )
        refused_too = _call(
            client,
            app,
            "files.write",
            body={
                "path": f"approvals/{request_id}.answer.json",
                "content": json.dumps({"approved": True}),
                "workspace": str(_home(tmp_path)),
            },
        )
    assert refused_too.status_code == 403
    assert written.status_code == 403, written.text
    assert not answer_file.exists()
    assert setting_suggestions.is_suggestion(_home(tmp_path), request_id)
    _untouched(tmp_path)


def test_an_answer_file_in_the_queue_applies_nothing_and_the_owner_can_still_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What applies every other question is an answer file appearing. Not this one: whoever can
    write into the queue — an agent with a shell, a script — answers nothing here."""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        request_id = _suggest(client, app)
        directory = _home(tmp_path) / "approvals"
        assert answer(_home(tmp_path), request_id, True, via="app") is False
        assert not (directory / f"{request_id}.answer.json").exists()
        (directory / f"{request_id}.answer.json").write_text(
            json.dumps({"approved": True, "answered_at": time.time()}), encoding="utf-8"
        )
        listed = client.get("/api/approvals").json()
        _untouched(tmp_path)
        owner = client.post(f"/api/approvals/{request_id}", json={"approved": False})
    assert [q["id"] for q in listed] == [request_id]
    assert owner.json()["outcome"] == "refused"
    assert not (directory / f"{request_id}.answer.json").exists()
    _untouched(tmp_path)


def test_a_chat_code_never_answers_a_suggestion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No code is issued for one; a code planted on the card is not honoured either."""
    from chimera.governance.pending import _code_hash

    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        request_id = _suggest(client, app)
    path = _home(tmp_path) / "approvals" / f"{request_id}.ask.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert "code_hash" not in data
    data["code_hash"] = _code_hash(request_id, "123456")
    data["expires_at"] = time.time() + 600
    path.write_text(json.dumps(data), encoding="utf-8")

    outcome = answer_with_code(_home(tmp_path), request_id, "123456", True, via="discord:1")
    assert outcome == "no_code"
    assert not (path.parent / f"{request_id}.answer.json").exists()
    assert setting_suggestions.is_suggestion(_home(tmp_path), request_id)
    _untouched(tmp_path)


def test_chimera_approve_yes_refuses_a_suggestion_and_no_retires_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The terminal can refuse one, never approve it: the app's own save is what applies a
    change to the RUNNING app, and a shell the agent was given could run this line."""
    from chimera.cli.main import app as cli

    monkeypatch.setenv("CHIMERA_HOME", str(_home(tmp_path)))
    monkeypatch.setenv(KEY, BEFORE)
    get_settings.cache_clear()
    request_id = setting_suggestions.suggest(
        _home(tmp_path),
        [setting_suggestions.Change(KEY, BEFORE, AFTER)],
        suggested_by="desktop_bridge",
    )
    runner = CliRunner()

    yes = runner.invoke(cli, ["approve", request_id, "--yes"])
    assert yes.exit_code == 1
    assert "approve it in the" in yes.output
    assert setting_suggestions.is_suggestion(_home(tmp_path), request_id)

    no = runner.invoke(cli, ["approve", request_id, "--no"])
    assert no.exit_code == 0, no.output
    assert not setting_suggestions.is_suggestion(_home(tmp_path), request_id)
    assert history(_home(tmp_path))[-1]["answered_via"] == "cli"
    assert os.environ[KEY] == BEFORE
    get_settings.cache_clear()


def test_the_guest_door_has_no_answering_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        request_id = _suggest(client, app)
        for path in (f"/guest/api/approvals/{request_id}", f"/guest/approvals/{request_id}"):
            got = client.post(path, json={"approved": True})
            assert got.status_code in (401, 403, 404, 405), (path, got.status_code)
        listed = client.get("/api/approvals").json()
    assert [q["id"] for q in listed] == [request_id]
    _untouched(tmp_path)
