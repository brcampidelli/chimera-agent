"""A stub coding agent and an app around it, for the tests of the conversation list's state and archive.

`Agent` edits on "write", raises on "boom" and waits on "hold" until released. `App` builds the API
over it in a temporary home and drives turns, the list and the approvals folder.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from chimera.config import Settings, get_settings
from chimera.core.agent import AgentResult
from chimera.interface import ChatSession

TIMEOUT = 10.0


class Agent:
    """Edits on "write", raises on "boom", waits on "hold"."""

    hold = threading.Event()
    held = threading.Event()

    def __init__(self, *_a: Any, **kwargs: Any) -> None:
        self.config = kwargs.get("config") or (_a[2] if len(_a) > 2 else None)

    def run(self, task: str, on_edit: Any = None, should_stop: Any = None, **_kw: Any) -> AgentResult:
        if "boom" in task:
            raise RuntimeError("the model went away")
        if "hold" in task:
            type(self).held.set()
            deadline = time.monotonic() + TIMEOUT
            while not type(self).hold.is_set() and time.monotonic() < deadline:
                if should_stop is not None and should_stop():
                    break
                time.sleep(0.01)
        folder = re.search(r"\[(.+?)\]", task)
        if folder is not None and "write" in task:
            (Path(folder.group(1)) / "a.txt").write_text("by the turn", encoding="utf-8")
            if on_edit is not None:
                on_edit("a.txt", "@@")
        return AgentResult(
            answer="done", steps=1, stopped_reason="final",
            transcript=[{"role": "user", "content": task}, {"role": "assistant", "content": "done"}],
        )


class App:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, agent: type, **env: str) -> None:
        import chimera.core
        from chimera.api import build_api_app

        self.home = tmp_path / "home"
        monkeypatch.setenv("CHIMERA_HOME", str(self.home))
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        get_settings.cache_clear()
        monkeypatch.setattr(chimera.core, "Agent", agent, raising=True)
        self.folder = tmp_path / "proj"
        self.folder.mkdir(exist_ok=True)
        settings = Settings(CHIMERA_HOME=str(self.home), CHIMERA_MEMORY_BACKEND="json", **env)
        self.app = build_api_app(
            lambda: ChatSession(agent()), workspace=self.folder, settings=settings
        )
        self.client = TestClient(self.app)

    def turn(self, message: str, session_id: str | None = None) -> str:
        body: dict[str, Any] = {
            "message": f"{message} [{self.folder}]", "workspace": str(self.folder), "stream": False,
        }
        if session_id:
            body["session_id"] = session_id
        text = self.client.post("/api/code/turn", json=body).text
        found = re.search(r'"session_id": ?"([0-9a-f]+)"', text)
        assert found, text[:400]
        return found.group(1)

    def rows(self, **params: Any) -> dict[str, dict[str, Any]]:
        response = self.client.get("/api/code/sessions", params=params)
        assert response.status_code == 200, response.text
        return {row["id"]: row for row in response.json()}

    def state(self, session_id: str) -> str:
        return str(self.rows()[session_id]["state"])

    def ask(self, run_id: str, request_id: str = "q1") -> None:
        directory = self.home / "approvals"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{request_id}.ask.json").write_text(
            json.dumps({"id": request_id, "action": "run_shell: rm -rf build", "reason": "destructive",
                        "asked_at": time.time(), "run_id": run_id}),
            encoding="utf-8",
        )
