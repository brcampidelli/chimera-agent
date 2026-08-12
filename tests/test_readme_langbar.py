"""The READMEs agree with each other, and with the app, on which languages exist.

Translation is the friendliest contribution this project has — no code, no toolchain, and someone who
speaks the language is instantly more qualified than the maintainer. But N files carrying the same
navigation bar is an N-way consistency problem maintained entirely by hand, and the way it fails is
petty and invisible: another language is added, one file is missed, and its readers can never reach
the new translation because the only link to it is the bar they are not looking at.

**Scope, deliberately.** This checks the *bar*, not the prose. Requiring the translations to match
the English in structure or length would repeat, in prose, exactly the mistake the i18n gate made in
code: it would block every English-only edit until eight translators were found, which is how you end
up with nobody editing anything. The READMEs are allowed to drift in content. They are not allowed to
disagree about what exists.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
READMES = sorted(ROOT.glob("README*.md"))
_IDS = [p.name for p in READMES]

#: The bar is one `<sub>` line naming every language; the current file is bold, the others link.
_BAR = re.compile(r"<sub>(?:<b>|<a href=)[^\n]*</sub>")
_LINK = re.compile(r'<a href="(README[^"]*\.md)">([^<]+)</a>')
_SELF = re.compile(r"<b>([^<]+)</b>")


def _bar(path: Path) -> str:
    m = _BAR.search(path.read_text(encoding="utf-8"))
    assert m, f"{path.name} has no language bar — every README carries one so readers can switch"
    return m.group(0)


#: README suffix -> the app's UI language code, where the two deliberately differ. The READMEs use
#: the regional tags people search for; the app's picker uses the bare subtag.
_SUFFIX_TO_LANG = {"": "en", "pt-BR": "pt", "zh-CN": "zh"}


def test_the_app_and_the_readmes_offer_the_same_languages() -> None:
    """The set of translations is defined once, in the app's `LANGS`, and the READMEs must match it.

    This replaces a hardcoded count. `assert len(READMES) == 9` caught a missing file, but it was a
    magic number somebody had to remember to bump — the same class of manual step as the bar itself,
    and it fails *after* the fact, saying only that a number changed. Deriving the expectation from
    `LANGS` says which language is missing which half, and needs no edit when a tenth arrives.

    Read as text: `LANGS` lives in a TypeScript file pytest cannot import, and re-listing the
    languages in Python would create a second list to forget. If that table is ever restructured this
    fails loudly, which is the right failure — better than matching nothing and passing forever.
    """
    source = (ROOT / "apps" / "desktop" / "src" / "lib" / "i18n.tsx").read_text(encoding="utf-8")
    table = re.search(r"export const LANGS = \[(.*?)\] as const;", source, re.S)
    assert table, "could not find the LANGS table — has i18n.tsx been restructured?"
    app = set(re.findall(r'code:\s*"(\w+)"', table.group(1)))
    assert len(app) > 1, "parsed LANGS as almost empty — the regex has gone stale"

    readmes = set()
    for path in READMES:
        suffix = path.name[len("README") : -len(".md")].lstrip(".")
        assert suffix in _SUFFIX_TO_LANG or suffix in app, (
            f"{path.name} maps to no UI language — add it to _SUFFIX_TO_LANG if the filename "
            "deliberately differs from the language code"
        )
        readmes.add(_SUFFIX_TO_LANG.get(suffix, suffix))

    assert app == readmes, (
        f"the app offers {sorted(app - readmes)} with no README, and "
        f"{sorted(readmes - app)} has a README the app cannot be switched to"
    )


@pytest.mark.parametrize("path", READMES, ids=_IDS)
def test_bar_names_every_language_exactly_once(path: Path) -> None:
    bar = _bar(path)
    linked = {p for p, _ in _LINK.findall(bar)}
    bolded = _SELF.findall(bar)

    assert len(bolded) == 1, f"{path.name}: exactly one language is the current one, got {bolded}"
    # The file links to all the others and never to itself — a self-link is a dead click.
    assert path.name not in linked, f"{path.name} links to itself in its own bar"
    expected = {p.name for p in READMES} - {path.name}
    assert linked == expected, (
        f"{path.name}: bar links {sorted(linked)}, expected the other {len(expected)}"
    )


@pytest.mark.parametrize("path", READMES, ids=_IDS)
def test_bar_uses_the_same_endonyms_everywhere(path: Path) -> None:
    """`Português`, not `Portuguese`. And the *same* spelling in every file.

    Someone looking for their language scans for the word they use for it. Inconsistent labels across
    files means the scan works on some pages and not others, which is worse than a uniform exonym.
    """
    expected = {
        "README.md": "English",
        "README.pt-BR.md": "Português",
        "README.es.md": "Español",
        "README.de.md": "Deutsch",
        "README.fr.md": "Français",
        "README.it.md": "Italiano",
        "README.pl.md": "Polski",
        "README.zh-CN.md": "中文",
        "README.ja.md": "日本語",
        "README.ru.md": "Русский",
    }
    bar = _bar(path)
    labels = dict(
        [(f, label.strip()) for f, label in _LINK.findall(bar)]
        + [(path.name, _SELF.findall(bar)[0].strip())]
    )
    assert labels == expected, f"{path.name}: language names differ from the canonical endonyms"


def test_every_linked_translation_exists() -> None:
    """The bar is the only navigation between translations; a broken entry orphans a language."""
    missing = []
    for path in READMES:
        for target, _ in _LINK.findall(_bar(path)):
            if not (ROOT / target).exists():
                missing.append(f"{path.name} -> {target}")
    assert not missing, f"language bar links to missing files: {missing}"
