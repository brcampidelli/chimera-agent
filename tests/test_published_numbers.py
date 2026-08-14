"""The published numbers must agree with the runs that produced them — in every language.

This exists because they did not. `docs/benchmarks.md` sat two rounds behind `bench/swe_bench/`
(its table stopped at run 2 while runs 3 and 4 were published), promised a Terminal-Bench result of
`Y ≫ X` that the actual run had refuted, and both faults were replicated across nine translations.
The whole suite was green throughout: nothing tied the page to the bench.

Text presence is deliberately not what is checked. A number can survive in a file that has since
grown a newer, different run — so each figure is matched against the ROW that claims to be its
source, and the translations are matched against the English page they are translations OF.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LANGS = ("de", "es", "fr", "it", "ja", "pl", "pt", "ru", "zh")


def _rows(markdown: str) -> list[list[str]]:
    """Every pipe-table row in a document, as trimmed cells. Separators and headers dropped."""
    found: list[list[str]] = []
    for line in markdown.splitlines():
        line = line.strip()
        if not line.startswith("|") or set(line) <= set("|- "):
            continue
        found.append([cell.strip() for cell in line.strip("|").split("|")])
    return found


def _numbers(text: str) -> set[str]:
    """Percentages in a locale-independent form.

    Translations write `42.1%` or `42,1 %` for the same figure, so the decimal mark and the space
    before the sign are normalised away — otherwise this test would fail on punctuation and teach
    people to delete it. Signs are kept: `+9.8` and `-9.8` are different claims.
    """
    normalised = text.replace(",", ".").replace("−", "-").replace(" %", "%").replace(" %", "%")
    return {m.group(0) for m in re.finditer(r"[+-]?\d+\.\d+%", normalised)}


def _section(markdown: str, start: str, stop: str = "\n## ") -> str:
    """The slice of a document from the first heading containing `start` to the next `stop`.

    Stops at the next `##`, not the next `###`: the table and the discussion live under sub-headings
    INSIDE the section, and cutting at the first one would leave only the intro paragraph — which
    contains no figures at all, so every check over it would pass by finding nothing.
    """
    at = markdown.find(start)
    assert at != -1, f"section {start!r} not found"
    end = markdown.find(stop, at + len(start))
    return markdown[at : end if end != -1 else len(markdown)]


@pytest.fixture(scope="module")
def swe_results() -> str:
    return (ROOT / "bench" / "swe_bench" / "RESULTS.md").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def docs_en() -> str:
    return (ROOT / "docs" / "benchmarks.md").read_text(encoding="utf-8")


def test_the_snapshot_constants_match_the_run_they_cite(swe_results: str) -> None:
    """`_SWE_BENCH` names the out-of-sample replication; its numbers must be that row's numbers.

    The constants are typed by hand into a `.py`. Re-running the bench updates the RESULTS and
    leaves the Python untouched, which is exactly the drift nobody notices — the app keeps serving
    the previous run's figures under the new run's name.
    """
    from chimera.eval.benchmark_snapshot import _SWE_BENCH

    row = next(r for r in _rows(swe_results) if r[0].strip("*").startswith("3 (replication)"))
    _, _, baseline, treatment, delta, ci, _ = row

    assert f"{_SWE_BENCH['baseline_rate']:.1%}" in baseline
    assert f"{_SWE_BENCH['treatment_rate']:.1%}" in treatment
    assert f"{_SWE_BENCH['delta']:+.1%}" in delta.replace("*", "")
    low, high = _SWE_BENCH["ci"]
    assert f"{low:+.1%}".replace("+", "").replace("-", "−") in ci.replace("*", "")
    assert f"{high:+.1%}" in ci.replace("*", "")
    assert _SWE_BENCH["n"] == 41


def test_the_decomposition_matches_the_fourth_run(swe_results: str) -> None:
    # The three-arm split is the part that contradicted our own registered prediction, so it is the
    # part most worth pinning: a later edit that quietly improves it would erase a retraction.
    from chimera.eval.benchmark_snapshot import _SWE_BENCH

    arms = {a["arm"]: a for a in _SWE_BENCH["decomposition"]}
    row = next(r for r in _rows(swe_results) if r[0].strip("*").startswith("4 (attribution)"))

    assert f"{arms['baseline']['rate']:.1%}" in row[2]
    assert f"{arms['scaffold']['rate']:.1%}" in row[3]
    assert arms["scaffold+diff-gate"]["rate"] == pytest.approx(0.439)
    # Precision is the mechanism the write-up claims: it climbs while the patch rate does not.
    assert [a["precision_when_edited"] for a in _SWE_BENCH["decomposition"]] == [0.50, 0.59, 0.67]


def _run_rows(markdown: str) -> dict[str, set[str]]:
    """Result rows, as `label -> the figures in that row`.

    Two things this deliberately does not do. It does not identify a row by its LABEL: an earlier
    version matched `"3"` against the page text and passed with the row deleted, because `3` occurs
    inside every percentage. And it does not parse the label at all — "pooled" is translated in nine
    languages, Japanese writes `3(再現)` with no space, Chinese uses a full-width `（`, so any pattern
    over the label fails on punctuation in exactly the languages least likely to be re-read.

    A result row is one carrying two or more figures. Headers and separators carry none.
    """
    out: dict[str, set[str]] = {}
    for row in _rows(markdown):
        figures = _numbers(" ".join(row[1:]))
        if len(figures) >= 2:
            out[row[0].strip("*").strip() or f"row {len(out) + 1}"] = figures
    return out


def test_the_published_page_shows_every_run_the_bench_published(docs_en: str, swe_results: str) -> None:
    """The exact failure this file was written for.

    `docs/benchmarks.md` is what the site republishes. When the bench gains a run, the page has to
    gain it too — otherwise the project's public claim is a stale subset of its own evidence, and
    the missing rows are never the flattering ones by accident.
    """
    page_rows = list(_run_rows(_section(docs_en, "## SWE-bench Verified")).values())
    missing = [
        label
        for label, figures in _run_rows(swe_results).items()
        if not any(figures <= published for published in page_rows)
    ]

    assert not missing, f"docs/benchmarks.md is missing SWE-bench runs the bench published: {missing}"


def test_the_page_makes_no_promise_its_own_result_refuted(docs_en: str) -> None:
    """Terminal-Bench came out NEGATIVE. The page used to end its setup by promising `Y ≫ X`.

    The phrase is allowed to survive as a quotation of what was retracted — that is the honest way
    to correct it — but not as a live claim, so it must appear alongside the retraction.
    """
    if "Y ≫ X" in docs_en:
        at = docs_en.find("Y ≫ X")
        context = docs_en[max(0, at - 400) : at + 400]
        assert "came out" in context or "refuted" in context, (
            "docs/benchmarks.md still promises Y ≫ X as a live claim; the measured result was 7.5% → 2.5%"
        )


@pytest.mark.parametrize("lang", LANGS)
def test_each_translation_carries_the_same_figures_as_the_source(lang: str, docs_en: str) -> None:
    """A translation left behind is the same false claim, in one more language.

    Nine of them shipped a table two rounds stale. The comparison is per TABLE ROW, not over the
    section as a whole: the prose around the table repeats the same figures, so a section-wide check
    stayed green with a row deleted — which is how the first version of this test proved nothing.
    Numbers are normalised across locales (`42,1 %` and `42.1%` are one figure) so it fails on stale
    content, never on punctuation.
    """
    translated = (ROOT / "docs" / "i18n" / lang / "benchmarks.md").read_text(encoding="utf-8")
    heading = next(
        line for line in translated.splitlines() if line.startswith("## ") and "SWE-bench" in line
    )
    translated_rows = list(_run_rows(_section(translated, heading)).values())

    missing = [
        label
        for label, figures in _run_rows(_section(docs_en, "## SWE-bench Verified")).items()
        if not any(figures <= row for row in translated_rows)
    ]

    assert not missing, f"docs/i18n/{lang}/benchmarks.md is missing published runs: {missing}"
