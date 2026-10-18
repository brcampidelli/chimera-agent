"""Chimera Design operations: every type applies and inverts exactly, and every refusal leaves no trace.

The round trip ``apply(apply(doc, ops).doc, inverse) == doc`` is what lets the app offer undo for the
model's edits as well as the person's; it is checked here for every operation type, including the
cascades (deleting a group, a page, an artboard) the handoff's reference got wrong. Payloads are in
the wire format the desktop's TypeScript model uses (camelCase).
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from chimera.design.ids import remap_new_ids
from chimera.design.model import DesignDocument, ShapeNode, TextNode, wire
from chimera.design.ops import (
    _APPLY,
    DESKTOP_OPERATION_TYPES,
    OPERATION_TYPES,
    DesignOperationError,
    apply_operations,
    parse_operations,
)
from chimera.design.tokens import default_tokens
from tests.design_fixtures import comment, frame, sample_dict, sample_doc, shape, text

_TOKENS = wire(default_tokens())
_TOKENS_SMALL = {"colors": {"ink": "#000000", "paper": "#FFFFFFFF"}}

ROUND_TRIPS: dict[str, list[dict[str, Any]]] = {
    "document.rename": [{"type": "document.rename", "title": "  Launch page  "}],
    "page.add empty": [{"type": "page.add", "page": {"id": "p9", "name": "New", "artboards": []}}],
    "page.add with content at index 0": [{
        "type": "page.add", "index": 0,
        "page": {"id": "p9", "artboards": [{"id": "a9", "width": 100, "height": 100,
                                            "nodeIds": ["x1"]}]},
        "nodes": [text("x1")],
    }],
    "page.rename": [{"type": "page.rename", "pageId": "p2", "name": "Mobile page"}],
    "page.delete keeps its position": [{"type": "page.delete", "pageId": "p2"}],
    "artboard.add": [{"type": "artboard.add", "pageId": "p1", "index": 0,
                      "artboard": {"id": "a9", "name": "Post", "width": 1080, "height": 1080,
                                   "background": "#FFFFFF", "nodeIds": []}}],
    "artboard.update": [{"type": "artboard.update", "pageId": "p1", "artboardId": "a1",
                         "patch": {"background": "#F3F5F7", "width": 1024}}],
    "artboard.delete": [{"type": "artboard.delete", "pageId": "p2", "artboardId": "a2"}],
    "node.add text": [{"type": "node.add", "pageId": "p1", "artboardId": "a1",
                       "node": text("n9")}],
    "node.add at a stacking index": [{"type": "node.add", "pageId": "p1", "artboardId": "a1",
                                      "node": text("n9"), "index": 1}],
    "node.add group with children": [{
        "type": "node.add", "pageId": "p1", "artboardId": "a1", "index": 1,
        "node": {"id": "n9", "type": "group", "name": "", "frame": frame(), "visible": True,
                 "childIds": ["n10", "n11"]},
        "children": [shape("n10"), text("n11", parentId="n9")],
    }],
    "node.add into a group": [{"type": "node.add", "pageId": "p1", "artboardId": "a1",
                               "parentId": "g1", "index": 0, "node": text("n9")}],
    "node.update text": [{"type": "node.update", "nodeId": "t1",
                          "patch": {"text": "New headline", "align": "center"}}],
    "node.update removes an optional field with null": [
        {"type": "node.update", "nodeId": "t1", "patch": {"fontToken": None, "colorToken": None}}
    ],
    "node.update adds then the inverse removes": [
        {"type": "node.update", "nodeId": "t2", "patch": {"parentId": "g1", "align": "right"}}
    ],
    "node.update path points": [{"type": "node.update", "nodeId": "pa",
                                 "patch": {"points": [0, 1, 1, 0]}}],
    "node.update reorders a group's children": [
        {"type": "node.update", "nodeId": "g1", "patch": {"childIds": ["t2", "s2"]}}
    ],
    "node.update lock": [{"type": "node.update", "nodeId": "t1", "patch": {"locked": True}}],
    "node.update unlock": [{"type": "node.update", "nodeId": "lk", "patch": {"locked": False}}],
    "node.move": [{"type": "node.move", "nodeId": "t1", "frame": frame(33.3, 41.7, 90, 20)}],
    "node.move a group with its children": [
        {"type": "node.move", "nodeId": "g1", "frame": frame(300.1, 290.7, 200, 200),
         "withChildren": True}
    ],
    "node.delete keeps the stacking slot": [{"type": "node.delete", "nodeId": "s1"}],
    "node.delete a group deletes its contents": [{"type": "node.delete", "nodeId": "g1"}],
    "node.delete inside a group": [{"type": "node.delete", "nodeId": "t2"}],
    "node.duplicate": [{"type": "node.duplicate", "nodeId": "g1",
                        "newIds": {"g1": "g1c", "s2": "s2c", "t2": "t2c"}}],
    "tokens.replace": [{"type": "tokens.replace", "tokens": _TOKENS_SMALL}],
    "comment.add": [{"type": "comment.add", "comment": comment("c9", nodeId="s1")}],
    "comment.add without an element": [{"type": "comment.add", "comment": comment("c9")}],
    "comment.resolve": [{"type": "comment.resolve", "commentId": "c1", "resolved": True}],
    "comment.delete": [{"type": "comment.delete", "commentId": "c1"}],
    "a mixed batch": [
        {"type": "document.rename", "title": "Batch"},
        {"type": "node.add", "pageId": "p1", "artboardId": "a1", "node": text("n9")},
        {"type": "node.update", "nodeId": "n9", "patch": {"text": "edited"}},
        {"type": "node.move", "nodeId": "g1", "frame": frame(0, 0, 200, 200)},
        {"type": "node.delete", "nodeId": "t1"},
        {"type": "artboard.delete", "pageId": "p2", "artboardId": "a2"},
        {"type": "tokens.replace", "tokens": _TOKENS},
    ],
}


def test_every_operation_type_has_a_round_trip_case() -> None:
    covered = {op["type"] for ops in ROUND_TRIPS.values() for op in ops}
    assert covered == set(OPERATION_TYPES) == set(_APPLY)
    assert set(DESKTOP_OPERATION_TYPES) <= set(OPERATION_TYPES)


@pytest.mark.parametrize("name", sorted(ROUND_TRIPS))
def test_applying_then_the_inverse_returns_the_original(name: str) -> None:
    doc = sample_doc()
    before = wire(doc)
    changed, inverse = apply_operations(doc, ROUND_TRIPS[name])
    assert wire(changed) != before
    assert wire(doc) == before  # never mutated
    restored, again = apply_operations(changed, inverse, max_ops=None)
    assert wire(restored) == before
    # And the inverse of the inverse redoes the change.
    redone, _ = apply_operations(restored, again, max_ops=None)
    assert wire(redone) == wire(changed)


def test_the_wire_format_is_camel_case_like_the_desktop() -> None:
    doc = wire(sample_doc())
    assert {"schemaVersion", "updatedAt", "nodes", "comments"} <= set(doc)
    assert "nodeIds" in doc["pages"][0]["artboards"][0]
    assert doc["nodes"]["g1"]["childIds"] == ["s2", "t2"]
    assert doc["nodes"]["t1"]["fontToken"] == "body"
    assert doc["nodes"]["im"]["assetId"] == "asset_1"
    assert set(doc["comments"][0]) == {"id", "artboardId", "nodeId", "x", "y", "text", "author",
                                       "resolved", "createdAt"}
    assert "rotation" not in doc["nodes"]["t1"]["frame"]  # absent optionals stay absent
    assert "_" not in "".join(doc["tokens"]["typography"]["body"])


def test_operations_exactly_as_the_desktop_writes_them_are_accepted() -> None:
    """One of each desktop operation, keys as `model.ts` declares them."""
    ops = [
        {"type": "document.rename", "title": "From the canvas"},
        {"type": "page.add", "page": {"id": "p_abc", "name": "Page", "artboards": [
            {"id": "ab_abc", "name": "Frame", "width": 1440, "height": 900,
             "background": "#FFFFFF", "nodeIds": []}]}},
        {"type": "artboard.add", "pageId": "p_abc", "artboard": {
            "id": "ab_def", "name": "Second", "width": 390, "height": 844,
            "background": "#F3F5F7", "nodeIds": []}},
        {"type": "artboard.update", "pageId": "p_abc", "artboardId": "ab_def",
         "patch": {"name": "Phone"}},
        {"type": "node.add", "pageId": "p_abc", "artboardId": "ab_abc", "index": 0,
         "node": {"id": "n_line", "type": "shape", "name": "Line", "visible": True,
                  "frame": {"x": 0, "y": 0, "width": 100, "height": 1, "rotation": 0},
                  "shape": "line", "points": [1, 0, 0, 1], "style": {"stroke": "#111827",
                                                                     "strokeWidth": 2}}},
        {"type": "node.update", "nodeId": "n_line", "patch": {"name": "Divider"}},
        {"type": "node.move", "nodeId": "n_line", "frame": {"x": 5, "y": 5, "width": 100,
                                                           "height": 1}},
        {"type": "comment.add", "comment": {"id": "c_abc", "artboardId": "ab_abc",
                                            "nodeId": "n_line", "x": 3, "y": 4, "text": "ok?",
                                            "author": "Bruno", "resolved": False,
                                            "createdAt": "2026-10-17T12:00:00.000Z"}},
        {"type": "comment.resolve", "commentId": "c_abc", "resolved": True},
        {"type": "comment.delete", "commentId": "c_abc"},
        {"type": "node.delete", "nodeId": "n_line"},
        {"type": "artboard.delete", "pageId": "p_abc", "artboardId": "ab_def"},
        {"type": "tokens.replace", "tokens": {"colors": {"ink": "#000000"}, "typography": {
            "body": {"family": "Inter", "size": 16, "weight": 400, "lineHeight": 1.5}},
            "spacing": {}, "radii": {}}},
    ]
    assert {op["type"] for op in ops} | {"page.delete"} == set(DESKTOP_OPERATION_TYPES)
    changed, inverse = apply_operations(sample_doc(), [*ops, {"type": "page.delete",
                                                              "pageId": "p_abc"}])
    restored, _ = apply_operations(changed, inverse, max_ops=None)
    assert wire(restored) == wire(sample_doc())
    for op in inverse:  # the inverses travel in the same format
        assert "_" not in "".join(k for k in wire(op) if k != "type")


def test_the_inverse_of_a_delete_carries_the_stacking_index() -> None:
    doc = sample_doc()
    changed, inverse = apply_operations(doc, [{"type": "node.delete", "nodeId": "g1"}])
    restore = wire(inverse[0])
    assert restore["type"] == "node.add" and restore["index"] == 2
    assert (restore["pageId"], restore["artboardId"]) == ("p1", "a1")
    assert sorted(n["id"] for n in restore["children"]) == ["s2", "t2"]
    restored, _ = apply_operations(changed, inverse, max_ops=None)
    assert restored.pages[0].artboards[0].node_ids == doc.pages[0].artboards[0].node_ids


def test_null_removes_an_optional_field_and_the_inverse_puts_it_back() -> None:
    doc = sample_doc()
    changed, inverse = apply_operations(
        doc, [{"type": "node.update", "nodeId": "t1", "patch": {"fontToken": None}}]
    )
    node = changed.nodes["t1"]
    assert isinstance(node, TextNode) and node.font_token is None
    assert "fontToken" not in wire(changed)["nodes"]["t1"]
    assert wire(inverse[0])["patch"] == {"fontToken": "body"}
    added, inverse = apply_operations(
        doc, [{"type": "node.update", "nodeId": "t1", "patch": {"align": "center"}}]
    )
    assert wire(inverse[0])["patch"] == {"align": None}  # the inverse removes what was added


def test_deleting_a_group_takes_its_children_and_moving_one_moves_them_on_request() -> None:
    doc = sample_doc()
    gone, _ = apply_operations(doc, [{"type": "node.delete", "nodeId": "g1"}])
    assert not {"g1", "s2", "t2"} & set(gone.nodes)
    alone, _ = apply_operations(
        doc, [{"type": "node.move", "nodeId": "g1", "frame": frame(310, 320, 200, 200)}]
    )
    assert (alone.nodes["t2"].frame.x, alone.nodes["t2"].frame.y) == (310, 310)  # desktop default
    moved, _ = apply_operations(doc, [{"type": "node.move", "nodeId": "g1", "withChildren": True,
                                       "frame": frame(310, 320, 200, 200)}])
    assert (moved.nodes["t2"].frame.x, moved.nodes["t2"].frame.y) == (320, 330)


def test_page_delete_undo_puts_the_page_back_where_it_was() -> None:
    doc = sample_doc()
    changed, inverse = apply_operations(doc, [
        {"type": "page.add", "page": {"id": "p9", "artboards": []}},
        {"type": "page.delete", "pageId": "p2"},
    ])
    assert [p.id for p in changed.pages] == ["p1", "p9"]
    restored, _ = apply_operations(changed, inverse, max_ops=None)
    assert [p.id for p in restored.pages] == ["p1", "p2"]


def test_comments_survive_their_element_and_undo_works_in_either_order() -> None:
    doc = sample_doc()
    step1, undo1 = apply_operations(doc, [{"type": "node.delete", "nodeId": "t1"}])
    assert step1.comments[0].node_id == "t1"  # dangling, kept
    step2, undo2 = apply_operations(step1, [{"type": "comment.delete", "commentId": "c1"}])
    # Undo the comment delete while its element is still gone: allowed.
    back1, _ = apply_operations(step2, undo2, max_ops=None)
    back2, _ = apply_operations(back1, undo1, max_ops=None)
    assert wire(back2) == wire(doc)


def test_path_and_line_points() -> None:
    doc = sample_doc()
    node = doc.nodes["pa"]
    assert isinstance(node, ShapeNode) and node.points == [0, 0, 0.5, 1, 1, 0.25]
    line = shape("ln", shape="line", points=[1, 0, 0, 1])
    changed, _ = apply_operations(doc, [{"type": "node.add", "pageId": "p1", "artboardId": "a1",
                                         "node": line}])
    assert isinstance(changed.nodes["ln"], ShapeNode)


def _refused(ops: list[dict[str, Any]], **kwargs: Any) -> str:
    doc = sample_doc()
    before = wire(doc)
    with pytest.raises(DesignOperationError) as caught:
        apply_operations(doc, ops, **kwargs)
    assert wire(doc) == before
    return str(caught.value)


def _add(node: dict[str, Any], **extra: Any) -> list[dict[str, Any]]:
    return [{"type": "node.add", "pageId": "p1", "artboardId": "a1", "node": node, **extra}]


@pytest.mark.parametrize(("ops", "fragment"), [
    ([{"type": "node.explode", "nodeId": "t1"}], "type"),
    ([{"type": "node.update", "nodeId": "lk", "patch": {"name": "x"}}], "locked"),
    ([{"type": "node.update", "nodeId": "lk", "patch": {"locked": False, "name": "x"}}], "locked"),
    ([{"type": "node.move", "nodeId": "lk", "frame": frame()}], "locked"),
    ([{"type": "node.delete", "nodeId": "lk"}], "locked"),
    ([{"type": "page.delete", "pageId": "p1"}], "locked"),
    ([{"type": "node.update", "nodeId": "t1", "patch": {"type": "shape"}}], "cannot set"),
    ([{"type": "node.update", "nodeId": "t1", "patch": {"id": "t9"}}], "cannot set"),
    ([{"type": "node.update", "nodeId": "t1", "patch": {"onclick": "x"}}], "cannot set"),
    ([{"type": "node.update", "nodeId": "t1", "patch": {"font_token": "body"}}], "cannot set"),
    ([{"type": "node.update", "nodeId": "t1", "patch": {"text": None}}], "cannot be removed"),
    ([{"type": "node.update", "nodeId": "t1", "patch": {"name": None}}], "cannot be removed"),
    ([{"type": "node.update", "nodeId": "g1", "patch": {"childIds": ["s2", "t2", "t1"]}}],
     "more than once"),
    ([{"type": "node.update", "nodeId": "g1", "patch": {"childIds": ["s2"]}}], "not placed"),
    ([{"type": "node.update", "nodeId": "t1", "patch": {"parentId": "g1"}}], "parent"),
    ([{"type": "node.update", "nodeId": "t1", "patch": {"style": {"fill": "red"}}}], "fill"),
    ([{"type": "node.update", "nodeId": "t1", "patch": {"frame": {**frame(), "width": "9"}}}],
     "width"),
    ([{"type": "node.update", "nodeId": "pa", "patch": {"points": [0, 0, 1]}}], "even"),
    ([{"type": "node.update", "nodeId": "pa", "patch": {"points": [0, 0, 1.5, 1]}}], "points"),
    ([{"type": "node.update", "nodeId": "pa", "patch": {"points": [0.5] * 8002}}], "points"),
    (_add(shape("ln", shape="line", points=[0, 0, 0.5, 0.5, 1, 1])), "two ends"),
    ([{"type": "node.update", "nodeId": "nope", "patch": {"text": "x"}}], "not found"),
    ([{"type": "node.add", "pageId": "p1", "artboardId": "nope", "node": text("n9")}],
     "not found"),
    ([{"type": "node.add", "pageId": "p2", "artboardId": "a1", "node": text("n9")}],
     "not on page"),
    ([{"type": "node.add", "artboardId": "a1", "node": text("n9")}], "pageId"),
    ([{"type": "artboard.update", "pageId": "p2", "artboardId": "a1", "patch": {"name": "x"}}],
     "not on page"),
    ([{"type": "artboard.update", "artboardId": "a1", "patch": {"name": "x"}}], "pageId"),
    ([{"type": "artboard.update", "pageId": "p1", "artboardId": "a1",
       "patch": {"background": None}}], "remove"),
    ([{"type": "artboard.delete", "pageId": "p1", "artboardId": "a2"}], "not on page"),
    ([{"type": "artboard.add", "pageId": "nope", "artboard": {"id": "a9", "width": 1,
                                                               "height": 1}}], "not found"),
    (_add(text("t1")), "already exists"),
    ([{"type": "page.add", "page": {"id": "p1"}}], "already exists"),
    ([{"type": "artboard.add", "pageId": "p1", "artboard": {"id": "a2", "width": 1,
                                                             "height": 1}}], "already exists"),
    (_add({"id": "n9", "type": "group", "frame": frame(), "childIds": ["t1"]}), "does not carry"),
    (_add({"id": "ga", "type": "group", "frame": frame(), "childIds": ["gb"]},
          children=[{"id": "gb", "type": "group", "frame": frame(), "childIds": ["ga"]}]),
     "more than once"),
    (_add(text("n9"), children=[text("n10")]), "nothing places"),
    (_add(text("n9"), parentId="t1"), "not a group"),
    ([{"type": "node.add", "pageId": "p2", "artboardId": "a2", "parentId": "g1",
       "node": text("n9")}], "not on artboard"),
    (_add(text("n9"), index=99), "past the end"),
    (_add({"id": "n9", "type": "image", "frame": frame(), "assetId": "../../etc/passwd"}),
     "assetId"),
    (_add({"id": "n9", "type": "image", "frame": frame(), "assetId": "https://x.test/a.png"}),
     "assetId"),
    (_add(text("bad id")), "id"),
    (_add({**text("n9"), "script": "alert(1)"}), "Extra inputs"),
    ([{"type": "page.add", "page": {"id": "p9", "artboards": [
        {"id": "a9", "width": 10, "height": 10, "nodeIds": ["t1"]}]}}], "does not carry"),
    ([{"type": "node.duplicate", "nodeId": "g1", "newIds": {"g1": "x"}}], "every element"),
    ([{"type": "comment.add", "comment": comment("c1")}], "already exists"),
    ([{"type": "comment.add", "comment": comment("c9", text="   ")}], "blank"),
    ([{"type": "comment.add", "comment": comment("c9", artboardId="../x")}], "artboardId"),
    ([{"type": "comment.resolve", "commentId": "c1"}], "resolved"),
    ([{"type": "comment.resolve", "commentId": "nope", "resolved": True}], "not found"),
    ([{"type": "artboard.update", "pageId": "p1", "artboardId": "a1",
       "patch": {"nodeIds": []}}], "cannot set"),
    ([{"type": "artboard.update", "pageId": "p1", "artboardId": "a1", "patch": {"width": 0}}],
     "width"),
])
def test_invalid_operations_are_refused_and_change_nothing(
    ops: list[dict[str, Any]], fragment: str
) -> None:
    assert fragment in _refused(ops)


@pytest.mark.parametrize("color", ["#FFF", "red", "#FFFFFF\n", "url(x)", "#GGGGGG", "#FFFFFFF"])
def test_colours_other_than_hex6_or_hex8_are_refused(color: str) -> None:
    _refused([{"type": "artboard.update", "pageId": "p1", "artboardId": "a1",
               "patch": {"background": color}}])


def test_a_failure_mid_batch_leaves_the_input_untouched_and_names_the_operation() -> None:
    message = _refused([
        {"type": "document.rename", "title": "changed"},
        {"type": "node.delete", "nodeId": "t1"},
        {"type": "node.delete", "nodeId": "missing"},
    ])
    assert message.startswith("operation 2:")


def test_the_operation_limit_is_enforced() -> None:
    ops = [{"type": "document.rename", "title": f"t{i}"} for i in range(5)]
    assert "limit is 4" in _refused(ops, max_ops=4)


def test_a_model_batch_cannot_change_locks() -> None:
    assert "owner" in _refused(
        [{"type": "node.update", "nodeId": "lk", "patch": {"locked": False}}],
        allow_lock_changes=False,
    )
    assert "owner" in _refused(_add(text("n9", locked=True)), allow_lock_changes=False)


def test_a_document_with_a_group_cycle_an_orphan_or_a_false_parent_does_not_validate() -> None:
    raw = sample_dict()
    raw["nodes"]["ca"] = {"id": "ca", "type": "group", "frame": frame(), "childIds": ["cb"]}
    raw["nodes"]["cb"] = {"id": "cb", "type": "group", "frame": frame(), "childIds": ["ca"]}
    with pytest.raises(ValidationError, match="cycle"):
        DesignDocument.model_validate(raw)
    raw = sample_dict()
    raw["nodes"]["orphan"] = text("orphan")
    with pytest.raises(ValidationError, match="not placed"):
        DesignDocument.model_validate(raw)
    raw = sample_dict()
    raw["pages"][1]["artboards"][0]["nodeIds"].append("t1")
    with pytest.raises(ValidationError, match="more than once"):
        DesignDocument.model_validate(raw)
    raw = sample_dict()
    raw["nodes"]["t1"]["parentId"] = "g1"
    with pytest.raises(ValidationError, match="parent"):
        DesignDocument.model_validate(raw)


def test_parsed_operations_keep_their_wire_shape() -> None:
    raw = ROUND_TRIPS["a mixed batch"]
    for parsed, original in zip(parse_operations(raw), raw, strict=True):
        assert set(wire(parsed)) == set(original)  # same camelCase keys, defaults aside


def test_remapping_replaces_introduced_ids_and_rewrites_later_references() -> None:
    ops = [
        {"type": "node.add", "pageId": "p1", "artboardId": "a1",
         "node": {"id": "card", "type": "group", "frame": frame(), "childIds": ["bg", "t1"]},
         "children": [shape("bg"), text("t1", parentId="card")]},  # "t1" collides with an existing one
        {"type": "node.update", "nodeId": "t1", "patch": {"text": "inside the new card"}},
        {"type": "node.duplicate", "nodeId": "card",
         "newIds": {"card": "card2", "bg": "bg2", "t1": "t1b"}},
        {"type": "comment.add", "comment": comment("note", nodeId="card2")},
    ]
    counter = iter(range(100))
    remapped, mapping = remap_new_ids(ops, lambda kind: f"{kind}_{next(counter)}")
    assert set(mapping) == {"card", "bg", "t1", "card2", "bg2", "t1b", "note"}
    new_t1 = mapping["t1"]
    assert remapped[0]["node"]["childIds"] == [mapping["bg"], new_t1]
    assert remapped[0]["children"][1]["parentId"] == mapping["card"]
    assert remapped[1]["nodeId"] == new_t1  # the new one, not the existing t1
    assert remapped[2]["nodeId"] == mapping["card"]
    assert remapped[2]["newIds"] == {mapping["card"]: mapping["card2"], mapping["bg"]:
                                     mapping["bg2"], new_t1: mapping["t1b"]}
    assert remapped[3]["comment"]["nodeId"] == mapping["card2"]
    assert remapped[3]["comment"]["artboardId"] == "a1"
    doc = sample_doc()
    changed, _ = apply_operations(doc, remapped)
    old, new = changed.nodes["t1"], changed.nodes[new_t1]
    assert isinstance(old, TextNode) and isinstance(new, TextNode)
    assert old.text == "Headline"  # the existing element was not touched
    assert new.text == "inside the new card"


def test_references_before_an_introduction_mean_the_existing_element() -> None:
    ops = [
        {"type": "node.update", "nodeId": "t1", "patch": {"text": "old one"}},
        {"type": "node.add", "pageId": "p1", "artboardId": "a1", "node": text("t1")},
    ]
    remapped, mapping = remap_new_ids(ops)
    assert remapped[0]["nodeId"] == "t1"
    assert remapped[1]["node"]["id"] == mapping["t1"] != "t1"
    assert remapped[1]["artboardId"] == "a1"
