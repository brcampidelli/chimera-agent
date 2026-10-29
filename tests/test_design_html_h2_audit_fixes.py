"""H2 audit fixes: the HTML prompts, the refinement's exact sources, the write that moves a frame, and
the schema the model is shown while HTML artboards are off.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from chimera.design import ops, prompts
from chimera.design.html_source import validate_artboard_html
from chimera.design.model import DesignDocument
from chimera.design.ops import apply_operations, parse_operations
from chimera.design.propose import HTML_SOURCE_CAP, html_sources, refine, summarize
from chimera.design.store import DesignStore
from chimera.governance.ledger_tool import fence_observation
from chimera.governance.sanitize import FENCE_CLOSE, FENCE_OPEN
from chimera.providers.gateway import CompletionResult
from chimera.tools.design import DesignTool

HTML_BOARD = "<main>hi</main>"


class FakeBackend:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[list[dict[str, Any]]] = []

    def complete(self, messages: list[Any], **_kwargs: Any) -> CompletionResult:
        self.calls.append([dict(m) for m in messages])
        return CompletionResult(content=self.reply, model="fake")


EMPTY_REPLY = json.dumps({"summary": "Quieter.", "assumptions": [], "operations": [],
                          "checks": [], "questions": []})


@pytest.fixture
def flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ops, "HTML_ARTBOARDS_ENABLED", True)


@pytest.fixture
def tool(tmp_path: Path, flag_on: None) -> DesignTool:
    store = DesignStore(tmp_path / "designs")
    tool = DesignTool(store, workspace=tmp_path)
    doc_id = json.loads(tool.run(action="create", title="H2"))["created"]
    doc = store.get(doc_id)
    doc, _ = apply_operations(doc, [{"type": "page.add", "page": {"id": "p1", "name": "Main", "artboards": []}}])
    store.save(doc, expected_revision=doc.revision)
    tool._test_document_id = doc_id  # type: ignore[attr-defined]
    return tool


def _doc(tool: DesignTool) -> DesignDocument:
    return tool.store.get(tool._test_document_id)  # type: ignore[attr-defined]


def _write(tool: DesignTool, **kwargs: Any) -> dict[str, Any]:
    doc = _doc(tool)
    result = tool.run(action="write_artboard", document_id=doc.id, expected_revision=doc.revision, **kwargs)
    return json.loads(result) if not result.startswith("error") else {"error": result}


# ---- 2 and 3: the HTML prompts ----------------------------------------------------------------

def test_the_html_operations_guide_has_real_line_breaks_and_one_artboard_add() -> None:
    guide = prompts.HTML_OPERATIONS_GUIDE
    assert "\\n" not in guide
    assert guide.count('"type": "artboard.add"') == 1
    assert '"html": "complete HTML document"' in guide
    assert '- {"type": "artboard.set_html"' in guide and '- {"type": "artboard.edit_html"' in guide


def test_the_html_generate_prompt_is_its_own_text_not_a_patched_copy() -> None:
    text = prompts.HTML_GENERATE
    assert "Do not include markdown, HTML" not in text
    assert "incremental operations" not in text
    assert "\\n" not in text
    assert "`artboard.add` with `html`" in text
    assert "var(--ds-color-" in text and "asset:<id>" in text


def test_the_html_refine_prompt_quotes_the_sources_and_names_edit_html() -> None:
    text = prompts.HTML_REFINE
    assert "{html_sources}" in text and "artboard.edit_html" in text
    assert "Do not replace the full document" not in text


def test_the_html_generate_example_is_one_valid_artboard_add() -> None:
    text = prompts.HTML_GENERATE.format(brief="b", constraints="{}", tokens="{}", document="{}",
                                        operations=prompts.HTML_OPERATIONS_GUIDE)
    examples = [line for line in text.splitlines() if line.startswith('{"type": "artboard.add"')]
    assert len(examples) == 1
    parsed = parse_operations([json.loads(examples[0])])
    assert parsed[0].type == "artboard.add"
    validate_artboard_html(json.loads(examples[0])["artboard"]["html"])


# ---- 4: the refinement sees each source exactly, fenced, capped ------------------------------

def test_the_refinement_gets_each_html_source_inside_the_fence(tool: DesignTool) -> None:
    _write(tool, name="Screen", width=390, height=844, artboard_id=None, html=HTML_BOARD, page_id="p1")
    doc = _doc(tool)
    board_id = doc.pages[0].artboards[0].id
    block = html_sources(doc)
    assert f"artboard {board_id} on page p1 ({len(HTML_BOARD)} bytes):\n{HTML_BOARD}" in block

    backend = FakeBackend(EMPTY_REPLY)
    refine(backend, doc, "make it quieter", model=None)
    content = backend.calls[0][-1]["content"]
    at = content.index(HTML_BOARD)
    assert content.rfind(FENCE_OPEN, 0, at) != -1
    assert content.find(FENCE_CLOSE, at) != -1


def test_an_oversized_source_is_not_quoted_and_the_model_is_sent_to_set_html(tool: DesignTool) -> None:
    big = "<main>" + "x" * HTML_SOURCE_CAP + "</main>"
    _write(tool, name="Big", width=390, height=844, html=big, page_id="p1")
    block = html_sources(_doc(tool))
    assert "not shown" in block and "artboard.set_html" in block
    assert "xxxx" not in block


def test_an_index_page_is_left_out_of_the_sources(tool: DesignTool) -> None:
    _write(tool, name="A", width=390, height=844, html=HTML_BOARD, page_id="p1")
    _write(tool, name="B", width=390, height=844, html=HTML_BOARD, page_id="p1")
    doc = _doc(tool)
    indexed = json.loads(tool.run(action="index", document_id=doc.id, expected_revision=doc.revision))
    assert indexed["indexed"] == 2
    block = html_sources(_doc(tool))
    assert block.count("on page p1") == 2  # the two screens, never the index's thumbnails


def test_summary_marks_html_boards_and_keeps_their_source_out_of_the_tool_output(tool: DesignTool) -> None:
    _write(tool, name="A", width=390, height=844, html=HTML_BOARD, page_id="p1")
    board = _doc(tool).pages[0].artboards[0]
    body = summarize(_doc(tool))
    entry = body["pages"][0]["artboards"][0]
    assert entry["html"] is True and entry["source_bytes"] == len(HTML_BOARD)
    assert HTML_BOARD not in json.dumps(body)
    assert board.id == entry["id"]


# ---- 5: write_artboard with an id moves the frame in the same atomic call -------------------

def test_write_with_an_id_updates_the_frame_and_the_source_in_one_revision(tool: DesignTool) -> None:
    made = _write(tool, name="Screen", width=390, height=844, html=HTML_BOARD, page_id="p1")
    before = _doc(tool).revision
    changed = _write(tool, name="Renamed", width=768, height=1024, mode="fill",
                     artboard_id=made["ids"]["artboard"], html="<main>new</main>")
    assert "error" not in changed
    doc = _doc(tool)
    board = doc.pages[0].artboards[0]
    assert (board.name, board.width, board.height, board.mode) == ("Renamed", 768, 1024, "fill")
    assert board.html == "<main>new</main>"
    assert doc.revision == before + 1


def test_write_with_an_id_and_the_same_frame_sends_only_the_source(tool: DesignTool) -> None:
    made = _write(tool, name="Screen", width=390, height=844, html=HTML_BOARD, page_id="p1")
    changed = _write(tool, name="Screen", width=390, height=844, html="<main>x</main>",
                     artboard_id=made["ids"]["artboard"])
    assert "error" not in changed
    assert _doc(tool).pages[0].artboards[0].html == "<main>x</main>"


# ---- 7: an index page holds no HTML artboard, and the read is fenced once --------------------

def test_write_with_an_index_thumbnail_id_is_refused_as_the_creation_refuses_it(tool: DesignTool) -> None:
    _write(tool, name="A", width=390, height=844, html=HTML_BOARD, page_id="p1")
    _write(tool, name="B", width=390, height=844, html=HTML_BOARD, page_id="p1")
    doc = _doc(tool)
    tool.run(action="index", document_id=doc.id, expected_revision=doc.revision)
    index_page = next(p for p in _doc(tool).pages if p.is_index)
    thumb = index_page.artboards[0].id
    refused = _write(tool, name="X", width=390, height=844, html="<main>y</main>", artboard_id=thumb)
    assert refused == {"error": "error: artboard_id was not found"}


def test_read_artboard_is_fenced_once_by_the_framework_and_never_by_the_tool(tool: DesignTool) -> None:
    made = _write(tool, name="Screen", width=390, height=844, html=HTML_BOARD, page_id="p1")
    raw = tool.run(action="read_artboard", document_id=tool._test_document_id,  # type: ignore[attr-defined]
                   artboard_id=made["ids"]["artboard"])
    assert raw == HTML_BOARD
    assert DesignTool.untrusted_output is True
    fenced = fence_observation(raw)
    assert fenced.startswith(FENCE_OPEN) and fenced.count(FENCE_OPEN) == 1


# ---- 6: the schema the model sees follows the flag -------------------------------------------

def test_with_html_artboards_off_the_schema_offers_no_html_action_or_property(tmp_path: Path,
                                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ops, "HTML_ARTBOARDS_ENABLED", False)
    offered = DesignTool(DesignStore(tmp_path / "designs"), workspace=tmp_path)
    actions = offered.parameters["properties"]["action"]["enum"]
    assert not {"write_artboard", "edit_artboard", "read_artboard"} & set(actions)
    for name in ("html", "old", "new", "name", "width", "height", "mode", "page_id", "artboard_id"):
        assert name not in offered.parameters["properties"], name
    assert "operations" in offered.parameters["properties"]


def test_with_html_artboards_on_the_schema_carries_the_html_actions(tmp_path: Path,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ops, "HTML_ARTBOARDS_ENABLED", True)
    on = DesignTool(DesignStore(tmp_path / "designs"), workspace=tmp_path)
    actions = on.parameters["properties"]["action"]["enum"]
    assert {"write_artboard", "edit_artboard", "read_artboard"} <= set(actions)
    assert {"html", "old", "new"} <= set(on.parameters["properties"])


def test_the_class_schema_matches_the_flag_off_schema() -> None:
    assert "html" not in DesignTool.parameters["properties"]
    assert "write_artboard" not in DesignTool.parameters["properties"]["action"]["enum"]
