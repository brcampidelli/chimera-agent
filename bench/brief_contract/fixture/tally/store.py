"""A ledger kept as a JSON list of entries in one file."""

import json


def load(path):
    """Read all entries from `path`."""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save(path, entries):
    """Write all entries to `path`."""
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(entries, fh, indent=2)


def add_entry(entries, date, description, cents, category="misc"):
    """Append a new entry and return it."""
    entry = {"date": date, "description": description, "cents": cents, "category": category}
    entries.append(entry)
    return entry


def total(entries):
    """Sum of all entry amounts, in cents."""
    return sum(e["cents"] for e in entries)


def by_category(entries):
    """Totals per category, in cents."""
    out = {}
    for e in entries:
        out[e["category"]] = out.get(e["category"], 0) + e["cents"]
    return out
