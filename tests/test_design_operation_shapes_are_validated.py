"""The operation shapes a model is shown are the shapes the models accept (`chimera.design.op_shapes`).

The examples travel in the ``design`` tool's description and in the error an ``apply`` returns, so a
wrong example would teach a model a wrong shape with the authority of the tool. These tests keep them
honest: every example must validate, must apply to a real document, and must be the line the
description carries. A refused operation must come back with the shape of that operation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chimera.design.model import DesignDocument, DesignTokens, wire
from chimera.design.op_shapes import (
    OPERATION_SHAPES,
    TOKENS_SHAPE,
    _line,
    invalid_operation_hint,
    shape_block,
    shape_for,
)
from chimera.design.ops import apply_operations, parse_operations
from chimera.design.store import DesignStore
from chimera.tools.design import DesignTool


def test_every_shape_example_validates_against_the_real_models() -> None:
    operations = [example for key, example in OPERATION_SHAPES.items() if key != "tokens.replace"]
    operations.append(OPERATION_SHAPES["tokens.replace"])
    parsed = parse_operations(operations)
    assert len(parsed) == len(OPERATION_SHAPES)


def test_the_tokens_example_is_a_valid_design_system() -> None:
    tokens = DesignTokens.model_validate(TOKENS_SHAPE)
    assert tokens.colors["primary"] == "#C8102E"


def test_the_example_sequence_applies_to_a_real_document() -> None:
    # Order matters: an artboard before its element, a page before its artboard. The examples are
    # written in the order a design is built, so applying them in that order is the contract.
    doc = DesignDocument.model_validate({"id": "fornalha", "title": "Fornalha"})
    sequence = [
        OPERATION_SHAPES["tokens.replace"],
        OPERATION_SHAPES["page.add"],
        OPERATION_SHAPES["artboard.add"],
        OPERATION_SHAPES["node.add.text"],
        OPERATION_SHAPES["node.add.shape"],
        OPERATION_SHAPES["node.update"],
    ]

    after, _inverse = apply_operations(doc, parse_operations(sequence))

    assert [page.id for page in after.pages] == ["home"]
    assert [board.id for board in after.pages[0].artboards] == ["desktop"]
    assert set(after.nodes) == {"title", "cta"}
    assert wire(after.nodes["title"])["text"] == "Pão de fermentação lenta"


def test_the_tool_description_carries_every_shape_as_one_line() -> None:
    description = DesignTool.description
    for key, example in OPERATION_SHAPES.items():
        assert _line(key, example) in description, key
    assert shape_block() in description
    # One line per operation, so the description stays scannable for the model.
    assert description.count("\n") >= len(OPERATION_SHAPES)


def test_a_wrong_page_add_is_refused_with_the_page_add_shape(tmp_path: Path) -> None:
    tool = DesignTool(DesignStore(tmp_path / "designs"), workspace=tmp_path)
    created = json.loads(tool.run(action="create", title="Fornalha"))

    out = tool.run(
        action="apply", document_id=created["created"], expected_revision=created["revision"],
        operations=[{"type": "page.add", "name": "Home"}],
    )

    assert out.startswith("error: the operations are not valid:")
    assert json.dumps(OPERATION_SHAPES["page.add"], ensure_ascii=False, separators=(",", ":")) in out


def test_a_refused_text_element_is_hinted_with_the_text_shape_not_the_rectangle() -> None:
    raw: list[Any] = [{
        "type": "node.add", "pageId": "home", "artboardId": "desktop",
        "node": {"type": "text", "id": "title", "frame": {"x": 0, "y": 0, "width": 10, "height": 10}},
    }]

    hint = invalid_operation_hint(raw)

    assert hint.startswith("operation 0 (node.add) must look like: node.add.text:")
    assert "node.add.shape" not in hint


def test_a_node_add_with_an_unknown_element_type_lists_the_three_element_shapes() -> None:
    raw: list[Any] = [{
        "type": "node.add", "pageId": "home", "artboardId": "desktop",
        "node": {"type": "rect", "id": "box"},
    }]

    hint = invalid_operation_hint(raw)

    for kind in ("node.add.text", "node.add.shape", "node.add.image"):
        assert kind in hint


def test_an_unknown_operation_type_names_the_known_ones() -> None:
    hint = invalid_operation_hint([{"type": "page.create", "name": "Home"}])

    assert "'page.create'" in hint
    assert "page.add" in hint and "tokens.replace" in hint


def test_a_valid_batch_gets_no_hint() -> None:
    assert invalid_operation_hint([OPERATION_SHAPES["page.add"]]) == ""
    assert shape_for(OPERATION_SHAPES["node.update"]) is not None


def test_a_refused_tokens_create_is_given_the_tokens_shape(tmp_path: Path) -> None:
    tool = DesignTool(DesignStore(tmp_path / "designs"), workspace=tmp_path)

    out = tool.run(action="create", title="Fornalha", tokens={"colors": {"primary": "red"}})

    assert out.startswith("error: the tokens are not a valid design system:")
    assert "The tokens look like:" in out
    assert '"primary": "#C8102E"' in out
