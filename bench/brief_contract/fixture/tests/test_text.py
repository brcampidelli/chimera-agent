from tally import text


def test_slugify():
    assert text.slugify("Hello World") == "hello-world"


def test_truncate_short_text_is_unchanged():
    assert text.truncate("short", 10) == "short"


def test_title_case():
    assert text.title_case("hello world") == "Hello World"
