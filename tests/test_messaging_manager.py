"""The in-app messaging manager: start/stop a platform adapter in a background thread + honest status.

Uses a fake adapter (no discord.py, no network): its start() blocks on an Event until stop() is
called, exactly like the real one, so the thread lifecycle and status are exercised for real.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from chimera.config import Settings
from chimera.providers import CompletionResult
from chimera.server import MessagingManager


class _FakeBackend:
    def complete(self, messages: list[Any], **kwargs: Any) -> CompletionResult:
        return CompletionResult(content="ok", model="fake")


class _FakeAdapter:
    """Blocks in start() until stop() (or the injected error) — mirrors a real adapter's lifecycle."""

    platform = "discord"  # SenderRegistry keys senders by platform

    def send(self, *_a: Any, **_k: Any) -> str:  # a MessageSender must be sendable
        return "ok"

    def __init__(self, *, fail: bool = False) -> None:
        self._stop = threading.Event()
        self.started = False
        self.stopped = False
        self._fail = fail

    def start(self, _route: Any) -> None:
        if self._fail:
            raise RuntimeError("bad token")
        self.started = True
        self._stop.wait()  # serve until stop()

    def stop(self) -> None:
        self.stopped = True
        self._stop.set()


def _manager(tmp_path: Path, *, adapter: _FakeAdapter, token: str | None = "t") -> MessagingManager:
    settings = Settings(CHIMERA_HOME=str(tmp_path), CHIMERA_DISCORD_BOT_TOKEN=token)
    return MessagingManager(
        settings=settings,
        backend=_FakeBackend(),
        model=None,
        max_steps=4,
        workspace=tmp_path,
        adapter_factory=lambda _p: adapter,
    )


def test_start_runs_the_adapter_and_status_reflects_it(tmp_path: Path) -> None:
    adapter = _FakeAdapter()
    mgr = _manager(tmp_path, adapter=adapter)

    assert mgr.status()["discord"] == {"configured": True, "running": False, "error": None}
    mgr.start("discord")
    # The adapter runs in a background thread; give it a moment to enter start().
    for _ in range(200):
        if adapter.started:
            break
        threading.Event().wait(0.01)
    assert adapter.started
    assert mgr.is_running("discord")
    assert mgr.status()["discord"]["running"] is True

    mgr.stop("discord")
    assert adapter.stopped
    assert mgr.is_running("discord") is False


def test_start_is_idempotent(tmp_path: Path) -> None:
    adapter = _FakeAdapter()
    mgr = _manager(tmp_path, adapter=adapter)
    mgr.start("discord")
    mgr.start("discord")  # second call must not spawn a second thread or raise
    active = [t for t in threading.enumerate() if t.name == "chimera-msg-discord"]
    assert len(active) == 1
    mgr.stop("discord")


def test_start_unconfigured_platform_raises(tmp_path: Path) -> None:
    # Uses the REAL adapter factory (no injection) so the token check runs: with no token, the
    # default factory raises before ever importing discord.py.
    settings = Settings(CHIMERA_HOME=str(tmp_path), CHIMERA_DISCORD_BOT_TOKEN=None)
    mgr = MessagingManager(
        settings=settings, backend=_FakeBackend(), model=None, max_steps=4, workspace=tmp_path
    )
    assert mgr.configured("discord") is False
    with pytest.raises(ValueError, match="not configured"):
        mgr.start("discord")


def test_a_dead_adapter_is_captured_in_status_not_raised(tmp_path: Path) -> None:
    adapter = _FakeAdapter(fail=True)
    mgr = _manager(tmp_path, adapter=adapter)
    mgr.start("discord")  # must not raise even though the adapter thread dies
    # Wait for the thread to actually end (not just for the error to be set — there's a moment where
    # error is recorded but the thread hasn't exited yet).
    for _ in range(200):
        if not mgr.is_running("discord") and mgr.status()["discord"]["error"]:
            break
        threading.Event().wait(0.01)
    status = mgr.status()["discord"]
    assert status["running"] is False
    assert "bad token" in (status["error"] or "")


def test_stop_all_closes_every_running_adapter(tmp_path: Path) -> None:
    adapter = _FakeAdapter()
    mgr = _manager(tmp_path, adapter=adapter)
    mgr.start("discord")
    mgr.stop_all()
    assert adapter.stopped
    assert mgr.is_running("discord") is False
