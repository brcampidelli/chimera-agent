from __future__ import annotations

from chimera.design.partial_html import partial_html, partial_target

# Built from chr(92) so that no editor, shell or heredoc along the way can rewrite a JSON escape.
BS = chr(92)
HEAD = '{"html":"a'


def test_partial_html_handles_empty_and_missing_key() -> None:
    assert partial_html("") is None
    assert partial_html('{"summary":"x"}') is None


def test_partial_html_decodes_json_escapes_and_ignores_trailing_escape() -> None:
    raw = '{"html":"line' + BS + "nquote: " + BS + '" slash: ' + BS + BS + " unicode: " + BS + "u2764"
    assert partial_html(raw) == 'line\nquote: " slash: \\ unicode: ❤'
    assert partial_html('{"html":"hello' + BS) == "hello"


def test_partial_html_returns_a_closed_string_without_following_json() -> None:
    assert partial_html('{"html":"<main>Hi</main>","more":true}') == "<main>Hi</main>"


def test_partial_html_uses_the_last_html_field() -> None:
    assert partial_html('{"html":"old","nested":{"html":"new') == "new"


def test_partial_html_joins_surrogate_pairs_into_one_character() -> None:
    pair = BS + "ud83d" + BS + "ude00"
    assert partial_html(HEAD + pair + "b") == "a\U0001f600b"


def test_partial_html_waits_for_the_low_half_of_a_pair() -> None:
    high = BS + "ud83d"
    for cut in (high, high + BS, high + BS + "u", high + BS + "ude0"):
        assert partial_html(HEAD + cut) == "a"


def test_partial_html_never_yields_a_lone_surrogate() -> None:
    high, low = BS + "ud83d", BS + "ude00"
    for buffer in (HEAD + high + 'X"', HEAD + low + '"', HEAD + high + '"'):
        text = partial_html(buffer)
        assert text is not None
        text.encode("utf-8")  # a lone surrogate would raise here, and break the SSE frame
        assert "�" in text


def test_partial_html_stops_at_a_cut_unicode_escape() -> None:
    assert partial_html('{"html":"ab' + BS + "u26") == "ab"
    assert partial_html('{"html":"ab' + BS + "u2") == "ab"


ADD = '{"summary":"s","operations":[{"type":"artboard.add","pageId":"p1","artboard":{"id":"landing","name":"L","nodeIds":[],"html":"'
SET = '{"operations":[{"type":"artboard.set_html","pageId":"p1","artboardId":"home","html":"'


def test_partial_target_names_the_artboard_of_an_add_operation() -> None:
    assert partial_target(ADD + "<main>Hi") == ("landing", "<main>Hi")


def test_partial_target_names_the_artboard_of_a_set_html_operation() -> None:
    assert partial_target(SET + "<p>x") == ("home", "<p>x")


def test_partial_target_does_not_borrow_the_id_of_an_earlier_operation() -> None:
    earlier = '{"operations":[{"type":"artboard.set_html","artboardId":"old","html":"<i>a</i>"},{"html":"<b>b'
    assert partial_target(earlier) == (None, "<b>b")


def test_partial_target_follows_the_operation_being_written_not_the_first() -> None:
    two = ADD + '<i>one</i>"}},{"type":"artboard.set_html","pageId":"p1","artboardId":"b","html":"<u>two'
    assert partial_target(two) == ("b", "<u>two")


def test_partial_target_ignores_edit_html_which_carries_a_fragment_not_a_page() -> None:
    edit = '{"operations":[{"type":"artboard.edit_html","artboardId":"a","old":"<b>","new":"<i>'
    assert partial_target(edit) == (None, None)


def test_partial_target_without_an_id_yet_still_yields_the_html() -> None:
    assert partial_target('{"operations":[{"html":"<a>') == (None, "<a>")
    assert partial_target("") == (None, None)
