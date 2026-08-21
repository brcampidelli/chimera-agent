"""The Profile screen was rendering a sentence addressed to the agent.

`MemoryManager.profile()` is a system-prompt block and its first line — "What you know about
the user:" — is an instruction TO A MODEL. The screen rendered the whole block verbatim, under
an already-translated panel heading, in an app translated into ten languages.
"""

from __future__ import annotations

from pathlib import Path


def test_the_profile_screen_gets_facts_not_a_sentence_addressed_to_the_model(
    tmp_path: Path,
) -> None:
    from chimera.memory import MemoryManager, MemoryStore

    mgr = MemoryManager(MemoryStore(tmp_path / "memory.json"))
    mgr.remember("prefers Portuguese", "persona")
    mgr.remember("works on three SaaS products", "persona")

    facts = mgr.profile_facts()

    assert "prefers Portuguese" in facts
    assert not any("What you know about the user" in f for f in facts)


def test_the_prompt_block_keeps_its_preamble() -> None:
    """It is an instruction to a model and the model needs it. Only the SCREEN did not."""
    import tempfile

    from chimera.memory import MemoryManager, MemoryStore

    with tempfile.TemporaryDirectory() as tmp:
        mgr = MemoryManager(MemoryStore(Path(tmp) / "memory.json"))
        mgr.remember("prefers Portuguese", "persona")

        block = mgr.profile()

    assert block.startswith("What you know about the user:")
    assert "- prefers Portuguese" in block


def test_no_persona_facts_is_empty_on_both(tmp_path: Path) -> None:
    from chimera.memory import MemoryManager, MemoryStore

    mgr = MemoryManager(MemoryStore(tmp_path / "memory.json"))

    assert mgr.profile() == "" and mgr.profile_facts() == []
