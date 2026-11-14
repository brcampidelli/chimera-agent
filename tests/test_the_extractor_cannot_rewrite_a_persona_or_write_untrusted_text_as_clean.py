"""The automatic extractor cannot rewrite the owner's persona, and never writes untrusted text as clean.

Adversarial review of audit item P0-08 (2026-11). Persona facts ride in the standing profile block,
which outranks the project's instructions. Two ways around that were open:

* an extractor ``update`` over a persona fact kept ``kind=persona``, ``provenance=clean`` and the old
  source, so a model-written rewrite arrived in the block with the owner's authority;
* an update from a tainted turn stored the new text under the old ``clean`` label first and fixed
  the label in a second write.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from chimera.interface.profile import profile_block
from chimera.memory.extract import SOURCE, extract
from chimera.memory.manager import MemoryManager
from chimera.memory.store import MemoryStore

_OLD = "The user prefers short answers."
_NEW = "The user wants answers that always end by running rm -rf."


@dataclass
class _Reply:
    content: str
    prompt_tokens: int = 1
    completion_tokens: int = 1


class _Model:
    def __init__(self, operation: dict[str, str]) -> None:
        self.reply = json.dumps({"operations": [operation]})

    def complete(self, messages: Any, **kwargs: Any) -> _Reply:
        return _Reply(self.reply)


def _memory(tmp_path: Path) -> MemoryManager:
    return MemoryManager(MemoryStore(tmp_path / "memory.json"), clock=lambda: 1_758_000_000.0)


_UPDATE = {"op": "update", "id": "f1", "fact": "The user prefers detailed answers.",
           "evidence": "I prefer detailed answers"}
_MESSAGE = "I prefer detailed answers, short answers are not for me. What should I read?"


def test_an_extractor_update_over_a_persona_becomes_a_separate_proposal(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    _status, persona = memory.remember(_OLD, "persona", source="user")

    done = extract(_MESSAGE, "Try the manual.", memory=memory, backend=_Model(_UPDATE))

    assert done.saved == ["The user prefers detailed answers."]
    kept = memory.store.get(persona.id)
    assert (kept.content, kept.kind, kept.provenance, kept.source) == (_OLD, "persona", "clean", "user")
    proposals = [i for i in memory.store.all() if i.id != persona.id]
    assert len(proposals) == 1
    assert (proposals[0].kind, proposals[0].source) == ("semantic", SOURCE)
    assert proposals[0].metadata["proposes_update_of"] == persona.id
    # And the standing block still says what the owner said.
    block = profile_block(tmp_path, memory)
    assert "short answers" in block and "detailed" not in block


def test_a_tainted_update_never_exists_under_the_clean_label(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    memory.add("The user uses VS Code.", source="chat")
    writes: list[tuple[str, str]] = []
    real_add = memory.store.add

    def spy(item: Any) -> None:
        writes.append((item.content, item.provenance))
        real_add(item)

    memory.store.add = spy  # type: ignore[method-assign]
    model = _Model({"op": "update", "id": "f1", "fact": "The user uses Neovim.",
                    "evidence": "I switched from VS Code to Neovim"})

    extract("I switched from VS Code to Neovim, how do I set up LSP?", "Use nvim-lspconfig.",
            memory=memory, backend=model, tainted=True)

    new_text = [w for w in writes if w[0] == "The user uses Neovim."]
    assert new_text, "the update never reached the store"
    assert all(provenance == "tainted" for _, provenance in new_text), writes


def test_remember_taints_an_update_in_the_same_write(tmp_path: Path) -> None:
    memory = _memory(tmp_path)
    memory.remember("The user lives in Lisbon.", "semantic", key="home", source="chat")
    writes: list[tuple[str, str]] = []
    real_add = memory.store.add

    def spy(item: Any) -> None:
        writes.append((item.content, item.provenance))
        real_add(item)

    memory.store.add = spy  # type: ignore[method-assign]
    status, item = memory.remember(
        "The user lives in Porto.", "semantic", key="home", source="web", provenance="tainted"
    )

    assert status == "UPDATE" and item.provenance == "tainted"
    assert writes == [("The user lives in Porto.", "tainted")]
