"""The multi-agent policy quotes its numbers from the bench files, and a quote that leaves its file fails.

`docs/multi-agent-policy.md` is a decision record: it says a hierarchy lost to one agent at equal
calls, that three panel models carry 1.46 votes, that the Manager approves a fifth of correct work.
A decision record is worth exactly as much as those numbers, and the way this project's pages have
gone wrong before is the page staying put while the bench moved (`test_published_numbers.py` exists
because `docs/benchmarks.md` sat two runs behind its source). The same failure here would be worse:
a policy that keeps refusing a mode on a number the bench has since revised.

So every figure in the policy's "What we measured" table is written as a code span, and each code
span must appear **verbatim** in one of the RESULTS files that row links to. Verbatim, not
normalised: the policy is told to quote, and a check that forgave `−26.7` against `-26.7` would be
forgiving the paraphrase that lets a number drift.

What this does NOT check: that the sentence around a number reads it correctly, or the literature
section's figures (those are papers, not files in this repository). Only a reader can do either.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
POLICY = ROOT / "docs" / "multi-agent-policy.md"
PROTOCOL = ROOT / "bench" / "PROTOCOL.md"

_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\]\(([^)]+)\)")


def _measured_rows() -> list[tuple[str, list[str], list[Path]]]:
    """(question, quoted figures, linked source files) for every row of the measured table."""
    text = POLICY.read_text(encoding="utf-8")
    start = text.index("## What we measured")
    end = text.index("\n## ", start + 1)
    rows = []
    for line in text[start:end].splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if not line.startswith("|") or len(cells) != 3 or set(cells[1]) <= set("-"):
            continue
        if cells[0] == "question":
            continue
        sources = [(POLICY.parent / target).resolve() for target in _LINK.findall(cells[2])]
        rows.append((cells[0], _CODE.findall(cells[1]), sources))
    return rows


def test_the_measured_table_is_there_to_check() -> None:
    """A table the parser cannot find would make every other test here pass by checking nothing."""
    rows = _measured_rows()
    assert len(rows) >= 10, f"found {len(rows)} rows in the measured table"
    for question, figures, sources in rows:
        assert figures, f"row {question!r} quotes no figure as a code span"
        assert sources, f"row {question!r} links no source file"


def test_every_figure_in_the_measured_table_is_in_the_file_it_cites() -> None:
    missing = []
    for question, figures, sources in _measured_rows():
        texts = [p.read_text(encoding="utf-8") for p in sources if p.is_file()]
        assert len(texts) == len(sources), f"row {question!r} links a file that does not exist"
        for figure in figures:
            if not any(figure in t for t in texts):
                names = ", ".join(str(p.relative_to(ROOT)) for p in sources)
                missing.append(f"{figure!r} (row {question!r}) is not in {names}")
    report = "\n".join(missing)
    assert not missing, f"the policy quotes figures its sources do not contain:\n{report}"


def test_the_protocol_rule_the_policy_points_at_exists() -> None:
    """The policy defers every reopening to PROTOCOL §10; a renumbered or deleted rule strands it."""
    policy = POLICY.read_text(encoding="utf-8")
    protocol = PROTOCOL.read_text(encoding="utf-8")
    assert "PROTOCOL.md` §10" in policy or "PROTOCOL.md) §10" in policy
    assert "## 10. Every multi-agent arm has a single-agent arm at equal cost" in protocol
