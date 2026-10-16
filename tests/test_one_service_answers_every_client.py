"""One background service per data folder, found by every client through one record (S1).

The desktop backend existed only while the app's window did: a terminal, a script or a phone had
nothing to reach with the app closed. ``chimera service start`` runs the same server detached, and
these tests drive the REAL command in a subprocess, with a throwaway home, and kill whatever it
started however the test ends (``tests/service_harness.py``).

What each one would catch if the rule broke:

* the server dies with the command that started it (spawned attached, or with a pipe);
* a second ``start`` spawns a second server instead of naming the first;
* ``stop`` leaves the process, or the record, behind;
* the API answers without the owner's token, or refuses the token the record names;
* two ``start`` commands at once leave two servers (the claim, not the port, decides);
* the owner's token alone approves a question — the per-question code (S30-30) must still be
  required, or a long-lived token would be an approval key.
"""

from __future__ import annotations

import concurrent.futures
import json
import re
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from chimera.core.service_record import ServiceRecord, call, pid_alive, request
from tests.service_harness import START_TIMEOUT, ServiceSandbox, wait_dead


@pytest.fixture
def sandbox(tmp_path: Path) -> Iterator[ServiceSandbox]:
    box = ServiceSandbox(tmp_path)
    try:
        yield box
    finally:
        box.close()


def _running(box: ServiceSandbox) -> ServiceRecord:
    record = box.record()
    assert record is not None, f"no record after start:\n{box.log()}"
    return record


def test_start_runs_a_server_that_outlives_the_command(sandbox: ServiceSandbox) -> None:
    started = sandbox.start()
    record = _running(sandbox)

    assert started["state"] == "started" and started["pid"] == record.pid
    assert "token" not in started, "start printed the owner's token"
    time.sleep(1.0)  # the `start` process has exited; the server must not have gone with it
    assert pid_alive(record.pid)
    status, info = call(record, "GET", "/api/info")
    assert status == 200 and info["pid"] == record.pid and info["mode"] == "service"

    shown = sandbox.cli("status", "--json")
    assert shown.returncode == 0, shown.stdout
    state = json.loads(shown.stdout)
    assert state["state"] == "alive" and state["pid"] == record.pid and "token" not in state


def test_a_second_start_says_it_is_already_running(sandbox: ServiceSandbox) -> None:
    sandbox.start()
    record = _running(sandbox)

    again = sandbox.cli("start", "--json")

    assert again.returncode == 0, again.stdout
    assert json.loads(again.stdout)["state"] == "already_running"
    assert _running(sandbox).id == record.id, "a second start replaced the running service"


def test_stop_ends_the_process_and_removes_the_record(sandbox: ServiceSandbox) -> None:
    sandbox.start()
    record = _running(sandbox)

    stopped = sandbox.cli("stop", "--timeout", "60")

    assert stopped.returncode == 0, stopped.stdout + stopped.stderr
    assert wait_dead(record.pid), "the service outlived its stop"
    assert not sandbox.record_path.exists(), "stop left the record behind"
    assert sandbox.cli("status").returncode == 1


def test_the_api_refuses_without_the_token_and_answers_with_the_records(sandbox: ServiceSandbox) -> None:
    sandbox.start()
    record = _running(sandbox)

    assert request(record.url, "", "GET", "/api/info")[0] == 401
    assert request(record.url, "not-the-token", "GET", "/api/sessions")[0] == 401
    assert request(record.url, record.token, "GET", "/api/info")[0] == 200
    assert request(record.url, "", "GET", "/api/health")[0] == 200, "health stays open, as before"


def test_two_starts_at_once_leave_one_service(sandbox: ServiceSandbox) -> None:
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        runs = list(pool.map(
            lambda _: sandbox.cli("start", "--port", "0", "--json", "--timeout", str(START_TIMEOUT)),
            range(2),
        ))

    outcomes = [json.loads(r.stdout.strip().splitlines()[-1]) for r in runs]
    assert all(r.returncode == 0 for r in runs), [r.stdout for r in runs]
    assert outcomes[0]["pid"] == outcomes[1]["pid"], "the two starts name different services"
    winner = outcomes[0]["pid"]
    spawned = {o["spawned_pid"] for o in outcomes if o.get("spawned_pid")}
    # A venv's `python.exe` on Windows is a launcher that runs the interpreter as its child, so the
    # pid `start` spawned is the winner's PARENT there. Every other spawned process must exit.
    losers = {pid for pid in spawned - {winner} if not _is_ancestor(pid, winner)}
    assert all(wait_dead(pid, 60) for pid in losers), "a second service kept running"
    assert pid_alive(winner)


def _is_ancestor(pid: int, of: int) -> bool:
    import psutil

    try:
        return any(parent.pid == pid for parent in psutil.Process(of).parents())
    except psutil.Error:
        return False


def test_the_owners_token_alone_does_not_approve_a_question(sandbox: ServiceSandbox) -> None:
    """The service holds the whole API, and the token is long-lived. A question asked by another
    process — here this test, standing in for a terminal run — is approved only with the code its
    asker was told; the token reaches the route and the route still says `needs_code`."""
    from chimera.governance import pending

    sandbox.start()
    record = _running(sandbox)
    delivered: list[str] = []
    outcome: dict[str, bool] = {}

    def ask() -> None:
        outcome["approved"] = pending.ask_durably(
            sandbox.home, "run_shell\ngit push --force", "policy: force push",
            deliver=delivered.append, wait_seconds=30, poll_seconds=0.05,
        )

    asker = threading.Thread(target=ask, daemon=True)
    asker.start()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not (pending.pending(sandbox.home) and delivered):
        time.sleep(0.05)
    question = pending.pending(sandbox.home)[0].id

    path = f"/api/approvals/{question}"

    status, answered = request(record.url, record.token, "POST", path, body={"approved": True}, timeout=10)

    assert status == 200 and answered["ok"] is False and answered["outcome"] == "needs_code", answered
    assert re.search(r"--code \d{6}", " ".join(delivered)), "the asker never sent its code"
    assert pending.is_waiting(sandbox.home, question), "the token's yes resolved the question"
    # A refusal needs no code from anywhere, and it also ends the asker here.
    refused = request(record.url, record.token, "POST", path, body={"approved": False}, timeout=10)
    assert refused == (200, {"ok": True})
    asker.join(35)
    assert outcome.get("approved") is False, "the question was approved by the token alone"


def test_the_frozen_backend_passes_service_through_and_prefixes_app_otherwise() -> None:
    import importlib.util

    root = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "chimera_backend", root / "apps" / "desktop" / "src-tauri" / "sidecar" / "chimera_backend.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.cli_argv(["exe", "service", "run", "--port", "1"]) == ["exe", "service", "run", "--port", "1"]
    assert module.cli_argv(["exe", "--no-open", "--port", "0"]) == ["exe", "app", "--no-open", "--port", "0"]
    assert module.cli_argv(["exe"]) == ["exe", "app"]
