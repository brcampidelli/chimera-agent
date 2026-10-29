from __future__ import annotations

from chimera.design.html_source import tokens_css
from chimera.design.model import DesignTokens, TypographyToken

#: The exact bytes the desktop's canonical writer (H3) produces for the same tokens. Declarations are
#: ``--name: value`` (colon, one space) joined by ``;`` with no space, names sorted, colours untouched.
EXPECTED = (
    ":root{--ds-color-brand: #AABBCC;--ds-color-text: #112233;"
    '--ds-font-body-family: "Inter";--ds-font-body-size: 16px;--ds-font-body-weight: 400;'
    "--ds-font-body-line-height: 1.5;"
    "--ds-font-display-family: \"A'B\";--ds-font-display-size: 32px;--ds-font-display-weight: 700;"
    "--ds-space-lg: 24px;--ds-space-sm: 8px;--ds-radius-card: 8px;--ds-radius-pill: 999px}"
)


def _tokens() -> DesignTokens:
    return DesignTokens.model_validate({
        "colors": {"text": "#112233", "brand": "#AABBCC"},
        "typography": {
            # 16.0 is a float in the model; JavaScript's String(16.0) is "16".
            "body": {"family": "Inter", "size": 16.0, "weight": 400, "lineHeight": 1.5},
            "display": {"family": "A'B", "size": 32, "weight": 700},
        },
        "spacing": {"lg": 24, "sm": 8},
        "radii": {"pill": 999, "card": 8},
    })


def test_tokens_are_emitted_as_stable_css_variables() -> None:
    assert tokens_css(_tokens()) == EXPECTED


def test_the_colour_keeps_its_case_and_a_fractional_line_height_keeps_its_point() -> None:
    css = tokens_css(_tokens())
    assert "--ds-color-brand: #AABBCC;" in css
    assert "--ds-font-body-line-height: 1.5;" in css


def test_an_integral_float_is_written_without_a_decimal_point() -> None:
    css = tokens_css(_tokens())
    assert "--ds-font-body-size: 16px;" in css
    assert "16.0" not in css


def test_a_font_family_escapes_quote_backslash_and_line_break_as_css_does() -> None:
    # The model's pattern refuses these characters, so the token is built without validation to
    # reach the escape path that a directly-written token would take.
    token = TypographyToken.model_construct(family='a"b\\c\nd', size=1.0, weight=400, line_height=None)
    tokens = DesignTokens.model_construct(typography={"x": token})
    assert tokens_css(tokens) == (
        ':root{--ds-font-x-family: "a\\"b\\\\c\\a d";--ds-font-x-size: 1px;--ds-font-x-weight: 400}'
    )
