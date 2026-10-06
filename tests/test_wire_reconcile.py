from __future__ import annotations

import json
import os
from pathlib import Path

from chimera.governance.reconcile import append_wire_record, digest, reconcile


def test_wire_log_contains_digests_only_and_is_append_only(tmp_path: Path) -> None:
    path = tmp_path / "wire.jsonl"
    secret = "Bearer never-write-this-password"
    request_digest = digest({"messages": [{"content": secret}]})
    response_digest = digest({"content": "synthetic response"})
    first = append_wire_record(
        path, model="fake/model", request_digest=request_digest, response_digest=response_digest
    )
    second = append_wire_record(
        path, model="fake/model", request_digest=request_digest, response_digest=response_digest
    )

    contents = path.read_text(encoding="utf-8")
    rows = [json.loads(line) for line in contents.splitlines()]
    assert first != second
    assert [row["wire_id"] for row in rows] == [first, second]
    assert secret not in contents
    assert "Authorization" not in contents
    assert all(set(row) == {"wire_id", "model", "request_digest", "response_digest", "ts"} for row in rows)
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_reconcile_detects_omission_fabrication_and_altered_copy(tmp_path: Path) -> None:
    wire = tmp_path / "wire.jsonl"
    steplog = tmp_path / "traces.jsonl"
    records = []
    steps = []
    for index in range(3):
        request_digest = digest({"request": index})
        response_digest = digest({"response": index})
        wire_id = append_wire_record(
            wire,
            model="fake/model",
            request_digest=request_digest,
            response_digest=response_digest,
        )
        records.append(wire_id)
        steps.append(
            {
                "index": index,
                "wire_id": wire_id,
                "request_digest": request_digest,
                "response_digest": response_digest,
            }
        )
    steplog.write_text(json.dumps({"steps": steps}) + "\n", encoding="utf-8")
    assert reconcile(wire, steplog)["clean"]

    steplog.write_text(json.dumps({"steps": steps[1:]}) + "\n", encoding="utf-8")
    assert reconcile(wire, steplog)["missing_steplog"] == [records[0]]

    fabricated = [*steps, {"index": 9, "wire_id": "made-up", "request_digest": "x", "response_digest": "y"}]
    steplog.write_text(json.dumps({"steps": fabricated}) + "\n", encoding="utf-8")
    assert reconcile(wire, steplog)["missing_wire"] == ["made-up"]

    altered = [dict(step) for step in steps]
    altered[1]["response_digest"] = "altered"
    steplog.write_text(json.dumps({"steps": altered}) + "\n", encoding="utf-8")
    assert reconcile(wire, steplog)["altered"] == [records[1]]

def test_gateway_tap_writes_only_digests(monkeypatch, tmp_path: Path) -> None:
    from types import SimpleNamespace

    import litellm

    from chimera.config import Settings
    from chimera.providers.gateway import LLMGateway, Message

    def fake(**_: object) -> SimpleNamespace:
        message = SimpleNamespace(content="reply with no credential", tool_calls=None)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")],
            usage=None,
            id="synthetic-id",
        )

    monkeypatch.setattr(litellm, "completion", fake)
    secret = "Bearer synthetic-secret-do-not-log"
    settings = Settings(
        _env_file=None,
        CHIMERA_HOME=str(tmp_path),
        CHIMERA_WIRE_LOG=True,
        wire_log=True,
    )
    result = LLMGateway(settings=settings).complete(
        [Message(role="user", content=secret)], model="ollama_chat/synthetic"
    )
    rows = [json.loads(line) for line in (tmp_path / "wire.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert result.wire_id == rows[0]["wire_id"]
    assert result.request_digest == rows[0]["request_digest"]
    assert result.response_digest == rows[0]["response_digest"]
    assert secret not in json.dumps(rows)
    assert "reply with no credential" not in json.dumps(rows)
    assert "api_key" not in json.dumps(rows).lower()
    assert "authorization" not in json.dumps(rows).lower()


def test_wire_log_disabled_by_default() -> None:
    from chimera.config import Settings

    assert Settings(_env_file=None).wire_log is False
