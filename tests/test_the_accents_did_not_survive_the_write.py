r"""A file whose accents arrived as `u00ed` instead of `í` is corrupted, and nothing else notices.

A model writing Portuguese prose emits `\uXXXX` for an accented character. When the backslash does
not survive the trip — a re-serialisation, a shell hop, a provider quirk — what lands is `u00ed`,
welded into the middle of the word. The file is still valid UTF-8. Still valid HTML. Every check
downstream passes, and a human opens the page and reads *Utensu00edlios*.

This is not hypothetical. On 2026-08-31 an agent built a marketplace in one run: four files, three
carrying their accents correctly, and the fourth with **zero** accented characters and 23 orphan
sequences. The verify command passed, the diff gate accepted the work, and the corruption reached
the screen. The corrupted file is reproduced below as a regression case.

The guard lives with the syntax check because it is the same idea — a full overwrite that is
obviously not what anyone meant is refused before it destroys the version that was fine — but it
carries its OWN message, because this content parses. Calling it "not valid html" would name the
wrong problem.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.tools.files import WriteFileTool, lost_escapes

# NOTE: this file trips its own guard, and that is correct — it carries the corrupted markup as a
# fixture. Swept over the whole repository (33,066 files) the detector fires exactly twice: on the
# file that was actually corrupted, and here. An agent asked to rewrite THIS file will be refused
# and needs `allow_invalid=true`, which is the escape hatch working as designed.

#: The real corrupted markup, trimmed. Every `u00XX` here stood for an accented letter.
REAL = (
    "<!DOCTYPE html><html lang='pt-BR'><head><meta charset='UTF-8'>"
    "<title>Feito Aqui</title></head><body>"
    "<p>Artesanato brasileiro com histu00f3ria e alma</p>"
    "<button>Utensu00edlios</button><button>Tu00eaxteis</button>"
    "<button>Decorau00e7u00e3o</button><button>Acessu00f3rios</button>"
    "<input placeholder='Buscar produtos, artesu00e3os ou regiu00f5es...'>"
    "<span>u{1F50D}</span><button>u2715</button>"
    "<footer>Feito Aqui u00ae 2024</footer></body></html>"
)

#: What it should have been.
CORRECT = (
    "<!DOCTYPE html><html lang='pt-BR'><head><meta charset='UTF-8'>"
    "<title>Feito Aqui</title></head><body>"
    "<p>Artesanato brasileiro com história e alma</p>"
    "<button>Utensílios</button><button>Têxteis</button>"
    "<button>Decoração</button><button>Acessórios</button>"
    "<input placeholder='Buscar produtos, artesãos ou regiões...'>"
    "<span>🔍</span><button>✕</button>"
    "<footer>Feito Aqui ® 2024</footer></body></html>"
)


def test_the_file_that_actually_shipped_is_refused(tmp_path: Path) -> None:
    """The regression case, verbatim from the run that produced it."""
    out = WriteFileTool(tmp_path).run(path="index.html", content=REAL)
    assert "refused" in out
    assert not (tmp_path / "index.html").exists(), "refused, and yet it wrote"


def test_the_same_file_written_correctly_goes_through(tmp_path: Path) -> None:
    """The control, and the one that would matter if the guard were too eager: the SAME markup with
    its accents intact must be written without a murmur."""
    out = WriteFileTool(tmp_path).run(path="index.html", content=CORRECT)
    assert "wrote" in out
    assert (tmp_path / "index.html").read_text(encoding="utf-8") == CORRECT


def test_the_message_names_the_problem_and_not_a_syntax_error(tmp_path: Path) -> None:
    """This content parses. Reporting it as invalid HTML would send the next attempt hunting a
    syntax error that is not there."""
    out = WriteFileTool(tmp_path).run(path="index.html", content=REAL)
    assert "corrupted" in out
    assert "not valid" not in out
    assert "u00ed" in out, "the message must show what it saw"


def test_the_escape_hatch_still_opens(tmp_path: Path) -> None:
    """Someone documenting escape sequences is writing exactly this on purpose."""
    out = WriteFileTool(tmp_path).run(path="notas.md", content=REAL, allow_invalid=True)
    assert "wrote" in out
    assert (tmp_path / "notas.md").read_text(encoding="utf-8") == REAL


def test_succeeded_is_not_a_lost_escape() -> None:
    """The false positive that a first version actually had, kept as a test because no amount of
    reasoning predicted it.

    `succeeded` contains `uccee`; `c`, `c`, `e` and `e` are all hex digits; and U+CCEE is a CJK
    ideograph. Accepting any textual code point fired on 79 of 32,655 files in this repository.
    """
    prosa = "The run succeeded. The retry succeeded. Everything succeeded, repeatedly."
    assert lost_escapes(prosa) is None


def test_a_file_full_of_real_accents_is_never_flagged() -> None:
    """The second condition, on its own. Even WITH orphan sequences present, a file whose accents
    survived is not a file whose accents were lost."""
    acentuado = "ção " * 40 + " u00ed u00e3 u00e7 "
    assert lost_escapes(acentuado) is None


def test_one_lonely_sequence_is_a_coincidence() -> None:
    """A hex fragment happens. A pattern of them does not."""
    assert lost_escapes("commit u00ed and nothing else") is None


def test_a_proper_escape_with_its_backslash_is_not_the_defect() -> None:
    r"""`\u00ed` in a JavaScript source is a correctly written escape, not a lost one."""
    js = r'const a = "\u00ed"; const b = "\u00e3"; const c = "\u00e7"; const d = "\u00fa";'
    assert lost_escapes(js) is None


@pytest.mark.parametrize(
    "suffix", ["html", "css", "js", "md", "txt", "svg"], ids=lambda s: f"a .{s} file"
)
def test_the_guard_is_not_limited_to_the_types_that_have_a_parser(
    tmp_path: Path, suffix: str
) -> None:
    """The syntax check can only cover `.py` and `.json` — those are what the standard library
    parses for free. This defect is visible in any text at all, and the file it actually hit was
    HTML, which has no check above it."""
    assert "refused" in WriteFileTool(tmp_path).run(path=f"a.{suffix}", content=REAL)


def test_a_python_file_that_parses_but_lost_its_accents_is_still_refused(tmp_path: Path) -> None:
    """The two checks are independent. Valid syntax is not evidence the text survived."""
    src = (
        "# -*- coding: utf-8 -*-\n"
        'TITULO = "Utensu00edlios"\n'
        'SUB = "Decorau00e7u00e3o"\n'
        'ALT = "Acessu00f3rios"\n'
        'MAIS = "histu00f3ria"\n'
    )
    import ast

    ast.parse(src)  # the syntax check has nothing to say about this
    assert "refused" in WriteFileTool(tmp_path).run(path="rotulos.py", content=src)
