"""The suggestions under a Code-screen answer are measured, the way the inline completion is (P4.5).

They are computed on the screen from facts of the turn and only fill the box; the server keeps the
one thing the plan asked of them — how often they are taken. Shown, picked, sent; two rates, each
null until it has a denominator; per kind; and never the suggestion's text.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chimera.api import suggestion_log
from chimera.config import Settings
from chimera.interface import ChatSession


def test_nothing_shown_is_a_null_rate_and_not_a_zero(tmp_path: Path) -> None:
    out = suggestion_log.stats(tmp_path)
    assert out["shown"] == 0
    assert out["pick_rate"] is None and out["send_rate"] is None
    assert out["note"]
    assert set(out["by_kind"]) == {"fix", "commit", "continue"}


def test_the_rates_are_picks_over_shown_and_sends_over_picks_per_kind(tmp_path: Path) -> None:
    for _ in range(4):
        suggestion_log.record(tmp_path, event="shown", kind="continue")
    suggestion_log.record(tmp_path, event="shown", kind="fix")
    suggestion_log.record(tmp_path, event="picked", kind="continue")
    suggestion_log.record(tmp_path, event="picked", kind="continue")
    suggestion_log.record(tmp_path, event="sent", kind="continue", edited=True)

    out = suggestion_log.stats(tmp_path)
    assert (out["shown"], out["picked"], out["sent"], out["edited"]) == (5, 2, 1, 1)
    assert out["pick_rate"] == pytest.approx(2 / 5)
    assert out["send_rate"] == pytest.approx(1 / 2)
    assert out["by_kind"]["continue"]["pick_rate"] == pytest.approx(2 / 4)
    assert out["by_kind"]["fix"]["pick_rate"] == 0.0
    assert out["by_kind"]["fix"]["send_rate"] is None
    assert out["by_kind"]["commit"]["shown"] == 0 and out["by_kind"]["commit"]["pick_rate"] is None
    assert out["note"] == ""


def test_the_ledger_keeps_the_kind_and_never_the_text(tmp_path: Path) -> None:
    suggestion_log.record(tmp_path, event="sent", kind="commit", edited=False)
    line = (tmp_path / "suggestion_outcomes.jsonl").read_text(encoding="utf-8")
    assert set(json.loads(line)) == {"at", "event", "kind", "edited"}


def test_a_ledger_that_cannot_be_written_does_not_fail_the_click(tmp_path: Path) -> None:
    blocked = tmp_path / "home"
    blocked.write_text("a file where the home directory should be", encoding="utf-8")
    suggestion_log.record(blocked, event="shown", kind="fix")  # must not raise


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from chimera.api import build_api_app
    from chimera.config import get_settings

    home = tmp_path / "home"
    monkeypatch.setenv("CHIMERA_HOME", str(home))
    get_settings.cache_clear()
    settings = Settings(CHIMERA_HOME=str(home))  # type: ignore[call-arg]
    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    # The chat factory is never called by these two routes, so it needs no agent behind it.
    return TestClient(build_api_app(lambda: ChatSession(None), workspace=ws, settings=settings))  # type: ignore[arg-type]  # never called


def test_the_routes_record_an_event_and_answer_with_the_rates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(tmp_path, monkeypatch)
    assert client.get("/api/suggestions/stats").json()["pick_rate"] is None

    assert client.post("/api/suggestions/event", json={"event": "shown", "kind": "fix"}).status_code == 200
    answer = client.post("/api/suggestions/event", json={"event": "picked", "kind": "fix"})
    assert answer.status_code == 200
    assert answer.json()["pick_rate"] == 1.0
    assert client.get("/api/suggestions/stats").json()["by_kind"]["fix"]["picked"] == 1


@pytest.mark.parametrize(
    "body",
    [
        {"event": "clicked", "kind": "fix"},
        {"event": "shown", "kind": "deploy"},
        {"event": "shown"},
    ],
)
def test_an_event_or_kind_the_ledger_does_not_know_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: dict[str, str]
) -> None:
    client = _client(tmp_path, monkeypatch)
    assert client.post("/api/suggestions/event", json=body).status_code == 422
    assert not (tmp_path / "home" / "suggestion_outcomes.jsonl").exists()
