from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from chimera.api.design_api import register_design_api
from chimera.config import Settings
from chimera.design.ops import DesignOperationError, apply_operations
from chimera.design.propose import propose
from chimera.design.store import DesignStore
from chimera.providers.gateway import CompletionResult
from chimera.tools.design import DesignTool
from tests.design_fixtures import sample_doc


def test_agent_apply_refuses_html_ops_until_h2(tmp_path: Path) -> None:
    tool = DesignTool(DesignStore(tmp_path / "designs"), workspace=tmp_path)
    created = json.loads(tool.run(action="create", title="Demo"))

    result = tool.run(
        action="apply", document_id=created["created"], expected_revision=created["revision"],
        operations=[{"type": "artboard.set_html", "pageId": "p1", "artboardId": "a1",
                    "html": "<main>hi</main>"}],
    )

    assert "not available yet" in result


def test_route_ops_refuse_html_from_desktop_or_proposal_until_h2(tmp_path: Path) -> None:
    app = FastAPI()
    settings = Settings(CHIMERA_HOME=str(tmp_path / "home"))  # type: ignore[call-arg]
    register_design_api(app, Depends(lambda: None), settings)
    client = TestClient(app)
    doc = client.post("/api/design", json={"title": "Demo"}).json()
    for origin in ("manual", "proposal"):
        response = client.post(
            f"/api/design/{doc['id']}/ops",
            json={"expected_revision": doc["revision"], "origin": origin,
                  "operations": [{"type": "artboard.set_html", "pageId": "p1",
                                  "artboardId": "a1", "html": "<p>x</p>"}]},
        )
        assert response.status_code == 422
        assert "not available yet" in response.text


class _ReplyBackend:
    """A model that always answers with the same proposal text."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    def complete(self, messages: list[Any], **_kwargs: Any) -> CompletionResult:
        self.calls += 1
        return CompletionResult(content=self.content, model="fake")


def _proposal_text(operations: list[dict[str, Any]]) -> str:
    body = {"summary": "s", "assumptions": [], "operations": operations, "checks": []}
    return "```json\n" + json.dumps(body) + "\n```"


_SET_HTML = {"type": "artboard.set_html", "pageId": "p1", "artboardId": "a1", "html": "<p>x</p>"}


def test_agent_propose_refuses_html_the_model_writes(tmp_path: Path) -> None:
    backend = _ReplyBackend(_proposal_text([_SET_HTML]))
    tool = DesignTool(DesignStore(tmp_path / "designs"), workspace=tmp_path, backend=backend)
    created = json.loads(tool.run(action="create", title="Demo"))

    result = tool.run(action="propose", document_id=created["created"], brief="a page")

    assert "not available yet" in result


def test_a_proposal_carrying_html_on_a_new_artboard_is_refused_in_the_dry_run() -> None:
    reply = _proposal_text([{"type": "artboard.add", "pageId": "p1", "artboard": {
        "id": "ab_new", "name": "New", "width": 390, "height": 844, "background": "#FFFFFF",
        "nodeIds": [], "html": "<p>x</p>"}}])
    outcome = propose(_ReplyBackend(reply), sample_doc(), "brief")
    assert not outcome.ok
    assert outcome.error is not None and "not available yet" in outcome.error


def test_html_nested_in_a_page_add_is_refused_by_the_domain() -> None:
    page = {"id": "p_new", "name": "Page", "artboards": [
        {"id": "ab_new", "name": "Frame", "width": 1440, "height": 900, "background": "#FFFFFF",
         "nodeIds": [], "html": "<main>x</main>"}]}
    with pytest.raises(DesignOperationError, match="not available yet"):
        apply_operations(sample_doc(), [{"type": "page.add", "page": page}])


def test_artboard_add_without_html_and_null_html_still_apply() -> None:
    plain = {"type": "artboard.add", "pageId": "p1", "artboard": {
        "id": "ab_new", "name": "New", "width": 390, "height": 844, "background": "#FFFFFF",
        "nodeIds": [], "html": None}}
    changed, _ = apply_operations(sample_doc(), [plain])
    assert any(board.id == "ab_new" for board in changed.pages[0].artboards)


def test_the_domain_switch_is_the_only_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.design import ops

    monkeypatch.setattr(ops, "HTML_ARTBOARDS_ENABLED", True)
    changed, _ = apply_operations(sample_doc(), [_SET_HTML])
    assert changed.pages[0].artboards[0].html == "<p>x</p>"
