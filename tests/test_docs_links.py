"""Every relative link in the root documents points at something that exists.

A broken link in `README.md` or `GOVERNANCE.md` is the cheapest possible mistake and one of the more
expensive ones to leave standing: these are the files a stranger reads first, and a 404 from the
governance document is read as "nobody maintains this", not as a typo.

Scope is deliberately narrow: relative links in the root-level `*.md` files, plus the templates a
contributor is handed. Not `docs/**` (large, and its links are internal cross-references that move
together), and never external URLs — a test that reaches the network fails for reasons that have
nothing to do with the commit that ran it.

**What this does NOT catch, said plainly.** The bug that prompted it was `GOVERNANCE.md` citing a
root ``PREREGISTRATION.md`` that has never existed — the real files live per suite, at
``bench/*/PREREGISTRATION.md``. That reference was *inline code*, not a link, so this test would have
missed it. Extending to inline code was tried and rejected: a survey of the 25 inline ``.md``
mentions in these documents found most of them are either families of files that legitimately exist
per folder (``RESULTS.md``, ``PREREGISTRATION.md``, ``DESIGN.md``) or illustrative example filenames
in prose (``planner.v2.md`` in the changelog, naming a shape rather than a file). A check over those
would cry wolf, and a test that cries wolf gets muted, which is worse than not having it.

So the invariant here is the narrow, honest one: **a link promises navigability, and this holds that
promise.** A filename mentioned in prose promises nothing, and is still on the reader.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

#: `[text](target)` — captures the target only. Skips images (`![...]`) via the negative lookbehind.
_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)]+)\)")

#: The files a newcomer actually lands on, plus the templates they are handed on the way in.
_DOCS = sorted(ROOT.glob("*.md")) + sorted((ROOT / ".github").rglob("*.md"))


def _is_external(target: str) -> bool:
    return target.startswith(("http://", "https://", "mailto:", "#"))


def _relative_targets(text: str) -> list[str]:
    out = []
    for raw in _LINK.findall(text):
        target = raw.split()[0].strip("<>")  # drop an optional "title" and angle-bracket wrapping
        if _is_external(target):
            continue
        out.append(target.split("#", 1)[0])  # a heading anchor is not a path
    return [t for t in out if t]


@pytest.mark.parametrize("doc", _DOCS, ids=lambda p: str(p.relative_to(ROOT)).replace("\\", "/"))
def test_relative_links_resolve(doc: Path) -> None:
    text = doc.read_text(encoding="utf-8")
    broken = []
    for target in _relative_targets(text):
        # Resolve from the document's own directory, which is how a Markdown renderer reads it —
        # resolving from the repo root would silently pass a link that is broken on GitHub.
        if not (doc.parent / target).exists():
            broken.append(target)
    assert not broken, f"{doc.relative_to(ROOT)} links to missing paths: {broken}"


def test_the_check_actually_looked_at_something() -> None:
    """A parametrized test over an empty glob passes while checking nothing.

    Without this, a refactor that moved the docs (or broke `_DOCS`) would turn the whole file into a
    green no-op, which is worse than not having it: it reports safety it is no longer providing.
    """
    assert len(_DOCS) >= 10, f"expected the root docs to be found, got {len(_DOCS)}"
    linked = sum(len(_relative_targets(d.read_text(encoding="utf-8"))) for d in _DOCS)
    assert linked >= 20, f"expected relative links to check, found {linked}"
