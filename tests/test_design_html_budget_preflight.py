"""The HTML checker's budget preflight does not depend on selectors matching."""

from unittest.mock import patch

import pytest

from chimera.design.html_check import check_html_artboard, summarize_html
from chimera.design.model import Artboard
from chimera.design.tokens import default_tokens


def _board(html: str) -> Artboard:
    return Artboard.model_construct(id="h1", name="x", width=800, height=600, background="#FFFFFF",
                                    node_ids=[], html=html)


@pytest.mark.parametrize("selector", ["div", ".absent", "#absent", "div#no.match"])
def test_unavoidable_rule_cost_refuses_even_if_no_selector_matches(selector: str) -> None:
    # Tag, class, ID and compound selectors cost the same even if none match.
    html = "<style>" + f"{selector}{{color:#111827}}" * 1000 + "</style>" + "<p>x</p>" * 500
    board, tokens = _board(html), default_tokens()
    with patch("chimera.design.html_check._compute", side_effect=AssertionError("unreachable")) as compute:
        findings, unchecked = check_html_artboard(board, tokens)
        summary = summarize_html(board, tokens)
    compute.assert_not_called()
    assert findings == []
    assert [u.reason for u in unchecked] == ["too_complex"]
    assert summary == {"artboard_id": "h1", "not_checked": "too_complex"}


def test_affordable_nonmatching_rule_remains_measurable() -> None:
    board = _board("<style>div#no.match{color:#111827}</style><p>x</p>")
    summary = summarize_html(board, default_tokens())
    assert summary["counts"] == {"p": 1}
    assert summary["texts"] == ["x"]


def test_declaration_preflight_reuses_classes_for_rules_with_few_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    from chimera.design import html_check
    from chimera.design.html_css import Budget, selector_matches

    # These modest rules fit: their declarations match no paragraphs, so all text stays measurable.
    declarations = "".join(f"--space{i}:0;" for i in range(100))
    selectors = [".absent-a", ".absent-b", ".absent-c"]
    css = "".join(f"{selector}{{{declarations}}}" for selector in selectors)
    html = f"<style>{css}</style>" + "".join(
        f'<p id="text{i}" class="note" style="color:#111827">x</p>' for i in range(24)
    )
    seen: dict[str, list[set[str]]] = {}

    def record(selector: str, tag: str, classes: set[str], element_id: str | None) -> bool:
        if selector in selectors and element_id:
            seen.setdefault(element_id, []).append(classes)
        return selector_matches(selector, tag, classes, element_id)

    monkeypatch.setattr(html_check, "Budget", lambda: Budget(10_000))
    monkeypatch.setattr(html_check, "selector_matches", record)
    summary = summarize_html(_board(html), default_tokens())

    assert summary["texts"] == ["x"] * 12
    assert summary["counts"] == {"p": 24}
    assert len(seen) == 24
    for classes in seen.values():
        assert len(classes) == 6  # Three preflight matches, then the same three in normal traversal.
        assert all(value is classes[0] for value in classes[:3])
        assert all(value is classes[3] for value in classes[3:])
