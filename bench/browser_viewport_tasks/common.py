"""What the site, the server, the pages' JavaScript and the checkers must agree on.

A page code is derived from a key by FNV-1a (32-bit) — the same arithmetic in Python here and in
the JavaScript the generator writes into the pages (`FNV_JS`), so a code a form prints in Chromium is
the code the checker expects. `test_the_python_and_javascript_codes_agree` in the dry-run compares
the two on the real browser rather than trusting that they match.
"""

from __future__ import annotations

import re

LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"  # no I or O: a code is read back by a model, not a scanner
CODE_RE = re.compile(r"\b[A-Z]{2}\d{4}[A-Z]\b")


def fnv1a(text: str) -> int:
    h = 0x811C9DC5
    for byte in text.encode("utf-8"):
        h ^= byte
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h


def code_for(key: str) -> str:
    """Seven characters, `AB1234C`: two letters, four digits, a letter."""
    h = fnv1a("m7|" + key)
    c1 = LETTERS[h % 24]
    h //= 24
    c2 = LETTERS[h % 24]
    h //= 24
    digits = h % 10000
    h //= 10000
    c3 = LETTERS[h % 24]
    return f"{c1}{c2}{digits:04d}{c3}"


# The same function in the page. ASCII keys only (the checkers never build another kind), where a
# UTF-16 code unit and a UTF-8 byte are the same number.
FNV_JS = r"""
function m7code(key) {
  const L = "ABCDEFGHJKLMNPQRSTUVWXYZ";
  const s = "m7|" + key;
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 0x01000193) >>> 0; }
  const c1 = L[h % 24]; h = Math.floor(h / 24);
  const c2 = L[h % 24]; h = Math.floor(h / 24);
  const d = String(h % 10000).padStart(4, "0"); h = Math.floor(h / 10000);
  return c1 + c2 + d + L[h % 24];
}
"""


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def go_key(site: str, slug: str) -> str:
    """The key of the page a link at `/go/<site>/<slug>` opens."""
    return f"go/{site}/{slug}"


def title_from_slug(slug: str) -> str:
    return " ".join(w.capitalize() for w in slug.split("-"))


def pick(key: str, n: int) -> int:
    """A deterministic choice in range(n) — the generator's only source of variety."""
    return fnv1a("pick|" + key) % n
