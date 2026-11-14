"""Three things the API promised in comments and nothing enforced.

* An exception no route caught reaches the client as a generic 500 with an id, never as its own text.
* A large upload does not stop the server from answering other requests while it is processed.
* A log line written under a turn names the turn, so one turn's story can be read out of
  ``backend.log`` without matching timestamps by eye.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sse_starlette")

import httpx
from fastapi.testclient import TestClient

from chimera.config import Settings
from chimera.core.agent import AgentResult
from chimera.interface import ChatSession


class _FakeAgent:
    def run(self, task: str, **_: Any) -> AgentResult:  # pragma: no cover - never called here
        return AgentResult(answer="", steps=0, stopped_reason="final")


def _app(tmp_path: Path) -> Any:
    from chimera.api import build_api_app

    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    return build_api_app(lambda: ChatSession(_FakeAgent()), settings=settings)


# --- 4. the global handler -------------------------------------------------------------------------

SECRET = "postgres://user:" + "hunter2" + "@db.internal/prod"


def test_an_uncaught_exception_is_a_generic_500_that_quotes_nothing(tmp_path: Path) -> None:
    app = _app(tmp_path)

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError(f"could not connect to {SECRET}")

    records: list[logging.LogRecord] = []

    class Catch(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    log = logging.getLogger("chimera.api.app")
    handler = Catch(level=logging.ERROR)
    log.addHandler(handler)
    try:
        res = TestClient(app, raise_server_exceptions=False).get("/boom")
    finally:
        log.removeHandler(handler)

    assert res.status_code == 500
    body = res.json()  # the old default was text/plain "Internal Server Error": this is the first failure
    assert body["detail"] == "internal server error"
    assert SECRET not in res.text and "hunter2" not in res.text
    # The id in the answer is the id in the log, which is how a person connects the two.
    assert [r for r in records if body["error_id"] in r.getMessage()], "the failure was not logged with its id"
    assert any(r.exc_info for r in records), "the log carries the traceback the client does not"


# --- 5. upload off the event loop ------------------------------------------------------------------


def test_health_answers_while_a_large_upload_is_being_processed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.api import attachments

    started = threading.Event()

    def slow_save(home: Path, name: str, data: bytes) -> attachments.Attachment:
        # Stands in for converting a 20 MB PDF: seconds of CPU with the GIL mostly released.
        started.set()
        time.sleep(2.0)
        return attachments.Attachment(id="a" * 32, name=name, kind="document", path=home / "x", text="t")

    monkeypatch.setattr(attachments, "save", slow_save)
    app = _app(tmp_path)

    async def scenario() -> float:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            # The clock starts BEFORE the upload is scheduled: if the conversion runs on the loop it
            # holds the loop, so even the 0.2 s sleep below returns late and the total shows it. A
            # clock started after "the conversion began" would start after the block was over.
            began = time.perf_counter()
            upload = asyncio.create_task(
                client.post("/api/attachments", files={"file": ("big.pdf", b"%PDF" + b"0" * 1_000_000, "application/pdf")})
            )
            await asyncio.sleep(0.2)
            health = await client.get("/api/health")
            elapsed = time.perf_counter() - began
            assert health.status_code == 200
            assert started.is_set(), "the upload never reached the conversion"
            assert (await upload).status_code == 200
            return elapsed

    elapsed = asyncio.run(scenario())
    assert elapsed < 1.0, f"/api/health answered after {elapsed:.2f}s with an upload in flight: the conversion is on the event loop"


# --- 6. the turn id in the log ---------------------------------------------------------------------


def _capture(name: str) -> tuple[logging.Logger, list[logging.LogRecord], logging.Handler]:
    from chimera.telemetry import get_logger

    log = get_logger(name)
    records: list[logging.LogRecord] = []

    class Catch(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = Catch(level=logging.DEBUG)
    log.addHandler(handler)
    return log, records, handler


def test_a_record_written_under_a_turn_carries_its_id() -> None:
    from chimera.telemetry import bind_turn, current_turn_id

    log, records, handler = _capture("turn_corr_a")
    try:
        log.warning("outside")
        with bind_turn("t" * 32):
            log.warning("inside")
            assert current_turn_id() == "t" * 32
        log.warning("after")
    finally:
        log.removeHandler(handler)
    assert [r.turn_id for r in records] == ["-", "t" * 32, "-"]  # type: ignore[attr-defined]
    assert current_turn_id() is None


def test_a_worker_thread_logs_under_the_turn_it_was_started_for() -> None:
    from chimera.telemetry import bound_to_turn

    log, records, handler = _capture("turn_corr_b")
    try:
        worker = threading.Thread(target=bound_to_turn("turn-xyz", lambda: log.warning("from the worker")))
        worker.start()
        worker.join()
    finally:
        log.removeHandler(handler)
    assert [r.turn_id for r in records] == ["turn-xyz"]  # type: ignore[attr-defined]


def test_backend_log_lines_name_the_turn(tmp_path: Path) -> None:
    from chimera.file_log import attach_file_log
    from chimera.telemetry import bind_turn, get_logger

    path = tmp_path / "logs" / "backend.log"
    assert attach_file_log(path) is not None
    root = logging.getLogger("chimera")
    try:
        with bind_turn("abc123"):
            get_logger("turn_corr_c").warning("tool failed")
        get_logger("turn_corr_c").warning("no turn here")
        for handler in root.handlers:
            handler.flush()
    finally:
        for handler in list(root.handlers):
            if getattr(handler, "baseFilename", None) == str(path.resolve()):
                root.removeHandler(handler)
                handler.close()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert any("[turn=abc123]" in line and "tool failed" in line for line in lines)
    assert any("[turn=-]" in line and "no turn here" in line for line in lines)


def test_the_turn_thread_is_started_bound_to_its_turn() -> None:
    """The wiring: a binding helper nobody calls is the failure the helper's own tests cannot see."""
    source = (Path(__file__).resolve().parents[1] / "chimera" / "api" / "code_api.py").read_text(encoding="utf-8")
    assert "target=bound_to_turn(turn_id," in source
