"""The permission-mode route and the bridge's turn, through the real API app.

What these pin: the PATCH stores a mode for the owner, refuses bypass (400) and an unknown
conversation (404), refuses a paired device that would widen (403), and answers an unreadable
conversation file with a handled error rather than a 500. A bridged turn runs under the stored mode,
and "Accept edits" never runs a command without a card even when ``CHIMERA_HOST_EXEC=allow``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")

from tests.test_code_permission_screen_security import (
    _bridged,
    _bridged_turn,
    _saved_conversation,
    _shell_app,
    _ShellAgent,
)


def _patch(client: Any, session: str, mode: str) -> Any:
    from chimera.api.screen_credential import HEADER_NAME

    return client.patch(
        f"/api/code/sessions/{session}/permission-mode", json={"mode": mode},
        headers={HEADER_NAME: "screen-secret"},
    )


def _owner(app: Any) -> Any:
    from fastapi.testclient import TestClient

    return TestClient(app)


def test_the_owner_stores_a_mode(tmp_path: Path, monkeypatch: Any) -> None:
    app, project, _ = _shell_app(tmp_path, monkeypatch)
    _saved_conversation(app, "m-1", project, switch_on=False)
    response = _patch(_owner(app), "m-1", "accept_edits")
    assert response.status_code == 200, response.text
    assert response.json()["permission_mode"] == "accept_edits"
    assert app.state.code_sessions.stored_permission_mode("m-1") == "accept_edits"


def test_bypass_is_refused_with_400(tmp_path: Path, monkeypatch: Any) -> None:
    app, project, _ = _shell_app(tmp_path, monkeypatch)
    _saved_conversation(app, "m-2", project, switch_on=False)
    assert _patch(_owner(app), "m-2", "bypass").status_code == 400
    assert _patch(_owner(app), "m-2", "nonsense").status_code == 400
    assert app.state.code_sessions.stored_permission_mode("m-2") == ""


def test_an_unknown_conversation_is_404(tmp_path: Path, monkeypatch: Any) -> None:
    app, _, _ = _shell_app(tmp_path, monkeypatch)
    assert _patch(_owner(app), "no-such", "manual").status_code == 404


def test_a_bridge_may_narrow_but_not_widen(tmp_path: Path, monkeypatch: Any) -> None:
    app, project, _ = _shell_app(tmp_path, monkeypatch)
    _saved_conversation(app, "m-3", project, switch_on=False)
    # No stored mode and no Settings approval: the conversation is in Auto (workspace, suspicious).
    assert _patch(_bridged(app), "m-3", "dont_ask").status_code == 403
    # Accept edits raises the reach to workspace_shell, which Auto does not have: also a widening.
    assert _patch(_bridged(app), "m-3", "accept_edits").status_code == 403
    assert _patch(_bridged(app), "m-3", "manual").status_code == 200
    assert app.state.code_sessions.stored_permission_mode("m-3") == "manual"


def test_an_unreadable_conversation_file_is_a_handled_error_not_a_500(tmp_path: Path, monkeypatch: Any) -> None:
    app, project, _ = _shell_app(tmp_path, monkeypatch)
    _saved_conversation(app, "m-4", project, switch_on=False)
    app.state.code_sessions._path("m-4").write_text("{not json", encoding="utf-8")
    response = _patch(_owner(app), "m-4", "manual")
    assert response.status_code == 409, response.text


def test_accept_edits_asks_before_a_command_even_with_host_exec_allow(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # `local` sandbox: an OS sandbox would isolate the run and skip the card by design.
    # The resolver returns None for `allow` before it looks at `ask`; the mode's promise must hold.
    monkeypatch.setenv("CHIMERA_SANDBOX", "local")
    app, project, _ = _shell_app(tmp_path, monkeypatch, CHIMERA_REACH="workspace_shell")
    _ShellAgent.taint = False
    _saved_conversation(app, "m-5", project, switch_on=False)
    assert _patch(_owner(app), "m-5", "accept_edits").status_code == 200
    _bridged_turn(_bridged(app), "m-5", project)
    assert len(_ShellAgent.outcomes) == 1
    assert not (project / "marker.txt").exists(), _ShellAgent.outcomes


def test_without_a_mode_the_same_turn_still_runs_its_command(tmp_path: Path, monkeypatch: Any) -> None:
    # The control: same app, same grant, no stored mode. If this did not run, the test above would
    # prove nothing about the mode.
    monkeypatch.setenv("CHIMERA_SANDBOX", "local")
    app, project, _ = _shell_app(tmp_path, monkeypatch, CHIMERA_REACH="workspace_shell")
    _ShellAgent.taint = False
    _saved_conversation(app, "m-6", project, switch_on=False)
    _bridged_turn(_bridged(app), "m-6", project)
    assert len(_ShellAgent.outcomes) == 1
    assert (project / "marker.txt").exists(), _ShellAgent.outcomes


def test_accept_edits_runs_without_a_card_where_the_sandbox_isolates_the_command(
    tmp_path: Path, monkeypatch: Any
) -> None:
    # The caveat in the mode's text: an isolating sandbox is the boundary, so no card is drawn.
    from chimera.sandbox.local import LocalSandbox

    monkeypatch.setenv("CHIMERA_SANDBOX", "local")
    monkeypatch.setattr(LocalSandbox, "is_isolated", staticmethod(lambda: True))
    app, project, _ = _shell_app(tmp_path, monkeypatch, CHIMERA_REACH="workspace_shell")
    _ShellAgent.taint = False
    _saved_conversation(app, "m-7", project, switch_on=False)
    assert _patch(_owner(app), "m-7", "accept_edits").status_code == 200
    _bridged_turn(_bridged(app), "m-7", project)
    assert (project / "marker.txt").exists(), _ShellAgent.outcomes
