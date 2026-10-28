from __future__ import annotations

import pytest

from chimera.design.html_source import MAX_ARTBOARD_HTML, DesignHtmlError, validate_artboard_html

LANDING = '''<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;700&display=swap">
<style>body { display:grid; grid-template-columns: 1fr 2fr; gap: 1rem }
.hero { display:flex; align-items:center; background:linear-gradient(#fff,#eee) }</style>
</head><body><main class="hero" style="display:flex; gap: 12px"><img src="asset:hero_1" alt="Hero">
<section><h1>Launch</h1><p>Welcome <a href="details.html">read more</a></p></section></main></body></html>'''


@pytest.mark.parametrize("source", [LANDING, "<svg viewBox='0 0 10 10'><path d='M0 0'/></svg>",
                                    "<p style='color:red'>Hello</p>", "<meta charset='UTF-8'>"])
def test_accepts_allowlisted_html_and_css(source: str) -> None:
    validate_artboard_html(source)


@pytest.mark.parametrize(("source", "reason"), [
    ("<script>alert(1)</script>", "<script>"),
    ("<iframe src='https://example.com'></iframe>", "<iframe>"),
    ("<object></object>", "<object>"),
    ("<embed>", "<embed>"),
    ("<base href='https://example.com'>", "<base>"),
    ("<frame>", "<frame>"),
    ("<frameset></frameset>", "<frameset>"),
    ("<applet></applet>", "<applet>"),
    ("<meta http-equiv='refresh'>", "http-equiv"),
    ("<svg><foreignObject></foreignObject></svg>", "foreignobject"),
    ("<svg><use href='#x'/></svg>", "<use>"),
    ("<svg><image href='x'/></svg>", "<image>"),
    ("<svg><animate attributeName='x'/></svg>", "animate"),
    ("<svg><set attributeName='x'/></svg>", "<set>"),
    ("<template></template>", "template"),
    ("<slot></slot>", "slot"),
    ("<portal></portal>", "portal"),
    ("<noscript>no</noscript>", "noscript"),
    ("<blink>no</blink>", "allowlisted"),
    ("<div ONCLICK='x'>x</div>", "onclick"),
    ("<div srcdoc='x'></div>", "srcdoc"),
    ("<a href='javascript:alert(1)'>x</a>", "scheme"),
    ("<a href='JaVaScRiPt :alert(1)'>x</a>", "scheme"),
    ("<a href='jav&#x61;script:alert(1)'>x</a>", "scheme"),
    ("<a href='../secret.html'>x</a>", "href"),
    ("<a href='/secret.html'>x</a>", "href"),
    ("<img src='https://example.com/a.png'>", "asset"),
    ("<img src='asset:../x'>", "asset"),
    ("<div style='background:url(https://example.com/a.png)'>x</div>", "asset"),
    ("<style>@import url(https://example.com/x);</style>", "CSS"),
    ("<style>a { width: expression(alert(1)) }</style>", "CSS"),
    ("<style>a { behavior: url(x) }</style>", "CSS"),
    ("<style>a { -moz-binding: url(x) }</style>", "CSS"),
    ("<style>@font-face { font-family:x; src:url(https://example.com/x) }</style>", "asset"),
    ("<div style='background:url(data:text/html,x)'>x</div>", "scheme"),
    ("<!doctype svg>", "doctype"),
])
def test_refuses_every_non_allowlisted_construct(source: str, reason: str) -> None:
    with pytest.raises(DesignHtmlError, match=reason):
        validate_artboard_html(source)


def test_refusal_message_never_echoes_a_long_source() -> None:
    source = "<script>" + "secret" * 1000 + "</script>"
    with pytest.raises(DesignHtmlError) as caught:
        validate_artboard_html(source)
    assert len(str(caught.value)) < 80
    assert "secret" not in str(caught.value)


def test_refuses_source_over_size_limit() -> None:
    with pytest.raises(DesignHtmlError, match="exceeds"):
        validate_artboard_html(" " * (MAX_ARTBOARD_HTML + 1))


@pytest.mark.parametrize("source", [
    "<svg><style><script>alert(1)</script></style></svg>",
    "<svg><style><img src=x onerror=alert(1)></style></svg>",
    "<svg><title><img src=x onerror=alert(1)></title></svg>",
    "<style>a{}<img src=x></style>",
    "<title>a<img src=x onerror=alert(1)></title>",
    "<svg><text>x</text><title>t</title></svg>",
    "<text>outside svg</text>",
    "<svg><a href='x.html'>x</a></svg>",
])
def test_refuses_svg_foreign_content_and_markup_inside_style_or_title(source: str) -> None:
    with pytest.raises(DesignHtmlError):
        validate_artboard_html(source)


def test_a_self_closed_svg_does_not_leave_the_svg_counter_raised() -> None:
    validate_artboard_html("<svg/><p>after</p>")
    with pytest.raises(DesignHtmlError, match="only allowed inside"):
        validate_artboard_html("<svg/><text>x</text>")


def test_accepts_svg_gradient_with_lowercase_allowlist_names() -> None:
    validate_artboard_html('<svg><defs><linearGradient id="g"><stop offset="0"/></linearGradient>'
                           '</defs><rect fill="url(#g)"/></svg>')


@pytest.mark.parametrize("source", [
    "<p style=\"background:u\72l(http://e.com/x)\">",
    "<p style=\"background:\75 rl(http://e.com/x)\">",
    "<style>@\\69mport url(asset:x)</style>",
    "<p style=\"background:image-set('http://e.com/x.png' 1x)\">",
    "<p style=\"background:-webkit-image-set(url(http://e.com/x) 1x)\">",
    "<style>a{background:url(http://e.com/x</style>",
    "<p style=\"background:url(http://e.com/x\">",
    "<p style=\"background:ur/**/l(http://e.com/x)\">",
    "<p style=\"background:URL(http://e.com/x)\">",
    "<p style=\"cursor:url(http://e.com/c.cur),auto\">",
    "<style>@import 'x.css';</style>",
    "<style>@namespace svg url(x);</style>",
    "<style>@charset 'utf-8';</style>",
    "<style>a{x:/* open</style>",
])
def test_refuses_css_escapes_unbalanced_and_non_asset_urls(source: str) -> None:
    with pytest.raises(DesignHtmlError):
        validate_artboard_html(source)


@pytest.mark.parametrize("source", [
    '<p style="background:url(asset:hero_1)">x</p>',
    '<p style="color:oklch(0.7 0.1 200);width:calc(100% - 2rem)">x</p>',
    "<style>@media (max-width:600px){.x{display:grid;grid-template-columns:"
    "repeat(auto-fit,minmax(min(240px,100%),1fr))}}</style>",
    '<p style="font-family:Metadata, sans-serif">x</p>',
    "<p style='content:\"behavior: x\"'>x</p>",
    "<style>@font-face{font-family:x;src:url(asset:font_1) format(\"woff2\")}</style>",
    "<style>a{background:url('asset:hero_1')}</style>",
])
def test_accepts_the_css_the_allowlist_names(source: str) -> None:
    validate_artboard_html(source)


@pytest.mark.parametrize("source", [
    '<p style="behavior:x">x</p>',
    '<p style="-moz-binding:x">x</p>',
    '<p style="width:expression(1)">x</p>',
    '<style>@font-face{src:url(asset:a) url(asset:b)}</style>',
])
def test_refuses_banned_css_as_property_or_function(source: str) -> None:
    with pytest.raises(DesignHtmlError):
        validate_artboard_html(source)


@pytest.mark.parametrize("source", [
    '<svg><rect fill="url(http://e.com/a.svg#x)"/></svg>',
    '<svg><rect title="URL(x)"/></svg>',
    '<svg><rect title="url(http://e.com/a)"/></svg>',
])
def test_refuses_svg_paint_references_outside_the_document(source: str) -> None:
    with pytest.raises(DesignHtmlError, match="url"):
        validate_artboard_html(source)


def test_accepts_svg_paint_reference_to_the_document() -> None:
    validate_artboard_html('<svg><rect fill="url(#g)" stroke="none"/></svg>')


@pytest.mark.parametrize("source", [
    "<div>x</div><img src=\"asset:a\" onerror=alert(1)",
    "<script",
    '<a href="javascript:alert(1)',
    "<p>a</p><!-- open",
    "<p>a < b</p>",
])
def test_refuses_input_that_ends_inside_a_tag_or_leaves_a_stray_lt(source: str) -> None:
    with pytest.raises(DesignHtmlError):
        validate_artboard_html(source)


@pytest.mark.parametrize("source", ["", "   \n\t  "])
def test_refuses_empty_source_and_points_to_null(source: str) -> None:
    with pytest.raises(DesignHtmlError, match="use null to remove"):
        validate_artboard_html(source)


@pytest.mark.parametrize("href", ["%2e%2e/x.html", ".%2e/x.html", "..\\x.html", "..%2fa.html"])
def test_refuses_relative_hrefs_that_climb_out_of_the_folder(href: str) -> None:
    with pytest.raises(DesignHtmlError, match="href"):
        validate_artboard_html(f'<a href="{href}">x</a>')


@pytest.mark.parametrize("href", ["Cart.html", "sub/Cart.html", "#top"])
def test_accepts_plain_relative_hrefs(href: str) -> None:
    validate_artboard_html(f'<a href="{href}">x</a>')


def test_refusal_messages_cut_long_names_to_forty_characters() -> None:
    cases = [f"<{'t' * 3000}>x</p>", f"<div {'a' * 3000}='x'>x</div>"]
    for source in cases:
        with pytest.raises(DesignHtmlError) as caught:
            validate_artboard_html(source)
        assert len(str(caught.value)) < 120
        assert "a" * 41 not in str(caught.value) and "t" * 41 not in str(caught.value)


@pytest.mark.parametrize("source", [
    '<a href="x.html" target="_blank">x</a>',
    "<input type='file' name='f'>",
    '<img src="asset:a" srcset="asset:a 2x">',
    '<video poster="asset:a">x</video>',
    '<table background="asset:a"><tr><td>x</td></tr></table>',
    '<div background="asset:a">x</div>',
])
def test_refuses_attributes_outside_the_spec(source: str) -> None:
    with pytest.raises(DesignHtmlError):
        validate_artboard_html(source)


@pytest.mark.parametrize("source", [
    "<style>a{background:url(asset:a/*);background:url(http://e/x.png);/**/)}</style>",
    '<p style="background:url(asset:a/*);background:url(http://e/x.png);/**/)">x</p>',
    "<style>@font-face{src:url(asset:a/*);src:url(http://e/f.woff);/**/)}</style>",
])
def test_refuses_a_css_comment_that_would_hide_an_unquoted_url(source: str) -> None:
    with pytest.raises(DesignHtmlError, match="CSS comments are not allowed"):
        validate_artboard_html(source)


@pytest.mark.parametrize("href", [' //evil.com/x.html', '&#9;//evil.com/x.html', '//evil.com/x.html'])
def test_refuses_protocol_relative_and_control_prefixed_hrefs(href: str) -> None:
    with pytest.raises(DesignHtmlError, match="href"):
        validate_artboard_html(f'<a href="{href}">x</a>')


def test_stray_less_than_says_how_to_write_it() -> None:
    with pytest.raises(DesignHtmlError) as caught:
        validate_artboard_html("<p>Preço < R$50</p>")
    assert "write &lt; for a literal less-than sign" in str(caught.value)
    assert len(str(caught.value)) <= 120


@pytest.mark.parametrize("source", [
    '<svg xmlns="http://evil"></svg>',
    '<svg xmlns:xlink="http://www.w3.org/1999/xlink"></svg>',
])
def test_refuses_svg_namespaces_other_than_svg(source: str) -> None:
    with pytest.raises(DesignHtmlError):
        validate_artboard_html(source)


def test_accepts_the_svg_namespace() -> None:
    validate_artboard_html('<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>')
