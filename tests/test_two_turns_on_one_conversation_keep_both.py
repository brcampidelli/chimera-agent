"""Two turns on the same conversation both stay in it.

Found reading the code on 2026-09-30 (R3 of the review of several conversations at once). The coding
route loaded the conversation before taking its lock and saved it inside the lock, so a second turn on
the same conversation (the owner and a guest on a shared link, or two tabs) waited for the first, then
ran on the history it had loaded before the first finished, and saved it: the first turn's exchange was
gone. The comment above the lock promised exactly the protection the code did not give.

Now the conversation is read again inside the lock, the turn's receipt is written on top of what is
stored, and a save is atomic, so a reader never meets half a file (which `load` would have read as a
fresh, empty conversation).
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings, get_settings
from chimera.core.agent import AgentResult
from chimera.core.code_session import CodeSession, CodeSessionStore
from chimera.interface import ChatSession

TIMEOUT = 10.0


class _Echo:
    """Answers each task after the history it was handed; the first task waits to be released."""

    first_entered = threading.Event()
    release_first = threading.Event()
    histories: dict[str, list[str]] = {}

    def __init__(self, *_a: Any, **kwargs: Any) -> None:
        self.config = kwargs.get("config") or (_a[2] if len(_a) > 2 else None)

    def run(self, task: str, history: list[dict[str, Any]] | None = None, **_kw: Any) -> AgentResult:
        seen = [str(m.get("content")) for m in (history or [])]
        type(self).histories[task] = seen
        if task == "first":
            type(self).first_entered.set()
            assert type(self).release_first.wait(TIMEOUT)
        transcript = list(history or []) + [
            {"role": "user", "content": task},
            {"role": "assistant", "content": f"answer to {task}"},
        ]
        return AgentResult(answer=f"answer to {task}", steps=1, stopped_reason="final", transcript=transcript)


@pytest.fixture
def agent() -> type[_Echo]:
    _Echo.first_entered.clear()
    _Echo.release_first.clear()
    _Echo.histories = {}
    yield _Echo
    _Echo.release_first.set()


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type) -> tuple[TestClient, Path]:
    import chimera.core
    from chimera.api import build_api_app

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    monkeypatch.setattr(chimera.core, "Agent", agent, raising=True)
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(home), CHIMERA_MEMORY_BACKEND="json")
    app = build_api_app(lambda: ChatSession(agent()), workspace=ws, settings=settings)
    return TestClient(app), home


def _stored(home: Path, session_id: str) -> dict[str, Any]:
    matches = list(home.rglob(f"{session_id}.json"))
    assert matches, "the conversation was never stored"
    return json.loads(matches[0].read_text(encoding="utf-8"))


def test_a_second_turn_waits_for_the_first_and_keeps_its_exchange(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type[_Echo]
) -> None:
    client, home = _client(tmp_path, monkeypatch, agent)
    opened = client.post("/api/code/turn", json={"message": "hello", "stream": False})
    # The route answers as a stream of frames; the conversation's id is in the `session` frame.
    session_id = re.search(r'"session_id": ?"([^"]+)"', opened.text).group(1)  # type: ignore[union-attr]

    first = threading.Thread(
        target=lambda: client.post(
            "/api/code/turn", json={"message": "first", "session_id": session_id, "stream": False}
        ),
        daemon=True,
    )
    first.start()
    assert agent.first_entered.wait(TIMEOUT)
    # The second turn arrives while the first is working: it loads the conversation as it stands.
    second = threading.Thread(
        target=lambda: client.post(
            "/api/code/turn", json={"message": "second", "session_id": session_id, "stream": False}
        ),
        daemon=True,
    )
    second.start()
    time.sleep(0.3)
    agent.release_first.set()
    first.join(TIMEOUT)
    second.join(TIMEOUT)
    assert not first.is_alive() and not second.is_alive()

    assert "answer to first" in agent.histories["second"], "the second turn ran on a stale history"
    contents = [m.get("content") for m in _stored(home, session_id)["messages"]]
    assert "answer to first" in contents, "the first turn's exchange was lost when the second saved"
    assert "answer to second" in contents
    assert len(_stored(home, session_id).get("receipts", [])) == 3, "every turn keeps its receipt"


def test_a_failed_save_leaves_the_previous_conversation_whole(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import chimera.core.code_session as code_session

    store = CodeSessionStore(tmp_path)
    session = CodeSession(None, session_id="s1", messages=[{"role": "user", "content": "kept"}])  # type: ignore[arg-type]
    store.save(session)

    def boom(*_a: Any, **_kw: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(code_session.os, "replace", boom)
    session.messages = [{"role": "user", "content": "half written"}]
    with pytest.raises(OSError):
        store.save(session)

    stored = json.loads((tmp_path / "s1.json").read_text(encoding="utf-8"))
    assert stored["messages"] == [{"role": "user", "content": "kept"}]
    assert not list(tmp_path.glob("*.tmp")), "a failed save leaves no temporary behind"


def test_refresh_brings_a_session_up_to_what_is_stored(tmp_path: Path) -> None:
    store = CodeSessionStore(tmp_path)
    assert store.load_existing("nobody", None) is None  # type: ignore[arg-type]
    store.save(CodeSession(None, session_id="s1", workspace="/p", messages=[{"role": "user", "content": "a"}]))  # type: ignore[arg-type]
    stale = CodeSession(None, session_id="s1")  # type: ignore[arg-type]

    store.refresh(stale)

    assert stale.messages == [{"role": "user", "content": "a"}]
    assert stale.workspace == "/p"
