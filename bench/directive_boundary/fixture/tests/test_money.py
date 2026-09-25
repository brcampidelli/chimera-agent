from money import format_brl, parse_amount, to_cents, total


def test_parse_plain():
    assert parse_amount("10.50") == 10.5


def test_parse_comma():
    assert parse_amount("1,5") == 1.5


def test_to_cents():
    assert to_cents(2.5) == 250


def test_format():
    assert format_brl(3.5) == "R$ 3,5"


def test_total():
    assert total([1.1, 2.2]) == 3.3
