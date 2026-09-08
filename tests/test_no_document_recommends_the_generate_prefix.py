"""No document recommends the generate prefix.

``ollama/`` is Ollama's ``/api/generate`` as LiteLLM routes it, and #384 measured — offline, batch
and stream — that a tool handed to it never reaches the model as a tool: the catalogue is pasted
into the prompt as a Python repr, and in stream the call comes back as prose the loop cannot see.
``ollama_chat/`` is ``/api/chat`` and round-trips. The docs — `docs/usage.md`, `docs/recipes.md`
and their nine translations — recommended exactly the prefix that cannot: ``CHIMERA_DEFAULT_MODEL=
ollama/llama3`` in prose and ``export CHIMERA_DEFAULT_MODEL=ollama/llama3.1`` in the fenced
quick-start. This holds the line that moved them to ``ollama_chat/``.

Two literals, both language-independent, because a guard written against the English prose fails
the other eight languages for being other languages — the rule this repository already learnt:

* ``CHIMERA_DEFAULT_MODEL=ollama/`` anywhere in a file. The assignment is the recommendation, in
  whatever language the sentence around it is written.
* a model slug ``ollama/<model>`` on a line inside a fenced code block. A fence is what a reader
  copies. Prose that MENTIONS the prefix — "the credential gate recognises ``ollama/…``" — is not a
  recommendation and stays legal, and so does a URL such as ``github.com/ollama/ollama``, which the
  lookbehind excludes.

Measured before the docs were edited (2026-09-08, 122 documents): the first literal matched 20
lines — `docs/usage.md`, `docs/recipes.md` and both files under each of `de es fr it ja pl pt ru
zh` — and the second the 10 fenced ``export`` lines among them. After: nothing, in either.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCUMENTS = sorted(ROOT.glob("docs/**/*.md")) + sorted(ROOT.glob("README*.md"))

_ENV_LITERAL = "CHIMERA_DEFAULT_MODEL=ollama/"
#: ``ollama/`` followed by a model name. The lookbehind refuses a path or word character before it,
#: so ``github.com/ollama/ollama`` is not a slug; ``ollama_chat/`` never matches at all — its
#: ``ollama`` is followed by ``_``, not ``/``. The model must START with a letter or digit, so the
#: prose ``ollama/…`` and the bare ``ollama/`` in backticks are mentions, not recommendations.
_GENERATE_SLUG = re.compile(r"(?<![\w./-])ollama/[A-Za-z0-9][\w.:-]*")
_FENCE = re.compile(r"^\s*(```|~~~)")


def _fenced_lines(markdown: str) -> Iterator[tuple[int, str]]:
    """Every line inside a fenced code block, with its 1-based line number."""
    inside = False
    for number, line in enumerate(markdown.splitlines(), start=1):
        if _FENCE.match(line):
            inside = not inside
            continue
        if inside:
            yield number, line


def _recommendations(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    where = path.relative_to(ROOT).as_posix()
    found = [
        f"{where}:{n}: {line.strip()}"
        for n, line in enumerate(text.splitlines(), start=1)
        if _ENV_LITERAL in line
    ]
    found += [
        f"{where}:{n}: {line.strip()}"
        for n, line in _fenced_lines(text)
        if _GENERATE_SLUG.search(line) and _ENV_LITERAL not in line  # not listed twice
    ]
    return found


def test_the_corpus_is_the_one_the_docstring_describes() -> None:
    """A glob that silently matched nothing would pass forever; say what is being read."""
    assert ROOT / "docs" / "usage.md" in DOCUMENTS
    assert ROOT / "docs" / "i18n" / "pt" / "recipes.md" in DOCUMENTS
    assert ROOT / "README.md" in DOCUMENTS


@pytest.mark.parametrize(
    "path", DOCUMENTS, ids=[p.relative_to(ROOT).as_posix() for p in DOCUMENTS]
)
def test_no_document_recommends_the_generate_prefix(path: Path) -> None:
    assert _recommendations(path) == [], (
        "ollama/ is Ollama's generate endpoint and cannot call tools; recommend ollama_chat/ "
        "(see tests/test_the_adapter_returns_the_tool_call_it_was_given.py)"
    )


def test_the_slug_pattern_catches_a_recommendation_and_spares_a_mention() -> None:
    """The guard's own sensitivity, pinned — a regex that matches nothing passes every document."""
    caught = [
        "export CHIMERA_DEFAULT_MODEL=ollama/llama3.1     # comment",
        "chimera agent --model ollama/qwen2.5:7b 'hi'",
        "CHIMERA_FALLBACK_MODELS=openrouter/x/y,ollama/llama3",
    ]
    spared = [
        "export CHIMERA_DEFAULT_MODEL=ollama_chat/llama3.1",
        "https://github.com/ollama/ollama",
        "the credential gate recognises `ollama/…` (and `ollama_chat/…`)",
        "the `ollama/` prefix",
    ]
    assert all(_GENERATE_SLUG.search(line) for line in caught)
    assert not any(_GENERATE_SLUG.search(line) for line in spared)
    assert list(_fenced_lines("a\n```bash\nx\n```\nb\n~~~\ny\n~~~\n")) == [(3, "x"), (7, "y")]
