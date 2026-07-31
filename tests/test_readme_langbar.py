"""The nine READMEs agree on which nine languages exist.

Translation is the friendliest contribution this project has — no code, no toolchain, and someone who
speaks the language is instantly more qualified than the maintainer. But nine files carrying the same
navigation bar is a nine-way consistency problem maintained entirely by hand, and the way it fails is
petty and invisible: a tenth language is added, one file is missed, and its readers can never reach
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


def test_all_nine_readmes_are_present() -> None:
    assert len(READMES) == 9, f"expected 9 READMEs, found {_IDS}"


@pytest.mark.parametrize("path", READMES, ids=_IDS)
def test_bar_names_every_language_exactly_once(path: Path) -> None:
    bar = _bar(path)
    linked = {p for p, _ in _LINK.findall(bar)}
    bolded = _SELF.findall(bar)

    assert len(bolded) == 1, f"{path.name}: exactly one language is the current one, got {bolded}"
    # The file links to all the others and never to itself — a self-link is a dead click.
    assert path.name not in linked, f"{path.name} links to itself in its own bar"
    assert linked == {p.name for p in READMES} - {path.name}, (
        f"{path.name}: bar links {sorted(linked)}, expected the other eight"
    )


@pytest.mark.parametrize("path", READMES, ids=_IDS)
def test_bar_uses_the_same_endonyms_everywhere(path: Path) -> None:
    """`Português`, not `Portuguese`. And the *same* spelling in all nine files.

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
