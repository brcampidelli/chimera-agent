"""The instrument check: runs before the first paid call, calls no model, costs nothing.

An instrument that cannot show the failure it counts turns every arm into "no failures" (lessons
§2q, §2t). Each check feeds a known-bad and a known-good input and asserts the instrument tells them
apart; the trivial-agent control (PROTOCOL §13) is the "does nothing" answer scoring zero.
"""

from __future__ import annotations

import json
from typing import Any

from briefs import BRIEFS, refinement
from measure import OverflowMeter, contrast, edit_outcome, first_attempt_edit

from chimera.design.model import Artboard, DesignDocument, Page
from chimera.design.tokens import default_tokens
from chimera.providers.gateway import CompletionResult

PHONE = ('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>x</title></head><body '
         'style="margin:0"><main style="padding:16px;background:#ffffff"><h1 style="color:{c};'
         'font-size:20px">Hello</h1>{extra}</main></body></html>')


def _board(color: str, extra: str = "", *, mode: str = "fixed", width: int = 390, height: int = 844) -> Artboard:
    return Artboard(id="ab", name="x", width=width, height=height, background="#FFFFFF",
                    html=PHONE.format(c=color, extra=extra), mode=mode)  # type: ignore[arg-type]


def _doc(board: Artboard) -> DesignDocument:
    return DesignDocument(id="d", title="t", tokens=default_tokens(), pages=[Page(id="home", name="Home", artboards=[board])])


def check(label: str, condition: bool) -> None:
    print(("ok   " if condition else "FAIL ") + label)
    if not condition:
        raise SystemExit(f"selftest failed: {label}")


class Scripted:
    """A backend that answers with one fixed JSON reply and reports a token count from the prompt size."""

    def __init__(self, reply: dict[str, Any]) -> None:
        self.reply = json.dumps(reply)
        self.calls: list[str] = []

    def complete(self, messages: list[Any], **_kwargs: Any) -> CompletionResult:
        text = "".join(m["content"] for m in messages)
        self.calls.append(text)
        return CompletionResult(content=self.reply, model="fake", prompt_tokens=len(text) // 4,
                                completion_tokens=50)


def run_selftest() -> None:
    tokens = default_tokens()
    # contrast: the instrument sees a pale grey on white and does not flag near-black.
    check("contrast flags #cccccc on white", contrast(_board("#cccccc"), tokens)["low"] >= 1)
    check("contrast passes #111111 on white", contrast(_board("#111111"), tokens) == {"measured": True, "low": 0, "partial": False})
    # overflow: a 1200 px child in a 390 px fixed artboard spills; the clean one and a long fill page do not.
    wide = '<div style="width:1200px;height:20px;background:#eee">wide</div>'
    tall = '<div style="height:3000px;background:#eee">tall</div>'
    with OverflowMeter() as meter:
        check("overflow flags a 1200px child in a 390px artboard", meter.overflows(_doc(_board("#111111", wide)), "ab"))
        check("overflow does not flag a clean artboard", not meter.overflows(_doc(_board("#111111")), "ab"))
        check("overflow flags content taller than a fixed artboard", meter.overflows(_doc(_board("#111111", tall)), "ab"))
        check("overflow does not flag a long page in a fill artboard",
              not meter.overflows(_doc(_board("#111111", tall, mode="fill")), "ab"))
    # first-attempt edit: the trivial agent (no operations) and the escape hatch score zero.
    edit = {"type": "artboard.edit_html", "pageId": "home", "artboardId": "t", "old": "a", "new": "b"}
    whole = {"type": "artboard.set_html", "pageId": "home", "artboardId": "t", "html": "<p>x</p>"}
    other = {**edit, "artboardId": "o"}
    check("trivial agent (no operations) is not a success", not first_attempt_edit(True, 1, [], "t"))
    check("a whole rewrite is not an edit", not first_attempt_edit(True, 1, [whole], "t"))
    check("an edit on another artboard is not a success", not first_attempt_edit(True, 1, [other], "t"))
    check("a re-asked answer is not a first-attempt success", not first_attempt_edit(True, 2, [edit], "t"))
    check("an operation that did not apply is not a success", not first_attempt_edit(False, 1, [edit], "t"))
    check("one clean edit on the target is a success", first_attempt_edit(True, 1, [edit], "t"))
    check("collateral edits are counted", edit_outcome([edit, other], "t")["collateral"] == 1)
    # the corpus can exhibit the effect: targets are not always the first screen, and a third are ordinal.
    shape = [refinement(i) for i in range(len(BRIEFS))]
    check("targets rotate (at least 4 distinct positions)", len({s[1] for s in shape}) >= 4)
    check("20 named and 10 ordinal requests", sum(s[2] == "named" for s in shape) == 20)
    check("every target position exists in its brief", all(s[1] < len(BRIEFS[i].screens) for i, s in enumerate(shape)))
    print("selftest passed (instruments only; the arm checks live in tests/test_design_lean_refine.py)")
