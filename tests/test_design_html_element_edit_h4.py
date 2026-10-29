from __future__ import annotations

import string
from typing import Any

import pytest

from chimera.design import ops as design_ops
from chimera.design import prompts
from chimera.design.html_source import DesignHtmlError, locate_html_element
from chimera.design.model import wire
from chimera.design.ops import DesignOperationError, apply_operations
from tests.design_fixtures import sample_doc


@pytest.fixture(autouse=True)
def _released(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(design_ops, "HTML_ARTBOARDS_ENABLED", True)


def _op(kind: str, **fields: Any) -> dict[str, Any]:
    return {"type": kind, "pageId": "p1", "artboardId": "a1", **fields}


def with_html(source: str):
    return apply_operations(sample_doc(), [_op("artboard.set_html", html=source)])[0]


def test_index_counts_start_tags_in_document_order_and_nothing_else() -> None:
    source = "<!doctype html><!-- lead --><main><p>Text</p><svg><g><text>x</text></g></svg></main>"
    assert [locate_html_element(source, i).tag for i in range(5)] == ["main", "p", "svg", "g", "text"]
    main = locate_html_element(source, 0)
    assert source[main.tag_start:main.tag_end] == "<main>" and main.closed
    nested = locate_html_element(source, 4)
    assert source[nested.tag_start:nested.tag_end] == "<text>" and nested.text_only


@pytest.mark.parametrize("index", [-1, 2, 100000, True])
def test_locator_rejects_invalid_or_missing_indexes(index: Any) -> None:
    with pytest.raises(DesignHtmlError):
        locate_html_element("<main><p>x</p></main>", index)


def test_set_style_adds_changes_and_removes_and_inverse_is_exact() -> None:
    original = with_html('<main><p style="color: red; padding: 2px">Hello</p></main>')
    added, inverse = apply_operations(original, [_op("artboard.set_style", index=1, property="color", value="blue")])
    assert added.pages[0].artboards[0].html == '<main><p style="padding: 2px; color: blue">Hello</p></main>'
    restored, _ = apply_operations(added, inverse, max_ops=None)
    assert wire(restored) == wire(original)
    removed, undo = apply_operations(added, [_op("artboard.set_style", index=1, property="color", value="")])
    assert removed.pages[0].artboards[0].html == '<main><p style="padding: 2px">Hello</p></main>'
    restored, _ = apply_operations(removed, undo, max_ops=None)
    assert wire(restored) == wire(added)


def test_set_style_drops_the_attribute_when_no_declaration_is_left() -> None:
    changed, _ = apply_operations(with_html('<main><p class="a" style="color: red">x</p></main>'),
                                  [_op("artboard.set_style", index=1, property="color", value="")])
    assert changed.pages[0].artboards[0].html == '<main><p class="a">x</p></main>'


def test_set_style_does_not_split_a_semicolon_inside_a_quoted_string() -> None:
    changed, _ = apply_operations(with_html("<main><p style=\"content: 'a;b'; color: red\">x</p></main>"),
                                  [_op("artboard.set_style", index=1, property="color", value="blue")])
    assert changed.pages[0].artboards[0].html == "<main><p style=\"content: 'a;b'; color: blue\">x</p></main>"


@pytest.mark.parametrize("value", ["red; color: blue", "red}", "{red"])
def test_set_style_refuses_braces_and_semicolons_in_the_value(value: str) -> None:
    with pytest.raises(DesignOperationError, match="may not contain"):
        apply_operations(with_html("<main><p>x</p></main>"), [_op("artboard.set_style", index=1, property="color", value=value)])


@pytest.mark.parametrize(("prop", "value"), [("behavior", "url(asset:x)"), ("color", "url(https://evil.example/x)")])
def test_set_style_refuses_disallowed_property_or_external_url(prop: str, value: str) -> None:
    original = with_html("<main><p>x</p></main>")
    before = wire(original)
    with pytest.raises(DesignOperationError):
        apply_operations(original, [_op("artboard.set_style", index=1, property=prop, value=value)])
    assert wire(original) == before


def test_set_text_escapes_markup_and_restores_exact_bytes() -> None:
    original = with_html("<main><p>old</p></main>")
    changed, inverse = apply_operations(original, [_op("artboard.set_text", index=1, text="A & <B>")])
    assert changed.pages[0].artboards[0].html == "<main><p>A &amp; &lt;B&gt;</p></main>"
    restored, _ = apply_operations(changed, inverse, max_ops=None)
    assert wire(restored) == wire(original)


def test_set_text_refuses_nested_children() -> None:
    with pytest.raises(DesignOperationError, match="child elements"):
        apply_operations(with_html("<main><p><b>x</b></p></main>"), [_op("artboard.set_text", index=1, text="no")])


def test_set_text_refuses_void_self_closing_and_unclosed_elements_with_a_reason() -> None:
    source = '<main><br><img src="asset:x"><svg><path/></svg><p>a<p>b</main>'
    original = with_html(source)
    for index, reason in [(1, "void"), (2, "void"), (4, "void"), (5, "explicit end tag")]:
        with pytest.raises(DesignOperationError, match=reason):
            apply_operations(original, [_op("artboard.set_text", index=index, text="no")])
    assert wire(original) == wire(with_html(source))


def test_set_text_is_exact_after_unicode_line_separators_form_feeds_and_bare_carriage_returns() -> None:
    source = "<main>a b\x0c<p>old\r</p>\r<p>keep</p></main>"
    changed, _ = apply_operations(with_html(source), [_op("artboard.set_text", index=1, text="new")])
    assert changed.pages[0].artboards[0].html == "<main>a b\x0c<p>new</p>\r<p>keep</p></main>"


def test_h4_ops_are_refused_when_gate_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    document = with_html("<main><p>x</p></main>")  # built while the gate is open
    monkeypatch.setattr(design_ops, "HTML_ARTBOARDS_ENABLED", False)
    for op in [
        _op("artboard.set_style", index=1, property="color", value="red"),
        _op("artboard.set_text", index=1, text="new"),
    ]:
        with pytest.raises(DesignOperationError, match="not available"):
            apply_operations(document, [op])


def test_refine_prompt_names_the_selected_element_as_data() -> None:
    assert "{html_selection}" in prompts.HTML_REFINE
    names = {name for _, name, _, _ in string.Formatter().parse(prompts.HTML_REFINE) if name}
    values = {name: f"<<{name}>>" for name in names}
    rendered = prompts.HTML_REFINE.format(**values)
    assert "<<html_selection>>" in rendered
    without = {name: value for name, value in values.items() if name != "html_selection"}
    with pytest.raises(KeyError):
        prompts.HTML_REFINE.format(**without)
