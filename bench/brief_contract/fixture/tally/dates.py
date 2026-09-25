"""Date helpers for ledger entries. Dates are stored as ISO strings (YYYY-MM-DD)."""

import datetime

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def parse_date(text):
    """Parse an ISO date string into a date."""
    return datetime.datetime.strptime(text, "%Y-%m-%d").date()


def month_name(month):
    """English name of a month number (1-12)."""
    return MONTHS[month]


def days_between(start, end):
    """Number of days from `start` to `end` (both ISO strings)."""
    return (parse_date(end) - parse_date(start)).days


def is_weekend(text):
    """Whether an ISO date falls on a Saturday or a Sunday."""
    return parse_date(text).weekday() > 5
