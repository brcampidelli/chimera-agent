"""HTML artboard tweaks change only declared CSS variables; directions are side by side and never applied blind."""

from __future__ import annotations

import json
from typing import Any

import pytest

from chimera.design import ops as design_ops
from chimera.design import propose as design_propose
from chimera.design.model import wire
from chimera.design.ops import DesignOperationError, apply_operations
from chimera.design.propose import MAX_PROPOSAL_OPS, propose_directions
from chimera.providers.gateway import CompletionResult
from tests.design_fixtures import sample_doc

PAGE = "p1"
BOARD = "a1"
TWEAK_HTML = (
    "<!doctype html><html><head><meta charset=\"utf-8\"><title>Tweaks</title><style>"
    ":root{ /* @tweak accent color \"Accent\" */ --tw-accent: #0e7490; "
    "/* @tweak scale number 0.8 1.4 0.1 \"Scale\" */ --tw-scale: 1; "
    "/* @tweak density choice \"Density\" \"compact\" \"comfortable\" */ --tw-density: comfortable; }"
    "p{ color: var(--tw-accent); }"
    "</style></head><body><p>Launch</p></body></html>"
)


@pytest.fixture(autouse=True)
def _released(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(design_ops, "HTML_ARTBOARDS_ENABLED", True)


def _op(kind: str, **fields: Any) -> dict[str, Any]:
    return {"type": kind, "pageId": PAGE, "artboardId": BOARD, **fields}


def _with_tweaks():
    return apply_operations(sample_doc(), [_op("artboard.set_html", html=TWEAK_HTML)], max_ops=None)[0]


def _html(doc: Any) -> str:
    return doc.pages[0].artboards[0].html


# ---- artboard.set_tweak -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "value", "before", "after"),
    [
        ("accent", "#cc5500", "--tw-accent: #0e7490;", "--tw-accent: #cc5500;"),
        ("scale", "1.2", "--tw-scale: 1;", "--tw-scale: 1.2;"),
        ("density", "compact", "--tw-density: comfortable;", "--tw-density: compact;"),
    ],
)
def test_set_tweak_accepts_a_valid_value_of_each_type(name: str, value: str, before: str, after: str) -> None:
    loaded = _with_tweaks()
    changed, _ = apply_operations(loaded, [_op("artboard.set_tweak", name=name, value=value)], max_ops=None)
    assert _html(changed) == _html(loaded).replace(before, after)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ghost", "#cc5500"),          # no such tweak
        ("accent", "red"),             # colour must be hex
        ("accent", "#12"),             # hex of the wrong length
        ("scale", "2"),                # above the range
        ("scale", "0.5"),              # below the range
        ("scale", "big"),              # not a number
        ("density", "loose"),          # not one of the options
        ("accent", "#cc5500;"),        # ';' would end the declaration
        ("accent", "#cc5500}"),        # '}' would close the rule
        ("accent", "url(http://x.test/a.png)"),
        ("accent", "expression(alert(1))"),
        ("accent", "@import 'x.css'"),
    ],
)
def test_set_tweak_refuses_a_bad_value_and_leaves_the_document_unchanged(name: str, value: str) -> None:
    loaded = _with_tweaks()
    before = wire(loaded)
    with pytest.raises(DesignOperationError):
        apply_operations(loaded, [_op("artboard.set_tweak", name=name, value=value)], max_ops=None)
    assert wire(loaded) == before


def test_set_tweak_and_its_inverse_restore_the_html_byte_for_byte() -> None:
    loaded = _with_tweaks()
    changed, inverse = apply_operations(
        loaded, [_op("artboard.set_tweak", name="accent", value="#cc5500")], max_ops=None
    )
    assert _html(changed) != _html(loaded)
    restored, _ = apply_operations(changed, inverse, max_ops=None)
    assert _html(restored) == _html(loaded)
    assert wire(restored) == wire(loaded)


# ---- propose_directions -------------------------------------------------------------------------


class FakeBackend:
    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.calls: list[list[dict[str, Any]]] = []

    def complete(self, messages: list[Any], **_kwargs: Any) -> CompletionResult:
        self.calls.append([dict(m) for m in messages])
        return CompletionResult(content=self.replies.pop(0), model="fake")


def _direction_reply(tag: str) -> str:
    board = {
        "id": f"board_{tag}", "name": "model's own name", "width": 390, "height": 844,
        "background": "#FFFFFF", "nodeIds": [], "mode": "fixed",
        "html": f"<!doctype html><html><head><meta charset=\"utf-8\"><title>{tag}</title></head>"
                f"<body><main>{tag}</main></body></html>",
    }
    body = {"summary": f"{tag} direction.", "assumptions": ["Desktop first"],
            "operations": [{"type": "artboard.add", "pageId": PAGE, "artboard": board}],
            "checks": ["Readable"]}
    return "```json\n" + json.dumps(body) + "\n```"


def _page_artboard_names(doc: Any) -> list[str]:
    return [board.name for board in doc.pages[0].artboards]


def test_one_or_five_directions_is_a_value_error() -> None:
    for count in (1, 5):
        with pytest.raises(ValueError):
            propose_directions(FakeBackend(), sample_doc(), "brief", count)


def test_three_directions_are_named_a_b_c_and_appended_in_order() -> None:
    doc = sample_doc()
    backend = FakeBackend(_direction_reply("one"), _direction_reply("two"), _direction_reply("three"))
    outcome = propose_directions(backend, doc, "a landing page", 3)
    assert outcome.ok and outcome.proposal is not None and outcome.preview is not None
    assert _page_artboard_names(outcome.preview) == ["Desktop", "Direction A", "Direction B", "Direction C"]
    # Appended, never inserted: the desktop lays artboards out in page order, so they sit side by side.
    added = [op for op in outcome.proposal.operations if op.type == "artboard.add"]
    assert len(added) == 3 and all(op.index is None for op in added)
    assert [op.artboard.id for op in added] == [b.id for b in outcome.preview.pages[0].artboards[1:]]


def test_the_proposal_is_previewed_never_applied_to_the_original_document() -> None:
    doc = sample_doc()
    before = wire(doc)
    backend = FakeBackend(_direction_reply("one"), _direction_reply("two"))
    outcome = propose_directions(backend, doc, "brief", 2)
    assert outcome.ok
    assert wire(doc) == before
    assert _page_artboard_names(doc) == ["Desktop"]


def test_a_direction_that_fails_does_not_take_the_others_down() -> None:
    backend = FakeBackend(_direction_reply("one"), "nope", "still nope", _direction_reply("three"))
    outcome = propose_directions(backend, sample_doc(), "brief", 3)
    assert outcome.ok and outcome.error is None
    assert _page_artboard_names(outcome.preview) == ["Desktop", "Direction A", "Direction C"]
    assert len(backend.calls) == 4  # A once, B re-asked once, C once


def test_directions_that_together_exceed_the_operation_limit_are_refused_not_trimmed() -> None:
    per_direction = MAX_PROPOSAL_OPS // 2 + 1  # each direction alone is within the limit

    def many(tag: str) -> str:
        # Updates of the existing board: a page holds at most 50 artboards, so adds cannot reach 200.
        ops = [{"type": "artboard.update", "pageId": PAGE, "artboardId": BOARD,
                "patch": {"name": f"{tag}{i}"}} for i in range(per_direction)]
        return "```json\n" + json.dumps({"summary": "s", "assumptions": [], "operations": ops,
                                         "checks": []}) + "\n```"

    doc = sample_doc()
    before = wire(doc)
    outcome = propose_directions(FakeBackend(many("a"), many("b")), doc, "brief", 2)
    assert not outcome.ok and outcome.preview is None
    assert outcome.error is not None and str(2 * per_direction) in outcome.error
    assert str(MAX_PROPOSAL_OPS) in outcome.error
    assert wire(doc) == before


def test_direction_labels_are_english_and_lettered() -> None:
    assert [design_propose._direction_label(i) for i in range(4)] == [
        "Direction A", "Direction B", "Direction C", "Direction D"]
