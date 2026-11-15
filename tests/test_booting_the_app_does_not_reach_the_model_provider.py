"""`chimera app` must not start a background fetch of the provider's model index under test.

The boot starts a daemon thread (`price-warmup`) that downloads OpenRouter's model list whenever a key
is configured. It outlives the command, so on CI the connection was charged to a later test. The
network guard only acts on Linux, so this asserts the cause directly: no fetch is made and no such
thread is left, on any OS.
"""

from __future__ import annotations

import threading
import time

import pytest
from typer.testing import CliRunner

pytest.importorskip("fastapi")

from chimera.cli.main import app
from chimera.providers import listing


def test_an_app_boot_with_a_key_neither_fetches_the_index_nor_leaves_a_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    fetched: list[str] = []
    monkeypatch.setattr(
        listing, "openrouter_models", lambda *a, **k: (fetched.append("index"), ((), "offline"))[1]
    )
    monkeypatch.setattr("uvicorn.Server.run", lambda self, **kw: None)

    result = CliRunner().invoke(app, ["app", "--no-open", "--no-memory", "--no-cron", "--port", "0"])
    assert result.exit_code == 0, result.output

    # A real warm-up would be running (or done) by now; give it the time it needs to show itself.
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if fetched:
            break
        time.sleep(0.05)
    assert fetched == []
    assert not [t for t in threading.enumerate() if t.name == "price-warmup"]
