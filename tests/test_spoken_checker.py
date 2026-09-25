"""The spoken-format checker of study 25's arm H9, pinned before it measured anything.

`bench/spoken_standard/checker.py` decides, deterministically, whether the part of an answer a voice
reads can be read aloud as written. Every rule it applies is held here from both sides — a text that
must be caught and a near miss that must not — because a checker that flags everything and one that
flags nothing both produce a clean-looking comparison. Held too:

* **It cuts where the reader cuts.** The desktop reader stops at a Markdown rule alone on its line
  (`screenPartStart` in `apps/desktop/src/lib/voice/speech-text.ts`); the cases below are that
  file's own tests, ported. Anything under the line is the screen's and is never judged.
* **An answer that starts with the line is not speakable.** Without `empty` it would pass with
  nothing spoken at all.
* **The first live failure is caught** — four paragraphs, two lists and a rocket, read aloud on
  2026-09-17 — and a plain answer in either language passes.
* **Arm B is the registered text.** Its hash is the one in `PREREGISTRATION.md`, it is no longer than
  the shipped note, it keeps the `---` convention, and it names none of the things it no longer
  forbids.
* **The statistics are the textbook's**: exact McNemar, Wilson, Newcombe's paired interval.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from bench.spoken_standard import run as h9  # noqa: E402
from bench.spoken_standard.checker import (  # noqa: E402
    ALL_KEYS,
    check,
    count_sentences,
    split_spoken,
)
from bench.spoken_standard.corpus import KINDS, corpus  # noqa: E402


def _shipped_note() -> str:
    """`SPOKEN_NOTE` read from the source without importing the API module (and its server stack)."""
    tree = ast.parse((REPO / "chimera" / "api" / "code_api.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "SPOKEN_NOTE":
            return ast.literal_eval(node.value)
    raise AssertionError("SPOKEN_NOTE not found")


def only(text: str) -> set[str]:
    """The kinds of violation in a text."""
    return {k for k, v in check(text).counts.items() if v}


# --- where the spoken part ends -----------------------------------------------------------------------


def test_cuts_at_the_line_the_desktop_reader_cuts_at() -> None:
    assert split_spoken("Gist here.\n\n---\n\n1. a\n2. b")[0] == "Gist here.\n\n"
    assert split_spoken("Gist here.\n***\nrest")[0] == "Gist here.\n"
    assert split_spoken("Gist here.\n___\nrest")[0] == "Gist here.\n"
    assert split_spoken("Gist here.\n   ----  \nrest")[0] == "Gist here.\n"
    assert split_spoken("No rule here --- inline") == ("No rule here --- inline", "", False)
    assert split_spoken("a\n--\nb")[2] is False
    assert split_spoken("a\n    ---\nb")[2] is False  # four spaces is code, not a rule


def test_nothing_under_the_line_is_judged() -> None:
    answer = (
        "There are three files to change, all in the API folder.\n\n---\n\n"
        "## Files\n\n- `chimera/api/code_api.py`\n- https://example.com/x/y\n\n```python\nx = 1\n```\n🚀"
    )
    result = check(answer)
    assert result.speakable
    assert result.has_rule and result.screen_nonempty


def test_an_answer_that_starts_with_the_line_is_not_speakable() -> None:
    result = check("---\nEverything is down here.")
    assert result.counts["empty"] == 1
    assert not result.speakable


# --- the two ends of the scale ------------------------------------------------------------------------


def test_the_first_live_failure_is_caught() -> None:
    incident = (
        "Sim, estou entendendo você perfeitamente! 🚀\n\n"
        "Aqui está o que posso fazer por você:\n\n"
        "- **Criar arquivos** e editar código\n"
        "- Rodar comandos no terminal\n"
        "- Pesquisar na web\n\n"
        "Também posso:\n\n"
        "1. Revisar seu projeto\n"
        "2. Explicar conceitos\n\n"
        "É só me dizer o que você precisa. Estou pronto para começar agora mesmo."
    )
    kinds = only(incident)
    assert {"list", "emphasis", "emoji", "sentences"} <= kinds
    assert not check(incident).speakable


@pytest.mark.parametrize(
    "text",
    [
        "Yes, I can hear you clearly. What would you like to work on?",
        "Estou te ouvindo, sim. O que você quer fazer agora?",
        "The command is find with a size filter of one hundred megabytes. I put the exact line on the "
        "screen.\n\n---\n\n```bash\nfind / -type f -size +100M\n```",
        "Node twenty two is the current long-term support release, and it is supported until April "
        "twenty twenty seven.",
    ],
)
def test_plain_spoken_answers_pass(text: str) -> None:
    result = check(text)
    assert result.speakable, result.counts


# --- each rule, from both sides -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("# Done\nIt works.", "heading"),
        ("Steps:\n- one\n- two", "list"),
        ("Steps:\n1. one\n2. two", "list"),
        ("Steps:\n* one", "list"),
        ("| a | b |\n|---|---|\n| 1 | 2 |", "table"),
        ("Here:\n```\nls -la\n```", "code_fence"),
        ("Run `npm install` first.", "inline_code"),
        ("This is **important**.", "emphasis"),
        ("This is *quite* important.", "emphasis"),
        ("This is _quite_ important.", "emphasis"),
        ("> quoted advice", "quote"),
        ("See [the docs](https://nextjs.org/docs).", "link"),
        ("Done 🚀", "emoji"),
        ("Done ✅", "emoji"),
        ("Careful ⚠️ here.", "emoji"),
        ("Open https://github.com/settings/tokens now.", "url"),
        ("Open www.python.org now.", "url"),
        ("Open github.com/settings/tokens now.", "url"),
        ("It is 4294967296.", "digits"),
        ("A year has 31536000 seconds.", "digits"),
        ("It shipped on 2008-12-03.", "digits"),
        ("Foi lançado em 03/12/2008.", "digits"),
        ("Update to 0.61.1 today.", "digits"),
        ("Update to v0.61.1 today.", "digits"),
        ("Use 192.168.0.1 as the gateway.", "digits"),
        ("The range is 192.168.255.255 at the top.", "digits"),
        ("The block is 10.0.0.0/8.", "digits"),
        ("The commit is e7ff732 on main.", "digits"),
        ("The id is 123e4567-e89b-12d3-a456-426614174000.", "digits"),
        ("Edit /etc/hosts and save.", "path"),
        ("Edit ~/.bashrc and save.", "path"),
        ("Edit ./run.sh and save.", "path"),
        ("It lives in C:\\Users\\bruno\\app.", "path"),
        ("Open src/app/page.tsx and save.", "path"),
        ("Open app/page.tsx and save.", "path"),
    ],
)
def test_each_rule_catches_its_case(text: str, kind: str) -> None:
    assert kind in only(text), check(text).counts


@pytest.mark.parametrize(
    "text",
    [
        "#1 is the priority. That is the plan.",  # a number sign, not a heading
        "It was -5 degrees this morning.",  # a minus, not a list marker
        "Use my_variable_name in the loop.",  # snake case, not italics
        "Two times three times four is 2*3*4.",  # arithmetic, not emphasis
        "Five * three is fifteen.",
        "Isso é ação — não é → nem … nem ©.",  # accents, dashes, arrows, ellipsis, ©
        "Python.org and supabase.com are the sites.",  # a bare domain is speakable
        "It works with Next.js/React out of the box.",  # js is not a top-level domain here
        "It costs 1.000.000 reais, or 4,294,967,296 in total.",  # thousands-grouped numbers
        "Node 22 came out in 2024 and uses port 8000, 5432 or 27017.",  # short numbers and years
        "Meet at 10:30 on 25/09.",  # two groups
        "The decade was added later.",  # hex letters without digits
        "Use and/or, e/ou, km/h, TCP/IP, 24/7 and package.json freely.",  # not paths
        "Put it in node_modules/ or src/ later.",  # a trailing separator only
    ],
)
def test_near_misses_are_not_caught(text: str) -> None:
    assert check(text).speakable, check(text).counts


# --- sentences ----------------------------------------------------------------------------------------


def test_sentences_are_counted_the_way_they_sound() -> None:
    assert count_sentences("") == 0
    assert count_sentences("Done") == 1
    assert count_sentences("One. Two! Three? Four… five.") == 5
    assert count_sentences("Pi is 3.14 and e is 2.72.") == 1  # a decimal is not a sentence end
    assert count_sentences("Files:\n- a\n- b\n- c") == 4  # every read line is at least one
    assert count_sentences('He said "stop." Then left.') == 2  # a closing quote after the stop
    assert count_sentences("One.\n\n---") == 1  # a line with no letter or digit is not read


def test_four_sentences_pass_and_five_do_not() -> None:
    four = "One is here. Two is here. Three is here. Four is here."
    assert check(four).speakable
    assert check(four + " Five is here.").counts["sentences"] == 1


def test_the_counts_are_deterministic_and_complete() -> None:
    text = "Open `x` at https://a.io/b 🚀"
    first, second = check(text), check(text)
    assert first.counts == second.counts
    assert set(first.counts) == set(ALL_KEYS)


def test_format_speakable_leaves_out_only_the_content_rules() -> None:
    content_only = check("Open https://github.com/settings/tokens, commit e7ff732, file /etc/hosts.")
    assert not content_only.speakable
    assert content_only.format_speakable
    assert not check("- a list item").format_speakable


# --- the arm under test -------------------------------------------------------------------------------


def test_arm_b_is_the_registered_text() -> None:
    assert h9.sha256(h9.NOTE_B) == h9.NOTE_B_SHA256
    prereg = (REPO / "bench" / "spoken_standard" / "PREREGISTRATION.md").read_text(encoding="utf-8")
    assert h9.NOTE_B_SHA256 in prereg
    assert h9.NOTE_B in prereg


def test_arm_b_is_no_longer_than_the_shipped_note_and_keeps_its_line() -> None:
    shipped = _shipped_note()
    assert len(h9.NOTE_B) <= len(shipped)
    assert len(h9.NOTE_B.split()) <= len(shipped.split())
    assert "---" in h9.NOTE_B and "above the line" in h9.NOTE_B


def test_arm_b_names_none_of_what_it_no_longer_forbids() -> None:
    lowered = h9.NOTE_B.lower()
    for word in ("no ", "never", "don't", "do not", "not ", "markdown", "emoji", "heading", "table"):
        assert word not in lowered, word


def test_the_order_alternates_so_neither_arm_always_goes_first() -> None:
    even, odd = h9.schedule(0), h9.schedule(1)
    assert even[:4] == ["A", "B", "B", "A"] and odd[:4] == ["B", "A", "A", "B"]
    for plan in (even, odd):
        assert plan.count("A") == plan.count("B") == h9.REPLICAS
        assert plan.count("N") == 1


def test_the_corpus_is_balanced() -> None:
    items = corpus()
    assert 30 <= len(items) <= 40
    assert len({r.id for r in items}) == len(items)
    assert sum(r.lang == "pt" for r in items) == sum(r.lang == "en" for r in items)
    assert {r.kind for r in items} == set(KINDS)


# --- the statistics -----------------------------------------------------------------------------------


def test_exact_mcnemar() -> None:
    assert h9.exact_binomial_two_sided(0, 10) == pytest.approx(2 / 1024)
    assert h9.exact_binomial_two_sided(5, 10) == 1.0
    assert h9.exact_binomial_two_sided(0, 0) == 1.0
    assert h9.exact_binomial_two_sided(2, 12) == pytest.approx(2 * (1 + 12 + 66) / 4096)


def test_wilson_and_newcombe() -> None:
    lo, hi = h9.wilson(36, 72)
    assert lo == pytest.approx(1 - hi) and 0.20 < hi - lo < 0.25
    theta, lo, hi = h9.newcombe_paired(30, 2, 12, 28)
    assert theta == pytest.approx(10 / 72)
    assert -1.0 <= lo < theta < hi <= 1.0 and lo > 0
    theta, lo, hi = h9.newcombe_paired(30, 6, 6, 30)
    assert theta == 0.0 and lo < 0 < hi
