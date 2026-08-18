"""Branching a conversation, and looking at what one actually is.

A code session is a linear message list that each turn REPLACES with the transcript that came back.
That is correct — the transcript already contains the history it was handed — and it means trying an
idea costs the thread you were on: there is no turn to go back to, only the list as it stands now. So
"what if I had asked it differently" is a question you can only answer by losing the answer you had.

Forking is a copy, deliberately, rather than a shared ancestor with two heads. Two files that share a
past and nothing else cannot leak into each other, and the alternative eventually does: the moment a
fork and its parent point at the same messages, a trim in one silently rewrites the other's history.

The second half is smaller and older: nothing could show you the file. `GET /api/code/sessions/{id}`
returns exchanges — messages parsed and folded into "you asked / it did / it answered" — which is
what a screen should render and exactly the wrong thing for the case where you are looking because a
message went missing. A view of what the parser dropped cannot be produced by the parser.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chimera.core.agent import AgentResult
from chimera.core.code_session import CodeSession, CodeSessionStore


class _StubAgent:
    def run(self, task: str, *, history: list[Any] | None = None, **_: Any) -> AgentResult:
        transcript: list[Any] = [{"role": "system", "content": "SYSTEM"}]
        transcript += list(history or [])
        transcript += [
            {"role": "user", "content": task},
            {"role": "assistant", "content": f"answer to {task}"},
        ]
        return AgentResult(answer="ok", steps=1, stopped_reason="final", transcript=transcript)


def _stored(tmp_path: Path, *turns: str, workspace: str = "/home/me/proj") -> CodeSessionStore:
    store = CodeSessionStore(tmp_path)
    session = CodeSession(_StubAgent(), session_id="original", workspace=workspace)
    for turn in turns:
        session.send(turn)
    store.save(session)
    return store


def test_a_fork_carries_the_conversation_and_the_project(tmp_path: Path) -> None:
    store = _stored(tmp_path, "what does this do?", "now rename it")

    new_id = store.fork("original")

    assert new_id is not None and new_id != "original"
    copy = json.loads((tmp_path / f"{new_id}.json").read_text(encoding="utf-8"))
    original = json.loads((tmp_path / "original.json").read_text(encoding="utf-8"))
    assert copy["messages"] == original["messages"], "the fork must start from the same past"
    assert copy["workspace"] == "/home/me/proj", "a branch is about the same project"


def test_the_fork_is_addressed_as_itself(tmp_path: Path) -> None:
    """The one field that must NOT be copied.

    ``session_id`` is inside the document as well as being the filename, and ``save()`` writes to
    the path derived from the field. A fork that kept the parent's id would be a file the next save
    overwrites its parent with — the branch destroying the thing it branched from.
    """
    store = _stored(tmp_path, "hello")

    new_id = store.fork("original")

    assert new_id is not None
    assert json.loads((tmp_path / f"{new_id}.json").read_text(encoding="utf-8"))["session_id"] == new_id


def test_a_turn_in_the_fork_leaves_the_original_alone(tmp_path: Path) -> None:
    """What forking is FOR, asserted end to end rather than on the copy's bytes."""
    store = _stored(tmp_path, "first question")
    new_id = store.fork("original")
    assert new_id is not None

    branch = store.load(new_id, _StubAgent())
    branch.send("a different second question")
    store.save(branch)

    parent = store.load("original", _StubAgent())
    assert [m["content"] for m in parent.messages if m["role"] == "user"] == ["first question"]
    assert len(branch.messages) > len(parent.messages)


def test_forking_something_that_is_not_there_is_not_an_accident(tmp_path: Path) -> None:
    """``None``, not a new empty conversation. Silently creating one would answer "branch this" with
    a blank session that looks like the branch succeeded and lost everything."""
    assert CodeSessionStore(tmp_path).fork("never-existed") is None
    assert list(tmp_path.glob("*.json")) == []


def test_a_corrupt_conversation_is_not_forked(tmp_path: Path) -> None:
    """``load()`` recovers from a bad file by starting fresh, which is right for continuing to work
    and wrong here: forking it would mint a *second* file with the same damage, and the copy would
    look like a real branch in the list."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")

    assert CodeSessionStore(tmp_path).fork("broken") is None


def test_a_fork_preserves_a_field_this_version_does_not_know(tmp_path: Path) -> None:
    """Copied at the document level, not round-tripped through ``CodeSession``'s three fields.

    A session file written by a newer build carries whatever that build stored. Rebuilding the
    document from today's dataclass would drop it, and the loss would be invisible — the fork opens,
    reads correctly, and is quietly missing something.
    """
    store = _stored(tmp_path, "hello")
    path = tmp_path / "original.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["some_future_field"] = {"kept": True}
    path.write_text(json.dumps(data), encoding="utf-8")

    new_id = store.fork("original")

    assert new_id is not None
    copy = json.loads((tmp_path / f"{new_id}.json").read_text(encoding="utf-8"))
    assert copy["some_future_field"] == {"kept": True}


def test_raw_is_the_bytes_on_disk(tmp_path: Path) -> None:
    store = _stored(tmp_path, "hello")

    assert store.raw("original") == (tmp_path / "original.json").read_text(encoding="utf-8")
    assert store.raw("never-existed") is None


def test_raw_shows_a_file_that_does_not_parse(tmp_path: Path) -> None:
    """The case the endpoint exists for. Every other read of a session runs it through
    ``json.loads`` and falls back to an empty conversation, so a damaged file is indistinguishable
    from an empty one — which is the opposite of what someone opening this wants to learn."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "broken.json").write_text('{"messages": [tru', encoding="utf-8")

    assert CodeSessionStore(tmp_path).raw("broken") == '{"messages": [tru'


@pytest.mark.parametrize("bad", ["", "///", "..."])
def test_an_unusable_id_is_refused_rather_than_resolved(tmp_path: Path, bad: str) -> None:
    """``_path`` strips everything that is not alnum/-/_ and raises when nothing survives. Both new
    methods go through it, so neither can be talked into naming a file outside the store."""
    store = CodeSessionStore(tmp_path)
    with pytest.raises(ValueError):
        store.fork(bad)
    with pytest.raises(ValueError):
        store.raw(bad)


# --- over HTTP ------------------------------------------------------------------------------------

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402


def _client(monkeypatch: Any, tmp_path: Path) -> TestClient:
    from chimera.api import build_api_app
    from chimera.config import Settings, get_settings
    from chimera.interface import ChatSession

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-x")
    get_settings.cache_clear()
    return TestClient(build_api_app(lambda: ChatSession(object()), settings=Settings()))


def test_the_app_forks_and_gets_a_row_for_the_new_conversation(
    monkeypatch: Any, tmp_path: Path
) -> None:
    """The response is the FORK's sidebar row.

    Returning the source's row would be worse than returning nothing: the caller drops it into the
    list and clicking it resumes the conversation the user just branched away from.
    """
    from chimera.config import get_settings

    _stored(tmp_path / "code_sessions", "what does this do?")
    client = _client(monkeypatch, tmp_path)

    row = client.post("/api/code/sessions/original/fork").json()

    assert row["id"] != "original"
    assert row["title"] == "what does this do?"
    assert row["workspace"] == "/home/me/proj"
    assert {m["id"] for m in client.get("/api/code/sessions").json()} == {"original", row["id"]}
    get_settings.cache_clear()


def test_forking_an_unknown_conversation_is_a_404(monkeypatch: Any, tmp_path: Path) -> None:
    """Unlike DELETE, which answers ``{ok: false}`` because a second click on Clear is not an error.
    A fork that produced nothing has no conversation for the caller to navigate to, and reporting
    success would send it to a session id that does not exist."""
    from chimera.config import get_settings

    client = _client(monkeypatch, tmp_path)
    assert client.post("/api/code/sessions/nope/fork").status_code == 404
    get_settings.cache_clear()


def test_the_app_can_read_the_stored_file(monkeypatch: Any, tmp_path: Path) -> None:
    from chimera.config import get_settings

    _stored(tmp_path / "code_sessions", "hello there")
    client = _client(monkeypatch, tmp_path)

    body = client.get("/api/code/sessions/original/raw").json()

    on_disk = (tmp_path / "code_sessions" / "original.json").read_text(encoding="utf-8")
    assert body["text"] == on_disk
    assert body["bytes"] == len(on_disk.encode("utf-8"))
    assert client.get("/api/code/sessions/nope/raw").status_code == 404
    get_settings.cache_clear()
