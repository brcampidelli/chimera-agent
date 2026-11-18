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
