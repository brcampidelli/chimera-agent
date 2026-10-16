"""S2: ``chimera code`` reaches the owner's background service, and the service's token is not an
approval key.

Fails if:

* a live, confirmed service is not preferred over the app's bridge, or a record the server does not
  confirm (an edited version, another pid) — or one pointing off this machine — is used anyway;
* ``chimera code new`` does not create a conversation through ``/api/code/turn`` and stream it, or
  starts something when no service runs instead of saying how to start one;
* the owner's token reaches the terminal's output;
* a yes typed in the terminal approves without the question's code — including in the case where
  this very process holds the code in memory, so a code-less write WOULD approve.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from chimera.cli import code_cmd, code_service
from chimera.cli.code_service import Found, ServiceCode, find, iter_sse
from chimera.core.service_record import Probe, ServiceRecord, new_record, probe, write_record
from chimera.governance import pending

TOKEN = "owner-token-s2-0123456789abcdefSECRET"
SESSION = {
    "id": "c0ffee000001", "title": "fix the build", "workspace": "", "turns": 3,
    "updated_at": time.time() - 120, "running": False,
}


def _record(home: Path, url: str = "http://127.0.0.1:47616") -> ServiceRecord:
    return new_record(url=url, token=TOKEN, home=home)


def _sse(*frames: tuple[str, dict[str, Any]]) -> bytes:
    return "".join(f"event: {e}\r\ndata: {json.dumps(d)}\r\n\r\n" for e, d in frames).encode()


class FakeService:
    """`/api/code/sessions`, `/api/code/turn` and the two answering routes, recorded."""

    def __init__(self, frames: list[tuple[str, dict[str, Any]]] | None = None) -> None:
        self.frames = frames or [
            ("session", {"session_id": "c0ffee000099", "turn_id": "t1", "started_at": 1.0}),
            ("token", {"text": "hello ", "seq": 1}),
            ("token", {"text": "from the service", "seq": 2}),
            ("tool", {"name": "read_file", "ok": True, "seq": 3}),
            ("done", {"answer": "hello from the service", "model": "test/model", "usd": 0.001,
                      "steps": 1, "seq": 4}),
        ]
        self.requests: list[tuple[str, str, Any, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"null") if request.content else None
        self.requests.append(
            (request.method, request.url.path, body, request.headers.get("authorization", ""))
        )
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"detail": "unauthorized"})
        if request.url.path == "/api/code/sessions":
            return httpx.Response(200, json=[SESSION])
        if request.url.path == "/api/code/turn":
            return httpx.Response(
                200, content=_sse(*self.frames), headers={"content-type": "text/event-stream"}
            )
        if request.url.path.startswith("/api/approvals/"):
            return httpx.Response(200, json={"ok": True})
        if request.url.path.startswith("/api/questions/"):
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404, json={"detail": "not scripted"})

    def turns(self) -> list[Any]:
        return [b for m, p, b, _ in self.requests if p == "/api/code/turn"]


def _bridge_must_not_be_used() -> Any:
    raise AssertionError("the app's bridge was used although a service was running")


def _run(
    monkeypatch: pytest.MonkeyPatch, args: list[str], found: Found, *, stdin: str = "",
    bridge: Any = _bridge_must_not_be_used,
) -> Any:
    from chimera.cli.main import app

    monkeypatch.setattr(code_cmd, "_service", lambda: found)
    monkeypatch.setattr(code_cmd, "_client", bridge)
    monkeypatch.setenv("COLUMNS", "200")
    return CliRunner().invoke(app, ["code", *args], input=stdin)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    folder = tmp_path / "home"
    folder.mkdir()
    return folder


# ---- which door ---------------------------------------------------------------------------------


class _InfoHandler(BaseHTTPRequestHandler):
    info: dict[str, Any] = {}

    def do_GET(self) -> None:  # noqa: N802 — the stdlib's name
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self.send_response(401)
            self.end_headers()
            return
        payload = json.dumps(self.info).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_args: Any) -> None:
        return


@pytest.fixture
def info_server() -> Iterator[tuple[str, type[_InfoHandler]]]:
    """A real HTTP server answering `/api/info`, so the record is checked by the real `probe`."""
    handler = type("Handler", (_InfoHandler,), {"info": {}})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", handler
    finally:
        server.shutdown()
        server.server_close()


def _answers_as(handler: type[_InfoHandler], record: ServiceRecord, **override: Any) -> None:
    handler.info = {
        "id": record.id, "version": record.version, "protocol": record.protocol,
        "pid": record.pid, "home": record.home, **override,
    }


def test_a_record_the_server_confirms_is_used(
    home: Path, info_server: tuple[str, type[_InfoHandler]], tmp_path: Path
) -> None:
    url, handler = info_server
    record = _record(home, url)
    write_record(record, tmp_path / "service-dev.json")
    _answers_as(handler, record)

    found = find(read=lambda: record)

    assert found.service is not None and found.service.record.id == record.id and not found.refused


def test_an_edited_record_is_refused_even_though_the_server_answers(
    home: Path, info_server: tuple[str, type[_InfoHandler]]
) -> None:
    """The file says one version, the process another: the version decides whether a client may
    replace the server, so a record whose version was edited is not trusted."""
    url, handler = info_server
    real = _record(home, url)
    _answers_as(handler, real)
    edited = ServiceRecord(**{**real.to_dict(), "version": "0.0.1-edited"})

    found = find(read=lambda: edited)

    assert found.service is None
    assert "version" in found.refused and TOKEN not in found.refused


def test_a_record_naming_another_process_is_refused(
    home: Path, info_server: tuple[str, type[_InfoHandler]]
) -> None:
    url, handler = info_server
    real = _record(home, url)
    _answers_as(handler, real, pid=real.pid + 1)

    found = find(read=lambda: real)

    assert found.service is None and "pid" in found.refused


def test_a_record_pointing_off_this_machine_is_refused_before_anything_is_sent(home: Path) -> None:
    asked: list[ServiceRecord] = []

    def check(record: ServiceRecord) -> Probe:
        asked.append(record)
        return Probe("alive")

    found = find(read=lambda: _record(home, "http://192.168.0.7:47616"), check=check)

    assert found.service is None and "off this machine" in found.refused
    assert asked == [], "the owner's token was offered to a host that is not this machine"


def test_a_live_service_is_preferred_over_the_bridge(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    fake = FakeService()
    found = Found(ServiceCode(_record(home), transport=httpx.MockTransport(fake)))

    result = _run(monkeypatch, ["list"], found)

    assert result.exit_code == 0, result.output
    assert SESSION["id"] in result.output
    assert [(m, p) for m, p, _, _ in fake.requests] == [("GET", "/api/code/sessions")]


def test_without_a_service_list_falls_back_to_the_bridge_and_says_why_a_record_was_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Bridge:
        full = False

        def sessions(self) -> list[dict[str, Any]]:
            return [SESSION]

    result = _run(
        monkeypatch, ["list"], Found(None, "the service record was not used: protocol 9"),
        bridge=Bridge,
    )

    assert result.exit_code == 0, result.output
    assert SESSION["id"] in result.output and "protocol 9" in result.output


def test_new_without_a_service_says_how_to_start_one_and_starts_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawned: list[Any] = []
    monkeypatch.setattr("subprocess.Popen", lambda *a, **k: spawned.append(a))

    result = _run(monkeypatch, ["new", "oi"], Found(None))

    assert result.exit_code == 1
    assert code_service.START_HINT in result.output
    assert spawned == [], "`chimera code new` started something on its own"


# ---- new and resume -----------------------------------------------------------------------------


def test_new_creates_a_conversation_and_streams_the_turn(
    monkeypatch: pytest.MonkeyPatch, home: Path, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    fake = FakeService()
    found = Found(ServiceCode(_record(home), transport=httpx.MockTransport(fake)))

    result = _run(monkeypatch, ["new", "oi", "--workspace", str(project)], found)

    assert result.exit_code == 0, result.output
    assert fake.turns() == [{"message": "oi", "workspace": str(project.resolve())}]
    assert all(auth == f"Bearer {TOKEN}" for *_, auth in fake.requests)
    assert "hello from the service" in result.output and "read_file ok" in result.output
    assert "conversation c0ffee000099" in result.output
    assert "chimera code resume c0ffee000099" in result.output
    assert result.output.count("hello from the service") == 1, "the answer was printed twice"
    assert TOKEN not in result.output


def test_resume_continues_that_conversation_through_the_service(
    monkeypatch: pytest.MonkeyPatch, home: Path, tmp_path: Path
) -> None:
    project = tmp_path / "p"
    project.mkdir()
    monkeypatch.setitem(SESSION, "workspace", str(project))
    fake = FakeService()
    found = Found(ServiceCode(_record(home), transport=httpx.MockTransport(fake)))

    result = _run(monkeypatch, ["resume", "c0ffee", "-m", "and now?"], found)

    assert result.exit_code == 0, result.output
    assert fake.turns() == [
        {"message": "and now?", "session_id": SESSION["id"], "workspace": str(project)}
    ]
    assert "through the background service" in result.output


def test_a_turn_error_exits_nonzero_and_never_prints_the_token(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    fake = FakeService([
        ("session", {"session_id": "c0ffee000099", "turn_id": "t1"}),
        ("error", {"message": f"provider said: bad header Bearer {TOKEN}"}),
    ])
    found = Found(ServiceCode(_record(home), transport=httpx.MockTransport(fake)))

    result = _run(monkeypatch, ["new", "oi"], found)

    assert result.exit_code == 1
    assert "bad header" in result.output and TOKEN not in result.output


def test_a_refused_token_reads_as_a_restart_not_a_crash(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    fake = FakeService()
    record = ServiceRecord(**{**_record(home).to_dict(), "token": "a-stale-one"})
    found = Found(ServiceCode(record, transport=httpx.MockTransport(fake)))

    result = _run(monkeypatch, ["new", "oi"], found)

    assert result.exit_code == 1 and "restarted" in result.output
    assert "a-stale-one" not in result.output


def test_sse_frames_are_parsed_across_keepalives_and_crlf() -> None:
    lines = [": ping", "event: token", "data: {\"text\": \"a\"}", "", "event: done", "data: {}", ""]
    assert list(iter_sse(lines)) == [("token", {"text": "a"}), ("done", {})]


# ---- approvals ----------------------------------------------------------------------------------


def _question_file(home: Path, request_id: str, shown: str) -> None:
    folder = home / "approvals"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{request_id}.ask.json").write_text(
        json.dumps({"id": request_id, "action": "run_shell\nrm -rf build", "reason": "deletes",
                    "asked_at": time.time(), "decision": "review", "code_shown": shown}),
        encoding="utf-8",
    )


def _approval_turn(request_id: str) -> FakeService:
    return FakeService([
        ("session", {"session_id": "c0ffee000099", "turn_id": "t1"}),
        ("approval", {"id": request_id, "action": "run_shell: rm -rf build", "reason": "deletes",
                      "p": 0.8, "band": "review", "wait_seconds": 300}),
        ("done", {"answer": "ok", "model": "m"}),
    ])


def _answer_file(home: Path, request_id: str) -> dict[str, Any] | None:
    path = home / "approvals" / f"{request_id}.answer.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _approved_over_http(fake: FakeService) -> bool:
    return any(
        p.startswith("/api/approvals/") and isinstance(b, dict) and b.get("approved")
        for _, p, b, _ in fake.requests
    )


def test_a_yes_without_a_code_approves_nothing(monkeypatch: pytest.MonkeyPatch, home: Path) -> None:
    _question_file(home, "0123456789ab", "message")
    fake = _approval_turn("0123456789ab")
    found = Found(ServiceCode(_record(home), transport=httpx.MockTransport(fake)))

    result = _run(monkeypatch, ["new", "oi"], found, stdin="yes\n\n")

    assert result.exit_code == 0, result.output
    assert "Approval needed" in result.output and "nothing approved" in result.output
    assert _answer_file(home, "0123456789ab") is None
    assert not _approved_over_http(fake), "a yes went to the screen's route, which needs no code"


def test_a_yes_with_the_code_is_written_with_it_for_the_asker_to_check(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    _question_file(home, "0123456789ab", "message")
    fake = _approval_turn("0123456789ab")
    found = Found(ServiceCode(_record(home), transport=httpx.MockTransport(fake)))

    result = _run(monkeypatch, ["new", "oi"], found, stdin="yes\n424242\n")

    assert result.exit_code == 0, result.output
    written = _answer_file(home, "0123456789ab")
    assert written is not None and written["approved"] is True
    assert written["code"] == "424242" and written["via"] == "terminal"
    assert not _approved_over_http(fake)


def test_a_no_goes_through_the_route_and_needs_no_code(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    _question_file(home, "0123456789ab", "message")
    fake = _approval_turn("0123456789ab")
    found = Found(ServiceCode(_record(home), transport=httpx.MockTransport(fake)))

    result = _run(monkeypatch, ["new", "oi"], found, stdin="no\n")

    assert result.exit_code == 0, result.output
    posted = [b for _, p, b, _ in fake.requests if p == "/api/approvals/0123456789ab"]
    assert posted == [{"approved": False}] and "refused" in result.output


def test_a_question_whose_code_is_only_on_the_apps_card_cannot_be_approved_here(
    monkeypatch: pytest.MonkeyPatch, home: Path
) -> None:
    _question_file(home, "0123456789ab", "screen")
    fake = _approval_turn("0123456789ab")
    found = Found(ServiceCode(_record(home), transport=httpx.MockTransport(fake)))

    result = _run(monkeypatch, ["new", "oi"], found, stdin="yes\n424242\n\n")

    assert result.exit_code == 0, result.output
    assert "only on the app's card" in result.output
    assert _answer_file(home, "0123456789ab") is None and not _approved_over_http(fake)


def test_against_a_real_asker_only_the_right_code_approves(home: Path) -> None:
    """The trap made real: this process asks, so it holds the code in memory, and a code-less
    `pending.answer(..., True)` from here WOULD approve. The terminal's yes still needs the code."""
    delivered: list[str] = []
    outcomes: dict[str, bool] = {}

    def ask(key: str) -> None:
        outcomes[key] = pending.ask_durably(
            home, f"run_shell\nrm -rf {key}", "deletes", deliver=delivered.append,
            wait_seconds=20, poll_seconds=0.02,
        )

    service = ServiceCode(_record(home), transport=httpx.MockTransport(FakeService()))

    def code_of(request_id: str) -> str:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            for line in delivered:
                if request_id in line and "--code " in line:
                    return line.split("--code ", 1)[1][:6]
            time.sleep(0.02)
        raise AssertionError("the asker never sent its code")

    def waiting(key: str) -> str:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            for q in pending.pending(home):
                if key in q.action:
                    return q.id
            time.sleep(0.02)
        raise AssertionError("the question was never asked")

    first = threading.Thread(target=ask, args=("first",), daemon=True)
    first.start()
    q1 = waiting("first")
    assert pending.approvable_here(q1), "the trap is not set: this process should hold the code"

    assert service.approve(q1, "") is False
    assert pending.is_waiting(home, q1), "a code-less yes resolved the question"
    wrong = f"{(int(code_of(q1)) + 1) % 10**6:06d}"
    assert service.approve(q1, wrong), "the answer file was not written"
    first.join(15)
    assert outcomes["first"] is False, "a wrong code approved the question"

    second = threading.Thread(target=ask, args=("second",), daemon=True)
    second.start()
    q2 = waiting("second")
    assert service.approve(q2, code_of(q2))
    second.join(15)
    assert outcomes["second"] is True


def test_the_probe_used_is_the_service_records_own() -> None:
    """`find` checks with the same `probe` the S1 commands use — not a looser copy of it."""
    import inspect

    assert inspect.signature(find).parameters["check"].default is probe


# ---- against the real app -----------------------------------------------------------------------


def test_a_conversation_started_in_the_terminal_is_the_apps_and_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real API behind the owner's token: the terminal starts a conversation, the screen's own
    request continues it, and the terminal continues it again — one conversation, three turns."""
    pytest.importorskip("sse_starlette")
    from typing import cast

    from fastapi.testclient import TestClient

    import chimera.core
    from chimera.api import build_api_app
    from chimera.config import get_settings
    from chimera.core.agent import AgentResult
    from chimera.interface import ChatSession
    from chimera.interface.session import SupportsRun

    class _Agent:
        def run(self, task: str, *, on_token: Any = None, history: Any = None, **_k: Any
                ) -> AgentResult:
            if on_token:
                on_token(f"did: {task}")
            transcript = [*(history or []), {"role": "user", "content": task},
                          {"role": "assistant", "content": f"did: {task}"}]
            return AgentResult(answer=f"did: {task}", steps=1, stopped_reason="final",
                               transcript=transcript, model="test/model")

    for key in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(chimera.core, "Agent", lambda *_a, **_k: _Agent(), raising=True)
    ws = tmp_path / "ws"
    ws.mkdir()
    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    monkeypatch.setenv("CHIMERA_SERVER_TOKEN", TOKEN)
    get_settings.cache_clear()
    try:
        app = build_api_app(lambda: ChatSession(cast(SupportsRun, None)), workspace=ws)
        with TestClient(app) as client:
            assert client.get("/api/code/sessions").status_code == 401, "the API answered without a token"
            service = ServiceCode(_record(home), transport=client._transport)

            started = code_cmd.follow_service_turn(
                service, {"message": "from the terminal", "workspace": str(ws)}, lambda _p: None
            )
            screen = client.post(
                "/api/code/turn",
                json={"message": "from the screen", "session_id": started.session_id},
                headers={"Authorization": f"Bearer {TOKEN}"},
            )
            assert screen.status_code == 200
            again = code_cmd.follow_service_turn(
                service, {"message": "terminal again", "session_id": started.session_id},
                lambda _p: None,
            )
            [row] = service.sessions()
    finally:
        get_settings.cache_clear()

    assert started.ok and again.ok, (started.error, again.error)
    assert started.session_id and again.session_id == started.session_id
    assert row["id"] == started.session_id and row["turns"] == 3
    assert Path(row["workspace"]) == ws.resolve()
