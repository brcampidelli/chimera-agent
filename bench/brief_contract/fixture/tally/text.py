"""Text helpers for descriptions and file names."""

import re


def slugify(title):
    """Turn a title into a lowercase, dash-separated slug."""
    slug = title.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug


def truncate(text, width):
    """Shorten `text` to `width` characters, marking the cut with '...'."""
    if len(text) <= width:
        return text
    return text[:width] + "..."


def title_case(text):
    """Capitalise every word."""
    return " ".join(word.capitalize() for word in text.split(" "))
