"""Five sites at once: the hierarchy's workers can read the web, and the answer says what it read.

Item 4 of the list audited on 2026-09-16. "Read these five sites and compare them" classified as
five sources and decomposed into five subtasks — and handed five workers that could open a file
and not a URL. The fetch tools join the worker set here (`scrape`, `http_get`, `web_search`), and
with them the thing that was missing before they could: a worker's taint ledger now travels on its
envelope, so an answer synthesised from five pages can say that it read untrusted content — the
way a tainted memory is labelled on recall and a tainted coding turn is on its receipt.

What is pinned: through the real route, a worker that fetches a page from a local server is
marked tainted on `worker_verified` and the run's `done` carries it; a worker that fetched nothing
is not, and neither is the run; a bare registry from an older factory still works and reports
nothing; the envelope's field defaults to `False` for everything written before it existed; the
worker is told to fetch what it is asked about and never to describe a page it did not fetch.
"""

from __future__ import annotations

import http.server
import json
import threading
from pathlib import Path
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from chimera.api.orchestration_api import register_orchestration_api
from chimera.config import Settings
from chimera.orchestration.hierarchy import WORKER_SYSTEM, HierarchicalOrchestrator, WorkerKit
from chimera.orchestration.spec import ResultEnvelope
from chimera.providers.gateway import CompletionResult, Message, MessageLike, ToolCall
from tests.test_hierarchy import _orchestrator

PAGE = b"<html><body><h1>Acme pricing</h1><p>Plan A costs 10 a month.</p></body></html>"


class _Site(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — the stdlib's spelling
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *_: Any) -> None:
        pass


@pytest.fixture()
def site() -> Any:
    server = http.server.HTTPServer(("127.0.0.1", 0), _Site)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/pricing"
    finally:
        server.shutdown()


class _FetchingBackend:
    """Decomposes into two subtasks; worker A fetches the page on its first step, worker B never
    fetches; the synthesis is a sentence."""

    def __init__(self, url: str) -> None:
        self.url = url
        self.fetched_once = False

    def complete(self, messages: list[MessageLike], *, model: str | None = None, **_: Any) -> CompletionResult:
        first = messages[0]
        data = first.as_dict() if isinstance(first, Message) else first
        system = str(data.get("content", "")) if data.get("role") == "system" else ""
        if "Split the user's task" in system:
            content = json.dumps([
                {"objective": f"Read the pricing page at {self.url} and report the price of plan A",
                 "output_format": "one line", "boundaries": "that page only"},
                {"objective": "State what a monthly plan is", "output_format": "one line",
                 "boundaries": "general knowledge"},
            ])
            return CompletionResult(content=content, model=model or "?", prompt_tokens=50, completion_tokens=50)
        if WORKER_SYSTEM in system:  # the agent loop wraps the role's prompt with its own sections
            user = "".join(
                str((m.as_dict() if isinstance(m, Message) else m).get("content") or "")
                for m in messages
            )
            if self.url in user and not self.fetched_once:
                self.fetched_once = True
                return CompletionResult(
                    content="", model=model or "?", prompt_tokens=50, completion_tokens=5,
                    tool_calls=[ToolCall(id="c1", name="http_get", arguments={"url": self.url})],
                )
            if self.url in user:
                return CompletionResult(
                    content="Plan A costs 10 a month (from the page).\n\nGaps\n(none)",
                    model=model or "?", prompt_tokens=50, completion_tokens=20,
                )
            return CompletionResult(
                content="A monthly plan bills every month.\n\nGaps\n(none)",
                model=model or "?", prompt_tokens=50, completion_tokens=20,
            )
        return CompletionResult(content="Plan A: 10 a month.", model=model or "?", prompt_tokens=50, completion_tokens=20)


def _read_sse(text: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    event = ""
    for line in text.splitlines():
        if line.startswith("event:"):
            event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            events.append((event, json.loads(line[len("data:") :].strip())))
    return events


def test_through_the_route_a_worker_that_fetched_a_page_is_marked_and_so_is_the_run(
    tmp_path: Path, site: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHIMERA_HOME", str(tmp_path / "home"))
    from chimera.config import get_settings

    get_settings.cache_clear()
    backend = _FetchingBackend(site)
    app = FastAPI()
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    register_orchestration_api(app, Depends(lambda: None), tmp_path, settings, backend_factory=lambda: backend)
    client = TestClient(app)

    task = f"Compare the pricing at {site} and at https://example.org/pricing and say which is cheaper"
    response = client.post("/api/orchestration/hierarchy", json={"task": task, "max_workers": 2, "fuse": False})
    assert response.status_code == 200
    events = _read_sse(response.text)
    kinds = [e for e, _ in events]
    assert "done" in kinds, kinds

    assert backend.fetched_once, "the worker never asked for the page"
    verified = [d for e, d in events if e == "worker_verified"]
    assert len(verified) == 2, [(e, d.get("reason"), d.get("detail")) for e, d in events]
    by_tainted = {d["tainted"] for d in verified}
    assert by_tainted == {True, False}, verified
    fetched = next(d for d in verified if d["tainted"])
    assert "page" in fetched["text"] or fetched["stage"], fetched  # a verified worker, marked
    done = next(d for e, d in events if e == "done")
    assert done["tainted"] is True
    assert done["envelopes"] == 2


def test_a_run_whose_workers_fetched_nothing_is_not_marked(tmp_path: Path) -> None:
    from tests.test_hierarchy import _READ_TASK, FakeBackend

    seen: list[WorkerKit] = []

    class _Ledger:
        def run_tainted(self) -> bool:
            return False

    def kit() -> WorkerKit:
        made = WorkerKit(registry=None, ledger=_Ledger())
        seen.append(made)
        return made

    orch = _orchestrator(FakeBackend(), tmp_path)
    orch.worker_tools = kit
    frames: list[tuple[str, dict[str, Any]]] = []
    orch.on_event = lambda event: frames.append((event.kind, dict(event.data)))
    result = orch.run(_READ_TASK)
    assert seen, "the factory was never asked"
    assert result.tainted is False
    assert all(e.tainted is False for e in result.envelopes)
    done = next(d for k, d in frames if k == "done")
    assert done["tainted"] is False
    assert all(d["tainted"] is False for k, d in frames if k == "worker_verified")


def test_a_ledger_that_saw_a_fetch_marks_the_envelope_and_the_result(tmp_path: Path) -> None:
    from tests.test_hierarchy import _READ_TASK, FakeBackend

    class _TaintedLedger:
        def run_tainted(self) -> bool:
            return True

    orch = _orchestrator(FakeBackend(), tmp_path)
    orch.worker_tools = lambda: WorkerKit(registry=None, ledger=_TaintedLedger())
    frames: list[tuple[str, dict[str, Any]]] = []
    orch.on_event = lambda event: frames.append((event.kind, dict(event.data)))
    result = orch.run(_READ_TASK)
    assert result.tainted is True
    assert all(e.tainted is True for e in result.envelopes)
    assert next(d for k, d in frames if k == "done")["tainted"] is True


def test_a_bare_registry_from_an_older_factory_still_works_and_reports_nothing(tmp_path: Path) -> None:
    from tests.test_hierarchy import _READ_TASK, FakeBackend

    orch = _orchestrator(FakeBackend(), tmp_path)
    orch.worker_tools = lambda: None  # "a registry" of nothing — the contract before WorkerKit
    result = orch.run(_READ_TASK)
    assert result.envelopes and result.tainted is False


def test_the_envelope_field_defaults_to_false_for_everything_written_before_it() -> None:
    old = ResultEnvelope.model_validate({"task_id": "t1", "summary": "written last month"})
    assert old.tainted is False
    assert ResultEnvelope(task_id="t2", tainted=True).model_dump()["tainted"] is True


def test_the_worker_is_told_to_fetch_what_it_is_asked_about_and_never_to_describe_a_page_it_did_not() -> None:
    assert "FETCH IT" in WORKER_SYSTEM
    assert "Never describe a page you have not fetched" in WORKER_SYSTEM
    assert "that page's claim" in WORKER_SYSTEM
    assert isinstance(HierarchicalOrchestrator, type)
