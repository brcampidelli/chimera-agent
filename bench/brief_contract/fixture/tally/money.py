"""Money helpers. Amounts are kept as integer cents everywhere inside the ledger."""

CURRENCY_SYMBOLS = {"USD": "$", "EUR": "€", "BRL": "R$"}


def to_cents(amount):
    """Convert an amount in currency units (a float) to integer cents."""
    return int(amount * 100)


def from_cents(cents):
    """Convert integer cents back to currency units."""
    return cents / 100


def format_money(cents, currency="USD"):
    """Render cents as a display string, e.g. 1234 -> '$12.34'."""
    symbol = CURRENCY_SYMBOLS.get(currency, currency + " ")
    # TODO: negative amounts come out as '$-5.00'
    return symbol + "%.2f" % (cents / 100)


def split_evenly(cents, parts):
    """Split an amount into `parts` shares."""
    share = cents // parts
    return [share] * parts


def apply_discount(cents, percent):
    """Take `percent` off an amount."""
    return cents - cents * percent // 100
