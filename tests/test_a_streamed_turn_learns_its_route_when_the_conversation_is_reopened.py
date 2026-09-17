"""A streamed turn's receipt records the router's generation ids, and learns its route on reopen.

Measured on the installed 0.58.0, the same day #494 shipped: a Code-screen turn's receipt carried
`cache_read_tokens` (5,376 of 11,158) and `provider: ""`. Not a wiring defect — with `litellm`
against OpenRouter, a streamed chunk exposes no provider anywhere (not on the chunk, not in
`_hidden_params`, not in `provider_specific_fields`); the same request without streaming answers
`provider` (`Relace`). The desktop streams by default, so the badge #494 added could never show
there. What the chunk does carry is its `id`, and the router's generation record names the route —
~9–11 s after the stream ends (404 before that, 3/3 trials), so nothing on the request path can
wait for it.

So: the ids go on the receipt when the turn ends, and the route is filled in where the receipt is
read back — the replay — bounded, newest first, never guessed, and written down so it is asked once.
"""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings
from chimera.core.agent import AgentResult
from chimera.core.steplog import StepLog, StepRecord
from chimera.interface import ChatSession
from chimera.providers import generation
from chimera.providers.gateway import CompletionResult, LLMGateway

# ------------------------------------------------------------------ the gateway keeps the id


def _chunk(text: str, *, gen_id: str = "") -> Any:
    class _D:
        content = text
        tool_calls = None

    class _C:
        delta = _D()
        finish_reason = None

    class _K:
        id = gen_id
        choices = [_C()]
        usage = None

    return _K()


def test_the_streamed_result_carries_the_first_chunks_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    gw = LLMGateway()

    def _stream(**_kwargs: Any) -> Any:
        yield _chunk("o", gen_id="")  # the first chunk of a stream may carry no id
        yield _chunk("k", gen_id="gen-1789611192-first")
        yield _chunk("!", gen_id="gen-1789611192-first")

    monkeypatch.setattr(gw, "_stream_once", _stream, raising=False)
    result = gw.stream_complete([{"role": "user", "content": "oi"}])
    assert result.generation_id == "gen-1789611192-first"
    assert result.provider == ""  # measured: the chunks never name the route


def test_the_non_streamed_result_carries_the_responses_id() -> None:
    """The batch path had the route already; the id travels there too, so both receipts read alike."""
    assert CompletionResult(content="x", model="m").generation_id == ""


# ------------------------------------------------------------------ the steplog lists them in order


def _log(*ids: str) -> StepLog:
    log = StepLog()
    for i, gen in enumerate(ids):
        log.add(
            StepRecord(
                index=i,
                prompt_tokens=10,
                completion_tokens=1,
                model="openrouter/m",
                generation_id=gen,
            )
        )
    return log


def test_generation_ids_keep_step_order_and_skip_the_steps_that_sent_none() -> None:
    assert _log("gen-a", "", "gen-c").generation_ids == ["gen-a", "gen-c"]
    assert StepLog().generation_ids == []
    assert _log("gen-a").steps[0].as_dict()["generation_id"] == "gen-a"


# ------------------------------------------------------------------ one lookup, never guessed


class _Resp:
    def __init__(self, body: dict[str, Any]) -> None:
        self._body = json.dumps(body).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *_a: Any) -> None:
        return None


def _opener_answering(body: dict[str, Any]) -> Any:
    seen: list[Any] = []

    def opener(req: Any, timeout: float) -> Any:
        seen.append((req, timeout))
        return _Resp(body)

    opener.seen = seen  # type: ignore[attr-defined]
    return opener


def test_lookup_reads_the_route_off_the_generation_record() -> None:
    opener = _opener_answering(
        {"data": {"id": "gen-1", "provider_name": "Together", "streamed": True}}
    )
    assert generation.lookup_route("gen-1", api_key="sk-x", opener=opener) == "Together"
    req, timeout = opener.seen[0]
    assert req.full_url == "https://openrouter.ai/api/v1/generation?id=gen-1"
    assert req.get_header("Authorization") == "Bearer sk-x"
    assert timeout == generation.LOOKUP_TIMEOUT


@pytest.mark.parametrize(
    "opener",
    [
        lambda req, timeout: (_ for _ in ()).throw(
            urllib.error.HTTPError(req.full_url, 404, "not yet", {}, io.BytesIO(b""))  # type: ignore[arg-type]
        ),
        lambda req, timeout: (_ for _ in ()).throw(TimeoutError("slow")),
        lambda req, timeout: _Resp({"data": {}}),
        lambda req, timeout: _Resp({"error": "no"}),
    ],
)
def test_a_404_a_timeout_and_a_nameless_record_all_leave_the_route_empty(opener: Any) -> None:
    """The first ~10 s after a stream the record is a 404 — ordinary, not an error, and never 'unknown'."""
    assert generation.lookup_route("gen-1", api_key="sk-x", opener=opener) == ""


def test_no_id_or_no_key_asks_nobody() -> None:
    def opener(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("asked")

    assert generation.lookup_route("", api_key="sk-x", opener=opener) == ""
    assert generation.lookup_route("gen-1", api_key="", opener=opener) == ""


# ------------------------------------------------------------------ the pass: newest first, within budget


def _receipt(provider: str, ids: list[str], model: str = "openrouter/deepseek/x") -> dict[str, Any]:
    return {"provider": provider, "generation_ids": ids, "model": model, "usd": 0.001}


def test_the_pass_fills_the_first_id_of_every_receipt_that_can_learn_and_leaves_the_rest() -> None:
    receipts = [
        _receipt("", ["gen-old-1", "gen-old-2"]),
        _receipt("Relace", ["gen-known"]),  # already knows
        _receipt("", ["gen-local"], model="ollama/llama"),  # not the router: nothing to ask
        _receipt("", []),  # a turn whose provider sent no ids
        _receipt("", ["gen-new-1", "gen-new-2"]),
    ]
    asked: list[str] = []

    def lookup(gen_id: str, **_: Any) -> str:
        asked.append(gen_id)
        return {"gen-new-1": "Together", "gen-old-1": ""}[gen_id]

    filled = generation.resolve_missing_routes(receipts, api_key="sk-x", lookup=lookup)

    assert asked == ["gen-new-1", "gen-old-1"], (
        "newest first, first id only, only the ones that can learn"
    )
    assert filled == 1
    assert receipts[4]["provider"] == "Together"
    assert receipts[0]["provider"] == "", "a record not there yet stays empty for the next reopen"
    assert receipts[1]["provider"] == "Relace" and receipts[2]["provider"] == ""


def test_the_pass_stops_at_its_budget() -> None:
    receipts = [_receipt("", [f"gen-{i}"]) for i in range(10)]
    ticks = iter([0.0, 0.0, 1.0, 1.0, 2.0, 2.0, 3.0, 3.0, 4.0, 4.0, 5.0, 5.0, 6.0])
    asked: list[str] = []

    def lookup(gen_id: str, **_: Any) -> str:
        asked.append(gen_id)
        return "R"

    filled = generation.resolve_missing_routes(
        receipts, api_key="sk-x", budget_seconds=2.5, lookup=lookup, clock=lambda: next(ticks)
    )
    assert filled == len(asked) < 10, "a long conversation's replay is not a tour of the router"


def test_the_pass_without_a_key_asks_nobody() -> None:
    def lookup(*_a: Any, **_k: Any) -> str:
        raise AssertionError("asked")

    assert (
        generation.resolve_missing_routes([_receipt("", ["gen-1"])], api_key="", lookup=lookup) == 0
    )


# ------------------------------------------------------------------ end to end: the turn records, the reopen learns


class _StreamedAgent:
    """A turn whose steps carry ids and no route — what a real streamed turn produces."""

    def run(self, task: str, **_: Any) -> AgentResult:
        log = StepLog()
        log.add(
            StepRecord(
                index=0,
                prompt_tokens=100,
                completion_tokens=5,
                model="openrouter/deepseek/x",
                generation_id="gen-1789611192-turn",
            )
        )
        return AgentResult(
            answer="hello",
            steps=1,
            stopped_reason="final",
            transcript=[
                {"role": "user", "content": task},
                {"role": "assistant", "content": "hello"},
            ],
            model="openrouter/deepseek/x",
            steplog=log,
        )


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **env: str) -> TestClient:
    import chimera.core
    from chimera.api import build_api_app

    monkeypatch.setattr(chimera.core, "Agent", lambda *_a, **_k: _StreamedAgent(), raising=True)
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"), **env)  # type: ignore[arg-type]
    return TestClient(
        build_api_app(lambda: ChatSession(_StreamedAgent()), workspace=ws, settings=settings)
    )


def _frames(response: Any) -> dict[str, dict[str, Any]]:
    event, out = "", {}
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: "):
            out[event] = json.loads(line[len("data: ") :])
    return out


def test_the_turn_records_its_ids_and_the_reopen_learns_and_keeps_the_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch, OPENROUTER_API_KEY="sk-test")
    frames = _frames(client.post("/api/code/turn", json={"message": "oi"}))
    done, session_id = frames["done"], frames["session"]["session_id"]
    assert done["generation_ids"] == ["gen-1789611192-turn"]
    assert done["provider"] == "", "at the end of a streamed turn the route is not knowable yet"

    asked: list[str] = []

    def lookup(gen_id: str, **_: Any) -> str:
        asked.append(gen_id)
        return "Relace"

    monkeypatch.setattr(generation, "lookup_route", lookup)
    replay = client.get(f"/api/code/sessions/{session_id}").json()
    assert replay["exchanges"][-1]["done"]["provider"] == "Relace"
    assert asked == ["gen-1789611192-turn"]

    # Written down: the next reopen does not ask, and still knows.
    monkeypatch.setattr(
        generation,
        "lookup_route",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("asked twice")),
    )
    again = client.get(f"/api/code/sessions/{session_id}").json()
    assert again["exchanges"][-1]["done"]["provider"] == "Relace"
    stored = json.loads(
        (tmp_path / "home" / "code_sessions" / f"{session_id}.json").read_text(encoding="utf-8")
    )
    assert stored["receipts"][-1]["provider"] == "Relace"
    assert stored["receipts"][-1]["generation_ids"] == ["gen-1789611192-turn"]


def test_a_record_not_there_yet_leaves_the_replay_honest_and_asks_again_next_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch, OPENROUTER_API_KEY="sk-test")
    session_id = _frames(client.post("/api/code/turn", json={"message": "oi"}))["session"][
        "session_id"
    ]
    asked: list[str] = []
    monkeypatch.setattr(generation, "lookup_route", lambda gen_id, **_: asked.append(gen_id) or "")

    assert (
        client.get(f"/api/code/sessions/{session_id}").json()["exchanges"][-1]["done"]["provider"]
        == ""
    )
    assert (
        client.get(f"/api/code/sessions/{session_id}").json()["exchanges"][-1]["done"]["provider"]
        == ""
    )
    assert asked == ["gen-1789611192-turn"] * 2, "unresolved is retried, resolved is not"


def test_without_a_router_key_the_reopen_asks_nobody(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    client = _client(tmp_path, monkeypatch)
    session_id = _frames(client.post("/api/code/turn", json={"message": "oi"}))["session"][
        "session_id"
    ]
    monkeypatch.setattr(
        generation, "lookup_route", lambda *a, **k: (_ for _ in ()).throw(AssertionError("asked"))
    )
    assert (
        client.get(f"/api/code/sessions/{session_id}").json()["exchanges"][-1]["done"]["provider"]
        == ""
    )
