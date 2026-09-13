"""The generated spec test's fence stripper: the boundary is decided once, the content is untouched.

arXiv 2609.06993 measured **38.0% of 37,600 generations content-correct and boundary-broken** — the
model wrote the right thing and the parse of its delimiters threw it away. Symmetric fences are the
cause: ``` opens and ``` closes, so a ``` *inside* the payload is indistinguishable from the one
that ends it, and the paper's prescription is to check the boundary separately from the content.

Chimera carried one instance. `_strip_fence` used
`^\\s*```(?:python)?\\s*|\\s*```\\s*$` with `re.MULTILINE`, where `$` matches at the end of every
LINE, so `re.sub` deleted every fence in the reply rather than the two around it. A valid module
whose docstring quoted a fenced example came out with both inner fences and five lines missing —
silently, and the generated test then no longer said what the model wrote.

The five other fence strippers in the tree (`core/checklist.py`, `core/ledger.py`,
`governance/quarantine.py`, `ops/chimera_blog_writer.py`) are NOT multiline, so `^` and `$` anchor
to the whole string and they strip at most one fence at each end. They were checked and left alone:
a change that is not a fix is a risk.
"""

from __future__ import annotations

import logging

import pytest

from chimera.core.spec_test import _strip_fence

FENCED_DOCSTRING = '''```python
def test_it():
    """Shows the fenced example from the docs:

    ```
    x = 1
    ```
    """
    assert True
```'''


def test_a_fence_inside_a_docstring_survives() -> None:
    """The regression. Before the fix this returned the module with both inner fences and the
    lines around them deleted — valid Python, different Python."""
    out = _strip_fence(FENCED_DOCSTRING)
    assert out.startswith("def test_it():")
    assert out.endswith("assert True")
    assert out.count("```") == 2, "the two fences inside the docstring are content, not boundary"
    assert "x = 1" in out


def test_the_outer_fence_is_removed_exactly_once() -> None:
    assert _strip_fence("```python\nassert True\n```") == "assert True"
    assert _strip_fence("```\nassert True\n```") == "assert True"


def test_an_unfenced_reply_is_returned_whole() -> None:
    """`_GEN_SYSTEM` asks for no fences at all, so this is the expected shape."""
    assert _strip_fence("import pytest\n\ndef test_x():\n    assert True") == (
        "import pytest\n\ndef test_x():\n    assert True"
    )


def test_a_line_that_merely_ENDS_with_a_fence_is_not_a_boundary() -> None:
    """The precise shape of the old bug: under MULTILINE, `\\s*```\\s*$` matched here."""
    source = 'BANNER = "see ```"\nassert True'
    assert _strip_fence(source) == source


def test_an_opener_with_no_closer_is_reported_not_repaired(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A truncated reply is a BOUNDARY failure. Trimming it quietly so it looks well-formed is how
    that gets recorded as the model writing bad code."""
    with caplog.at_level(logging.WARNING, logger="chimera.core.spec_test"):
        out = _strip_fence("```python\ndef test_it():\n    assert True")
    assert out == "def test_it():\n    assert True"
    assert "never closed it" in caplog.text


def test_a_closing_fence_needs_its_own_line() -> None:
    """`assert x == \"```\"` ends with a fence and is not a boundary; the closer is anchored to a
    line of its own at the very end."""
    source = 'x = "```"'
    assert _strip_fence(source) == source
