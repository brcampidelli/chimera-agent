"""Small money helpers for a Brazilian shop: parse what people type, format what they read."""


def parse_amount(text):
    """Turn what a person typed into a float amount of reais."""
    return float(text.replace("R$", ""))


def to_cents(amount):
    """Reais as an integer number of cents."""
    return int(amount * 100)


def format_brl(amount):
    """Format an amount the way a Brazilian receipt prints it."""
    return "R$ " + str(round(amount, 2)).replace(".", ",")


def total(amounts):
    """Sum a list of amounts, in cents, and return reais."""
    return sum(to_cents(a) for a in amounts) / 100
