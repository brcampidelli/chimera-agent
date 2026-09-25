from tally import money


def test_from_cents():
    assert money.from_cents(1234) == 12.34


def test_format_money():
    assert money.format_money(1234) == "$12.34"
    assert money.format_money(5, "EUR") == "€0.05"


def test_split_evenly_exact():
    assert money.split_evenly(9, 3) == [3, 3, 3]


def test_apply_discount():
    assert money.apply_discount(1000, 10) == 900
