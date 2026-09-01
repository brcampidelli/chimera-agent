"""The most expensive route in the product kept no record of what it emitted.

`runlog` exists, is tested, and its docstring states the argument exactly: *"close the app, reload
the page, or lose the connection, and the answer was gone while the bill stayed. The cost was
recorded and the product was not."* It was imported by one file — the orchestration API — and the
coding turn, which costs more per call than anything else here, emitted frames with no number and
kept none of them.

The `seq` is the load-bearing part, not the file. A client that has read up to `seq` asks for what
came after; anything it already applied is dropped. That is what makes replay-then-live and
live-only land on the same state instead of two, and it is why the number is stamped at the single
writer rather than counted by the reader.

`404` for an unknown id, never `200` with an empty list: a turn that was never recorded and a turn
with nothing new are opposite instructions for a client deciding whether to keep asking. The
orchestration route's own comment records what the second answer cost there.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chimera.orchestration import runlog


@pytest.fixture
def home(tmp_path: Path) -> Path:
    caminho = tmp_path / "home"
    caminho.mkdir(parents=True, exist_ok=True)
    return caminho


@pytest.fixture
def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """The real app over a temporary home, built the way `test_api.py` builds it.

    The provider keys are cleared, and that is not tidiness. `build_api_app`'s session factory only
    covers the CHAT path; the coding turn builds its own agent from the gateway — so the first
    version of this fixture made a real, paid model call on a developer machine with a key in the
    environment, and the assertion below passed because a live model answered. A test that spends
    money is a test somebody eventually stops running.
    """
    from fastapi.testclient import TestClient

    for chave in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
                  "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(chave, raising=False)

    from chimera.api import build_api_app
    from chimera.config import Settings
    from chimera.interface.session import ChatSession

    class _Agente:
        def run(self, task: str, **_kwargs: Any) -> Any:
            class _R:
                answer = "olá"
                steps = 1
                stopped_reason = "final"
                prompt_tokens = 10
                completion_tokens = 2
                usd = 0.001
                tool_names: list[str] = []
                model = "openrouter/test-model"

            return _R()

    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))  # type: ignore[call-arg]
    return TestClient(build_api_app(lambda: ChatSession(_Agente()), settings=settings))


# ------------------------------------------------------------------ the transcript


def test_a_turn_is_recorded_where_orchestration_is_not(home: Path) -> None:
    """Separate areas, so listing or pruning one never reaches the other's runs."""
    runlog.append(home, "t1", "token", {"text": "olá", "seq": 1}, area="code")

    assert (home / "code" / "t1" / "frames.jsonl").exists()
    assert not (home / "orchestration" / "t1").exists()


def test_frames_come_back_in_order(home: Path) -> None:
    for i, texto in enumerate(["a", "b", "c"], start=1):
        runlog.append(home, "t1", "token", {"text": texto, "seq": i}, area="code")

    assert [f["text"] for f in runlog.frames(home, "t1", area="code")] == ["a", "b", "c"]


def test_since_returns_only_what_is_new(home: Path) -> None:
    """The whole mechanism in one assertion: a client that read three asks for what came after."""
    for i in range(1, 6):
        runlog.append(home, "t1", "token", {"text": str(i), "seq": i}, area="code")

    assert [f["seq"] for f in runlog.frames(home, "t1", since=3, area="code")] == [4, 5]


def test_an_unknown_turn_is_distinguishable_from_a_quiet_one(home: Path) -> None:
    """`frames()` cannot tell them apart — both come back empty — and they are opposite
    instructions for a client deciding whether to keep polling."""
    runlog.append(home, "conhecido", "token", {"text": "a", "seq": 1}, area="code")

    assert runlog.exists(home, "conhecido", area="code") is True
    assert runlog.exists(home, "nunca-existiu", area="code") is False


def test_an_area_it_does_not_know_is_refused(home: Path) -> None:
    """Checked against a list rather than sanitised. It is not user input today; the day it is, a
    whitelist is the difference between a directory name and a path traversal."""
    with pytest.raises(ValueError, match="area"):
        runlog.run_dir(home, "t1", area="../../etc")


def test_a_truncated_tail_line_does_not_lose_the_rest(home: Path) -> None:
    """The file is appended to by a LIVE turn, so the last line is routinely half-written when a
    reader arrives. Refusing the whole transcript over it would lose the frames worth replaying."""
    runlog.append(home, "t1", "token", {"text": "a", "seq": 1}, area="code")
    caminho = home / "code" / "t1" / "frames.jsonl"
    with caminho.open("a", encoding="utf-8") as h:
        h.write('{"event": "token", "seq": 2, "text": "meio')

    assert [f["seq"] for f in runlog.frames(home, "t1", area="code")] == [1]


# ------------------------------------------------------------------ the route


def test_the_route_replays_from_a_cursor(api_client: Any, home: Path) -> None:
    for i in range(1, 4):
        runlog.append(home, "t9", "token", {"text": str(i), "seq": i}, area="code")

    resposta = api_client.get("/api/code/turns/t9?since=1")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert [f["seq"] for f in corpo["frames"]] == [2, 3]
    assert corpo["seq"] == 3


def test_the_route_refuses_an_unknown_turn(api_client: Any) -> None:
    assert api_client.get("/api/code/turns/nao-existe").status_code == 404


def test_the_cursor_holds_when_there_is_nothing_new(api_client: Any, home: Path) -> None:
    """`seq` echoes the `since` that was asked for, so a client polling a quiet turn does not walk
    its cursor backwards to zero and replay everything on the next call."""
    runlog.append(home, "t9", "token", {"text": "a", "seq": 1}, area="code")

    corpo = api_client.get("/api/code/turns/t9?since=1").json()

    assert corpo["frames"] == []
    assert corpo["seq"] == 1


def test_a_live_turn_numbers_its_frames(api_client: Any, home: Path) -> None:
    """End to end: the numbers have to exist on the wire, or nothing downstream can use them."""
    resposta = api_client.post(
        "/api/code/turn", json={"message": "diga oi", "workspace": str(home)}
    )

    assert resposta.status_code == 200
    quadros = [
        json.loads(linha[6:])
        for linha in resposta.text.splitlines()
        if linha.startswith("data: ")
    ]
    assert quadros, "the turn emitted nothing"
    assert quadros[0].get("turn_id"), "the first frame must carry the turn's identity"
    assert all("seq" in q for q in quadros[1:]), "every frame after the first must be numbered"


def test_a_live_turn_leaves_a_transcript_that_can_be_replayed(api_client: Any, home: Path) -> None:
    """The end-to-end assertion that matters, and the one the wire-shape test above cannot make.

    Checking that frames carry a `seq` proves they were numbered; it says nothing about whether any
    of them reached disk. Two sabotages — removing the `runlog.append` outright, and writing to the
    orchestration area instead — passed every other test in this file, because a turn that persists
    nothing streams exactly like one that persists everything.
    """
    resposta = api_client.post(
        "/api/code/turn", json={"message": "diga oi", "workspace": str(home)}
    )
    inicial = next(
        json.loads(linha[6:]) for linha in resposta.text.splitlines() if linha.startswith("data: ")
    )
    turn_id = inicial["turn_id"]

    # From the store, not from the response: this is what a client that lost the connection sees.
    replay = api_client.get(f"/api/code/turns/{turn_id}?since=0")

    assert replay.status_code == 200, "the turn kept no transcript"
    quadros = replay.json()["frames"]
    assert quadros, "the transcript is empty"
    assert [q["seq"] for q in quadros] == sorted(q["seq"] for q in quadros)
    # A TERMINAL frame, either kind. With no provider configured the turn ends in `error` and with
    # one it ends in `done`; both are the turn's outcome, and a replaying client needs whichever
    # happened. Asserting only `done` made this test depend on a key being present, which is the
    # same as depending on the network.
    assert any(q.get("event") in {"done", "error"} for q in quadros), "no outcome was recorded"


def test_a_coding_turn_does_not_land_in_the_orchestration_listing(
    api_client: Any, home: Path
) -> None:
    """Separate areas, asserted through the API rather than by reading a directory.

    A coding turn filed under `orchestration/` would appear in the fan-out run list, where nothing
    can render it — and it would be pruned by that list's ceiling, so a busy day of chat would
    silently evict the fan-out transcripts somebody was keeping.
    """
    api_client.post("/api/code/turn", json={"message": "diga oi", "workspace": str(home)})

    assert runlog.recent(home) == []
