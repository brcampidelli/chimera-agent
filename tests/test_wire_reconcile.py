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


# ---------------------------------------------------------------- review fixes (streaming, OFF path)

_FAKE_KEY = "sk-or-v1-FAKEKEYfakekeyFAKEKEY0123456789"


def _stream_chunks(**_: object) -> object:
    from types import SimpleNamespace

    delta = SimpleNamespace(content="streamed reply", tool_calls=None)
    yield SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=None)], usage=None)
    end = SimpleNamespace(content=None, tool_calls=None)
    yield SimpleNamespace(choices=[SimpleNamespace(delta=end, finish_reason="stop")], usage=None)


def test_the_streaming_path_is_tapped_and_never_writes_the_key(monkeypatch, tmp_path: Path) -> None:
    """The coding turn streams by default. Untapped, every streamed step reached the steplog with no
    wire_id and reconciliation called a real exchange fabricated."""
    monkeypatch.setenv("OPENROUTER_API_KEY", _FAKE_KEY)  # owned: the gateway exports it
    from chimera.config import Settings
    from chimera.providers.gateway import LLMGateway

    settings = Settings(
        _env_file=None, CHIMERA_HOME=str(tmp_path), CHIMERA_WIRE_LOG=True, OPENROUTER_API_KEY=_FAKE_KEY,
        CHIMERA_OPENROUTER_KEYS=_FAKE_KEY
    )
    gw = LLMGateway(settings=settings)
    seen: dict[str, object] = {}

    def _stream(**kwargs: object) -> object:
        seen.update(kwargs)
        return _stream_chunks()

    monkeypatch.setattr(gw, "_stream_once", _stream, raising=False)
    result = gw.stream_complete([{"role": "user", "content": "oi"}], model="openrouter/x/y")

    raw = (tmp_path / "wire.jsonl").read_text(encoding="utf-8")
    rows = [json.loads(line) for line in raw.splitlines()]
    assert seen.get("api_key") == _FAKE_KEY  # the key really was in the call the tap watched
    assert len(rows) == 1 and result.wire_id == rows[0]["wire_id"]
    assert result.response_digest == rows[0]["response_digest"]
    assert _FAKE_KEY not in raw and "FAKEKEY" not in raw
    assert "streamed reply" not in raw


def test_the_batch_path_never_writes_the_key(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", _FAKE_KEY)  # owned: the gateway exports it
    from types import SimpleNamespace

    import litellm

    from chimera.config import Settings
    from chimera.providers.gateway import LLMGateway

    seen: dict[str, object] = {}

    def fake(**kwargs: object) -> SimpleNamespace:
        seen.update(kwargs)
        message = SimpleNamespace(content="ok", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None)

    monkeypatch.setattr(litellm, "completion", fake)
    settings = Settings(
        _env_file=None, CHIMERA_HOME=str(tmp_path), CHIMERA_WIRE_LOG=True, OPENROUTER_API_KEY=_FAKE_KEY,
        CHIMERA_OPENROUTER_KEYS=_FAKE_KEY
    )
    LLMGateway(settings=settings).complete([{"role": "user", "content": "oi"}], model="openrouter/x/y")
    raw = (tmp_path / "wire.jsonl").read_text(encoding="utf-8")
    assert seen.get("api_key") == _FAKE_KEY
    assert "FAKEKEY" not in raw


def test_off_the_gateway_touches_no_file(monkeypatch, tmp_path: Path) -> None:
    from types import SimpleNamespace

    import litellm

    from chimera.config import Settings
    from chimera.providers.gateway import LLMGateway

    def fake(**_: object) -> SimpleNamespace:
        message = SimpleNamespace(content="ok", tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None)

    monkeypatch.setattr(litellm, "completion", fake)
    gw = LLMGateway(settings=Settings(_env_file=None, CHIMERA_HOME=str(tmp_path)))
    monkeypatch.setattr(gw, "_stream_once", lambda **_: _stream_chunks(), raising=False)
    a = gw.complete([{"role": "user", "content": "oi"}], model="ollama_chat/x")
    b = gw.stream_complete([{"role": "user", "content": "oi"}], model="ollama_chat/x")
    assert not (tmp_path / "wire.jsonl").exists()
    assert (a.wire_id, b.wire_id) == ("", "")


def test_off_a_trace_step_is_byte_identical_to_before_the_option() -> None:
    """The three wire keys were written into every trace line even with the option off."""
    from chimera.core.steplog import StepRecord

    row = StepRecord(index=1, prompt_tokens=3, completion_tokens=4, model="m").as_dict()
    assert not {"wire_id", "request_digest", "response_digest"} & set(row)
    tapped = StepRecord(index=1, prompt_tokens=3, completion_tokens=4, model="m", wire_id="w",
                        request_digest="q", response_digest="r").as_dict()
    assert tapped["wire_id"] == "w" and tapped["response_digest"] == "r"


def test_an_edit_to_retained_content_is_not_detected_documented_limit(tmp_path: Path) -> None:
    """Reconciliation compares the digests the step COPIED from the gateway; it does not recompute
    them from the step's clipped content. Pinned so RESULTS cannot overstate "altered copy"."""
    wire = tmp_path / "wire.jsonl"
    q, r = digest({"q": 1}), digest({"r": 1})
    wid = append_wire_record(wire, model="m", request_digest=q, response_digest=r)
    step = {"index": 1, "wire_id": wid, "request_digest": q, "response_digest": r, "content": "edited"}
    steplog = tmp_path / "traces.jsonl"
    steplog.write_text(json.dumps({"steps": [step]}) + "\n", encoding="utf-8")
    assert reconcile(wire, steplog)["clean"]
