"""The upload endpoints, which had no test at all on either side of the wire.

That absence is why a shipped bug survived: the client sent `Content-Type: application/json` beside
a FormData body, so the multipart bytes arrived labelled as JSON with no boundary, and both
endpoints answered `422 field required` about a file that was in the request. The client half is
covered in `apps/desktop/src/lib/api.upload.test.ts`; this is the server half — proof that a
correctly-formed upload works, and that the malformed one fails the exact way it did in the browser.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sse_starlette")

from fastapi.testclient import TestClient  # noqa: E402

from chimera.config import Settings  # noqa: E402
from chimera.core.agent import AgentResult  # noqa: E402
from chimera.interface import ChatSession  # noqa: E402


class _FakeAgent:
    def run(self, task: str, **_: Any) -> AgentResult:  # pragma: no cover - never called here
        return AgentResult(answer="", steps=0, stopped_reason="final")


def _client(tmp_path: Any) -> TestClient:
    from chimera.api import build_api_app

    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))
    return TestClient(build_api_app(lambda: ChatSession(_FakeAgent()), settings=settings))


def test_a_properly_formed_upload_is_accepted(tmp_path: Any) -> None:
    client = _client(tmp_path)
    res = client.post("/api/attachments", files={"file": ("notes.txt", b"hello", "text/plain")})

    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "notes.txt"
    # The response carries an id, never the content.
    assert "id" in body and "hello" not in res.text


def test_multipart_bytes_labelled_as_json_are_rejected(tmp_path: Any) -> None:
    """The shipped failure, reproduced: the file is there, and the server cannot see it.

    Worth pinning rather than assuming, because the symptom is so misleading — the error names the
    field as missing, which sends you looking at the form and not at the header.
    """
    client = _client(tmp_path)
    res = client.post(
        "/api/attachments",
        content=b'--x\r\nContent-Disposition: form-data; name="file"; filename="a.txt"\r\n\r\nhi\r\n--x--\r\n',
        headers={"Content-Type": "application/json"},
    )

    assert res.status_code == 422
    assert res.json()["detail"][0]["loc"] == ["body", "file"]


def test_an_upload_without_a_file_is_a_422_not_a_crash(tmp_path: Any) -> None:
    client = _client(tmp_path)
    assert _client(tmp_path).post("/api/attachments").status_code == 422
    assert client.post("/api/transcribe").status_code == 422
