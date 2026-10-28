from __future__ import annotations

import json
from typing import Any

import pytest

from chimera.design import ops as design_ops
from chimera.design.migrate import load_document
from chimera.design.model import wire
from chimera.design.ops import DesignOperationError, apply_operations
from tests.design_fixtures import sample_doc

PAGE = "p1"
BOARD = "a1"
SOURCE = "<main><p>Launch</p></main>"


@pytest.fixture(autouse=True)
def _released(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(design_ops, "HTML_ARTBOARDS_ENABLED", True)  # released in H2


def _op(kind: str, **fields: Any) -> dict[str, Any]:
    return {"type": kind, "pageId": PAGE, "artboardId": BOARD, **fields}


def test_set_and_null_inverse_restore_html_byte_for_byte() -> None:
    original = sample_doc()
    changed, inverse = apply_operations(original, [_op("artboard.set_html", html=SOURCE)])
    assert changed.pages[0].artboards[0].html == SOURCE
    restored, _ = apply_operations(changed, inverse, max_ops=None)
    assert wire(restored) == wire(original)
    cleared, inverse = apply_operations(changed, [_op("artboard.set_html", html=None)])
    restored, _ = apply_operations(cleared, inverse, max_ops=None)
    assert wire(restored) == wire(changed)


def test_edit_and_its_inverse_restore_bytes_exactly() -> None:
    loaded = apply_operations(sample_doc(), [_op("artboard.set_html", html=SOURCE)])[0]
    edited, inverse = apply_operations(loaded, [_op("artboard.edit_html", old="Launch", new="Launch  ")])
    assert edited.pages[0].artboards[0].html == "<main><p>Launch  </p></main>"
    restored, _ = apply_operations(edited, inverse, max_ops=None)
    assert wire(restored) == wire(loaded)


@pytest.mark.parametrize(("old", "count"), [("missing", 0), ("Launch", 2)])
def test_edit_refuses_nonunique_old_text_without_changes(old: str, count: int) -> None:
    source = SOURCE if count == 0 else "<main>Launch <p>Launch</p></main>"
    loaded = apply_operations(sample_doc(), [_op("artboard.set_html", html=source)])[0]
    before = wire(loaded)
    with pytest.raises(DesignOperationError, match=f"occurs {count} times"):
        apply_operations(loaded, [_op("artboard.edit_html", old=old, new="Changed")])
    assert wire(loaded) == before


def test_edit_introducing_script_is_atomic() -> None:
    loaded = apply_operations(sample_doc(), [_op("artboard.set_html", html=SOURCE)])[0]
    before = wire(loaded)
    with pytest.raises(DesignOperationError, match="<script>"):
        apply_operations(loaded, [_op("artboard.edit_html", old="Launch", new="<script>x</script>")])
    assert wire(loaded) == before


def test_v1_saved_document_migrates_to_v2_with_defaults() -> None:
    v1 = wire(sample_doc())
    v1["schemaVersion"] = 1
    v1["pages"][0]["artboards"][0].pop("html", None)
    v1["pages"][0]["artboards"][0].pop("mode", None)
    doc = load_document(json.loads(json.dumps(v1)))
    assert doc.schema_version == 2
    assert doc.pages[0].artboards[0].html is None
    assert doc.pages[0].artboards[0].mode == "fixed"
