"""A link in a translated page resolves from where the translation lives, not from where the English does.

`tests/test_docs_links.py` holds the root documents to the promise a link makes — navigability — and
scopes itself away from `docs/**` on purpose: those links are "internal cross-references that move
together", so a page and the links into it are edited in the same commit, and a checker over them
would mostly repeat what the author already knows. That reasoning is right for the English pages and
wrong for the translations, which is why this is a sibling file with its own scope rather than a
second parametrize bolted onto the first.

A translation does not move together with anything. It is produced by copying the English page into
`docs/i18n/<lang>/` — two directories deeper — and the relative links come along **verbatim**:
`../bench/swe_bench/RESULTS.md` is correct from `docs/` and points at nothing from `docs/i18n/pt/`.
Nothing in the writing process notices, because the target is a real file and the text reads well;
only a renderer resolving the path from the file's own directory sees the 404.

**Measured 2026-09-08, before this file existed: 63 broken relative links across 45 of the 99
translated pages, and 0 in the English ones** — seven distinct targets, each copied into all nine
languages: `../bench/swe_bench/RESULTS.md` (benchmarks, usage), `../bench/local_lift/RESULTS.md`
(recipes), `../chimera/evolution` and `../examples` (extending), `audits/sleeper-channels.md` and
`commands.md` (index). The last is the odd one: `commands.md` is the one page that is not
translated, so the correct target from a translation is the English page, `../../commands.md`.

The invariant: **every relative link and image target in `docs/**/*.md` resolves from the directory
of the file that carries it.** Resolution is lexical and case-exact — this suite also runs on a
Windows box, where `Path.exists()` would accept `../Bench/RESULTS.md` and GitHub would not.

What this deliberately does NOT check, said plainly:

- **Heading anchors.** `page.md#section` is checked as `page.md`; whether `#section` exists is still
  on the reader. A `?query` is dropped the same way.
- **External URLs** (`http://`, `https://`, `mailto:`, anything with `://`) and bare `#anchors`. A
  test that reaches the network fails for reasons that have nothing to do with the commit that ran it.
- **A `](...)` inside a fenced code block.** A renderer draws no link there, so it promises nothing.
  At the time of writing there were none in `docs/**`, so this exclusion currently excludes nothing.
- **Whether the target is the *intended* file.** A link to an existing wrong page passes.

Unlike the root test, images (`![alt](path)`) are held to the same promise: a missing image is a
broken box on the page, which is the same failure in a different shape.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

#: Every `](...)` — links and images alike. Only the target is captured; the text is not looked at.
_TARGET = re.compile(r"\]\(([^)]*)\)")
_FENCE = re.compile(r"^\s*(```|~~~)")


def _relative_targets(text: str) -> Iterator[tuple[int, str]]:
    """(line number, path) for every relative link or image target outside a fenced code block.

    Anchors and queries are stripped, external URLs and bare anchors are skipped, and an optional
    "title" or angle-bracket wrapping is dropped the way a renderer drops it.
    """
    in_fence = False
    marker = None
    for lineno, line in enumerate(text.splitlines(), 1):
        fence = _FENCE.match(line)
        if fence:
            if not in_fence:
                in_fence, marker = True, fence.group(1)
            elif fence.group(1) == marker:
                in_fence, marker = False, None
            continue
        if in_fence:
            continue
        for raw in _TARGET.findall(line):
            raw = raw.strip()
            if raw.startswith("<"):
                target = raw[1 : raw.find(">")] if ">" in raw else raw[1:]
            else:
                target = raw.split()[0] if raw.split() else ""
            if not target or "://" in target:
                continue
            if target.lower().startswith(("mailto:", "tel:", "data:", "#")):
                continue
            path = target.split("#", 1)[0].split("?", 1)[0]
            if path:
                yield lineno, path


def _resolves(root: Path, directory: Path, target: str) -> bool:
    """Whether `target`, resolved from `directory`, names something that exists under `root`.

    Lexical (`normpath`) and case-exact: each component must appear in its parent's listing under
    exactly that spelling. `Path.exists()` on Windows would pass `../Bench/RESULTS.md`; GitHub, and
    the Linux runner, would not. A path that climbs out of `root` does not resolve.
    """
    joined = os.path.join(str(directory.relative_to(root)), target)
    rel = os.path.normpath(joined).replace("\\", "/")
    if rel == ".." or rel.startswith("../"):
        return False
    cur = root
    for part in rel.split("/"):
        if part in ("", "."):
            continue
        try:
            names = os.listdir(cur)
        except (NotADirectoryError, FileNotFoundError):
            return False
        if part not in names:
            return False
        cur = cur / part
    return True


def _broken(root: Path, doc: Path) -> list[str]:
    """One line per relative target in `doc` that does not resolve, naming the likely cause.

    The cause that has actually happened is named when it is the cause: a target that resolves from
    `docs/` but not from the file's own directory was copied verbatim from the English page, and the
    fix is the depth prefix that gets from this directory back up to `docs/`.
    """
    english_dir = root / "docs"
    out = []
    for lineno, target in _relative_targets(doc.read_text(encoding="utf-8")):
        if _resolves(root, doc.parent, target):
            continue
        hint = ""
        if doc.parent != english_dir and _resolves(root, english_dir, target):
            up = "../" * len(doc.parent.relative_to(english_dir).parts)
            hint = (
                f" — resolves from docs/, so it was copied verbatim from the English page; from "
                f"this directory it needs `{up}` in front"
            )
        out.append(f"line {lineno}: `{target}`{hint}")
    return out


_DOCS = sorted(DOCS.rglob("*.md"))


def _id(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


@pytest.mark.parametrize("doc", _DOCS, ids=_id)
def test_a_docs_link_resolves_from_the_file_that_carries_it(doc: Path) -> None:
    broken = _broken(ROOT, doc)
    assert not broken, (
        f"{_id(doc)} carries {len(broken)} link(s) that do not resolve from its own directory:\n  "
        + "\n  ".join(broken)
    )


def test_the_check_actually_looked_at_something() -> None:
    """A parametrized test over an empty glob passes while checking nothing.

    Measured 2026-09-08: 112 files and 231 relative targets. The floors are about half of that, so
    dropping a language does not trip them and an emptied or moved `docs/` does.
    """
    assert len(_DOCS) >= 60, f"expected docs/**/*.md to be found, got {len(_DOCS)}"
    linked = sum(1 for d in _DOCS for _ in _relative_targets(d.read_text(encoding="utf-8")))
    assert linked >= 120, f"expected relative links to check, found {linked}"


def test_the_check_names_the_link_a_translation_copied_from_the_english(tmp_path: Path) -> None:
    """The exact shape of the 63: the English path, verbatim, two directories deeper.

    Run against a synthetic tree so the guard proves itself live in every run, not only by the
    sabotage that was run once when it was written.
    """
    (tmp_path / "bench" / "swe_bench").mkdir(parents=True)
    (tmp_path / "bench" / "swe_bench" / "RESULTS.md").write_text("# r\n", encoding="utf-8")
    (tmp_path / "docs" / "i18n" / "pt").mkdir(parents=True)
    (tmp_path / "docs" / "commands.md").write_text("# c\n", encoding="utf-8")
    english = tmp_path / "docs" / "benchmarks.md"
    english.write_text("[r](../bench/swe_bench/RESULTS.md) [c](commands.md)\n", encoding="utf-8")
    copied = tmp_path / "docs" / "i18n" / "pt" / "benchmarks.md"
    copied.write_text(english.read_text(encoding="utf-8"), encoding="utf-8")
    fixed = tmp_path / "docs" / "i18n" / "pt" / "usage.md"
    fixed.write_text(
        "[r](../../../bench/swe_bench/RESULTS.md) [c](../../commands.md)\n", encoding="utf-8"
    )

    assert _broken(tmp_path, english) == []
    assert _broken(tmp_path, fixed) == []
    broken = _broken(tmp_path, copied)
    assert len(broken) == 2, broken
    assert "`../bench/swe_bench/RESULTS.md`" in broken[0] and "needs `../../`" in broken[0]
    assert "`commands.md`" in broken[1] and "needs `../../`" in broken[1]


def test_what_the_check_deliberately_ignores(tmp_path: Path) -> None:
    """Anchors, queries, external URLs and fenced code are not held to the promise; images and case are."""
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "page.md").write_text("# p\n", encoding="utf-8")
    (docs / "Shot.png").write_bytes(b"")
    lines = [
        "[a](page.md#section) [q](page.md?raw=1) [x](https://example.org/missing.md)",
        "[m](mailto:a@b.c) [h](#top) [t](<page.md> 'a title') [d](.)",
        "```",
        "[inside a fence](nowhere.md)",
        "```",
        "![image](Shot.png) ![wrong case](shot.png) [gone](missing.md)",
    ]
    (docs / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    broken = _broken(tmp_path, docs / "index.md")
    assert [b.split("`")[1] for b in broken] == ["shot.png", "missing.md"], broken
