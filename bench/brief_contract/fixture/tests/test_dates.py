import datetime

from tally import dates


def test_parse_date():
    assert dates.parse_date("2026-09-25") == datetime.date(2026, 9, 25)


def test_days_between():
    assert dates.days_between("2026-09-01", "2026-09-25") == 24


def test_sunday_is_weekend():
    assert dates.is_weekend("2026-09-27")
