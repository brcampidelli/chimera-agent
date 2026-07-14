"""Tests for the desktop API (FastAPI + SSE), no network — a fake agent drives the real ChatSession.

Skipped entirely when the optional 'desktop' extra (fastapi/sse-starlette) isn't installed.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sse_starlette")

from fastapi.testclient import TestClient  # noqa: E402

from chimera.config import Settings  # noqa: E402
from chimera.core.agent import AgentResult, ToolActivity  # noqa: E402
from chimera.interface import ChatSession  # noqa: E402


class _FakeAgent:
    """Agent stub: streams two token deltas + one tool activity, returns a rich AgentResult."""

    def run(
        self,
        task: str,
        *,
        on_token: Callable[[str], None] | None = None,
        on_tool: Callable[[ToolActivity], None] | None = None,
    ) -> AgentResult:
        if on_tool is not None:
            on_tool(ToolActivity(name="read_file", arguments={}, ok=True, observation="ok"))
        if on_token is not None:
            on_token("Hel")
            on_token("lo")
        return AgentResult(
            answer="Hello",
            steps=1,
            stopped_reason="final",
            prompt_tokens=10,
            completion_tokens=2,
            usd=0.001,
            tool_names=["read_file"],
            model="openrouter/test-model",
        )


def _client(tmp_path: Any, *, token: str | None = None) -> TestClient:
    from chimera.api import build_api_app

    # Construct via validation aliases (the fields only populate by alias, not python name), so home
    # actually points at tmp_path and doesn't pollute the repo's .chimera dir.
    kwargs: dict[str, Any] = {"CHIMERA_HOME": str(tmp_path / "home")}
    if token is not None:
        kwargs["CHIMERA_SERVER_TOKEN"] = token
    settings = Settings(**kwargs)

    def factory() -> ChatSession:
        return ChatSession(_FakeAgent())

    return TestClient(build_api_app(factory, settings=settings))


def _read_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse a raw SSE stream body into (event, data-dict) pairs."""
    events: list[tuple[str, dict[str, Any]]] = []
    event = ""
    for line in text.splitlines():
        if line.startswith("event:"):
            event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            events.append((event, json.loads(line[len("data:"):].strip())))
    return events


def test_chat_stream_emits_session_token_tool_done(tmp_path: Any) -> None:
    client = _client(tmp_path)
    resp = client.post("/api/chat/stream", json={"message": "hi", "stream": True})
    assert resp.status_code == 200
    events = _read_sse(resp.text)
    kinds = [e for e, _ in events]
    assert kinds[0] == "session"  # client learns its session id first
    assert "token" in kinds and "tool" in kinds and kinds[-1] == "done"
    tokens = [d["text"] for e, d in events if e == "token"]
    assert tokens == ["Hel", "lo"]  # deltas in order
    tool = next(d for e, d in events if e == "tool")
    assert tool == {"name": "read_file", "ok": True}
    done = next(d for e, d in events if e == "done")
    assert done["answer"] == "Hello"
    assert done["prompt_tokens"] == 10 and done["completion_tokens"] == 2
    assert done["usd"] == 0.001 and done["tool_names"] == ["read_file"]
    assert "route_meta" in done and done["route_meta"] is None  # single-model turn -> honest null


def test_chat_stream_without_streaming_still_answers(tmp_path: Any) -> None:
    client = _client(tmp_path)
    resp = client.post("/api/chat/stream", json={"message": "hi", "stream": False})
    events = _read_sse(resp.text)
    assert "token" not in [e for e, _ in events]  # no token events when streaming is off
    done = next(d for e, d in events if e == "done")
    assert done["answer"] == "Hello"


def test_fuse_flag_swaps_agent_backend_for_the_turn_then_restores(tmp_path: Any) -> None:
    """`fuse=true` routes THIS turn through the provided fusion backend (so its trace surfaces),
    and the session agent's original backend is restored afterwards."""
    from chimera.api import build_api_app

    fuse_backend = object()  # stands in for the FusionEngine
    seen: dict[str, Any] = {}

    class _SwappableAgent:
        def __init__(self) -> None:
            self.backend: Any = object()  # the session's normal backend

        def run(self, task: str, *, on_token: Any = None, on_tool: Any = None) -> AgentResult:
            seen["backend_during_run"] = self.backend
            fused = self.backend is fuse_backend
            return AgentResult(
                answer="F",
                steps=1,
                stopped_reason="final",
                route_meta={"kind": "fusion", "panel": []} if fused else None,
            )

    agent = _SwappableAgent()
    default_backend = agent.backend
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    client = TestClient(
        build_api_app(lambda: ChatSession(agent), settings=settings, fuse_backend=fuse_backend)
    )

    resp = client.post("/api/chat/stream", json={"message": "hard one", "fuse": True, "stream": True})
    done = next(d for e, d in _read_sse(resp.text) if e == "done")
    assert seen["backend_during_run"] is fuse_backend  # swapped in for the fused turn
    assert done["route_meta"] == {"kind": "fusion", "panel": []}  # fusion trace surfaced
    assert agent.backend is default_backend  # restored after the turn


def test_chat_turn_appends_usage_line_and_usage_endpoint_summarizes(tmp_path: Any) -> None:
    from chimera.api.usage import load_usage

    client = _client(tmp_path)
    client.post("/api/chat/stream", json={"message": "hi", "stream": True})

    # A usage record was appended for the turn, carrying the turn's real signals.
    records = load_usage(tmp_path / "home" / "usage.jsonl")
    assert len(records) == 1
    rec = records[0]
    assert rec.model == "openrouter/test-model"
    assert rec.prompt_tokens == 10 and rec.completion_tokens == 2
    assert rec.usd == 0.001 and rec.tools == 1 and rec.route_kind is None

    # And GET /api/usage returns the aggregated summary shape over that record.
    summary = client.get("/api/usage").json()
    assert {"totals", "by_day", "by_model", "by_session", "cache_hit_pct", "route_mix"} <= set(summary)
    assert summary["totals"]["turns"] == 1
    assert summary["totals"]["usd"] == 0.001 and summary["totals"]["unpriced_turns"] == 0
    assert summary["by_model"][0]["model"] == "openrouter/test-model"
    assert summary["route_mix"] == {"single": 1, "fusion": 0, "cascade": 0}


def test_runs_endpoint_returns_receipts_newest_first(tmp_path: Any) -> None:
    from chimera.api.runs import RunReceipt, append_run

    # Seed two run receipts under the client's home (append order = chronological).
    run_log = tmp_path / "home" / "runs.jsonl"
    append_run(run_log, RunReceipt(ts="2026-07-13T00:00:00+00:00", task="older", success=True))
    append_run(
        run_log,
        RunReceipt(
            ts="2026-07-13T01:00:00+00:00",
            task="newer",
            success=False,
            verify_command="pytest -q",
        ),
    )

    client = _client(tmp_path)
    runs = client.get("/api/runs").json()
    assert [r["task"] for r in runs] == ["newer", "older"]  # most recent first
    assert runs[0]["success"] is False and runs[0]["verify_command"] == "pytest -q"
    assert runs[1]["success"] is True and runs[1]["verify_command"] is None


def test_post_runs_streams_events_done_and_persists_receipt(tmp_path: Any) -> None:
    """The run trigger streams `event` frames + a terminal `done`, and (via the agent's run_log) a
    receipt lands in runs.jsonl. Uses an injected factory that builds a REAL AutonomousAgent over a
    fake worker — no LLM — so the endpoint wiring (SSE marshalling + receipt persistence) is exercised.
    """
    from chimera.api import build_api_app
    from chimera.api.app import RunRequest
    from chimera.api.runs import load_runs
    from chimera.core.autonomous import AutonomousAgent, AutonomousConfig
    from chimera.core.checkpoint import WorkspaceGuard
    from chimera.core.events import EventSink

    class _FakeWorker:
        def run(self, task: str) -> AgentResult:
            return AgentResult(answer="did it", steps=1, stopped_reason="final")

    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))

    def solve_factory(
        req: RunRequest, ws: Any, on_event: EventSink, settings: Any
    ) -> AutonomousAgent:
        # No verifier / planner / manager: verify abstains → manager approves → success on attempt 1.
        return AutonomousAgent(
            _FakeWorker(),
            guard=WorkspaceGuard(ws),
            workspace=ws,
            on_event=on_event,
            run_log=settings.home / "runs.jsonl",
            config=AutonomousConfig(max_attempts=req.max_attempts, use_planner=False, use_manager=False),
        )

    client = TestClient(
        build_api_app(
            lambda: ChatSession(_FakeAgent()), settings=settings, solve_agent_factory=solve_factory
        )
    )
    resp = client.post("/api/runs", json={"task": "make it so", "max_attempts": 2})
    assert resp.status_code == 200
    events = _read_sse(resp.text)
    kinds = [e for e, _ in events]
    assert "event" in kinds and kinds[-1] == "done"
    done = next(d for e, d in events if e == "done")
    assert done["success"] is True and done["answer"] == "did it" and done["attempts"] == 1
    # Each streamed `event` frame carries the AgentEvent kind + text (compact, no huge answer field).
    ev = next(d for e, d in events if e == "event")
    assert "kind" in ev and "text" in ev and "answer" not in ev

    # The receipt was persisted by the agent's run_log — the read-only GET now lists it.
    receipts = load_runs(tmp_path / "home" / "runs.jsonl")
    assert len(receipts) == 1 and receipts[0].task == "make it so" and receipts[0].success is True
    listed = client.get("/api/runs").json()
    assert listed[0]["task"] == "make it so"


def test_fs_tree_and_file_endpoints_scope_to_the_workspace(tmp_path: Any) -> None:
    """The read-only fs endpoints list a workspace's tree and read a file, guarded by the app's
    workspace and the path-escape check (a `..` → 400; an invalid workspace param → 400)."""
    from chimera.api import build_api_app

    ws = tmp_path / "ws"
    (ws / "src").mkdir(parents=True)
    (ws / "src" / "main.py").write_text("print('x')\n", encoding="utf-8")
    (ws / ".git").mkdir()
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    client = TestClient(
        build_api_app(lambda: ChatSession(_FakeAgent()), settings=settings, workspace=ws)
    )

    tree = client.get("/api/fs/tree").json()
    names = [e["name"] for e in tree["entries"]]
    assert names == ["src"] and ".git" not in names  # dir only, ignored dir pruned

    sub = client.get("/api/fs/tree", params={"path": "src"}).json()
    assert [e["path"] for e in sub["entries"]] == ["src/main.py"]

    f = client.get("/api/fs/file", params={"path": "src/main.py"}).json()
    assert f["content"] == "print('x')\n" and f["note"] == ""

    # A path escape is a clean 400 (never a 500), and an invalid workspace param is a 400 too.
    assert client.get("/api/fs/file", params={"path": "../secret"}).status_code == 400
    assert (
        client.get("/api/fs/tree", params={"workspace": str(tmp_path / "nope")}).status_code == 400
    )


def test_fs_file_put_writes_and_guards(tmp_path: Any) -> None:
    """PUT /api/fs/file writes a (new) file atomically inside the workspace and returns its byte
    count; a path escape or oversize content is a clean 400 (never a 500)."""
    from chimera.api import build_api_app

    ws = tmp_path / "ws"
    ws.mkdir()
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    client = TestClient(
        build_api_app(lambda: ChatSession(_FakeAgent()), settings=settings, workspace=ws)
    )

    resp = client.put("/api/fs/file", json={"path": "pkg/new.py", "content": "x = 1\n"})
    assert resp.status_code == 200
    assert resp.json() == {"path": "pkg/new.py", "bytes": 6}
    assert (ws / "pkg" / "new.py").read_bytes() == b"x = 1\n"  # parent dir created, content on disk

    # A `..` escape and content over the 1 MB cap both map to 400 (never a 500).
    assert client.put("/api/fs/file", json={"path": "../evil", "content": "x"}).status_code == 400
    big = "a" * 1_000_001
    assert client.put("/api/fs/file", json={"path": "big.txt", "content": big}).status_code == 400


def test_session_is_persisted_and_listed_and_deletable(tmp_path: Any) -> None:
    client = _client(tmp_path)
    resp = client.post("/api/chat/stream", json={"message": "remember me", "stream": True})
    sid = next(d for e, d in _read_sse(resp.text) if e == "session")["session_id"]

    listed = client.get("/api/sessions").json()
    assert any(s["id"] == sid and s["turns"] == 1 for s in listed)
    assert listed[0]["title"] == "remember me"  # title = first user message

    got = client.get(f"/api/sessions/{sid}").json()
    assert got["turns"] == [{"user": "remember me", "assistant": "Hello"}]

    assert client.delete(f"/api/sessions/{sid}").json() == {"deleted": True}
    assert client.get(f"/api/sessions/{sid}").status_code == 404


def _token_client(monkeypatch: Any, tmp_path: Any, token: str) -> TestClient:
    # The guard reads get_settings() fresh (so a runtime token change enforces), so the token must be
    # in the process settings, not just the injected Settings — set it via env + clear the cache.
    from chimera.config import Settings, get_settings

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-x")
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", token)
    get_settings.cache_clear()
    from chimera.api import build_api_app

    return TestClient(build_api_app(lambda: ChatSession(_FakeAgent()), settings=Settings()))


def test_bearer_token_guards_chat_when_configured(monkeypatch: Any, tmp_path: Any) -> None:
    from chimera.config import get_settings

    client = _token_client(monkeypatch, tmp_path, "s3cret")
    assert client.post("/api/chat/stream", json={"message": "hi"}).status_code == 401
    ok = client.post(
        "/api/chat/stream", json={"message": "hi"}, headers={"Authorization": "Bearer s3cret"}
    )
    assert ok.status_code == 200
    get_settings.cache_clear()


def test_reads_require_token_when_configured(monkeypatch: Any, tmp_path: Any) -> None:
    from chimera.config import get_settings

    client = _token_client(monkeypatch, tmp_path, "s3cret")
    # A GET read now requires the token too (transcripts/memory/config must not be readable without it).
    assert client.get("/api/config").status_code == 401
    assert client.get("/api/memory").status_code == 401
    assert client.get("/api/config", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert client.get("/api/health").status_code == 200  # health stays open for liveness checks
    get_settings.cache_clear()


def test_patch_config_rejects_newline_in_value(tmp_path: Any) -> None:
    # A newline in the value would inject extra .env lines even though the key is allowlisted.
    from chimera.api.config_api import patch_config

    with pytest.raises(ValueError, match="newline"):
        patch_config(
            {"CHIMERA_CACHE": "1\nOPENROUTER_API_KEY=sk-evil"}, env_path=tmp_path / ".env"
        )
    assert not (tmp_path / ".env").exists()  # nothing was written


def test_health_ok(tmp_path: Any) -> None:
    assert _client(tmp_path).get("/api/health").json()["status"] == "ok"


def test_read_config_masks_every_secret(tmp_path: Any) -> None:
    from chimera.api.config_api import read_config

    settings = Settings(
        CHIMERA_HOME=str(tmp_path), OPENROUTER_API_KEY="sk-supersecretvalue9999", CHIMERA_SERVER_TOKEN="tok"
    )
    cfg = read_config(settings)
    blob = json.dumps(cfg)
    assert "sk-supersecretvalue9999" not in blob  # the raw key never appears anywhere
    openrouter = next(p for p in cfg["providers"] if p["env"] == "OPENROUTER_API_KEY")
    assert openrouter["set"] is True and openrouter["hint"] == "…9999"  # only a last-4 hint
    assert cfg["server"]["token_set"] is True  # server token: presence only, no hint field leaked


def test_patch_config_rejects_unknown_keys(tmp_path: Any) -> None:
    from chimera.api.config_api import patch_config

    with pytest.raises(ValueError, match="not editable"):
        patch_config({"CHIMERA_HOME": "/etc/evil", "PATH": "x"}, env_path=tmp_path / ".env")


def test_patch_config_writes_env_atomically(tmp_path: Any) -> None:
    from chimera.api.config_api import patch_config

    env = tmp_path / ".env"
    env.write_text("EXISTING=1\n", encoding="utf-8")
    result = patch_config(
        {"CHIMERA_DEFAULT_MODEL": "openrouter/x", "OPENROUTER_API_KEY": "sk-new"}, env_path=env
    )
    assert result["updated"] == ["CHIMERA_DEFAULT_MODEL", "OPENROUTER_API_KEY"]
    text = env.read_text(encoding="utf-8")
    assert "EXISTING=1" in text  # pre-existing lines preserved
    assert "CHIMERA_DEFAULT_MODEL=openrouter/x" in text
    assert not list(tmp_path.glob(".env.tmp"))  # atomic temp cleaned up


def test_patch_config_updates_process_env_live(monkeypatch: Any, tmp_path: Any) -> None:
    # A key set through the wizard must be usable THIS session (no restart): patch_config also writes
    # os.environ, so the running gateway / get_settings() sees it immediately, not only the .env file.
    from chimera.api.config_api import patch_config

    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    patch_config({"OPENROUTER_API_KEY": "sk-live-now"}, env_path=tmp_path / ".env")
    import os

    assert os.environ["OPENROUTER_API_KEY"] == "sk-live-now"


def test_config_endpoint_shape(tmp_path: Any) -> None:
    cfg = _client(tmp_path).get("/api/config").json()
    assert {"models", "memory", "cache", "sandbox", "server", "providers"} <= set(cfg)
    # no provider entry ever carries a raw key field
    assert all(set(p) == {"env", "label", "set", "hint"} for p in cfg["providers"])


def test_cron_list_enable_disable_delete(monkeypatch: Any, tmp_path: Any) -> None:
    # features.py reads get_settings().home, so point HOME at tmp_path and clear the cache; the client
    # then shares that settings instance.
    from chimera.config import Settings, get_settings
    from chimera.scheduler import CronJob, CronStore

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-x")
    get_settings.cache_clear()
    store = CronStore(tmp_path / "scheduler" / "jobs.json")
    store.add(CronJob(id="j1", name="daily", trigger="cron", schedule="0 9 * * *", action="brief"))

    from fastapi.testclient import TestClient

    from chimera.api import build_api_app

    client = TestClient(build_api_app(lambda: ChatSession(_FakeAgent()), settings=Settings()))
    jobs = client.get("/api/cron").json()
    assert [j["id"] for j in jobs] == ["j1"] and jobs[0]["action"] == "brief"

    assert client.post("/api/cron/j1/disable").json()["enabled"] is False
    assert client.post("/api/cron/j1/enable").json()["enabled"] is True
    assert client.post("/api/cron/nope/enable").status_code == 404
    assert client.delete("/api/cron/j1").json() == {"deleted": True}
    assert client.get("/api/cron").json() == []
    get_settings.cache_clear()


def _feature_client(monkeypatch: Any, tmp_path: Any) -> TestClient:
    from chimera.config import Settings, get_settings

    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path))
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-x")
    get_settings.cache_clear()
    from chimera.api import build_api_app

    return TestClient(build_api_app(lambda: ChatSession(_FakeAgent()), settings=Settings()))


def test_memory_add_list_delete(monkeypatch: Any, tmp_path: Any) -> None:
    from chimera.config import get_settings

    client = _feature_client(monkeypatch, tmp_path)
    r = client.post("/api/memory", json={"content": "Bruno prefers HSL palettes", "kind": "semantic"})
    assert r.json()["status"] in ("ADD", "UPDATE")
    item_id = r.json()["item"]["id"]
    listed = client.get("/api/memory").json()
    assert any(m["content"] == "Bruno prefers HSL palettes" for m in listed)
    assert client.post("/api/memory", json={"content": "x", "kind": "bogus"}).status_code == 400
    assert client.delete(f"/api/memory/{item_id}").json() == {"deleted": True}
    get_settings.cache_clear()


def test_skills_list_and_approve(monkeypatch: Any, tmp_path: Any) -> None:
    from chimera.config import get_settings
    from chimera.evolution import SkillStore
    from chimera.evolution.learned_skill import LearnedSkill

    store = SkillStore(tmp_path / "skills.json")
    store.add(LearnedSkill(name="reread", description="reread trick premises", do="x", check="y", status="pending"))
    client = _feature_client(monkeypatch, tmp_path)
    data = client.get("/api/skills").json()
    assert any(s["name"] == "reread" for s in data["stats"])
    assert client.post("/api/skills/reread/approve").json() == {"approved": True}
    assert client.post("/api/skills/nope/approve").status_code == 404
    get_settings.cache_clear()
