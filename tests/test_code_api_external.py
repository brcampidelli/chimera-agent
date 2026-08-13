"""`POST /api/code/turn` with ``provider`` set — the same turn, done by somebody else's agent.

The value of this endpoint is not its loop. It is everything around the loop: the checkpoint, the
verifier, the revert offer, the transcript, the receipt. Those apply to any worker, and the tests
here exist to hold that claim to account — an external turn that skipped the verifier would be a
second, weaker product wearing the same screen.

The external agent is :mod:`tests.acp_fake_agent`, driven as a ``custom`` provider. That is the same
path a user takes when pointing Chimera at an adapter it does not ship a spec for, so the test
exercises the configuration surface as well as the protocol.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sse_starlette")

from fastapi.testclient import TestClient  # noqa: E402

from chimera.config import Settings  # noqa: E402
from chimera.core.context_budget import RunState  # noqa: E402
from chimera.interface import ChatSession  # noqa: E402

FAKE = Path(__file__).with_name("acp_fake_agent.py")


class _NeverRuns:
    """The native agent. If any of these tests reaches it, the provider branch did not take."""

    run_state = RunState()

    def run(self, *_a: Any, **_k: Any) -> Any:
        raise AssertionError("the native loop ran for a turn that named an external provider")


def _client(tmp_path: Path, script: list[dict], monkeypatch: Any) -> tuple[TestClient, Path]:
    from chimera.api import build_api_app

    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    monkeypatch.setenv("FAKE_ACP_SCRIPT", json.dumps(script))
    # The scenario has to survive the secret scrubber, and the `custom` spec passes nothing through
    # by name — so it is carried on the command line's environment the same way a real adapter's
    # config would be, via the parent process this test controls.
    monkeypatch.setattr(
        "chimera.acp.agents.CUSTOM",
        __import__("chimera.acp.agents", fromlist=["AcpAgentSpec"]).AcpAgentSpec(
            key="custom", label="Custom", argv=[], passthrough_env=("FAKE_ACP_SCRIPT",)
        ),
        raising=True,
    )
    import chimera.core

    monkeypatch.setattr(chimera.core, "Agent", lambda *_a, **_k: _NeverRuns(), raising=True)
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    client = TestClient(
        build_api_app(lambda: ChatSession(_NeverRuns()), workspace=ws, settings=settings)
    )
    return client, ws


def _frames(response: Any) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    event = ""
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: "):
            out.append((event, json.loads(line[len("data: ") :])))
    return out


def _turn(client: TestClient, **body: Any) -> list[tuple[str, dict[str, Any]]]:
    payload = {
        "message": "do the thing",
        "provider": "custom",
        "provider_command": f'"{sys.executable}" -u "{FAKE}"',
        **body,
    }
    response = client.post("/api/code/turn", json=payload)
    assert response.status_code == 200
    return _frames(response)


def _text(chunk: str) -> dict:
    return {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": chunk}}


@pytest.fixture(autouse=True)
def _close_agents() -> Any:
    """Every test gets a clean registry. A connection kept between tests is a process kept between
    tests, and the second one would answer with the first one's scenario."""
    from chimera.acp.registry import registry

    yield
    registry().close_all()


def test_an_external_turn_speaks_this_endpoint_s_language(tmp_path: Path, monkeypatch: Any) -> None:
    """The whole design: the same event vocabulary, so the screen needs no second implementation."""
    client, _ = _client(tmp_path, [{"notify": _text("done thinking")}], monkeypatch)

    frames = _turn(client)
    kinds = [event for event, _ in frames]

    assert kinds[0] == "session"
    assert "token" in kinds and kinds[-1] == "done"
    done = dict(frames)["done"]
    assert done["answer"] == "done thinking"
    assert done["external"] == "custom"
    assert done["route_meta"] == {"provider": "custom", "external": True}


def test_the_numbers_the_native_loop_owns_are_absent_rather_than_zero(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A zero step count reads as "it did nothing". None reads as "this number does not exist here",
    which is the truth: the steps happened inside somebody else's loop."""
    client, _ = _client(tmp_path, [{"notify": _text("hi")}], monkeypatch)
    done = dict(_turn(client))["done"]

    assert done["steps"] is None
    assert done["context_peak_tokens"] is None
    assert done["fused"] is False


def test_an_external_edit_is_verified_like_any_other(tmp_path: Path, monkeypatch: Any) -> None:
    """The claim this endpoint makes, made good for a worker it did not write.

    Without this the external branch would be the weaker of the two buttons on the screen — the
    exact asymmetry the native turn was rewritten to remove.
    """
    client, ws = _client(
        tmp_path,
        [{"call": {"method": "fs/write_text_file",
                   "params": {"path": "app.py", "content": "x = 2\n"}}}],
        monkeypatch,
    )
    # A project that says how to check itself. `resolve_verify` infers this from the tree.
    (ws / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")

    frames = _turn(client)
    kinds = [event for event, _ in frames]

    assert "edit" in kinds
    assert "verified" in kinds, "an external turn edited a file and nothing judged it"
    assert (ws / "app.py").read_text(encoding="utf-8") == "x = 2\n"


def test_a_write_region_still_refuses_the_writes_that_come_through_us(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Half a guarantee, stated as half. The region governs the calls the agent routes through our
    handler; it cannot govern the ones it makes with its own tools, which is why the posture note
    says the guarantee is the checkpoint."""
    client, ws = _client(
        tmp_path,
        [{"call": {"method": "fs/write_text_file",
                   "params": {"path": "secrets.env", "content": "KEY=1"}}}],
        monkeypatch,
    )
    (ws / "src").mkdir()

    frames = _turn(client, write_region=["src/**"])
    done = dict(frames)["done"]

    assert not (ws / "secrets.env").exists()
    assert done["refused_writes"] == ["secrets.env"]


def test_a_granted_permission_is_reported_and_not_hidden(tmp_path: Path, monkeypatch: Any) -> None:
    """We answer permission prompts on the user's behalf, because gating a prompt the agent did not
    have to ask is theatre. The honest half of that bargain is that every grant is on the receipt."""
    client, _ = _client(
        tmp_path,
        [{"call": {"method": "session/request_permission",
                   "params": {"toolCall": {"title": "Delete build/"},
                              "options": [{"optionId": "y", "name": "Allow", "kind": "allow_once"}]}}}],
        monkeypatch,
    )
    done = dict(_turn(client))["done"]
    assert done["auto_approved"] == ["Delete build/"]


def test_the_conversation_is_recorded_even_though_the_agent_owns_it(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The agent keeps the context; we keep the record. Without the record the sidebar shows an
    untitled empty session for work that really happened."""
    client, _ = _client(tmp_path, [{"notify": _text("the answer")}], monkeypatch)
    frames = _turn(client)
    session_id = dict(frames)["session"]["session_id"]

    listed = client.get("/api/code/sessions").json()
    rows = listed["sessions"] if isinstance(listed, dict) else listed
    assert session_id in [row["id"] for row in rows]
    # And it is not an empty shell: the answer became the title the sidebar shows.
    assert any(row["id"] == session_id and row["turns"] for row in rows)


def test_an_unknown_provider_is_a_named_failure(tmp_path: Path, monkeypatch: Any) -> None:
    client, _ = _client(tmp_path, [], monkeypatch)
    response = client.post(
        "/api/code/turn", json={"message": "hi", "provider": "not-a-real-provider"}
    )
    frames = _frames(response)
    errors = [payload for event, payload in frames if event == "error"]
    assert errors and "not-a-real-provider" in errors[0]["message"]


def test_a_custom_provider_with_no_command_says_so(tmp_path: Path, monkeypatch: Any) -> None:
    client, _ = _client(tmp_path, [], monkeypatch)
    response = client.post("/api/code/turn", json={"message": "hi", "provider": "custom"})
    errors = [p for e, p in _frames(response) if e == "error"]
    assert errors and "command" in errors[0]["message"]


def test_the_adapter_s_own_words_survive_to_the_screen(tmp_path: Path, monkeypatch: Any) -> None:
    """"the coding turn failed" is right when the failure is ours to debug. An adapter that could
    not authenticate has already said something more useful, and hiding it turns a two-minute fix
    into a support thread."""
    client, _ = _client(tmp_path, [{"exit": 4}], monkeypatch)
    errors = [p for e, p in _turn(client) if e == "error"]
    assert errors, "an agent that died mid-turn reported nothing"
    assert "4" in errors[0]["message"]


def test_the_native_turn_is_untouched_by_any_of_this(tmp_path: Path, monkeypatch: Any) -> None:
    """No provider means no behaviour change, asserted rather than assumed: this endpoint has other
    callers, and a default that quietly moved would move them too."""
    from chimera.api.code_api import CodeSeams

    assert CodeSeams().provider is None
    assert CodeSeams().provider_command is None


def test_the_registry_keeps_one_agent_per_conversation(tmp_path: Path, monkeypatch: Any) -> None:
    """A `session/prompt` is one message in a conversation the AGENT keeps. A process per turn makes
    every turn turn one, and "no, the other one" gets answered with "which one?"."""
    from chimera.acp.registry import registry

    client, _ = _client(tmp_path, [{"notify": _text("hi")}], monkeypatch)
    first = _turn(client)
    session_id = dict(first)["session"]["session_id"]
    assert registry().live() == 1

    _turn(client, session_id=session_id)
    assert registry().live() == 1, "the second turn started a second agent"


def test_a_second_conversation_gets_its_own_agent(tmp_path: Path, monkeypatch: Any) -> None:
    from chimera.acp.registry import registry

    client, _ = _client(tmp_path, [{"notify": _text("hi")}], monkeypatch)
    _turn(client)
    _turn(client)  # no session_id: a new conversation each time
    assert registry().live() == 2


def test_nothing_is_left_running_after_the_registry_closes(tmp_path: Path, monkeypatch: Any) -> None:
    """An agent left alive holds the workspace and a model connection. The desktop app quitting
    mid-turn must not leave one behind for the user to find in a task manager."""
    from chimera.acp.registry import registry

    client, _ = _client(tmp_path, [{"notify": _text("hi")}], monkeypatch)
    _turn(client)
    assert registry().live() == 1
    registry().close_all()
    assert registry().live() == 0


def test_the_scenario_env_reaches_the_child(tmp_path: Path, monkeypatch: Any) -> None:
    # Guards the test harness itself: if the scrubber ate FAKE_ACP_SCRIPT, every test above would
    # be driving an agent with an empty script and passing for the wrong reason.
    client, _ = _client(tmp_path, [{"notify": _text("scenario reached the child")}], monkeypatch)
    done = dict(_turn(client))["done"]
    assert done["answer"] == "scenario reached the child"
