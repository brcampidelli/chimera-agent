"""A translated page says which English it was made from, and nothing was checking that it still is.

Every file under `docs/i18n/<lang>/` carries `source_sha256` in its front matter: the hash of the
English page it was translated from. When the English moves and the translation does not, the hash
stops matching and the file becomes a page that *looks* current in nine languages and is not. The
mechanism has been there since the translations were first written; what did not exist is anything
that notices. `tests/test_skill_translations.py` says the same sentence about the skill sidecar and
was written because that sidecar went stale twice — this is the same failure one directory over,
and it was found the same way: by reading files, not by a failing build.

**Measured 2026-09-08, before this file existed: 53 of 99 translated pages were stale**, six pages
in every one of the nine languages. All of them were refreshed the same day, so `KNOWN_STALE` is
empty and every page is checked. The ratchet stays, because the backlog is what it is for:

- A page in :data:`KNOWN_STALE` is allowed to be stale. The list is dated, and it may only shrink.
- **Any other drift fails.** A page that leaves the list can never quietly return to it, and a page
  that has never been on it fails the moment the English moves without its translations.
- A page whose translations are all current but is still listed also fails, with "remove it from
  KNOWN_STALE" — an allowlist that outlives its reason is how the next backlog starts.

What this does NOT check: whether a translation is any *good*, or whether it is complete. A file can
carry the right hash and translate three sentences of nine. Only a reader can catch that; the hash
catches the cheaper mistake, which is the one that actually happened nine times.
"""

from __future__ import annotations

import hashlib
import pathlib

import pytest

DOCS = pathlib.Path(__file__).resolve().parents[1] / "docs"
I18N = DOCS / "i18n"

#: Pages whose translations were already stale when this guard was written (2026-09-08), with the
#: number of languages behind at that moment. Entries may be REMOVED as translations are refreshed;
#: adding one is what this test exists to prevent.
KNOWN_STALE: frozenset[str] = frozenset()
"""Empty since 2026-09-08, and the shape of the list is kept for the day it is needed again.

It held five pages for the length of one afternoon. The backlog it named — 53 of 99 translated
pages carrying the hash of an English they no longer matched — is gone, so every page is now
checked without exception, and adding an entry here is a decision someone has to defend in a
review rather than a default."""


def _front_matter_hash(path: pathlib.Path) -> str | None:
    """The `source_sha256` a translation declares, or None when it declares none.

    Read from the first few lines rather than parsed as YAML: the front matter is two keys deep and
    a parser dependency here would be the only reason this test could fail for something other than
    the thing it measures.
    """
    for line in path.read_text(encoding="utf-8").splitlines()[:8]:
        if line.startswith("source_sha256:"):
            return line.split(":", 1)[1].strip() or None
    return None


def _languages() -> list[str]:
    return sorted(p.name for p in I18N.iterdir() if p.is_dir())


def _pages() -> list[str]:
    langs = _languages()
    assert langs, "docs/i18n has no language directories"
    return sorted(p.name for p in (I18N / langs[0]).glob("*.md"))


def _behind(page: str) -> list[str]:
    """Languages whose translation of `page` does not declare the current English hash."""
    english = DOCS / page
    if not english.exists():
        return []
    current = hashlib.sha256(english.read_bytes()).hexdigest()
    out = []
    for lang in _languages():
        translated = I18N / lang / page
        if not translated.exists():
            out.append(f"{lang} (missing)")
        elif _front_matter_hash(translated) != current:
            out.append(lang)
    return out


@pytest.mark.parametrize("page", _pages())
def test_a_translation_declares_the_english_it_was_made_from(page: str) -> None:
    behind = _behind(page)
    if page in KNOWN_STALE:
        assert behind, (
            f"docs/{page} is listed in KNOWN_STALE but every translation is current. "
            "Remove it from the list — an allowlist that outlives its reason is how the next "
            "backlog starts."
        )
        return
    assert not behind, (
        f"docs/{page} changed and {len(behind)} translation(s) still declare the old "
        f"source_sha256: {', '.join(behind)}. Update the translation and its front-matter hash "
        "(sha256 of the English file), or — if the change was cosmetic — say so in the PR and "
        "refresh the hash. Do NOT add the page to KNOWN_STALE: that list only shrinks."
    )


def test_every_translated_page_has_an_english_original() -> None:
    orphans = [
        f"{lang}/{page}"
        for lang in _languages()
        for page in (p.name for p in (I18N / lang).glob("*.md"))
        if not (DOCS / page).exists()
    ]
    assert not orphans, f"translated pages with no English original: {orphans}"


def test_every_translation_declares_a_hash() -> None:
    """A missing hash is worse than a stale one: nothing can ever detect the drift."""
    missing = [
        f"{lang}/{page}"
        for lang in _languages()
        for page in _pages()
        if (I18N / lang / page).exists() and _front_matter_hash(I18N / lang / page) is None
    ]
    assert not missing, f"translations with no source_sha256: {missing}"


def test_the_known_stale_list_only_names_real_pages() -> None:
    """A typo in the allowlist would silently exempt nothing and hide a real page's drift."""
    unknown = sorted(KNOWN_STALE - set(_pages()))
    assert not unknown, f"KNOWN_STALE names pages that do not exist: {unknown}"
