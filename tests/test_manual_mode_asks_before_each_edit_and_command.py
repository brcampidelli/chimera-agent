"""Manual asks before every file edit and every command, on the turn's own card, before it happens.

Through the real turn: a real ``POST /api/code/turn`` from the owner's screen, the registry the API
built for it, the real write tools, and the real ``POST /api/approvals/{id}`` that answers the card.
Only the model is faked: its "agent" calls the tools itself, in the order a test scripts.

What these pin: an edit asks and shows the diff it would make; "allow once" covers one call;
"allow in this conversation" covers that tool in the conversation and is gone when the mode changes;
a refusal writes nothing; a turn with no screen refuses at once, naming why, and writes no question;
read-only calls never ask; and Manual asks about what the reach allows and never widens it.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from chimera.api.manual_gate import (  # noqa: E402
    NEEDS_SCREEN,
    ManualAllowances,
    proposed_diff,
)
from chimera.api.screen_credential import HEADER_NAME  # noqa: E402
from tests.test_code_permission_screen_security import _bridged, _saved_conversation  # noqa: E402

MANUAL = {"reach": "workspace", "approval": "always", "commands": "asks", "edits": "asks"}
AUTO = {"reach": "workspace", "approval": "suspicious", "commands": "granted", "edits": "granted"}
SCREEN = {HEADER_NAME: "screen-secret"}


class _ScriptedAgent:
    """Calls the scripted tools through the registry the API built, and records what each returned."""

    calls: list[tuple[str, dict[str, Any]]] = []
    outcomes: list[str] = []

    def __init__(self, *args: Any, **_kwargs: Any) -> None:
        self.registry = args[1] if len(args) > 1 else None

    def run(self, task: str, **_kw: Any) -> Any:
        from chimera.core.agent import AgentResult

        cls = type(self)
        for name, arguments in cls.calls:
            cls.outcomes.append(str(self.registry.get(name).run(**arguments)))
        return AgentResult(
            answer="done", steps=1, stopped_reason="final",
            transcript=[{"role": "user", "content": task}, {"role": "assistant", "content": "done"}],
        )


def _app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **env: str) -> tuple[Any, Path, Path]:
    import chimera.core
    from chimera.api.app import build_api_app
    from chimera.api.screen_credential import ScreenCredential
    from chimera.config import Settings, get_settings
    from chimera.interface import ChatSession

    home = tmp_path / "home"
    project = tmp_path / "shop"
    project.mkdir()
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    monkeypatch.setattr(chimera.core, "Agent", _ScriptedAgent, raising=True)
    _ScriptedAgent.calls = []
    _ScriptedAgent.outcomes = []
    settings = Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json", CHIMERA_APPROVAL_WAIT=0.5, **env)
    app = build_api_app(
        lambda: ChatSession(_ScriptedAgent()), workspace=tmp_path, settings=settings,
        screen=ScreenCredential("screen-secret"),
    )
    return app, project, home


class _Owner:
    """The person at the card: answers each question that appears, in the order given, and keeps what
    each one showed. ``None`` as an answer means "leave it"; the thread stops when the list is spent."""

    def __init__(self, app: Any, home: Path, answers: list[dict[str, Any]]) -> None:
        self.app, self.home, self.answers = app, home, answers
        self.seen: list[Any] = []
        self.thread = threading.Thread(target=self._loop, daemon=True)

    def __enter__(self) -> _Owner:
        self.thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.thread.join(20.0)

    def _loop(self) -> None:
        from chimera.governance.pending import pending

        client = TestClient(self.app)
        answered: set[str] = set()
        deadline = time.monotonic() + 30.0
        while self.answers and time.monotonic() < deadline:
            for question in pending(self.home):
                if question.id in answered or not self.answers:
                    continue
                answered.add(question.id)
                self.seen.append(question)
                body = self.answers.pop(0)
                response = client.post(f"/api/approvals/{question.id}", json=body, headers=SCREEN)
                assert response.status_code == 200, response.text
            time.sleep(0.02)


def _turn(client: Any, session: str, project: Path, posture: dict[str, str], **headers: str) -> None:
    response = client.post(
        "/api/code/turn",
        json={
            "message": "change the file", "session_id": session, "workspace": str(project),
            "stream": False, "posture": posture,
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text


def _write(path: str, content: str) -> tuple[str, dict[str, Any]]:
    return "write_file", {"path": path, "content": content}


def _questions(home: Path) -> list[Any]:
    from chimera.governance.pending import pending

    return pending(home)


# --- An edit asks, and the card shows the change --------------------------------------------------


def test_an_edit_asks_with_the_diff_and_runs_only_after_a_yes(tmp_path: Path, monkeypatch: Any) -> None:
    app, project, home = _app(tmp_path, monkeypatch)
    (project / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
    _saved_conversation(app, "man-1", project, switch_on=False)
    _ScriptedAgent.calls = [_write("a.txt", "one\nthree\n")]
    with _Owner(app, home, [{"approved": True}]) as owner:
        _turn(TestClient(app), "man-1", project, MANUAL, **SCREEN)
    assert len(owner.seen) == 1, "the edit did not ask"
    card = owner.seen[0]
    assert card.action.startswith("write_file: a.txt")
    assert "-two" in card.action and "+three" in card.action, card.action
    assert card.allow_key == "write_file:a.txt"
    assert (project / "a.txt").read_text(encoding="utf-8") == "one\nthree\n"


def test_a_refusal_writes_nothing(tmp_path: Path, monkeypatch: Any) -> None:
    app, project, home = _app(tmp_path, monkeypatch)
    (project / "a.txt").write_text("keep\n", encoding="utf-8")
    _saved_conversation(app, "man-2", project, switch_on=False)
    _ScriptedAgent.calls = [_write("a.txt", "lost\n"), _write("b.txt", "new\n")]
    with _Owner(app, home, [{"approved": False}, {"approved": False}]) as owner:
        _turn(TestClient(app), "man-2", project, MANUAL, **SCREEN)
    assert len(owner.seen) == 2
    assert (project / "a.txt").read_text(encoding="utf-8") == "keep\n"
    assert not (project / "b.txt").exists()
    assert all("declined" in out for out in _ScriptedAgent.outcomes), _ScriptedAgent.outcomes


def test_allow_once_covers_one_call(tmp_path: Path, monkeypatch: Any) -> None:
    app, project, home = _app(tmp_path, monkeypatch)
    _saved_conversation(app, "man-3", project, switch_on=False)
    _ScriptedAgent.calls = [_write("a.txt", "1\n"), _write("b.txt", "2\n")]
    with _Owner(app, home, [{"approved": True}, {"approved": False}]) as owner:
        _turn(TestClient(app), "man-3", project, MANUAL, **SCREEN)
    assert len(owner.seen) == 2, "a yes once must not cover the next edit"
    assert (project / "a.txt").exists() and not (project / "b.txt").exists()


# --- Allow in this conversation: bounded, and undone by a mode change -----------------------------


def test_allow_in_this_conversation_covers_that_file_and_ends_with_the_mode(
    tmp_path: Path, monkeypatch: Any
) -> None:
    app, project, home = _app(tmp_path, monkeypatch)
    _saved_conversation(app, "man-4", project, switch_on=False)
    client = TestClient(app)

    _ScriptedAgent.calls = [_write("a.txt", "1\n"), _write("a.txt", "2\n")]
    with _Owner(app, home, [{"approved": True, "remember": "conversation"}]) as owner:
        _turn(client, "man-4", project, MANUAL, **SCREEN)
    assert len(owner.seen) == 1, "the same file asked again after 'allow in this conversation'"
    assert (project / "a.txt").read_text(encoding="utf-8") == "2\n"
    assert app.state.manual_allowances.allowed("man-4", "write_file:a.txt")

    # A later turn of the same conversation, still Manual: that file no longer asks...
    _ScriptedAgent.calls = [_write("./a.txt", "3\n")]
    _turn(client, "man-4", project, MANUAL, **SCREEN)
    assert _questions(home) == [] and (project / "a.txt").read_text(encoding="utf-8") == "3\n"

    # ...but another file does, and another conversation never inherits it.
    _ScriptedAgent.calls = [_write("b.txt", "x\n")]
    with _Owner(app, home, [{"approved": False}]) as other_file:
        _turn(client, "man-4", project, MANUAL, **SCREEN)
    assert len(other_file.seen) == 1 and not (project / "b.txt").exists()
    assert not app.state.manual_allowances.allowed("man-other", "write_file:a.txt")

    # A turn in another mode drops it, and Manual asks again afterwards.
    _ScriptedAgent.calls = [_write("d.txt", "4\n")]
    _turn(client, "man-4", project, AUTO, **SCREEN)
    assert not app.state.manual_allowances.allowed("man-4", "write_file:a.txt")
    _ScriptedAgent.calls = [_write("a.txt", "5\n")]
    with _Owner(app, home, [{"approved": False}]) as again:
        _turn(client, "man-4", project, MANUAL, **SCREEN)
    assert len(again.seen) == 1 and (project / "a.txt").read_text(encoding="utf-8") == "3\n"


def test_changing_the_stored_mode_drops_what_was_allowed(tmp_path: Path, monkeypatch: Any) -> None:
    app, project, _home = _app(tmp_path, monkeypatch)
    _saved_conversation(app, "man-5", project, switch_on=False)
    app.state.manual_allowances.allow("man-5", "edit_file:a.txt")
    client = TestClient(app)
    same = client.patch("/api/code/sessions/man-5/permission-mode", json={"mode": "manual"}, headers=SCREEN)
    assert same.status_code == 200 and app.state.manual_allowances.allowed("man-5", "edit_file:a.txt")
    other = client.patch("/api/code/sessions/man-5/permission-mode", json={"mode": "auto"}, headers=SCREEN)
    assert other.status_code == 200 and not app.state.manual_allowances.allowed("man-5", "edit_file:a.txt")


def test_an_allowance_is_for_one_tool_and_one_file_not_for_every_write(
    tmp_path: Path, monkeypatch: Any
) -> None:
    app, project, home = _app(tmp_path, monkeypatch)
    (project / "a.txt").write_text("x\n", encoding="utf-8")
    _saved_conversation(app, "man-6", project, switch_on=False)
    app.state.manual_allowances.allow("man-6", "write_file:b.txt")
    _ScriptedAgent.calls = [
        _write("b.txt", "free\n"),
        _write("c.txt", "asks\n"),
        ("edit_file", {"path": "b.txt", "old": "free", "new": "other tool"}),
    ]
    with _Owner(app, home, [{"approved": False}, {"approved": False}]) as owner:
        _turn(TestClient(app), "man-6", project, MANUAL, **SCREEN)
    assert [q.action.splitlines()[0] for q in owner.seen] == ["write_file: c.txt", "edit_file: b.txt"]
    assert (project / "b.txt").read_text(encoding="utf-8") == "free\n"


# --- No screen: refuse at once, name why, write no question ----------------------------------------


@pytest.mark.parametrize("origin", ["plain", "bridge"])
def test_a_turn_without_a_screen_refuses_at_once(tmp_path: Path, monkeypatch: Any, origin: str) -> None:
    app, project, home = _app(tmp_path, monkeypatch)
    _saved_conversation(app, "man-7", project, switch_on=False)
    _ScriptedAgent.calls = [_write("a.txt", "no\n")]
    client = _bridged(app) if origin == "bridge" else TestClient(app)
    started = time.monotonic()
    _turn(client, "man-7", project, MANUAL)
    assert time.monotonic() - started < 10.0, "a turn with no screen waited for an answer"
    assert not (project / "a.txt").exists()
    assert _ScriptedAgent.outcomes and NEEDS_SCREEN[:40] in _ScriptedAgent.outcomes[0], _ScriptedAgent.outcomes
    assert _questions(home) == [], "a question was written for nobody"


def test_a_command_in_a_turn_without_a_screen_is_refused_with_the_same_sentence(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setenv("CHIMERA_SANDBOX", "local")
    app, project, home = _app(tmp_path, monkeypatch, CHIMERA_REACH="workspace_shell", CHIMERA_HOST_EXEC="allow")
    _saved_conversation(app, "man-8", project, switch_on=False)
    posture = {**MANUAL, "reach": "workspace_shell"}
    _ScriptedAgent.calls = [("run_shell", {"command": "echo hi > marker.txt"})]
    _turn(_bridged(app), "man-8", project, posture)
    assert not (project / "marker.txt").exists()
    assert _questions(home) == []
    assert _ScriptedAgent.outcomes and NEEDS_SCREEN[:40] in _ScriptedAgent.outcomes[0], _ScriptedAgent.outcomes


# --- Commands ask on the same card ------------------------------------------------------------------


def test_a_command_asks_and_allow_in_this_conversation_is_for_that_command_only(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setenv("CHIMERA_SANDBOX", "local")
    from chimera.sandbox.local import LocalSandbox

    monkeypatch.setattr(LocalSandbox, "is_isolated", staticmethod(lambda: False))
    # The owner's grant is what lets a command exist at all; Manual then asks about it.
    app, project, home = _app(tmp_path, monkeypatch, CHIMERA_REACH="workspace_shell", CHIMERA_HOST_EXEC="allow")
    _saved_conversation(app, "man-9", project, switch_on=False)
    posture = {**MANUAL, "reach": "workspace_shell"}
    first = "python -c \"open('one.txt','w').write('1')\""
    second = "python -c \"open('two.txt','w').write('2')\""
    _ScriptedAgent.calls = [("run_shell", {"command": first}), ("run_shell", {"command": first}),
                            ("run_shell", {"command": second})]
    with _Owner(app, home, [{"approved": True, "remember": "conversation"}, {"approved": False}]) as owner:
        _turn(TestClient(app), "man-9", project, posture, **SCREEN)
    actions = [q.action for q in owner.seen]
    assert actions[0] == f"run_shell: {first}", actions
    assert len(actions) == 2 and actions[1] == f"run_shell: {second}", actions
    assert owner.seen[0].allow_key == f"run_shell:{first}"
    assert (project / "one.txt").exists() and not (project / "two.txt").exists()


# --- Reading never asks; the posture is never widened -------------------------------------------------


def test_reading_never_asks(tmp_path: Path, monkeypatch: Any) -> None:
    app, project, home = _app(tmp_path, monkeypatch)
    (project / "a.txt").write_text("hello\n", encoding="utf-8")
    _saved_conversation(app, "man-10", project, switch_on=False)
    _ScriptedAgent.calls = [("read_file", {"path": "a.txt"}), ("list_dir", {"path": "."})]
    started = time.monotonic()
    _turn(TestClient(app), "man-10", project, MANUAL, **SCREEN)
    assert time.monotonic() - started < 10.0
    assert _questions(home) == []
    assert "hello" in _ScriptedAgent.outcomes[0]


def test_a_read_only_reach_still_removes_the_write_tools_so_nothing_is_asked(
    tmp_path: Path, monkeypatch: Any
) -> None:
    app, project, home = _app(tmp_path, monkeypatch)
    _saved_conversation(app, "man-11", project, switch_on=False)
    _ScriptedAgent.calls = [_write("a.txt", "x\n")]
    posture = {**MANUAL, "reach": "read_only"}
    _turn(TestClient(app), "man-11", project, posture, **SCREEN)
    # The write tool is not in the registry at all: the scripted call fails, nothing is asked.
    assert _ScriptedAgent.outcomes == [] and _questions(home) == [] and not (project / "a.txt").exists()


def test_a_yes_cannot_write_outside_the_workspace(tmp_path: Path, monkeypatch: Any) -> None:
    # The gate is inside the tool's own jail: a yes releases the call to the tool, which still refuses
    # a path outside the project.
    app, project, home = _app(tmp_path, monkeypatch)
    _saved_conversation(app, "man-12", project, switch_on=False)
    outside = tmp_path / "outside.txt"
    _ScriptedAgent.calls = [_write("../outside.txt", "escaped\n")]
    with _Owner(app, home, [{"approved": True}]) as owner:
        _turn(TestClient(app), "man-12", project, MANUAL, **SCREEN)
    assert len(owner.seen) >= 1, "the call never reached the gate, so this proves nothing"
    assert not outside.exists()
    # The tool's own jail then asks about the path outside the project and is not answered: refused.
    assert _ScriptedAgent.outcomes == [], "the write returned a result instead of being refused"


# --- The preview and the store, unit level ----------------------------------------------------------------


def test_the_diff_of_an_edit_and_of_a_new_file(tmp_path: Path) -> None:
    (tmp_path / "f.txt").write_text("a\nb\n", encoding="utf-8")
    edit = proposed_diff(tmp_path, "edit_file", {"path": "f.txt", "old": "b", "new": "c"})
    assert "-b" in edit and "+c" in edit
    created = proposed_diff(tmp_path, "write_file", {"path": "g.txt", "content": "x\n"})
    assert "/dev/null" in created and "+x" in created
    assert proposed_diff(tmp_path, "apply_patch", {"patch": "@@ -1 +1 @@"}) == "@@ -1 +1 @@"


def test_a_long_diff_is_cut_and_says_so() -> None:
    from chimera.api.manual_gate import MAX_DIFF_CHARS, _bounded

    cut = _bounded("x" * (MAX_DIFF_CHARS + 50))
    assert "50 more characters not shown" in cut


def test_allowances_are_per_conversation_and_clearable() -> None:
    store = ManualAllowances()
    store.allow("s1", "write_file")
    assert store.allowed("s1", "write_file") and not store.allowed("s2", "write_file")
    assert not store.allowed("", "write_file")
    store.clear("s1")
    assert not store.allowed("s1", "write_file")
