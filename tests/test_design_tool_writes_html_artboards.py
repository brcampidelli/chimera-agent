from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chimera.design import ops
from chimera.design.ops import apply_operations
from chimera.design.store import DesignStore
from chimera.governance.ledger_tool import fence_observation
from chimera.governance.sanitize import FENCE_CLOSE, FENCE_OPEN
from chimera.tools.design import DESIGN_FRAME_MAX_BYTES, DesignTool, design_frame


@pytest.fixture
def tool(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DesignTool:
    monkeypatch.setattr(ops, "HTML_ARTBOARDS_ENABLED", True)
    store = DesignStore(tmp_path / "designs")
    tool = DesignTool(store, workspace=tmp_path)
    created = tool.run(action="create", title="HTML test")
    doc_id = json.loads(created)["created"]
    doc = store.get(doc_id)
    page = {"id": "page_main", "name": "Main", "artboards": []}
    doc, _ = apply_operations(doc, [{"type": "page.add", "page": page}])
    store.save(doc, expected_revision=doc.revision)
    tool._test_document_id = doc_id  # type: ignore[attr-defined]
    return tool


def _call(tool: DesignTool, action: str, **kwargs: Any) -> dict[str, Any]:
    doc_id = tool._test_document_id  # type: ignore[attr-defined]
    doc = tool.store.get(doc_id)
    result = tool.run(action=action, document_id=doc_id, expected_revision=doc.revision, **kwargs)
    if action == "read_artboard":
        return {"source": result}
    return json.loads(result)


def test_write_creates_one_artboard(tool: DesignTool) -> None:
    result = _call(tool, "write_artboard", name="Screen", width=390, height=844,
                   html="<main>hello</main>")
    assert result["revision"] == 3
    doc = tool.store.get(tool._test_document_id)  # type: ignore[attr-defined]
    board = next(b for p in doc.pages for b in p.artboards if b.id == result["ids"]["artboard"])
    assert board.html == "<main>hello</main>" and board.mode == "fixed"


def test_write_existing_id_replaces_html(tool: DesignTool) -> None:
    made = _call(tool, "write_artboard", name="Screen", width=390, height=844,
                 html="<main>old</main>")
    changed = _call(tool, "write_artboard", name="Screen", width=390, height=844,
                    artboard_id=made["ids"]["artboard"], html="<main>new</main>")
    assert changed["ids"]["artboard"] == made["ids"]["artboard"]
    assert "<main>new</main>" in _call(tool, "read_artboard", artboard_id=made["ids"]["artboard"])["source"]


def test_edit_replaces_unique_segment(tool: DesignTool) -> None:
    made = _call(tool, "write_artboard", name="Screen", width=390, height=844,
                 html="<main>old</main>")
    _call(tool, "edit_artboard", artboard_id=made["ids"]["artboard"], old="old", new="new")
    assert "<main>new</main>" in _call(tool, "read_artboard", artboard_id=made["ids"]["artboard"])["source"]


def test_validator_error_includes_line(tool: DesignTool) -> None:
    result = tool.run(action="write_artboard", document_id=tool._test_document_id, expected_revision=2,
                      name="Broken", width=390, height=844, html="<script>bad</script>")
    assert result.startswith("error: line 1:")


def test_flag_off_refuses_new_actions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ops, "HTML_ARTBOARDS_ENABLED", False)
    tool = DesignTool(DesignStore(tmp_path / "designs"), workspace=tmp_path)
    doc = json.loads(tool.run(action="create", title="x"))
    result = tool.run(action="write_artboard", document_id=doc["created"], expected_revision=1,
                      name="x", width=100, height=100, html="<p>x</p>")
    assert result == "error: HTML artboards are not available yet"


def test_summary_has_no_html_source(tool: DesignTool) -> None:
    source = "<main>secret source</main>"
    made = _call(tool, "write_artboard", name="Screen", width=390, height=844, html=source)
    summary = tool.run(action="summary", document_id=tool._test_document_id)
    assert source not in summary and "source_bytes" in summary and "css_variables" in summary
    assert made["ids"]["artboard"] in summary


def test_read_artboard_is_fenced(tool: DesignTool) -> None:
    made = _call(tool, "write_artboard", name="Screen", width=390, height=844,
                 html="<main>hello</main>")
    raw = _call(tool, "read_artboard", artboard_id=made["ids"]["artboard"])["source"]
    # The tool returns the source as it is; the ledger fences every untrusted tool result once.
    assert raw == "<main>hello</main>"
    fenced = fence_observation(raw)
    assert fenced.startswith(FENCE_OPEN) and fenced.endswith(FENCE_CLOSE)
    assert fenced.count(FENCE_OPEN) == 1


def test_oversized_operation_frame_omits_operations() -> None:
    operation = {"type": "artboard.set_html", "pageId": "p1", "artboardId": "a1",
                 "html": "x" * (DESIGN_FRAME_MAX_BYTES + 1)}
    frame = design_frame("doc", "title", 2, 1, "write_artboard", base_revision=1,
                         operations=[operation])
    assert "operations" not in frame and "base_revision" not in frame
