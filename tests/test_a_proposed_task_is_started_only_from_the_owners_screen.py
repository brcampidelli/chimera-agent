"""A task the agent proposes is a card; only the owner's screen starts it, and taint travels with it.

What each test catches if the rule breaks:

* the tool starting something, or returning "started" for a card nobody clicked;
* a card offered on a turn with no screen to show it, or on a background work (it would vanish);
* a card whose heading or prompt runs past the limits, or a sixth card in one turn;
* a card the server did not keep: the start route trusting a title or prompt from the browser;
* a card started from another conversation, twice, or with an id that was never proposed;
* a conversation begun from a tainted card that gets the bypass, or starts without the taint;
* a failed start that uses up the card;
* the start route taking a bearer token, the desktop bridge or a headless caller as the owner's click.

Paired browser tab: a tab paired with a code from the desktop app holds the screen credential, so it
may start a card. A paired phone is not a screen (`test_a_paired_device_is_the_owner_but_not_the_screen`)
and is refused here with the same 403 as a bearer token.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.api.screen_credential import HEADER_NAME
from chimera.config import Settings, get_settings
from chimera.core.agent import AgentResult
from chimera.core.task_cards import (
    CARDS_FILE_NAME,
    KEY_FILE_NAME,
    MAX_CARDS_PER_SESSION,
    SEEN_FILE_NAME,
    TaskCardStore,
    bypass_for_turn,
    card_screen_turn,
    forget_all_stores,
    open_store,
    seed_taint,
)
from chimera.governance.ledger import TaintLedger
from chimera.interface import ChatSession
from chimera.tools.task_suggestion import (
    PER_TURN_MAX,
    PROMPT_MAX,
    PROPOSED,
    TITLE_MAX,
    WITHDRAWN,
    DismissTaskTool,
    SuggestTaskTool,
    TaskSuggestionTray,
)

TOKEN = "owner-bearer-for-the-task-card-tests"
SESSION = "s-origin"


def _store(tmp_path: Path) -> TaskCardStore:
    return TaskCardStore(tmp_path / "task_cards.json")


def _tray(
    tmp_path: Path, *, tainted: bool = False,
) -> tuple[TaskSuggestionTray, list[tuple[str, dict[str, Any]]], TaskCardStore]:
    store = _store(tmp_path)
    tray = TaskSuggestionTray(store=store)
    tray.session_id = SESSION
    tray.tainted = lambda: tainted
    seen: list[tuple[str, dict[str, Any]]] = []
    tray.emit = lambda name, data: seen.append((name, data))
    return tray, seen, store


def _suggest(tool: SuggestTaskTool, **overrides: Any) -> str:
    args: dict[str, Any] = {
        "title": "Remove the dead export",
        "tldr": "parse_legacy has no caller since 0.70.",
        "prompt": "In chimera/x.py remove parse_legacy; no caller remains. Run the test suite.",
    }
    args.update(overrides)
    return tool.run(**args)


# --- the tool ----------------------------------------------------------------------------------


def test_a_proposal_becomes_a_card_kept_by_the_server_and_nothing_is_started(tmp_path: Path) -> None:
    tray, seen, store = _tray(tmp_path)
    result = json.loads(_suggest(SuggestTaskTool(tray)))
    assert result == {"proposed": result["proposed"], "started": False}
    [(name, card)] = seen
    assert name == PROPOSED
    assert card["id"] == result["proposed"]
    assert card["prompt"].startswith("In chimera/x.py")
    kept = store.get(SESSION, card["id"])
    assert kept is not None and kept.prompt == card["prompt"] and kept.used is False


def test_a_card_proposed_after_external_content_carries_its_taint(tmp_path: Path) -> None:
    tray, seen, store = _tray(tmp_path, tainted=True)
    _suggest(SuggestTaskTool(tray))
    assert seen[0][1]["tainted"] is True
    card = store.get(SESSION, seen[0][1]["id"])
    assert card is not None and card.tainted is True


def test_a_proposal_with_no_screen_or_no_store_is_refused_not_recorded(tmp_path: Path) -> None:
    assert _suggest(SuggestTaskTool(TaskSuggestionTray(store=_store(tmp_path)))).startswith("error:")
    tray, seen, _ = _tray(tmp_path)
    tray.session_id = None
    assert _suggest(SuggestTaskTool(tray)).startswith("error:")
    assert seen == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", "x" * (TITLE_MAX + 1)),
        ("title", "   "),
        ("tldr", "x" * 281),
        ("prompt", "x" * (PROMPT_MAX + 1)),
        ("prompt", ""),
    ],
)
def test_a_card_past_its_limits_is_refused(tmp_path: Path, field: str, value: str) -> None:
    tray, seen, _ = _tray(tmp_path)
    assert _suggest(SuggestTaskTool(tray), **{field: value}).startswith("error:")
    assert seen == []


def test_one_turn_holds_at_most_five_cards_and_no_repeated_heading(tmp_path: Path) -> None:
    tray, seen, _ = _tray(tmp_path)
    tool = SuggestTaskTool(tray)
    assert _suggest(tool, title="Same heading").startswith("{")
    assert _suggest(tool, title="same heading").startswith("error:")
    for index in range(1, PER_TURN_MAX):
        assert _suggest(tool, title=f"Task {index}").startswith("{")
    assert _suggest(tool, title="one too many").startswith("error:")
    assert len(seen) == PER_TURN_MAX


def test_a_dismiss_withdraws_only_a_card_this_turn_proposed(tmp_path: Path) -> None:
    tray, seen, store = _tray(tmp_path)
    card_id = json.loads(_suggest(SuggestTaskTool(tray)))["proposed"]
    dismiss = DismissTaskTool(tray)
    assert dismiss.run(id="0123456789ab").startswith("error:")
    assert json.loads(dismiss.run(id=card_id, reason="done here"))["withdrawn"] == card_id
    assert seen[-1] == (WITHDRAWN, {"id": card_id, "reason": "done here"})
    assert store.get(SESSION, card_id) is None
    assert dismiss.run(id=card_id).startswith("error:")


def test_the_registry_offers_the_cards_only_on_a_screen_turn(tmp_path: Path) -> None:
    from chimera.api.code_api import CodeSeams, assemble_registry
    from chimera.governance.approval import ApprovalAnnouncer

    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"), CHIMERA_MEMORY_BACKEND="json")
    tray, _, _ = _tray(tmp_path)

    def names(**kwargs: Any) -> set[str]:
        registry, _ledger = assemble_registry(
            CodeSeams(), tmp_path, settings, gateway=None, steps=3, surface="api:turn", **kwargs
        )
        return {tool.name for tool in registry.tools()}

    assert {"suggest_task", "dismiss_task"} <= names(approval_sink=ApprovalAnnouncer(), task_tray=tray)
    assert "suggest_task" not in names(approval_sink=ApprovalAnnouncer(), task_tray=None)
    assert "suggest_task" not in names(task_tray=tray)  # no screen to answer on


# --- the store and the turn rules --------------------------------------------------------------


def test_a_card_is_claimed_once_and_only_by_its_own_conversation(tmp_path: Path) -> None:
    tray, seen, store = _tray(tmp_path)
    card_id = json.loads(_suggest(SuggestTaskTool(tray)))["proposed"]
    assert store.get("s-other", card_id) is None
    assert store.claim("s-other", card_id) == "missing"
    assert store.claim(SESSION, card_id) == "ok"
    assert store.claim(SESSION, card_id) == "used"
    store.release(SESSION, card_id)
    assert store.claim(SESSION, card_id) == "ok"


def test_the_store_keeps_at_most_the_cap_per_conversation_and_drops_unused_first(tmp_path: Path) -> None:
    store = _store(tmp_path)
    first = store.propose(session_id=SESSION, title="t", tldr="x", prompt="p", tainted=False)
    for index in range(MAX_CARDS_PER_SESSION - 1):
        store.propose(session_id=SESSION, title=f"t{index}", tldr="x", prompt="p", tainted=False)
    store.propose(session_id=SESSION, title="last", tldr="x", prompt="p", tainted=False)
    assert store.get(SESSION, first) is None


def test_a_tainted_conversation_stays_tainted_and_a_clean_one_is_not_marked(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.mark_started("clean", tainted=False)
    store.mark_started("bad", tainted=True)
    assert store.is_tainted_session("bad") is True
    assert store.is_tainted_session("clean") is False
    assert store.is_tainted_session(None) is False


def test_a_tainted_card_seeds_the_run_with_taint_and_turns_the_bypass_off() -> None:
    ledger = TaintLedger()
    assert ledger.run_tainted() is False
    seed_taint(ledger)
    assert ledger.run_tainted() is True
    # The project has the bypass saved: a tainted conversation still does not get it.
    assert bypass_for_turn(seeded=True, project_on=lambda: True)() is False


def test_a_clean_card_inherits_the_project_bypass_as_any_new_conversation_does() -> None:
    assert bypass_for_turn(seeded=False, project_on=lambda: True)() is True
    assert bypass_for_turn(seeded=False, project_on=lambda: False)() is False
    assert bypass_for_turn(seeded=False, project_on=None, session_bypass=True)() is True


def test_a_background_work_is_never_a_screen_turn() -> None:
    from chimera.api.works import Work

    work = Work(
        id="w1", parent="s-origin", session_id="w-session", turn_id="t1", workspace=".", title="bg",
    )
    assert card_screen_turn(background=work, via_screen=True, channel_origin="", guest=False) is False
    assert card_screen_turn(background=None, via_screen=True, channel_origin="", guest=False) is True
    assert card_screen_turn(background=None, via_screen=False, channel_origin="", guest=False) is False
    assert card_screen_turn(background=None, via_screen=True, channel_origin="discord", guest=False) is False
    assert card_screen_turn(background=None, via_screen=True, channel_origin="", guest=True) is False


# --- the start route ---------------------------------------------------------------------------


class _Idle:
    def __init__(self, *_a: Any, **kwargs: Any) -> None:
        self.config = kwargs.get("config") or (_a[2] if len(_a) > 2 else None)

    def run(self, task: str, **_kw: Any) -> AgentResult:  # pragma: no cover - no turn runs here
        return AgentResult(answer="ok", steps=1, stopped_reason="final",
                           transcript=[{"role": "user", "content": task}, {"role": "assistant", "content": "ok"}])


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import chimera.core

    forget_all_stores()
    path = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(path))
    get_settings.cache_clear()
    monkeypatch.setattr(chimera.core, "Agent", _Idle, raising=True)
    return path


@pytest.fixture
def client(tmp_path: Path, home: Path) -> TestClient:
    from chimera.api import build_api_app
    from chimera.api import screen_credential as sc

    settings = Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json", CHIMERA_SERVER_TOKEN=TOKEN)
    return TestClient(
        build_api_app(
            lambda: ChatSession(_Idle()), workspace=tmp_path, settings=settings,
            screen=sc.for_mode("app", server_token=TOKEN),
        )
    )


def _screen(client: TestClient) -> dict[str, str]:
    return {"authorization": f"Bearer {TOKEN}", HEADER_NAME: str(client.app.state.screen_credential.value)}


def _bearer() -> dict[str, str]:
    return {"authorization": f"Bearer {TOKEN}"}


def _card(home: Path, *, tainted: bool = False) -> str:
    return open_store(home / CARDS_FILE_NAME).propose(
        session_id=SESSION, title="Add the missing test", tldr="The parser has no test.",
        prompt="Write the test for the parser and run it.", tainted=tainted,
    )


def _origin_with_folder(home: Path, folder: Path) -> None:
    sessions = home / "code_sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / f"{SESSION}.json").write_text(json.dumps({"workspace": str(folder)}), encoding="utf-8")


def _body(card_id: str, **extra: Any) -> dict[str, Any]:
    return {"origin_session_id": SESSION, "card_id": card_id, **extra}


def test_the_start_route_refuses_a_bearer_token_with_the_screen_reason(client: TestClient, home: Path) -> None:
    card_id = _card(home)
    response = client.post("/api/code/task-suggestions/start", json=_body(card_id), headers=_bearer())
    assert response.status_code == 403
    assert "no screen credential" in response.json()["detail"]


def test_the_start_route_refuses_the_desktop_bridge_with_the_bridge_reason(
    client: TestClient, home: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import chimera.api.code_api as code_api

    monkeypatch.setattr(code_api, "turn_origin", lambda scope: (True, True))
    card_id = _card(home)
    response = client.post("/api/code/task-suggestions/start", json=_body(card_id), headers=_screen(client))
    assert response.status_code == 403
    assert "not through the desktop bridge" in response.json()["detail"]


def test_the_start_route_refuses_an_unknown_card_id_with_the_missing_reason(client: TestClient) -> None:
    response = client.post(
        "/api/code/task-suggestions/start", json=_body("0123456789ab"), headers=_screen(client),
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "no proposed task with this id in that conversation"


def test_the_start_route_refuses_a_card_of_another_conversation_as_missing(client: TestClient, home: Path) -> None:
    card_id = open_store(home / CARDS_FILE_NAME).propose(
        session_id="s-other", title="t", tldr="x", prompt="p", tainted=False,
    )
    response = client.post("/api/code/task-suggestions/start", json=_body(card_id), headers=_screen(client))
    assert response.status_code == 404
    assert response.json()["detail"] == "no proposed task with this id in that conversation"


def test_the_start_route_refuses_a_reused_card_with_the_already_started_reason(client: TestClient, home: Path) -> None:
    card_id = _card(home)
    store = open_store(home / CARDS_FILE_NAME)
    assert store.claim(SESSION, card_id) == "ok"
    response = client.post("/api/code/task-suggestions/start", json=_body(card_id), headers=_screen(client))
    assert response.status_code == 409
    assert response.json()["detail"] == "this proposed task was already started"


def test_the_start_route_refuses_a_conversation_with_no_folder_and_keeps_the_card(
    client: TestClient, home: Path,
) -> None:
    card_id = _card(home)
    response = client.post("/api/code/task-suggestions/start", json=_body(card_id), headers=_screen(client))
    assert response.status_code == 404
    assert "has no folder" in response.json()["detail"]
    assert open_store(home / CARDS_FILE_NAME).get(SESSION, card_id).used is False


def test_a_refused_placement_gives_the_claim_back_so_the_card_can_be_started_again(
    client: TestClient, home: Path, tmp_path: Path,
) -> None:
    # The folder exists but no registered project names it, so placement refuses. The card must not
    # be left used by a start that never happened.
    card_id = _card(home)
    folder = tmp_path / "unregistered"
    folder.mkdir()
    _origin_with_folder(home, folder)
    response = client.post("/api/code/task-suggestions/start", json=_body(card_id), headers=_screen(client))
    assert response.status_code == 400
    assert "unknown project" in response.json()["detail"]
    assert open_store(home / CARDS_FILE_NAME).get(SESSION, card_id).used is False


def test_the_start_route_validates_its_body_and_takes_no_title_or_prompt(client: TestClient, home: Path) -> None:
    card_id = _card(home)
    assert client.post(
        "/api/code/task-suggestions/start", json=_body("not-a-card"), headers=_screen(client),
    ).status_code == 422
    smuggled = _body(card_id, prompt="rm -rf /", title="pwned")
    response = client.post("/api/code/task-suggestions/start", json=smuggled, headers=_screen(client))
    # The extra fields are not part of the contract: the card's own stored prompt is what would run,
    # and the request is refused by the folder check, not started with the smuggled text.
    assert response.status_code == 404
    assert "has no folder" in response.json()["detail"]


# --- the store cannot be edited by the agent ----------------------------------------------------


def _tamper(path: Path, change: Any) -> None:
    wrapper = json.loads(path.read_text(encoding="utf-8"))
    change(wrapper["body"])
    path.write_text(json.dumps(wrapper), encoding="utf-8")


def test_the_store_survives_a_restart_with_its_signature_intact(tmp_path: Path) -> None:
    store = _store(tmp_path)
    card_id = store.propose(session_id=SESSION, title="t", tldr="x", prompt="p", tainted=True)
    store.mark_started("child", tainted=True)
    reopened = _store(tmp_path)
    card = reopened.get(SESSION, card_id)
    assert not reopened.broken and card is not None and card.tainted is True
    assert reopened.is_tainted_session("child") is True


def test_a_card_whose_taint_was_edited_on_disk_closes_the_store_after_a_restart(tmp_path: Path) -> None:
    store = _store(tmp_path)
    card_id = store.propose(session_id=SESSION, title="t", tldr="x", prompt="p", tainted=True)
    store.mark_started("child", tainted=True)
    _tamper(store.path, lambda body: body["cards"][card_id].update(tainted=False, used=False))
    reopened = _store(tmp_path)
    # Fails closed: the card cannot be started, nothing can be proposed, and every conversation that
    # exists counts as tainted, so the edit cannot buy a clean ledger or a bypass.
    assert reopened.broken is True
    assert reopened.get(SESSION, card_id) is None
    assert reopened.claim(SESSION, card_id) == "missing"
    assert reopened.is_tainted_session("child") is True and reopened.is_tainted_session("any") is True
    with pytest.raises(OSError):
        reopened.propose(session_id=SESSION, title="t", tldr="x", prompt="p", tainted=False)


def test_clearing_the_tainted_conversations_on_disk_closes_the_store(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.mark_started("child", tainted=True)
    _tamper(store.path, lambda body: body.update(card_tainted=[]))
    assert _store(tmp_path).is_tainted_session("child") is True  # not forgotten: the store is closed


def test_a_deleted_key_or_a_deleted_file_closes_the_store(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.mark_started("child", tainted=True)
    (tmp_path / KEY_FILE_NAME).unlink()
    assert _store(tmp_path).broken is True
    second = tmp_path / "second"
    other = TaskCardStore(second / CARDS_FILE_NAME)
    other.mark_started("child", tainted=True)
    (second / CARDS_FILE_NAME).unlink()
    assert TaskCardStore(second / CARDS_FILE_NAME).broken is True


def test_a_file_the_agent_rewrites_during_the_session_changes_nothing_in_memory(tmp_path: Path) -> None:
    store = open_store(tmp_path / CARDS_FILE_NAME)
    card_id = store.propose(session_id=SESSION, title="t", tldr="x", prompt="p", tainted=True)
    store.mark_started("child", tainted=True)
    (tmp_path / CARDS_FILE_NAME).write_text('{"body": {"cards": {}}, "mac": "x"}', encoding="utf-8")
    card = store.get(SESSION, card_id)
    assert card is not None and card.tainted is True
    assert store.is_tainted_session("child") is True


def test_the_shell_fence_names_the_card_file_and_its_key(tmp_path: Path) -> None:
    from chimera.core.queue_fence import reaches_governance_files

    for name in (CARDS_FILE_NAME, KEY_FILE_NAME):
        command = f"cat {tmp_path / name}"
        assert reaches_governance_files(command, home=tmp_path, cwd=tmp_path) is not None
    assert reaches_governance_files("echo task cards", home=tmp_path, cwd=tmp_path) is None


# --- taint decided by the server, at the end of the turn and by inheritance ----------------------


def test_a_card_proposed_before_a_web_read_in_the_same_turn_is_tainted_by_the_end_of_it(tmp_path: Path) -> None:
    tray, _seen, store = _tray(tmp_path, tainted=False)
    card_id = json.loads(_suggest(SuggestTaskTool(tray)))["proposed"]
    assert store.get(SESSION, card_id).tainted is False  # type: ignore[union-attr]
    store.settle_turn(SESSION, tray.proposed_ids, tainted=True)
    assert store.get(SESSION, card_id).tainted is True  # type: ignore[union-attr]


def test_a_conversation_that_read_external_content_in_an_earlier_turn_proposes_tainted_cards(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    store.settle_turn(SESSION, [], tainted=True)  # an earlier turn read the web
    tray = TaskSuggestionTray(store=store)
    tray.session_id = SESSION
    tray.tainted = lambda: False  # this turn has read nothing yet
    frames: list[tuple[str, dict[str, Any]]] = []
    tray.emit = lambda name, data: frames.append((name, data))
    _suggest(SuggestTaskTool(tray))
    assert frames[0][1]["tainted"] is True
    assert store.get(SESSION, frames[0][1]["id"]).tainted is True  # type: ignore[union-attr]


def test_a_clean_turn_settles_nothing(tmp_path: Path) -> None:
    store = _store(tmp_path)
    card_id = store.propose(session_id=SESSION, title="t", tldr="x", prompt="p", tainted=False)
    store.settle_turn(SESSION, [card_id], tainted=False)
    assert store.get(SESSION, card_id).tainted is False  # type: ignore[union-attr]


def test_a_full_conversation_of_used_cards_can_still_propose_and_drops_the_oldest_used(tmp_path: Path) -> None:
    store = _store(tmp_path)
    ids = [
        store.propose(session_id=SESSION, title=f"t{n}", tldr="x", prompt="p", tainted=False)
        for n in range(MAX_CARDS_PER_SESSION)
    ]
    for card_id in ids:
        assert store.claim(SESSION, card_id) == "ok"
    newest = store.propose(session_id=SESSION, title="new", tldr="x", prompt="p", tainted=False)
    assert store.get(SESSION, newest) is not None
    kept = [i for i in ids if store.get(SESSION, i) is not None]
    assert len(kept) == MAX_CARDS_PER_SESSION - 1


# --- through the real route: the turn a card starts ----------------------------------------------


class _Seen:
    """What each turn's registry was built with: (bypass on while the turn ran, run tainted)."""

    def __init__(self) -> None:
        self.turns: list[tuple[bool, bool]] = []


@pytest.fixture
def seen(monkeypatch: pytest.MonkeyPatch) -> _Seen:
    import chimera.api.code_api as code_api
    import chimera.core

    real = code_api.assemble_registry
    record = _Seen()
    pending: list[Any] = []

    def spy(*args: Any, **kwargs: Any) -> Any:
        result = real(*args, **kwargs)
        pending.append((kwargs.get("bypass_permissions"), result[1]))
        return result

    class _Reads(_Idle):
        def run(self, task: str, **kw: Any) -> AgentResult:
            switch, ledger = pending[-1]
            on = bool(switch()) if callable(switch) else bool(switch)
            record.turns.append((on, bool(ledger.run_tainted())))
            return super().run(task, **kw)

    monkeypatch.setattr(code_api, "assemble_registry", spy)
    monkeypatch.setattr(chimera.core, "Agent", _Reads, raising=True)
    return record


@pytest.fixture
def project(home: Path, tmp_path: Path) -> Path:
    """A registered project with the owner's bypass switched on, and the conversation cards come from."""
    from chimera.core.code_projects import CodeProjectRegistry

    folder = tmp_path / "proj"
    folder.mkdir()
    registry = CodeProjectRegistry(home / "code_projects.json")
    registry.register(str(folder))
    assert registry.set_bypass(str(folder), True) is None
    _origin_with_folder(home, folder)
    return folder


def _session_of(response_text: str) -> str:
    import re

    found = re.search(r'"session_id": ?"([0-9a-f]+)"', response_text)
    assert found, response_text[:300]
    return found.group(1)


def test_a_conversation_started_from_a_clean_card_inherits_the_project_bypass(
    client: TestClient, home: Path, project: Path, seen: _Seen,
) -> None:
    card_id = _card(home, tainted=False)
    response = client.post("/api/code/task-suggestions/start", json=_body(card_id), headers=_screen(client))
    assert response.status_code == 200, response.text
    assert seen.turns == [(True, False)]  # the control: the spy can see the bypass when it is on


def test_a_conversation_started_from_a_tainted_card_has_taint_and_no_bypass_in_every_turn(
    client: TestClient, home: Path, project: Path, seen: _Seen,
) -> None:
    card_id = _card(home, tainted=True)
    response = client.post("/api/code/task-suggestions/start", json=_body(card_id), headers=_screen(client))
    assert response.status_code == 200, response.text
    child = _session_of(response.text)
    assert seen.turns == [(False, True)]  # first turn: seeded taint, bypass off, project switch on
    second = client.post(
        "/api/code/turn", json={"message": "and then?", "session_id": child, "stream": False},
        headers=_screen(client),
    )
    assert second.status_code == 200, second.text
    assert seen.turns[-1] == (False, True)  # the ledger is new, the record is not
    forget_all_stores()  # and it survives a restart: the conversation is in the signed file
    assert open_store(home / CARDS_FILE_NAME).is_tainted_session(child) is True


def test_do_it_here_with_a_tainted_card_does_not_use_the_bypass_and_ignores_the_body_message(
    client: TestClient, home: Path, project: Path, seen: _Seen,
) -> None:
    card_id = _card(home, tainted=True)
    body = {
        "message": "a different message the browser made up", "session_id": SESSION,
        "task_card_id": card_id, "stream": False,
    }
    response = client.post("/api/code/turn", json=body, headers=_screen(client))
    assert response.status_code == 200, response.text
    assert seen.turns == [(False, True)]
    assert "made up" not in response.text
    # The conversation it ran in is marked, so the next plain message there keeps both rules.
    again = client.post(
        "/api/code/turn", json={"message": "next", "session_id": SESSION, "stream": False},
        headers=_screen(client),
    )
    assert again.status_code == 200, again.text
    assert seen.turns[-1] == (False, True)
    second = client.post("/api/code/turn", json=body, headers=_screen(client))
    assert second.status_code == 409  # the card is used


def test_do_it_here_needs_the_screen_and_a_card_of_that_conversation(
    client: TestClient, home: Path, project: Path, seen: _Seen,
) -> None:
    card_id = _card(home, tainted=False)
    body = {"message": "x", "session_id": SESSION, "task_card_id": card_id, "stream": False}
    assert client.post("/api/code/turn", json=body, headers=_bearer()).status_code == 403
    other = {**body, "session_id": "s-someone-else"}
    assert client.post("/api/code/turn", json=other, headers=_screen(client)).status_code == 404
    assert seen.turns == []


def test_two_clicks_at_once_start_one_conversation(
    client: TestClient, home: Path, project: Path, seen: _Seen,
) -> None:
    import threading

    card_id = _card(home, tainted=False)
    codes: list[int] = []
    gate = threading.Barrier(2)

    def click() -> None:
        gate.wait()
        codes.append(
            client.post(
                "/api/code/task-suggestions/start", json=_body(card_id), headers=_screen(client),
            ).status_code
        )

    threads = [threading.Thread(target=click) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(codes) == [200, 409]
    assert len(seen.turns) == 1


def test_a_tampered_store_refuses_the_start_route_and_do_it_here(
    client: TestClient, home: Path, project: Path, seen: _Seen,
) -> None:
    card_id = _card(home, tainted=True)
    _tamper(home / CARDS_FILE_NAME, lambda body: body["cards"][card_id].update(tainted=False))
    forget_all_stores()  # the restart that reads the edited file
    start = client.post("/api/code/task-suggestions/start", json=_body(card_id), headers=_screen(client))
    assert start.status_code == 409 and "integrity" in start.json()["detail"]
    here = client.post(
        "/api/code/turn",
        json={"message": "x", "session_id": SESSION, "task_card_id": card_id, "stream": False},
        headers=_screen(client),
    )
    assert here.status_code == 409
    assert seen.turns == []


def test_a_start_that_raises_after_the_claim_gives_the_card_back(
    client: TestClient, home: Path, project: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import chimera.api.code_api as code_api

    card_id = _card(home)

    def boom(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("the turn could not start")

    monkeypatch.setattr(code_api, "assemble_registry", boom)
    with pytest.raises(RuntimeError):
        client.post("/api/code/task-suggestions/start", json=_body(card_id), headers=_screen(client))
    assert open_store(home / CARDS_FILE_NAME).get(SESSION, card_id).used is False  # type: ignore[union-attr]


# --- the store was seen: deleting its files does not reset it; the owner is told; cleanup -------


def test_deleting_both_files_does_not_reset_the_taint_it_recorded(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.mark_started("child", tainted=True)
    (tmp_path / CARDS_FILE_NAME).unlink()
    (tmp_path / KEY_FILE_NAME).unlink()
    reopened = _store(tmp_path)
    assert reopened.broken is True and reopened.is_tainted_session("child") is True
    # The owner's reset deletes all three, and then the store starts empty and healthy.
    (tmp_path / SEEN_FILE_NAME).unlink()
    assert _store(tmp_path).broken is False


def test_the_marker_is_fenced_like_the_pair(tmp_path: Path) -> None:
    from chimera.core.queue_fence import reaches_governance_files

    assert reaches_governance_files(f"rm {tmp_path / SEEN_FILE_NAME}", home=tmp_path, cwd=tmp_path) is not None
    assert reaches_governance_files("rm task_cards.seen", home=None, cwd=tmp_path) is not None


def test_the_owner_is_told_while_the_store_is_closed(client: TestClient, home: Path) -> None:
    _card(home)
    assert client.get("/api/code/task-cards/status", headers=_bearer()).json()["broken"] is False
    forget_all_stores()
    (home / KEY_FILE_NAME).unlink()
    body = client.get("/api/code/task-cards/status", headers=_bearer()).json()
    assert body["broken"] is True
    assert set(body["files"]) == {CARDS_FILE_NAME, KEY_FILE_NAME, SEEN_FILE_NAME}


def test_deleting_a_conversation_removes_its_cards_and_taint_records(tmp_path: Path) -> None:
    from chimera.core.code_session import CodeSession, CodeSessionStore

    forget_all_stores()
    store = open_store(tmp_path / CARDS_FILE_NAME)
    mine = store.propose(session_id="gone", title="t", tldr="x", prompt="p", tainted=True)
    kept = store.propose(session_id="stays", title="t", tldr="x", prompt="p", tainted=False)
    store.mark_started("gone", tainted=True)
    sessions = CodeSessionStore(tmp_path / "code_sessions")
    sessions.save(CodeSession(_Idle(), session_id="gone", workspace=str(tmp_path)))  # type: ignore[arg-type]
    assert sessions.delete("gone") is True
    assert store.get("gone", mine) is None and store.get("stays", kept) is not None
    assert store.is_tainted_session("gone") is False
    forget_all_stores()
    assert open_store(tmp_path / CARDS_FILE_NAME).get("stays", kept) is not None  # persisted


def test_the_file_has_a_global_ceiling_and_drops_used_cards_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import chimera.core.task_cards as cards

    monkeypatch.setattr(cards, "MAX_CARDS_TOTAL", 4)
    store = _store(tmp_path)
    ids = [
        store.propose(session_id=f"s{n}", title="t", tldr="x", prompt="p", tainted=False) for n in range(4)
    ]
    assert store.claim("s2", ids[2]) == "ok"  # the only used one, though not the oldest
    store.propose(session_id="s9", title="t", tldr="x", prompt="p", tainted=False)
    assert store.get("s2", ids[2]) is None
    assert all(store.get(f"s{n}", ids[n]) is not None for n in (0, 1, 3))


def test_two_do_it_here_clicks_in_one_conversation_send_one(
    client: TestClient, home: Path, project: Path, seen: _Seen,
) -> None:
    import threading

    first, second = _card(home), _card(home)
    codes: list[int] = []
    gate = threading.Barrier(2)

    def click(card_id: str) -> None:
        gate.wait()
        body = {"message": "x", "session_id": SESSION, "task_card_id": card_id, "stream": False}
        codes.append(client.post("/api/code/turn", json=body, headers=_screen(client)).status_code)

    threads = [threading.Thread(target=click, args=(card_id,)) for card_id in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(codes) == [200, 409]
    assert len(seen.turns) == 1
