"""Every spoken request is written down with the label the classifier gave it, before it routes.

The voice router's split — talk against work — is a deterministic regex (`classify_task`) that
study 20 §3 named as a decision that should carry a number. It cannot be measured for one reason:
no corpus of real spoken requests exists, and a classifier compared on sentences its author wrote is
compared on its author. So the first step is a record, not a model (`chimera/api/spoken_log.py`):
each spoken request lands in `<home>/voice/requests.jsonl` with the regex's label, and the person
who spoke it fills `reviewed` later. A hundred rows is the bench.

Two things this file holds. The row is written for BOTH labels — the `work` branch returns early
into the spoken-work frames, so a record placed after the routing would never see the label the
corpus needs most. And a typed request writes nothing: the corpus is of speech.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from chimera.api.spoken_log import FILE, read_spoken_requests


def _client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[TestClient, Path]:
    from tests.test_a_spoken_turn_is_answered_for_the_ear import _client as build

    client = build(tmp_path, monkeypatch)
    return client, tmp_path / "home"


def _rows(home: Path) -> list[dict[str, object]]:
    return read_spoken_requests(home)


def test_a_spoken_talk_request_is_recorded_with_the_talk_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, home = _client(tmp_path, monkeypatch)
    r = client.post("/api/code/turn", json={"message": "are you there?", "spoken": True})
    assert r.status_code == 200
    rows = _rows(home)
    assert len(rows) == 1
    row = rows[0]
    assert row["message"] == "are you there?"
    assert row["label"] == "talk"
    assert row["has_works"] is False
    assert row["reviewed"] is None  # the column the person fills; written as null on purpose
    assert isinstance(row["ts"], str) and row["ts"]


def test_a_spoken_work_request_is_recorded_before_it_routes_to_the_work_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `work` is the branch that returns early. A record placed after the routing would hold only
    # `talk` rows — the half of the corpus the classifier is least in doubt about.
    client, home = _client(tmp_path, monkeypatch)
    r = client.post(
        "/api/code/turn",
        json={"message": "cria um arquivo README com a descrição do projeto", "spoken": True},
    )
    assert r.status_code == 200
    rows = _rows(home)
    assert [row["label"] for row in rows] == ["work"]
    assert rows[0]["message"].startswith("cria um arquivo")


def test_a_typed_request_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, home = _client(tmp_path, monkeypatch)
    r = client.post("/api/code/turn", json={"message": "cria um arquivo README", "spoken": False})
    assert r.status_code == 200
    assert not (home / FILE).exists()
    assert _rows(home) == []


def test_the_file_is_one_json_object_per_line_and_a_bad_line_is_skipped(tmp_path: Path) -> None:
    from chimera.api.spoken_log import record_spoken_request

    home = tmp_path / "home"
    record_spoken_request(home, "oi", "talk", session_id="s1", has_works=False)
    record_spoken_request(home, "conserta o login", "work", session_id="s1", has_works=True)
    path = home / FILE
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and all(json.loads(line) for line in lines)
    path.open("a", encoding="utf-8").write("not json\n")
    rows = read_spoken_requests(home)
    assert [r["label"] for r in rows] == ["talk", "work"]
    assert rows[1]["has_works"] is True and rows[1]["session_id"] == "s1"
