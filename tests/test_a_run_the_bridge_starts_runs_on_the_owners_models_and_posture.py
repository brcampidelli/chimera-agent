"""A run the bridge starts runs on the owner's models and reaches no further than his posture.

Two decisions of the owner on 2026-10-04, at EVERY bridge tier:

* which model answers is his: the model choices became owner-only settings the bridge may only
  suggest, and a per-run field that picks one (`model`, `roles`, `profile`, `fuse`, the fusion
  roles, `cascade`, `verifier_model`, an outside `provider`) is the same choice made one run at a
  time — the review found every one of them still free at the operate tier;
* how far a run reaches is his: a posture wider than his (a reach past it, a looser approval), host
  execution or a ``verify`` shell command where his reach has no shell, and ``auto_approve``, are
  refused — Full control included, which until then could send any posture. Equal or narrower
  passes: a client may still ask a run to do less.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.api.bridge_routes import MODEL_CHOICE_KEYS, model_choices_in, wider_than
from chimera.server.desktop_mcp import DesktopMCP
from tests.test_the_desktop_bridge_reaches_only_its_table import (
    _app,
    _call,
    _capture_route,
    _grant,
)

TIERS = pytest.mark.parametrize("full", [False, True], ids=["operate", "full"])
CHOICES: dict[str, Any] = {
    "model": "openrouter/other/model",
    "roles": {"edit": "openrouter/other/model"},
    "profile": "max",
    "fuse": True,
    "fusion_panel": ["openrouter/a/b"],
    "fusion_judge": "openrouter/a/b",
    "fusion_synthesizer": "openrouter/a/b",
    "cascade": True,
    "verifier_model": "openrouter/a/b",
    "provider": "codex",
    "provider_command": "codex exec",
    "retry_of": {"blocked_model": "openrouter/a/b"},
}


def test_the_model_choice_set_is_the_audited_one() -> None:
    assert set(CHOICES) == MODEL_CHOICE_KEYS


@TIERS
@pytest.mark.parametrize("key", sorted(CHOICES))
def test_no_tier_chooses_the_model_of_a_run(
    key: str, full: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, full=full)
    seen = _capture_route(app, monkeypatch)
    with TestClient(app) as client:
        flat = _call(client, app, "test.seams", body={"task": "x", key: CHOICES[key]})
        nested = _call(
            client, app, "kanban.add_card", body={"title": "t", "extra": [{key: CHOICES[key]}]}
        )
    for refused in (flat, nested):
        assert refused.status_code == 403, refused.text
        assert refused.json()["detail"].startswith("which model answers is the owner's decision")
        assert key in refused.json()["detail"]
    assert seen == []


@TIERS
def test_the_real_routes_that_start_work_refuse_a_model(
    full: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, full=full)
    cases = [
        ("conversations.send", {"message": "hi", "model": "a/b"}),
        ("runs.start", {"task": "x", "roles": {"edit": "a/b"}}),
        ("chat.send", {"message": "hi", "fuse": True}),
        ("agents.batch", {"tasks": [{"task": "x"}], "profile": "max"}),
        ("orchestration.hierarchy", {"task": "x", "verifier_model": "a/b"}),
        ("lifecycle.start", {"task": "x", "fusion_judge": "a/b"}),
        ("kanban.run", {"model": "a/b"}),
    ]
    with TestClient(app) as client:
        for route, body in cases:
            got = _call(client, app, route, body=body, wait_seconds=0)
            assert got.status_code == 403, (route, got.text)


@TIERS
def test_an_unset_or_false_choice_is_no_choice(
    full: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, full=full)
    seen = _capture_route(app, monkeypatch)
    with TestClient(app) as client:
        got = _call(
            client,
            app,
            "test.seams",
            body={"task": "x", "fuse": False, "cascade": False, "model": None, "roles": None},
        )
    assert got.status_code == 200 and len(seen) == 1


@TIERS
@pytest.mark.parametrize(
    "posture",
    [
        {"reach": "workspace_shell", "approval": "suspicious"},
        {"reach": "workspace", "approval": "never"},
        {"reach": "workspace_shell", "approval": "never"},
        {"reach": "everything", "approval": "always"},
        {"reach": "workspace", "approval": "whenever"},
    ],
)
def test_no_tier_widens_the_owners_posture(
    posture: dict[str, str], full: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, full=full)  # the owner's: workspace / suspicious
    seen = _capture_route(app, monkeypatch)
    with TestClient(app) as client:
        got = _call(client, app, "test.seams", body={"task": "x", "posture": posture})
    assert got.status_code == 403, got.text
    assert got.json()["detail"].startswith("the posture is the owner's decision")
    assert seen == []


@TIERS
@pytest.mark.parametrize(
    "posture",
    [
        {"reach": "workspace", "approval": "suspicious"},  # equal
        {"reach": "read_only", "approval": "suspicious"},
        {"reach": "workspace", "approval": "always"},
        {"reach": "read_only", "approval": "always"},
        {"reach": "workspace"},  # approval left out means the default, which is the owner's
    ],
)
def test_any_tier_may_ask_for_the_owners_posture_or_a_narrower_one(
    posture: dict[str, str], full: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, full=full)
    seen = _capture_route(app, monkeypatch)
    with TestClient(app) as client:
        got = _call(client, app, "test.seams", body={"task": "x", "posture": posture})
    assert got.status_code == 200, got.text
    assert seen[0]["posture"] == posture


def test_an_approval_left_out_is_wider_than_an_owner_who_always_asks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, full=True, CHIMERA_APPROVAL="always")
    seen = _capture_route(app, monkeypatch)
    with TestClient(app) as client:
        got = _call(client, app, "test.seams", body={"task": "x", "posture": {"reach": "read_only"}})
    assert got.status_code == 403 and "posture.approval" in got.json()["detail"]
    assert seen == []


@TIERS
def test_host_execution_and_verify_follow_the_owners_shell_grant(
    full: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _app(tmp_path, monkeypatch, full=full)
    seen = _capture_route(app, monkeypatch)
    granted, plain = tmp_path / "granted", tmp_path / "plain"
    granted.mkdir()
    plain.mkdir()
    _grant(tmp_path / "home", granted)
    with TestClient(app) as client:
        no_shell = [
            _call(client, app, "test.seams", body={"task": "x", "workspace": str(plain), key: v})
            for key, v in (("allow_host_exec", True), ("verify", "pytest -q"))
        ]
        with_shell = [
            _call(client, app, "test.seams", body={"task": "x", "workspace": str(granted), key: v})
            for key, v in (("allow_host_exec", True), ("verify", "pytest -q"))
        ]
        auto = _call(client, app, "test.seams", body={"task": "x", "auto_approve": True})
    assert [r.status_code for r in no_shell] == [403, 403]
    assert [r.status_code for r in with_shell] == [200, 200]
    assert auto.status_code == 403
    assert len(seen) == 2


def test_a_route_that_only_describes_is_not_policed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`app.posture` explains what a posture would mean and `app.roles` which models a profile
    gives; they start nothing, so naming a wide posture or a profile there chooses nothing."""
    app = _app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        described = _call(
            client, app, "app.posture", body={"reach": "workspace_shell", "approval": "never"}
        )
        roles = _call(client, app, "app.roles", body={"profile": "max"})
    assert described.status_code == 200 and described.json()["status"] == 200
    assert roles.status_code == 200


def test_the_scans_read_nested_bodies_and_ignore_empty_values() -> None:
    assert model_choices_in({"tasks": [{"task": "a", "model": "x/y"}]}) == ["model"]
    assert model_choices_in({"model": "", "fuse": False, "roles": None}) == []
    assert wider_than(
        {"tasks": [{"posture": {"reach": "workspace_shell"}}]},
        reach="workspace",
        approval="suspicious",
    ) == ["posture.reach"]
    assert wider_than({"posture": "anything"}, reach="workspace", approval="suspicious") == [
        "posture"
    ]
    assert wider_than(
        {"posture": {"reach": "workspace_shell", "approval": "never"}, "allow_host_exec": True},
        reach="workspace_shell",
        approval="never",
    ) == []


def test_the_mcp_send_tool_offers_no_model_and_says_the_posture_only_narrows() -> None:
    found = {"url": "http://127.0.0.1:65003", "token": "t", "pid": 1, "version": "x", "full": True}
    calls: list[Any] = []

    def http(method: str, url: str, token: str, body: Any, timeout: float) -> tuple[int, Any]:
        calls.append(body)
        return 200, {"route": "conversations.send", "status": 200, "job": {"done": True}}

    mcp = DesktopMCP(discover=lambda: found, http=http)
    send = next(s for s in mcp.tool_specs() if s["name"] == "desktop_send")
    assert "model" not in send["inputSchema"]["properties"]
    assert "only to NARROW" in send["inputSchema"]["properties"]["posture"]["description"]
    mcp.dispatch("desktop_send", {"message": "go", "model": "a/b"})
    assert "model" not in calls[0]["body"]
