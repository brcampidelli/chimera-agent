"""The instruments of the Design bench: contrast, overflow and "an edit applied on the first attempt".

Nothing here calls a model. Each instrument is checked against a known-bad and a known-good input by
``run.py --selftest`` before the first paid call (PREREGISTRATION.md, controls), because an
instrument that cannot show the failure it is meant to count turns every arm into "no failures".

- **Contrast** is the product's own measurement (``chimera.design.html_check``): the number of text
  elements whose computed colour on its computed background falls under WCAG AA. An artboard the
  checker could not read is "not measured" and leaves the denominator, never counts as clean.
- **Overflow** is a render, because overflow is a layout fact: the artboard is drawn at its own size
  in Chromium (the PNG export's page) and its content box is compared with its scroll box, so a
  1200 px grid in a 390 px phone is caught and a long page in a ``fill`` artboard is not.
- **First-attempt edit** is read off the proposal outcome, never off the model's own claim.
"""

from __future__ import annotations

from typing import Any

from chimera.design.export_html import artboards, render_print_html
from chimera.design.html_check import check_html_artboard
from chimera.design.model import Artboard, DesignDocument, DesignTokens

EDIT_TYPES = frozenset({"artboard.edit_html", "artboard.set_style", "artboard.set_text", "artboard.set_tweak"})
BOARD_TYPES = EDIT_TYPES | {"artboard.set_html"}
#: Gap reasons that mean the checker measured nothing about this artboard's contrast.
UNMEASURED = frozenset({"checker_error", "no_text", "empty", "too_complex", "structure_unbalanced", "not_checked"})


def contrast(board: Artboard, tokens: DesignTokens) -> dict[str, Any]:
    """``{"measured": bool, "low": n}``: low-contrast text elements, or not measured at all."""
    issues, gaps = check_html_artboard(board, tokens)
    low = sum(1 for issue in issues if issue.kind == "low_contrast")
    reasons = {gap.reason for gap in gaps}
    measured = not (reasons & UNMEASURED)
    return {"measured": measured, "low": low, "partial": measured and "contrast_unmeasurable" in reasons}


class OverflowMeter:
    """One Chromium for many measurements. Not thread-safe: call it from one thread."""

    def __init__(self) -> None:
        self._pw: Any = None
        self._browser: Any = None

    def __enter__(self) -> OverflowMeter:
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._browser is not None:
            self._browser.close()
        if self._pw is not None:
            self._pw.stop()

    def overflows(self, doc: DesignDocument, board_id: str) -> bool:
        """Whether the artboard's content spills past the artboard's own box."""
        boards = artboards(doc)
        index = next(i for i, b in enumerate(boards) if b.id == board_id)
        board = boards[index]
        context = self._browser.new_context(
            java_script_enabled=True, offline=True,
            viewport={"width": int(board.width), "height": int(board.height)},
        )
        try:
            context.route("**/*", lambda route: route.continue_() if route.request.url.startswith("data:") else route.abort())
            page = context.new_page()
            page.set_content(render_print_html(doc), wait_until="load")
            fixed = board.mode == "fixed"
            return bool(page.evaluate(
                """([i, fixed]) => {
                    const el = document.querySelector(`body > [data-chimera-artboard="${i}"]`);
                    if (!el) return null;
                    const wide = el.scrollWidth > el.clientWidth + 1;
                    const tall = fixed && el.scrollHeight > el.clientHeight + 1;
                    return wide || tall;
                }""",
                [index, fixed],
            ))
        finally:
            context.close()


def edit_outcome(operations: list[dict[str, Any]], target_board: str) -> dict[str, Any]:
    """What a refinement's operations did to the target artboard and to the others.

    ``edits`` counts ``artboard.edit_html``; ``whole`` is a full ``artboard.set_html`` (the escape
    hatch the prompt reserves for a source that is not shown); ``collateral`` counts operations on any
    other artboard.
    """
    on_target = [op for op in operations if op.get("artboardId") == target_board and op.get("type") in BOARD_TYPES]
    other = [op for op in operations if op.get("type") in BOARD_TYPES and op.get("artboardId") != target_board]
    return {
        "edits": sum(1 for op in on_target if op["type"] == "artboard.edit_html"),
        "small": sum(1 for op in on_target if op["type"] in EDIT_TYPES),
        "whole": sum(1 for op in on_target if op["type"] == "artboard.set_html"),
        "collateral": len(other),
        "ops": len(operations),
    }


def first_attempt_edit(ok: bool, attempts: int, operations: list[dict[str, Any]], target_board: str) -> bool:
    """The registered success: the proposal applied cleanly in a dry run on the first model answer,
    contains at least one ``artboard.edit_html`` on the target artboard, and rewrote no artboard whole."""
    shape = edit_outcome(operations, target_board)
    return bool(ok and attempts == 1 and shape["edits"] >= 1 and shape["whole"] == 0)
