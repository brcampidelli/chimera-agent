"""Two stores is a decision; the sentence that denied it was a defect.

``chimera chat`` writes ``<home>/sessions`` (``ChatTurn`` prose pairs) and the desktop's coding
conversation writes ``<home>/code_sessions`` (the model's own message list, tool calls included,
plus turn receipts). They are kept apart on purpose — ``chimera/core/code_session.py`` opens with
the argument, and ``tests/test_receipts_survive_reopening.py`` pins the half that a merge would
break.

What was false was the sentence, not the split. ``chat``'s docstring promised *"It is the same
store the desktop app reads, so a thread started here can be continued there and the other way
round."* It has never been true: the app deleted its chat screens on 2026-08-07 (``ea27efc``) and
``chat`` gained persistence two days later (``37d8d3e``). ``docs/commands.md`` is generated from
that docstring, so the claim was published three times over.

This is the guard, and it points BOTH ways. Today no line of app code fetches ``/api/sessions``, so
the docstrings must not say it does. The day somebody wires the Sessions screen back up, this test
fails and asks for the sentence back — which is the failure mode a one-way assertion would miss.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chimera.cli.main import chat, sessions

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps" / "desktop" / "src"

#: Named in the docstrings, and this is what makes the naming checkable rather than decorative.
CHAT_STORE = "<home>/sessions"
CODE_STORE = "<home>/code_sessions"

#: The sentences that assert a desktop reader, matched as phrases rather than on "desktop app".
#:
#: The coarse version would be wrong in both directions: the corrected docstrings still mention the
#: app — to say where its coding conversations go instead — and a future false claim could be
#: written without the words "desktop app" at all. These are the exact families that were there,
#: and the assertion message tells an author which one to write when the app reads this store again.
CLAIMS = (
    "the desktop app reads",
    "the desktop app shows",
    "the same ones the desktop app",
    "opens in the app",
)


def _app_sources() -> list[Path]:
    """The app's own code — not its tests, and not the generated OpenAPI types.

    ``lib/api-schema.ts`` is generated from the server's schema and therefore names every endpoint
    the API has, including the ones nothing calls. Reading it as evidence of use would make this
    guard answer "yes" forever.
    """
    if not APP.is_dir():
        return []
    out = []
    for path in APP.rglob("*"):
        if path.suffix not in {".ts", ".tsx"} or not path.is_file():
            continue
        if ".test." in path.name or path.name == "api-schema.ts":
            continue
        out.append(path)
    return out


def _app_reads_the_chat_store() -> bool:
    return any("/api/sessions" in p.read_text(encoding="utf-8") for p in _app_sources())


def test_the_docstrings_claim_a_desktop_reader_only_when_there_is_one() -> None:
    if not _app_sources():
        pytest.skip("the desktop app sources are not in this tree")

    claimed = [
        name
        for name, doc in (("chat", chat.__doc__ or ""), ("sessions", sessions.__doc__ or ""))
        if any(claim in doc for claim in CLAIMS)
    ]

    if _app_reads_the_chat_store():
        assert claimed == ["chat", "sessions"], (
            "the app fetches /api/sessions again — say so in both docstrings (one of "
            f"{CLAIMS}) and regenerate the reference: python -m chimera.cli.schema_dump > "
            "chimera/_cli_snapshot.json && python scripts/gen_command_reference.py"
        )
    else:
        assert not claimed, (
            f"{claimed}: no line of app code fetches /api/sessions, so the terminal must not "
            "promise a thread started here opens there"
        )


def test_each_docstring_names_the_store_it_writes() -> None:
    """A reader who is told there are two stores has to be told which is which, or the split is
    just as confusing as the false promise it replaced."""
    doc = chat.__doc__ or ""
    assert CHAT_STORE in doc
    assert CODE_STORE in doc  # and where the app's coding conversations go instead


def test_the_generated_reference_carries_the_same_sentence() -> None:
    """`docs/commands.md` is generated from the docstring, so it cannot disagree — unless the
    snapshot it is generated FROM is stale, which is the case this catches."""
    page = (ROOT / "docs" / "commands.md").read_text(encoding="utf-8")

    assert "It is the same store the desktop app reads" not in page
    assert CHAT_STORE in page


def test_the_two_stores_are_different_directories(tmp_path: Path) -> None:
    """Stated in code, because the docstrings above are only honest if this stays true."""
    from chimera.api.sessions import SessionStore
    from chimera.config import Settings
    from chimera.core.code_session import CodeSessionStore

    settings = Settings(CHIMERA_HOME=str(tmp_path))
    chat_store = SessionStore(settings.home / "sessions")
    code_store = CodeSessionStore(settings.home / "code_sessions")

    assert chat_store.root != code_store.root
